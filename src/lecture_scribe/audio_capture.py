"""Capture of the sound this computer is playing.

The output device is looked up at run time rather than pinned in the config
file: both Windows and PipeWire rename the current output when headphones are
plugged in. Recording writes straight to disk block by block, so the audio
survives whatever happens to the process.
"""

import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import numpy as np
import soundfile as sf

BLOCK_FRAMES = 1024
"""Frames read at a time, about 64 ms at 16 kHz."""

_CAPTURE_CHANNELS = 2
_SILENCE_CHECK_SECONDS = 10.0
_WAV_HEADER_BYTES = 44

# Characters Windows rejects in a path component.
_FORBIDDEN_IN_NAMES = frozenset('<>:"/\\|?*')


class AudioDeviceError(Exception):
    """Raised when a recording cannot be started at all.

    Either no usable output device could be found or addressed, or the WAV
    file could not be created. A recording that starts and then fails is not
    an error of this kind: the audio already captured is kept and the reason
    is reported on the :class:`Recording` instead.
    """


@dataclass(frozen=True, slots=True)
class LoopbackDevice:
    """An output device whose signal can be recorded back.

    Attributes:
        name: Human readable name as reported by the operating system.
        id: Backend identifier of the output device itself.
        is_default: Whether the system currently plays through this device.
    """

    name: str
    id: str
    is_default: bool


@dataclass(frozen=True, slots=True)
class Progress:
    """Live state of a running recording.

    Attributes:
        elapsed: Seconds recorded so far.
        rms: Level of the block just written, from 0.0 to 1.0.
        bytes_written: Size the WAV file has reached.
        warning: Something the user should see now, or ``None``.
    """

    elapsed: float
    rms: float
    bytes_written: int
    warning: str | None


@dataclass(frozen=True, slots=True)
class Recording:
    """Summary of a finished recording.

    Attributes:
        path: The WAV file that was written.
        duration: Length in seconds.
        peak: Loudest single sample, from 0.0 to 1.0.
        mean_rms: Average level across the whole recording.
        interrupted: Why the recording ended before it was asked to, or
            ``None`` when it ran to the stop event as intended. The file is
            complete and playable either way.
    """

    path: Path
    duration: float
    peak: float
    mean_rms: float
    interrupted: str | None = None


def list_loopback_devices() -> list[LoopbackDevice]:
    """Enumerate the output devices available for loopback recording.

    Returns:
        Every output device, the system default first, then by name.

    Raises:
        AudioDeviceError: The sound backend is missing or unreachable.
    """
    soundcard = _import_soundcard()

    try:
        speakers = list(soundcard.all_speakers())
    except RuntimeError as error:
        raise AudioDeviceError(f"cannot list output devices: {error}") from error

    try:
        default_id: str | None = str(soundcard.default_speaker().id)
    except RuntimeError:
        # A machine with no default output still has devices worth showing.
        default_id = None

    devices = [
        LoopbackDevice(
            name=str(speaker.name),
            id=str(speaker.id),
            is_default=str(speaker.id) == default_id,
        )
        for speaker in speakers
    ]
    devices.sort(key=lambda device: (not device.is_default, device.name))
    return devices


def resolve_device(devices: Sequence[LoopbackDevice], wanted: str) -> LoopbackDevice:
    """Choose which device to record from.

    Args:
        devices: Candidates, as returned by :func:`list_loopback_devices`.
        wanted: ``"auto"`` for the system default, otherwise a case insensitive
            substring of the device name.

    Returns:
        The single matching device.

    Raises:
        AudioDeviceError: Nothing matched, the match is ambiguous, or the
            system reports no default output device.
    """
    if not devices:
        raise AudioDeviceError("no output devices found")

    if wanted == "auto":
        for device in devices:
            if device.is_default:
                return device
        raise AudioDeviceError(
            "the system reports no default output device, "
            "name one with --device <substring>"
        )

    needle = wanted.casefold()
    matches = [device for device in devices if needle in device.name.casefold()]
    if not matches:
        available = ", ".join(device.name for device in devices)
        raise AudioDeviceError(
            f"no output device matches '{wanted}'; available: {available}"
        )
    if len(matches) > 1:
        ambiguous = ", ".join(device.name for device in matches)
        raise AudioDeviceError(f"'{wanted}' matches several devices: {ambiguous}")
    return matches[0]


def record_loopback(
    device: LoopbackDevice,
    path: Path,
    *,
    sample_rate: int,
    stop: threading.Event,
    silence_rms: float = 0.0,
    on_progress: Callable[[Progress], None] | None = None,
) -> Recording:
    """Record what an output device is playing until ``stop`` is set.

    Samples go through an open file handle block by block, so the audio
    recorded so far is already on disk if the process dies.

    Args:
        device: Output device to capture, from :func:`resolve_device`.
        path: WAV file to create. Its parent directory must already exist.
        sample_rate: Recording rate in Hz.
        stop: Event that ends the recording, set from a signal handler.
        silence_rms: Level below which the opening seconds count as silence
            and a warning is raised. Zero disables the check.
        on_progress: Called after every block with the current state.

    Returns:
        What the finished file contains, including why it ended early if a
        disk or a device gave out part way through.

    Raises:
        AudioDeviceError: The device has no loopback input, cannot be opened,
            or the file could not be created -- that is, nothing was recorded
            at all.
    """
    microphone = _loopback_microphone(device)

    frames = 0
    energy = 0.0
    peak = 0.0
    interrupted: str | None = None
    silence_checked = silence_rms <= 0.0
    silence_frames = int(_SILENCE_CHECK_SECONDS * sample_rate)

    # A full disk, an unplugged device or a sound server going away all reach
    # here as OSError or RuntimeError. Leaving the `with` closes the file on
    # the way out, so whatever was captured stays valid and playable, and a
    # lecture that stops at minute 47 is worth far more than an exception.
    try:
        with (
            sf.SoundFile(
                path, mode="w", samplerate=sample_rate, channels=1, subtype="PCM_16"
            ) as wav,
            # Two channels are requested even though one is written: asking
            # WASAPI for a single channel returns garbage, so the mixdown
            # happens here.
            microphone.recorder(
                samplerate=sample_rate,
                channels=_CAPTURE_CHANNELS,
                blocksize=BLOCK_FRAMES,
            ) as recorder,
        ):
            while not stop.is_set():
                block = recorder.record(numframes=BLOCK_FRAMES)
                # Mean rather than sum: adding two channels clips past 1.0.
                mono = np.mean(block, axis=1)
                wav.write(mono)

                block_energy = float(np.dot(mono, mono))
                frames += len(mono)
                energy += block_energy
                peak = max(peak, float(np.max(np.abs(mono))))

                warning = None
                if not silence_checked and frames >= silence_frames:
                    silence_checked = True
                    if math.sqrt(energy / frames) < silence_rms:
                        warning = (
                            f"the first {_SILENCE_CHECK_SECONDS:.0f} seconds are "
                            f"silent, check that the lecture plays through "
                            f"'{device.name}' -- recording continues"
                        )

                if on_progress is not None:
                    on_progress(
                        Progress(
                            elapsed=frames / sample_rate,
                            rms=math.sqrt(block_energy / len(mono)),
                            bytes_written=_WAV_HEADER_BYTES + frames * 2,
                            warning=warning,
                        )
                    )
    except (OSError, RuntimeError) as error:
        if frames == 0:
            raise AudioDeviceError(f"cannot record to {path}: {error}") from error
        interrupted = (
            f"the recording stopped early: {error}. Everything captured up to "
            "that point was kept, and can be transcribed as it is"
        )

    return Recording(
        path=path,
        duration=frames / sample_rate,
        peak=peak,
        mean_rms=math.sqrt(energy / frames) if frames else 0.0,
        interrupted=interrupted,
    )


def lecture_dir(root: Path, course: str, when: datetime) -> Path:
    """Pick the folder for one lecture, without creating it.

    Args:
        root: Directory that holds one folder per lecture.
        course: Subject name as typed on the command line.
        when: Start of the lecture, used for the date part of the name.

    Returns:
        ``<root>/<date>_<course>``, with a numeric suffix when that name is
        taken, so a second lecture on the same subject and day never lands on
        top of the first one.

    Raises:
        ValueError: The course name has no characters usable in a folder name.
    """
    base = f"{when:%Y-%m-%d}_{course_slug(course)}"
    candidate = root / base
    counter = 2
    while candidate.exists():
        candidate = root / f"{base}-{counter}"
        counter += 1
    return candidate


def course_slug(course: str) -> str:
    """Turn a course name into one usable path component.

    Whitespace becomes a dash and characters Windows forbids are dropped; the
    rest is left alone, so Korean subject names survive intact.

    Args:
        course: Subject name as typed on the command line.

    Returns:
        The cleaned name.

    Raises:
        ValueError: Nothing usable was left after cleaning.
    """
    dashed = "".join("-" if character.isspace() else character for character in course)
    kept = "".join(
        character
        for character in dashed
        if character not in _FORBIDDEN_IN_NAMES and character.isprintable()
    )
    slug = kept.strip("-. ")
    if not slug:
        raise ValueError(
            f"course name '{course}' has no characters usable in a folder name"
        )
    return slug


def _import_soundcard() -> ModuleType:
    """Import soundcard, turning a broken sound stack into a clear error.

    Returns:
        The soundcard module.

    Raises:
        AudioDeviceError: The backend is missing or refuses to connect.
    """
    # soundcard connects to PulseAudio while being imported, so a broken sound
    # stack fails here rather than halfway through a recording.
    try:
        import soundcard
    except (ImportError, RuntimeError, OSError) as error:
        raise AudioDeviceError(
            f"sound backend unavailable: {error}. On Linux, check that PipeWire "
            "or PulseAudio is running."
        ) from error
    return cast(ModuleType, soundcard)


# soundcard ships no type information, so its microphone object stays untyped.
def _loopback_microphone(device: LoopbackDevice) -> Any:  # noqa: ANN401
    """Open the loopback input that carries an output device's signal.

    The lookup goes by exact identifier rather than through soundcard's fuzzy
    name matching. A USB microphone with a headphone jack appears twice under
    one name, once as an output and once as a real microphone, and a name
    match can silently return the microphone instead of the loopback.

    Args:
        device: Output device whose signal is wanted.

    Returns:
        A soundcard microphone reading that device's output.

    Raises:
        AudioDeviceError: No loopback input exists for this device.
    """
    soundcard = _import_soundcard()

    # PulseAudio exposes the loopback as a separate "<output>.monitor" source,
    # while WASAPI reuses the output device's own identifier for it.
    wanted = {device.id, f"{device.id}.monitor"}
    try:
        microphones = list(soundcard.all_microphones(include_loopback=True))
    except RuntimeError as error:
        raise AudioDeviceError(f"cannot list inputs: {error}") from error

    for microphone in microphones:
        if str(microphone.id) in wanted:
            return microphone

    raise AudioDeviceError(
        f"no loopback input for '{device.name}'; the device may have been "
        "unplugged since it was listed"
    )
