"""Copying a finished transcript to cloud storage through rclone.

Only the ``.txt`` ever leaves the machine; the recording stays where it was
made. The copy is made by the external ``rclone`` program, which keeps the
sign-in to the storage provider in its own config file, so this tool never
touches a token and needs no library for it.

An upload is a convenience on top of a transcript already safe on disk, so
nothing here raises: :func:`push_transcript` returns an :class:`Upload` that
says where the file went or why it did not, and the front end reports it.
"""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lecture_scribe.config import UploadConfig

_PROBE_TIMEOUT_SECONDS = 15.0
"""``rclone version`` is local, but ``listremotes`` may wait on a locked config."""

_NOTIFY_TIMEOUT_SECONDS = 2.0
"""notify-send answers in milliseconds; this only bounds a desktop with no server."""

_NOTIFY_SHOWN_MS = 4000

_INSTALL_HINT = "install it from https://rclone.org/install/ and run 'rclone config'"


@dataclass(frozen=True, slots=True)
class Upload:
    """The outcome of copying one transcript.

    Attributes:
        source: The local transcript.
        destination: Where it was meant to go, as an rclone path.
        error: Why it did not get there, or ``None`` when it did.
    """

    source: Path
    destination: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the transcript arrived."""
        return self.error is None


@dataclass(frozen=True, slots=True)
class UploadStatus:
    """What ``scribe doctor`` reports about uploading.

    Attributes:
        enabled: Whether ``[upload]`` names a destination.
        rclone: First line of ``rclone version``, or ``None`` when not found.
        remote_found: Whether the configured remote is set up in rclone, or
            ``None`` when that could not be asked.
        problem: What stops an upload, or ``None`` when nothing does.
    """

    enabled: bool
    rclone: str | None
    remote_found: bool | None
    problem: str | None


def remote_path(transcript: Path, root: Path, remote: str) -> str:
    """Work out where a transcript goes, keeping its folders under ``root``.

    ``~/lectures/Computer_Network/2026-09-28_x/2026-09-28_x.txt`` goes to
    ``<remote>/Computer_Network/2026-09-28_x/2026-09-28_x.txt``, so Drive is
    sorted by course the same way the disk is.

    Args:
        transcript: The local transcript.
        root: ``output.dir``, the folder whose layout is mirrored.
        remote: rclone destination, ``<remote>:<folder>`` or ``<remote>:``.

    Returns:
        The rclone path of the copy. A transcript outside ``root`` keeps only
        its own folder, so a lecture opened from elsewhere still lands in a
        folder named after it rather than loose in the remote's root.
    """
    try:
        relative = transcript.resolve().relative_to(root.resolve())
    except ValueError:
        relative = Path(transcript.parent.name, transcript.name)
    # rclone paths use forward slashes on Windows too.
    tail = PurePosixPath(*relative.parts).as_posix()
    separator = "" if remote.endswith((":", "/")) else "/"
    return f"{remote}{separator}{tail}"


def push_transcript(transcript: Path, root: Path, config: UploadConfig) -> Upload:
    """Copy one transcript to the configured remote, replacing an older copy.

    Args:
        transcript: The local transcript, already written.
        root: ``output.dir``, whose folder layout the remote mirrors.
        config: The ``[upload]`` section; must be enabled.

    Returns:
        Where the transcript went, or the reason it did not, with the command
        that retries it by hand.
    """
    destination = remote_path(transcript, root, config.remote)
    program = shutil.which("rclone")
    if program is None:
        return Upload(transcript, destination, f"rclone is not found; {_INSTALL_HINT}")

    retry = f"retry with: rclone copyto '{transcript.resolve()}' '{destination}'"
    try:
        # copyto rather than copy: the destination names the file itself,
        # and missing folders on the way are created.
        subprocess.run(
            [program, "copyto", str(transcript), destination],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.timeout,
        )
    except subprocess.TimeoutExpired:
        reason = f"rclone gave up after {config.timeout:.0f} s, is the network up?"
        return Upload(transcript, destination, f"{reason} {retry}")
    except subprocess.CalledProcessError as error:
        reason = _last_line(error.stderr) or f"rclone exited with {error.returncode}"
        return Upload(transcript, destination, f"{reason}; {retry}")
    return Upload(transcript, destination)


def announce(upload: Upload) -> None:
    """Show a small desktop notification that a transcript arrived.

    Only a success is announced: a failure is reported by the front end,
    where the command that retries it can be copied. Without notify-send, as
    on Windows, there is simply no notification.

    Args:
        upload: A finished upload.
    """
    program = shutil.which("notify-send")
    if not upload.ok or program is None:
        return
    try:
        # Output captured so a complaint from notify-send cannot break into
        # the command line's live status line.
        subprocess.run(
            announce_command(Path(program), upload),
            check=False,
            capture_output=True,
            timeout=_NOTIFY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return


def announce_command(program: Path, upload: Upload) -> list[str]:
    """Build the notify-send invocation announcing an upload.

    Args:
        program: The notify-send executable.
        upload: A successful upload.

    Returns:
        The argument list, program first.
    """
    return [
        str(program),
        "--app-name=lecture scribe",
        # The icon `scribe launcher` installs; a server that cannot find it
        # shows the notification without one.
        "--icon=lecture-scribe",
        f"--expire-time={_NOTIFY_SHOWN_MS}",
        "Transcript uploaded",
        # Not transient, unlike the keep-awake notice: where the file went
        # stays true afterwards and is worth finding in the history.
        upload.destination,
    ]


def upload_status(config: UploadConfig) -> UploadStatus:
    """Check whether an upload could run, without uploading anything.

    Args:
        config: The ``[upload]`` section.

    Returns:
        The rclone version and whether the remote exists. A problem is only
        reported when upload is enabled: without rclone, a machine that does
        not upload is simply fine.
    """
    program = shutil.which("rclone")
    if program is None:
        problem = f"upload.remote is set but rclone is not found; {_INSTALL_HINT}"
        return UploadStatus(
            config.enabled, None, None, problem if config.enabled else None
        )

    version = _probe([program, "version"])
    version_line = version.splitlines()[0] if version else "rclone, version unknown"
    if not config.enabled:
        return UploadStatus(False, version_line, None, None)

    listed = _probe([program, "listremotes"])
    if listed is None:
        return UploadStatus(True, version_line, None, "'rclone listremotes' failed")
    name = config.remote.split(":", 1)[0] + ":"
    if name in listed.split():
        return UploadStatus(True, version_line, True, None)
    missing = f"rclone has no remote '{name}'; create it with 'rclone config'"
    return UploadStatus(True, version_line, False, missing)


def _probe(command: list[str]) -> str | None:
    """Run a quick rclone query and return its output, or ``None`` on failure."""
    try:
        done = subprocess.run(
            command,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return done.stdout


def _last_line(text: str | None) -> str:
    """Return the last non-empty line, where rclone puts its final error."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
