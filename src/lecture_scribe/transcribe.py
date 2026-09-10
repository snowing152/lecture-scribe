"""Speech recognition of a recorded WAV file.

Loading the model and running it on one file are kept apart: `scribe text`
loads a model once and reuses it for every WAV given on the command line,
since loading alone can take longer than transcribing a short recording.
"""

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

# Standard faster-whisper model names live under this Hugging Face account.
_REPO_DIR_TEMPLATE = "models--Systran--faster-whisper-{name}"


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
        available: Whether at least one CUDA device was found.
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
class Transcription:
    """Result of transcribing one WAV file.

    Attributes:
        segments: Recognised speech, materialized in full so that a decoding
            error surfaces here rather than while some later module iterates
            a generator.
        audio_duration: Length of the source audio in seconds.
        decode_seconds: Wall-clock time the decoder took.
    """

    segments: list[Segment]
    audio_duration: float
    decode_seconds: float


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
    """Report whether CTranslate2 can see a CUDA device.

    This only asks the CUDA driver how many devices exist; it does not load
    cuBLAS or cuDNN, so a missing runtime library is not caught here. That
    failure surfaces instead as a :class:`TranscriptionError` from
    :func:`load_model`, the first place a model actually runs a forward pass.

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
    return GpuStatus(
        True,
        count,
        f"{count} CUDA device(s) visible "
        "(cuBLAS and cuDNN are loaded only when asr.gpu = true)",
    )


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

    Returns:
        The recognised segments plus timing.

    Raises:
        TranscriptionError: The file could not be decoded, or GPU decoding
            was requested but the CUDA runtime libraries are not installed.
    """
    start = time.monotonic()
    try:
        raw_segments, info = model.transcribe(
            str(wav),
            language=language,
            beam_size=beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
        )
        segments = [
            Segment(
                start=raw.start,
                end=raw.end,
                text=raw.text.strip(),
                avg_logprob=raw.avg_logprob,
            )
            for raw in cast(Iterator[Any], raw_segments)
        ]
    except (OSError, RuntimeError) as error:
        raise TranscriptionError(f"cannot transcribe {wav}: {error}") from error

    return Transcription(
        segments=segments,
        audio_duration=float(info.duration),
        decode_seconds=time.monotonic() - start,
    )


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
