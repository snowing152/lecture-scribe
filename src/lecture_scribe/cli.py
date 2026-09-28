"""Command line entry point: argument parsing and wiring, nothing else."""

import argparse
import math
import platform
import shutil
import signal
import sys
import threading
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import FrameType

from lecture_scribe.audio_capture import (
    AudioDeviceError,
    LoopbackDevice,
    Progress,
    lecture_dir,
    list_loopback_devices,
    record_loopback,
    recording_path,
    resolve_device,
)
from lecture_scribe.config import (
    CONFIG_FILENAME,
    Config,
    ConfigError,
    load_config,
    merge_cli,
)
from lecture_scribe.format_text import render_transcript
from lecture_scribe.keep_awake import awake_status, keep_awake
from lecture_scribe.launcher import LauncherError
from lecture_scribe.launcher import install as install_launcher
from lecture_scribe.launcher import remove as remove_launcher
from lecture_scribe.transcribe import (
    Decoding,
    TranscriptionError,
    compute_type,
    gpu_status,
    load_model,
    model_status,
    transcribe,
    transcript_path,
)
from lecture_scribe.upload import announce, push_transcript, upload_status

# Commands whose arguments are already fixed but whose behaviour arrives later.
_PENDING = {"find": 5}

_LABEL_WIDTH = 14


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line interface.

    Args:
        argv: Argument list to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as error:
        print(f"config error: {error}", file=sys.stderr)
        return 2

    command: str = args.command
    if command == "doctor":
        return _doctor(config)
    if command == "rec":
        device: str | None = args.device
        course: str = args.course
        then_text: bool = args.then_text
        return _rec(merge_cli(config, device=device), course, then_text=then_text)
    if command == "text":
        wavs: list[Path] = args.wav
        model: str | None = args.model
        return _text(merge_cli(config, model=model), wavs)
    if command == "gui":
        return _gui(config)
    if command == "launcher":
        return _launcher(remove=args.remove)

    step = _PENDING[command]
    print(f"`scribe {command}` arrives in step {step}.", file=sys.stderr)
    return 1


def _build_parser() -> argparse.ArgumentParser:
    """Describe the full command line, including the commands still pending."""
    parser = argparse.ArgumentParser(
        prog="scribe",
        description="Record a lecture playing on this computer and transcribe it.",
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    commands.add_parser("doctor", help="check devices, model and GPU; writes nothing")

    rec = commands.add_parser("rec", help="record system audio until Ctrl+C")
    rec.add_argument(
        "--course", required=True, help="subject name, used in the folder name"
    )
    rec.add_argument("--then-text", action="store_true", help="transcribe once stopped")
    rec.add_argument("--device", help="substring of the output device to record")

    text = commands.add_parser("text", help="transcribe one or more recordings")
    text.add_argument("wav", nargs="+", type=Path, help="WAV files to transcribe")
    text.add_argument("--model", help="override the configured ASR model")

    commands.add_parser("gui", help="open the desktop window")

    launcher = commands.add_parser(
        "launcher", help="put the window in this machine's application menu"
    )
    launcher.add_argument(
        "--remove", action="store_true", help="take the menu entry back out"
    )

    find = commands.add_parser("find", help="full text search across all transcripts")
    find.add_argument("query", help="text to look for")

    return parser


def _gui(config: Config) -> int:
    """Open the desktop window.

    Qt is imported here rather than at module level: it takes noticeably
    longer to import than everything else the CLI touches, and `scribe rec`
    should not pay for a window it never opens.

    Args:
        config: Effective configuration.

    Returns:
        The process exit code.
    """
    try:
        from lecture_scribe.gui import run
    except ImportError as error:
        print(
            f"error: PySide6 is not installed: {error}. Run 'uv sync'.",
            file=sys.stderr,
        )
        return 1
    return run(config)


def _launcher(*, remove: bool) -> int:
    """Add the window to the desktop's application menu, or take it out.

    The entry is pinned to the current directory rather than to the package,
    because that is the directory whose `config.toml` the tool reads. Running
    this from somewhere else deliberately produces a different entry.

    Args:
        remove: Whether to delete the entry instead of writing one.

    Returns:
        The process exit code.
    """
    if remove:
        try:
            deleted = remove_launcher()
        except LauncherError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
        if not deleted:
            print("nothing to remove, there is no menu entry")
            return 0
        for path in deleted:
            _row("removed", str(path))
        return 0

    workdir = Path.cwd()
    try:
        installed = install_launcher(workdir)
    except LauncherError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print("added to the application menu")
    _row("entry", str(installed.entry))
    _row("runs", str(installed.target))
    _row("starts in", str(installed.workdir))
    if installed.icon is not None:
        _row("icon", str(installed.icon))

    if not (workdir / CONFIG_FILENAME).is_file():
        print(
            f"\nnote: there is no {CONFIG_FILENAME} here, so the window will run "
            "on the built-in defaults. Run this again from the directory holding "
            "your config file to pin that one instead."
        )
    return 0


def _rec(config: Config, course: str, *, then_text: bool) -> int:
    """Record system audio into a fresh lecture folder until Ctrl+C.

    Args:
        config: Effective configuration, command line overrides applied.
        course: Subject name, used for the folder name.
        then_text: Whether transcription was asked for afterwards.

    Returns:
        The process exit code.
    """
    if then_text:
        print("note: --then-text is not wired up yet, it arrives in step 4\n")

    try:
        device = resolve_device(list_loopback_devices(), config.audio.device)
    except AudioDeviceError as error:
        print(f"audio error: {error}", file=sys.stderr)
        return 1

    try:
        folder = lecture_dir(config.output.dir, course, datetime.now())
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    folder.mkdir(parents=True)
    target = recording_path(folder)

    awake = keep_awake("recording a lecture")
    print(f"recording to {target}")
    _row("device", f"{device.name} (loopback)")
    _row("format", f"{config.audio.sample_rate} Hz mono, 16 bit")
    _row("keep awake", _awake_line(awake.held, awake.detail))
    _row("stop", "Ctrl+C")
    print()

    stop = threading.Event()
    _install_stop_handler(stop)
    status = _StatusLine()

    def show(progress: Progress) -> None:
        """Draw the recording line, and any warning that arrived with it."""
        if progress.warning is not None:
            status.say(f"warning: {progress.warning}")
        status.draw(_recording_line(progress))

    try:
        recording = record_loopback(
            device,
            target,
            sample_rate=config.audio.sample_rate,
            stop=stop,
            silence_rms=config.audio.silence_rms,
            on_progress=show,
        )
    except AudioDeviceError as error:
        status.clear()
        print(f"audio error: {error}", file=sys.stderr)
        return 1
    finally:
        awake.release()
    status.clear()

    print(f"stopped after {_hms(recording.duration)}")
    _row("size", f"{target.stat().st_size / 1_000_000:.1f} MB")
    _row("level", f"mean {_dbfs(recording.mean_rms)}, peak {_dbfs(recording.peak)}")
    if recording.interrupted is not None:
        print(f"\n{recording.interrupted}.", file=sys.stderr)
        return 1
    if recording.mean_rms < config.audio.silence_rms:
        print(
            "\nthe recording is silent from end to end. The audio was kept anyway; "
            f"check that the lecture was playing through '{device.name}'."
        )
    return 0


def _text(config: Config, wavs: list[Path]) -> int:
    """Transcribe one or more recordings, writing a readable transcript of each.

    Paths are checked before the model is loaded: large-v3 can take a while
    to load or download, and a typo in a filename should fail in an instant
    rather than after that wait -- doubly so since a model already loading in
    another process blocks a second load on the same cache until it finishes.

    The model is loaded once and reused for every valid file: loading alone
    can take longer than transcribing a short recording, and a lecture
    archive may hold many files.

    One bad file is reported and skipped rather than aborting the rest of the
    batch; a missing model affects every file identically and aborts
    immediately instead. Ctrl+C ends the whole batch: it is the user saying
    they want their machine back, not that this one file is unwanted.

    Args:
        config: Effective configuration, command line overrides applied.
        wavs: Recordings to transcribe.

    Returns:
        The process exit code: 0 if every file transcribed, 1 if any failed.
    """
    failures = 0
    existing = []
    for wav in wavs:
        if wav.is_file():
            existing.append(wav)
        else:
            print(f"error: {wav} does not exist", file=sys.stderr)
            failures += 1
    if not existing:
        return 1

    # Loading the model, and on a first run downloading it, is part of the
    # wait: an idle suspend during it would end the batch just the same.
    with keep_awake("transcribing lectures") as awake:
        if not awake.held:
            print(f"note: the desktop may lock or suspend meanwhile, {awake.detail}")
        return _transcribe_all(config, existing, failures)


def _transcribe_all(config: Config, wavs: list[Path], failures: int) -> int:
    """Load the model once, then transcribe each recording in turn.

    Args:
        config: Effective configuration, command line overrides applied.
        wavs: Recordings already known to exist.
        failures: Files already reported as missing.

    Returns:
        The process exit code: 0 if every file transcribed, 1 if any failed.
    """
    print(f"loading {config.asr.model} ({compute_type(config.asr.gpu)}) ...")
    try:
        model = load_model(config.asr.model, gpu=config.asr.gpu)
    except TranscriptionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Loading, and on the first run downloading, is the one stretch a
        # press should end outright: nothing has been read or written yet.
        print("\ninterrupted while loading the model.", file=sys.stderr)
        return 130

    status = _StatusLine()
    # Installed only now: a Ctrl+C during a model download of several
    # gigabytes should still stop it dead, and nothing is at stake yet.
    stop = threading.Event()
    _install_stop_handler(stop, absorb_repeats=False)

    for wav in wavs:
        print(f"\n{wav}")
        # The decoder scans the whole file for speech before it yields the
        # first segment -- 21 seconds on an 81 minute lecture -- and the
        # progress line cannot start until then. Saying so beats a dead
        # terminal that looks like a hang.
        status.draw("  reading the recording and finding the speech in it ...")
        try:
            result = transcribe(
                model,
                wav,
                language=config.asr.language,
                beam_size=config.asr.beam_size,
                on_progress=lambda decoding: status.draw(_decoding_line(decoding)),
                stop=stop,
            )
        except TranscriptionError as error:
            status.clear()
            print(f"  error: {error}", file=sys.stderr)
            failures += 1
            continue
        except KeyboardInterrupt:
            status.clear()
            print("\n  interrupted; nothing was written.", file=sys.stderr)
            return 130
        status.clear()

        if result.interrupted is not None:
            # Half a lecture written to a transcript would read as a whole
            # one, and would sit on top of a complete transcript from an
            # earlier run. The partial segments are dropped instead.
            print(f"  {result.interrupted}.", file=sys.stderr)
            return 1

        text_path = transcript_path(wav)
        text_path.write_text(
            render_transcript(
                result.segments,
                title=wav.parent.name,
                model=config.asr.model,
                language=config.asr.language,
                audio_duration=result.audio_duration,
                paragraph_gap=config.output.paragraph_gap,
                paragraph_target=config.output.paragraph_target,
                paragraph_max=config.output.paragraph_max,
            ),
            encoding="utf-8",
        )

        ratio = (
            result.decode_seconds / result.audio_duration
            if result.audio_duration > 0
            else 0.0
        )
        _row("segments", str(len(result.segments)))
        _row("audio", _hms(result.audio_duration))
        _row(
            "decoding",
            f"{_hms(result.decode_seconds)} ({ratio:.2f}x audio length)",
        )
        _row("wrote", text_path.name)
        if config.upload.enabled:
            _upload(config, text_path, status)

    return 1 if failures else 0


def _install_stop_handler(
    stop: threading.Event, *, absorb_repeats: bool = True
) -> None:
    """Make Ctrl+C end the work in hand instead of raising KeyboardInterrupt.

    Args:
        stop: Event the working loop watches.
        absorb_repeats: Whether later presses are swallowed as well.
            Recording wants them swallowed: by then the loop has been left and
            the WAV is being closed, which is the moment worth protecting.
            Recognition does not: the decoder scans a long file for half a
            minute before it reaches a point where it can give up, and it
            writes nothing until it is finished, so a second press should get
            the user out at once.
    """

    def handle(_signum: int, _frame: FrameType | None) -> None:
        stop.set()
        if not absorb_repeats:
            signal.signal(signal.SIGINT, signal.default_int_handler)

    signal.signal(signal.SIGINT, handle)
    # A logout or a plain `kill` should end the lecture the same way.
    signal.signal(signal.SIGTERM, handle)


def _upload(config: Config, transcript: Path, status: "_StatusLine") -> None:
    """Copy a written transcript to the remote and say how it went.

    A failure is a warning, not an error: the transcript is safe on disk and
    the exit code stays what recognition made it.
    """
    status.draw(f"  uploading to {config.upload.remote} ...")
    upload = push_transcript(transcript, config.output.dir, config.upload)
    status.clear()
    if upload.ok:
        _row("uploaded", upload.destination)
        announce(upload)
    else:
        print(f"  warning: not uploaded, {upload.error}", file=sys.stderr)


class _StatusLine:
    """The one line of live progress redrawn while a command works.

    Nothing is drawn when output is not a terminal, so a log file does not
    fill up with carriage returns.
    """

    _INTERVAL_SECONDS = 0.25

    def __init__(self) -> None:
        """Prepare the line, checking once whether it can be redrawn at all."""
        self._enabled = sys.stdout.isatty()
        self._width = 0
        self._next_draw = 0.0

    def draw(self, line: str) -> None:
        """Redraw the line, no more often than the refresh interval.

        Args:
            line: The text to show, already formatted.
        """
        now = time.monotonic()
        if not self._enabled or now < self._next_draw:
            return
        self._next_draw = now + self._INTERVAL_SECONDS

        self._width = len(line)
        print(f"\r{line}", end="", flush=True)

    def say(self, message: str) -> None:
        """Print a message on a row of its own, leaving the line tidy.

        Args:
            message: The text to print.
        """
        self.clear()
        print(message)

    def clear(self) -> None:
        """Wipe the status line so later output starts on a clean row."""
        if self._enabled and self._width:
            print("\r" + " " * self._width + "\r", end="", flush=True)
            self._width = 0


def _hms(seconds: float) -> str:
    """Format a duration as ``H:MM:SS``."""
    whole = int(seconds)
    return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def _dbfs(level: float) -> str:
    """Format a level from 0.0 to 1.0 as decibels below full scale."""
    if level <= 0.0:
        return "-inf dBFS"
    return f"{20 * math.log10(level):.1f} dBFS"


def _bar(fraction: float, width: int = 12) -> str:
    """Draw a coarse bar for a fraction from 0.0 to 1.0."""
    filled = round(width * max(0.0, min(1.0, fraction)))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def _meter(level: float, width: int = 12) -> str:
    """Draw a coarse level bar, from -60 dBFS to clipping."""
    loudness = 0.0
    if level > 0.0:
        loudness = (20 * math.log10(level) + 60) / 60
    return _bar(loudness, width)


def _recording_line(progress: Progress) -> str:
    """Format the live line shown while recording."""
    return (
        f"  {_hms(progress.elapsed)}   {_dbfs(progress.rms):>11}  "
        f"{_meter(progress.rms)}  {progress.bytes_written / 1_000_000:6.1f} MB"
    )


def _decoding_line(decoding: Decoding) -> str:
    """Format the live line shown while recognising.

    The estimate assumes the rest of the file decodes at the rate the part
    already done did, which is close enough on one lecture recorded in one
    sitting.
    """
    done = decoding.fraction
    speed = decoding.position / decoding.elapsed if decoding.elapsed > 0 else 0.0
    left = decoding.elapsed / done - decoding.elapsed if done > 0 else 0.0
    return (
        f"  {done * 100:3.0f}%  {_bar(done)}  {_hms(decoding.position)} of "
        f"{_hms(decoding.audio_duration)}  {speed:5.1f}x  {_hms(left)} left"
    )


def _doctor(config: Config) -> int:
    """Print what the tool can see on this machine.

    Args:
        config: Effective configuration.

    Returns:
        ``0`` when everything needed is in place, ``1`` otherwise.
    """
    problems: list[str] = []
    print("lecture-scribe doctor\n")

    print("system")
    _row("os", f"{platform.system()} {platform.release()} ({platform.machine()})")
    _row("python", platform.python_version())
    # Which environment this is matters more than it looks: a checkout and a
    # `uv tool install` are separate, and one can hold the CUDA library while
    # the other does not.
    _row("environment", sys.prefix)
    _row(
        "config",
        str(config.source) if config.source else f"defaults, no ./{CONFIG_FILENAME}",
    )
    awake = awake_status()
    _row("keep awake", _awake_line(awake.available, awake.detail))

    print("\naudio")
    problems += _audio_report(config)

    print("\nasr")
    problems += _asr_report(config)

    print("\noutput")
    _output_report(config)

    print("\nupload")
    problems += _upload_report(config)

    if problems:
        print()
        for problem in problems:
            print(f"problem: {problem}")
        return 1
    print("\nready")
    return 0


def _audio_report(config: Config) -> list[str]:
    """Print the audio section of the doctor report and return any problems."""
    try:
        devices = list_loopback_devices()
    except AudioDeviceError as error:
        _row("devices", f"ERROR: {error}")
        return [str(error)]

    selected: LoopbackDevice | None = None
    problems: list[str] = []
    try:
        selected = resolve_device(devices, config.audio.device)
    except AudioDeviceError as error:
        problems.append(str(error))

    _row("devices", f"{len(devices)} output device(s), recordable via loopback")
    for device in devices:
        marker = "->" if selected is not None and device.id == selected.id else "  "
        default = "  (system default)" if device.is_default else ""
        print(f"    {marker} {device.name}{default}")

    chosen = selected.name if selected is not None else "ERROR, see below"
    _row("selection", f'audio.device = "{config.audio.device}" -> {chosen}')
    _row("capture", f"{config.audio.sample_rate} Hz mono")
    return problems


def _asr_report(config: Config) -> list[str]:
    """Print the recognition section of the doctor report and return problems."""
    status = model_status(config.asr.model)
    gpu = gpu_status()

    _row("model", f"{config.asr.model}, language {config.asr.language}")
    weights = (
        "on disk" if status.cached else "not downloaded, fetched on first `scribe text`"
    )
    _row("weights", weights)
    _row("location", str(status.location))
    _row("gpu", gpu.detail)
    _row(
        "compute type",
        f"{compute_type(config.asr.gpu)}, asr.gpu = {str(config.asr.gpu).lower()}",
    )

    if config.asr.gpu and not gpu.available:
        if gpu.device_count == 0:
            return ["asr.gpu is true but no CUDA device is usable"]
        # The library has to sit in the environment scribe is started from,
        # and a checkout and a `uv tool install` are two different ones. This
        # is worth spelling out: installing into the wrong one looks like it
        # worked and changes nothing.
        return [
            "asr.gpu is true but cuBLAS is missing from the environment "
            "above. Install it where scribe starts from: 'uv sync --extra "
            "cuda' for a checkout, or 'uv tool install --editable \".[cuda]\" "
            "--force' for the scribe on your PATH."
        ]
    return []


def _output_report(config: Config) -> None:
    """Print where results will be written and how much room is left for them."""
    target = config.output.dir
    exists = target.is_dir()
    _row("dir", f"{target}{'' if exists else ', created on first recording'}")

    anchor = target
    while not anchor.exists() and anchor != anchor.parent:
        anchor = anchor.parent
    free_gb = shutil.disk_usage(anchor).free / 1_000_000_000
    # An hour of 16 kHz mono PCM is about 115 MB, so this is only ever a sanity check.
    _row("free space", f"{free_gb:.1f} GB")


def _upload_report(config: Config) -> list[str]:
    """Print the upload section of the doctor report and return any problems."""
    status = upload_status(config.upload)
    if status.enabled:
        _row("remote", config.upload.remote)
    else:
        _row("remote", "off, no upload.remote in config.toml")
    _row("rclone", status.rclone or "not found")
    if status.remote_found is not None:
        name = config.upload.remote.split(":", 1)[0]
        _row(
            "sign-in",
            f"remote '{name}' {'found' if status.remote_found else 'MISSING'}",
        )
    return [status.problem] if status.problem else []


def _awake_line(held: bool, detail: str) -> str:
    """Say whether the desktop is kept from locking, and by what or why not."""
    return f"yes, via {detail}" if held else f"no, the desktop may lock: {detail}"


def _row(label: str, value: str) -> None:
    """Print one aligned line of the doctor report."""
    print(f"  {label:<{_LABEL_WIDTH}} {value}")


if __name__ == "__main__":
    sys.exit(main())
