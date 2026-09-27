"""Speech recognition of a recorded WAV file.

Loading the model and running it on one file are kept apart: `scribe text`
loads a model once and reuses it for every WAV given on the command line,
since loading alone can take longer than transcribing a short recording.
"""

import ctypes
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType
from typing import Any, cast

# Standard faster-whisper model names live under this Hugging Face account.
_REPO_DIR_TEMPLATE = "models--Systran--faster-whisper-{name}"

_CUDA_PACKAGES = ("nvidia.cublas", "nvidia.cuda_nvrtc")
"""Packages the ``cuda`` extra unpacks the CUDA shared libraries into."""

# The one CUDA library this CTranslate2 build reaches for. It carries no
# cuDNN reference at all, whatever the faster-whisper documentation says.
_CUBLAS_LIBRARY = "cublas64_12.dll" if os.name == "nt" else "libcublas.so.12"


class TranscriptionError(Exception):
    """Raised when the model cannot be loaded or a file cannot be decoded."""


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """Whether a model is ready to run offline.

    Attributes:
        name: Model name as configured.
        cached: Whether the weights are already on disk.
        location: Directory the weights are expected in.
    """

    name: str
    cached: bool
    location: Path


@dataclass(frozen=True, slots=True)
class GpuStatus:
    """Whether CUDA is usable for decoding.

    Attributes:
        available: Whether decoding on the GPU would actually run here, which
            takes both a device and the CUDA library to drive it.
        device_count: Number of CUDA devices visible to CTranslate2.
        detail: One line description for the doctor report.
    """

    available: bool
    device_count: int
    detail: str


@dataclass(frozen=True, slots=True)
class Segment:
    """One recognised utterance.

    Attributes:
        start: Start time in seconds from the beginning of the recording.
        end: End time in seconds.
        text: Recognised text, whitespace-trimmed.
        avg_logprob: Mean log probability the decoder assigned to this
            segment; less negative is more confident.
    """

    start: float
    end: float
    text: str
    avg_logprob: float


@dataclass(frozen=True, slots=True)
class Decoding:
    """How far recognition has got through one recording.

    The decoder yields segments as it finishes them, so this is reported
    once per segment rather than on a timer.

    Attributes:
        position: End of the last segment recognised, in seconds from the
            start of the recording. Silence skipped by the VAD is already
            accounted for, so this is a real position in the audio.
        audio_duration: Length of the whole recording in seconds.
        elapsed: Wall-clock seconds spent decoding so far.
        segments: How many segments have been recognised.
        text: Text of the segment just recognised.
    """

    position: float
    audio_duration: float
    elapsed: float
    segments: int
    text: str

    @property
    def fraction(self) -> float:
        """How much of the recording is done, from 0.0 to 1.0.

        Returns:
            The share of the audio recognised so far. A recording of no
            length counts as finished rather than dividing by zero.
        """
        if self.audio_duration <= 0.0:
            return 1.0
        return max(0.0, min(1.0, self.position / self.audio_duration))


@dataclass(frozen=True, slots=True)
class Transcription:
    """Result of transcribing one WAV file.

    Attributes:
        segments: Recognised speech, materialized in full so that a decoding
            error surfaces here rather than while some later module iterates
            a generator.
        audio_duration: Length of the source audio in seconds.
        decode_seconds: Wall-clock time the decoder took.
        interrupted: Why recognition gave up before the end of the file, or
            ``None`` when it ran to the last segment. The segments already
            decoded are handed back either way, but they cover only the part
            of the lecture named in the message, so a transcript written from
            them would look finished while stopping mid-lecture.
    """

    segments: list[Segment]
    audio_duration: float
    decode_seconds: float
    interrupted: str | None = None


def compute_type(gpu: bool) -> str:
    """Pick the numeric precision for the decoder.

    Full float16 large-v3 needs roughly 10 GB of VRAM, which most laptop GPUs
    do not have; int8_float16 fits comfortably and costs little accuracy.

    Args:
        gpu: Whether the model runs on CUDA.

    Returns:
        A CTranslate2 compute type name.
    """
    return "int8_float16" if gpu else "int8"


def hub_cache_dir() -> Path:
    """Locate the Hugging Face cache without importing huggingface_hub.

    Returns:
        The directory downloaded models are stored in, whether or not it
        exists yet.
    """
    hub = os.environ.get("HF_HUB_CACHE")
    if hub:
        return Path(hub)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    cache_root = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_root) if cache_root else Path.home() / ".cache"
    return base / "huggingface" / "hub"


def model_status(name: str) -> ModelStatus:
    """Report whether model weights are already downloaded.

    Only the standard model names follow the Hugging Face naming pattern, so
    an unusual name may be reported as missing even when it is cached; the
    first real run downloads or reuses it correctly either way.

    Args:
        name: Model name such as ``large-v3``, or a path to a local model
            directory.

    Returns:
        Where the weights are expected and whether they are there.
    """
    local = Path(name).expanduser()
    if local.is_dir():
        return ModelStatus(
            name=name, cached=(local / "model.bin").is_file(), location=local
        )

    location = hub_cache_dir() / _REPO_DIR_TEMPLATE.format(name=name)
    cached = any(location.glob("snapshots/*/model.bin"))
    return ModelStatus(name=name, cached=cached, location=location)


def gpu_status() -> GpuStatus:
    """Report whether the GPU could decode a lecture on this machine.

    Two separate things have to hold: the driver must show a device, and
    cuBLAS must be loadable. A machine can easily have the first without the
    second, which used to be found out twenty minutes into a lecture rather
    than here -- CTranslate2 asks for the library at its first forward pass,
    not when the model is loaded.

    Returns:
        The device count plus a line explaining it, including the reason when
        no device is usable.
    """
    # ctranslate2 ships with faster-whisper; importing it lazily keeps doctor
    # useful on a machine where only the recording half is installed.
    try:
        import ctranslate2
    except ImportError as error:
        return GpuStatus(False, 0, f"unknown, ctranslate2 not importable ({error})")

    try:
        count = int(ctranslate2.get_cuda_device_count())
    except RuntimeError as error:
        return GpuStatus(False, 0, f"unavailable ({error})")

    if count == 0:
        return GpuStatus(False, 0, "no CUDA device, decoding runs on the CPU")

    if not _cublas_available():
        return GpuStatus(
            False,
            count,
            f"{count} CUDA device(s) visible, but {_CUBLAS_LIBRARY} cannot "
            "be loaded in this environment",
        )
    return GpuStatus(True, count, f"{count} CUDA device(s) visible, cuBLAS ready")


def load_model(model: str, *, gpu: bool) -> Any:  # noqa: ANN401 (untyped library)
    """Load an ASR model, downloading it on first use.

    Args:
        model: faster-whisper model name or a path to a local model directory.
        gpu: Whether to run on CUDA instead of the CPU.

    Returns:
        A ``faster_whisper.WhisperModel`` ready to transcribe.

    Raises:
        TranscriptionError: The weights could not be read or downloaded, or
            the requested device or compute type is not supported. A missing
            CUDA runtime library is reported only once transcription is
            attempted, since loading the model does not exercise it.
    """
    faster_whisper = _import_faster_whisper()
    device = "cuda" if gpu else "cpu"
    if gpu:
        _preload_cuda_libraries()
    try:
        return faster_whisper.WhisperModel(
            model, device=device, compute_type=compute_type(gpu)
        )
    except (OSError, ValueError, RuntimeError) as error:
        raise TranscriptionError(f"cannot load model '{model}': {error}") from error


def transcribe(
    model: Any,  # noqa: ANN401 (untyped library)
    wav: Path,
    *,
    language: str,
    beam_size: int,
    on_progress: Callable[[Decoding], None] | None = None,
    stop: threading.Event | None = None,
) -> Transcription:
    """Recognise the speech in one WAV file.

    Language is fixed rather than auto-detected: on the first seconds of a
    recording, auto-detection confuses Korean with Japanese, and the mistake
    then applies to the whole file. VAD trims silence before it reaches the
    model, which both speeds up decoding and avoids the hallucinated text
    Whisper produces on pure silence. Context from previous segments is
    switched off on purpose: on a 90 minute recording, one wrong segment would
    otherwise keep dragging the same error into every segment after it.

    Args:
        model: A model from :func:`load_model`.
        wav: Recording to transcribe.
        language: Spoken language, as a Whisper language code.
        beam_size: Beam search width.
        on_progress: Called with each segment as the decoder finishes it.
            The total length is known before the first one arrives, so a
            caller can show real progress rather than a spinner.
        stop: Event that ends recognition early, leaving the segments decoded
            so far on the result with a reason. It is read between segments,
            and the decoder scans the whole file for speech before yielding
            the first one -- 21 seconds on a measured 81 minute lecture --
            so a stop set at the very beginning takes that long to bite.

    Returns:
        The recognised segments plus timing, and why it stopped if it did
        not reach the end.

    Raises:
        TranscriptionError: The file could not be decoded, or GPU decoding
            was requested but the CUDA runtime libraries are not installed.
    """
    start = time.monotonic()
    segments: list[Segment] = []
    interrupted: str | None = None
    try:
        raw_segments, info = model.transcribe(
            str(wav),
            language=language,
            beam_size=beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
        )
        # The length is known before any segment is decoded, which is what
        # makes progress reportable at all.
        audio_duration = float(info.duration)
        for raw in cast(Iterator[Any], raw_segments):
            segment = Segment(
                start=raw.start,
                end=raw.end,
                text=raw.text.strip(),
                avg_logprob=raw.avg_logprob,
            )
            segments.append(segment)
            if on_progress is not None:
                on_progress(
                    Decoding(
                        position=segment.end,
                        audio_duration=audio_duration,
                        elapsed=time.monotonic() - start,
                        segments=len(segments),
                        text=segment.text,
                    )
                )
            # Asked between segments rather than inside one: a segment being
            # decoded is already paid for, and abandoning the generator is
            # what actually stops the work.
            if stop is not None and stop.is_set():
                minutes, seconds = divmod(int(segment.end), 60)
                interrupted = (
                    f"recognition was stopped {minutes}:{seconds:02d} into the "
                    "recording, so nothing was written; the recording itself "
                    "is untouched and can be transcribed again"
                )
                break
    except (OSError, RuntimeError) as error:
        raise TranscriptionError(f"cannot transcribe {wav}: {error}") from error

    return Transcription(
        segments=segments,
        audio_duration=audio_duration,
        decode_seconds=time.monotonic() - start,
        interrupted=interrupted,
    )


def transcript_path(wav: Path) -> Path:
    """Pick where the transcript of a recording is written.

    Named after the folder as it is now rather than after the WAV, so a
    recording from before files were named this way, still ``audio.wav``,
    does not end up beside an ``audio.txt``.

    Args:
        wav: The recording being transcribed.

    Returns:
        ``<folder>/<folder name>.txt``.
    """
    return wav.parent / f"{wav.parent.name}.txt"


def earlier_transcript(wav: Path) -> Path | None:
    """Find a transcript already made from a recording, if there is one.

    Args:
        wav: The recording.

    Returns:
        The first of these that exists: the name `transcript_path` gives
        today; the WAV's own name with ``.txt``, which is where a transcript
        is after its folder was renamed, since both files carry the old
        folder name; and ``transcript.txt``, the name every transcript had
        before 2026-09-14. None when there is no transcript at all.
    """
    candidates = (
        transcript_path(wav),
        wav.with_suffix(".txt"),
        wav.parent / "transcript.txt",
    )
    return next((path for path in candidates if path.is_file()), None)


def _cublas_available() -> bool:
    """Whether the CUDA library CTranslate2 needs can be loaded.

    Returns:
        Whether cuBLAS is reachable, from the environment's own copy or from
        a system-wide install. Loading it costs a memory mapping and no
        decoding, so it is cheap enough to ask in the doctor report.
    """
    _preload_cuda_libraries()
    try:
        ctypes.CDLL(_CUBLAS_LIBRARY)
    except OSError:
        return False
    return True


def _preload_cuda_libraries() -> None:
    """Make the CUDA libraries inside the virtual environment findable.

    CTranslate2 reaches for ``libcublas.so.12`` with a bare ``dlopen``, which
    searches the system directories and never looks inside the environment
    the ``cuda`` extra installed it into. Loading each library here by its
    full path registers its soname with the loader, so CTranslate2's own
    dlopen finds it already in memory. The alternative is an LD_LIBRARY_PATH
    exported before every run, which a desktop menu entry cannot do.

    A library that will not load is passed over rather than reported. The
    error worth showing is CTranslate2's own, which names what it wanted.
    """
    libraries: list[Path] = []
    for package in _CUDA_PACKAGES:
        try:
            spec = find_spec(package)
        except ImportError:
            continue
        if spec is None or spec.submodule_search_locations is None:
            continue
        for location in spec.submodule_search_locations:
            root = Path(location)
            # The wheels put shared objects under lib/ and DLLs under bin/.
            # Only the first of the two has been run here.
            libraries += sorted(root.glob("lib/lib*.so.*"))
            libraries += sorted(root.glob("bin/*.dll"))

    # Two passes: these libraries carry no RUNPATH, so one that needs a
    # sibling loads only once that sibling is in memory globally. libcublas
    # needs libcublasLt, and sorting by name puts them the wrong way round.
    for _attempt in range(2):
        unresolved: list[Path] = []
        for library in libraries:
            try:
                ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                unresolved.append(library)
        if not unresolved:
            return
        libraries = unresolved


def _import_faster_whisper() -> ModuleType:
    """Import faster_whisper, turning a broken install into a clear error.

    Returns:
        The faster_whisper module.

    Raises:
        TranscriptionError: The package is missing.
    """
    try:
        import faster_whisper
    except ImportError as error:
        raise TranscriptionError(
            f"faster-whisper is not installed: {error}. Run 'uv sync'."
        ) from error
    return cast(ModuleType, faster_whisper)
