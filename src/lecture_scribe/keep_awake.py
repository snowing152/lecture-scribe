"""Keeping the desktop from locking or sleeping while a lecture is worked on.

A lecture runs for an hour and more with nobody at the keyboard, and a
desktop left idle that long dims, locks, turns the screen off and in the end
suspends -- which stops a recording as surely as pulling the plug, and a
recognition along with it. For as long as either runs, the desktop is asked
to treat the machine as in use, the way a video player asks it.

On Linux the request goes over D-Bus to ``org.freedesktop.ScreenSaver``,
which desktops and standalone shells serve for exactly this. On Windows
``SetThreadExecutionState`` does the same job. Where neither answers, the
work goes ahead regardless: nothing here is a condition for recording, and
:class:`Inhibition` says why the desktop may lock.

A request the desktop accepts on Linux is announced with a brief, transient
notification, so that walking away is a decision made knowing the screen
will stay on.
"""

import ctypes
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from PySide6.QtDBus import QDBusConnection, QDBusMessage

_SERVICE = "org.freedesktop.ScreenSaver"
_PATH = "/org/freedesktop/ScreenSaver"
_APP_NAME = "lecture-scribe"

_TIMEOUT_MS = 3000
"""How long to wait on the desktop. A shell that hangs must not hold up a lecture."""

_NOTIFY_TIMEOUT_SECONDS = 2.0
"""notify-send answers in milliseconds; this only bounds a desktop with no server."""

_NOTIFY_SHOWN_MS = 4000

# ES_CONTINUOUS makes the request last until it is replaced, instead of only
# resetting the idle timer once.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


@dataclass(frozen=True, slots=True)
class AwakeStatus:
    """Whether this desktop can be kept from locking and sleeping.

    Attributes:
        available: Whether a request would be accepted here.
        detail: What would take the request, or why nothing would.
    """

    available: bool
    detail: str


class Inhibition:
    """A request that the desktop neither lock nor sleep, kept until released.

    Usable as a context manager, which releases it on the way out. Releasing
    twice, or releasing a request the desktop never accepted, does nothing.

    Attributes:
        held: Whether the desktop accepted the request.
        detail: What holds the request, or why nothing does.
    """

    def __init__(
        self, held: bool, detail: str, *, release: Callable[[], object] | None = None
    ) -> None:
        """Wrap the outcome of one request.

        Args:
            held: Whether the desktop accepted the request.
            detail: What holds the request, or why nothing does.
            release: Hands idle handling back to the desktop. ``None`` when
                there is nothing to hand back.
        """
        self.held = held
        self.detail = detail
        self._release = release

    def release(self) -> None:
        """Let the desktop lock and sleep again once it has been idle long enough."""
        release, self._release = self._release, None
        if release is not None:
            release()

    def __enter__(self) -> Self:
        """Return the request itself; it is already in force."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the request, however the block was left."""
        self.release()


def keep_awake(reason: str) -> Inhibition:
    """Ask the desktop not to lock or sleep until the result is released.

    On Windows the request belongs to the calling thread, so release it from
    the same thread that made it.

    Args:
        reason: What the machine is busy with, shown by desktops that list
            who is keeping them awake.

    Returns:
        The request, held or not. One that is not held says why.
    """
    if sys.platform == "linux":
        return _inhibit_freedesktop(reason)
    if sys.platform == "win32":
        return _inhibit_windows()
    return Inhibition(False, f"not supported on {sys.platform}")


def awake_status() -> AwakeStatus:
    """Report whether the desktop could be kept awake, and by what.

    Only looks: nothing is inhibited, so the doctor stays free of side effects.

    Returns:
        Whether a request would be taken, with the program taking it.
    """
    if sys.platform == "win32":
        return AwakeStatus(True, "SetThreadExecutionState")
    if sys.platform != "linux":
        return AwakeStatus(False, f"not supported on {sys.platform}")

    try:
        from PySide6.QtDBus import QDBusConnection, QDBusMessage
    except ImportError as error:
        return AwakeStatus(False, f"PySide6 is not importable ({error})")

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return AwakeStatus(False, "no D-Bus session bus to ask")
    reply = _call(
        bus,
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "GetConnectionUnixProcessID",
        [_SERVICE],
    )
    if reply.type() != QDBusMessage.MessageType.ReplyMessage:
        return AwakeStatus(False, _explain(reply.errorName(), reply.errorMessage()))
    return AwakeStatus(
        True, f"{_SERVICE}, served by {_process_name(reply.arguments()[0])}"
    )


def _inhibit_freedesktop(reason: str) -> Inhibition:
    """Inhibit idle handling through ``org.freedesktop.ScreenSaver``."""
    try:
        from PySide6.QtDBus import QDBusConnection, QDBusMessage
    except ImportError as error:
        return Inhibition(False, f"PySide6 is not importable ({error})")

    # A connection of its own, so that closing it is the release. UnInhibit
    # wants its cookie back as an unsigned int, which PySide6 cannot send: it
    # goes out as a signed one and the desktop refuses the call. Closing is
    # also what the desktop sees when the process is killed outright, so a
    # clean exit and a crash let go the same way.
    name = f"{_APP_NAME}-{uuid.uuid4().hex}"
    bus = QDBusConnection.connectToBus(QDBusConnection.BusType.SessionBus, name)
    if bus.isConnected():
        reply = _call(bus, _SERVICE, _PATH, _SERVICE, "Inhibit", [_APP_NAME, reason])
        held = reply.type() == QDBusMessage.MessageType.ReplyMessage
        detail = _SERVICE if held else _explain(reply.errorName(), reply.errorMessage())
    else:
        held, detail = False, "no D-Bus session bus to ask"
    # Qt closes a named connection only once no QDBusConnection refers to it.
    # With this one still alive, disconnecting kept the inhibit in force until
    # the process exited.
    del bus

    def release() -> None:
        QDBusConnection.disconnectFromBus(name)

    if not held:
        release()
        return Inhibition(False, detail)
    _notify(reason)
    return Inhibition(True, detail, release=release)


def _inhibit_windows() -> Inhibition:
    """Inhibit idle handling through ``SetThreadExecutionState``."""
    # An if block rather than an assert: mypy skips the block on Linux, where
    # ctypes has no windll, but checks the code after an assert regardless.
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        # Passed as an explicit unsigned value: the default int conversion
        # overflows on ES_CONTINUOUS, whose top bit is set.
        flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
        if kernel32.SetThreadExecutionState(ctypes.c_uint32(flags)) == 0:
            return Inhibition(False, "SetThreadExecutionState refused the request")
        return Inhibition(
            True,
            "SetThreadExecutionState",
            release=lambda: kernel32.SetThreadExecutionState(
                ctypes.c_uint32(_ES_CONTINUOUS)
            ),
        )
    return Inhibition(False, f"not supported on {sys.platform}")


def _call(
    bus: "QDBusConnection",
    service: str,
    path: str,
    interface: str,
    method: str,
    arguments: list[object],
) -> "QDBusMessage":
    """Make one blocking D-Bus call and return the reply or the error."""
    from PySide6.QtDBus import QDBus, QDBusMessage

    message = QDBusMessage.createMethodCall(service, path, interface, method)
    message.setArguments(arguments)
    return bus.call(message, QDBus.CallMode.Block, _TIMEOUT_MS)


def _notify(reason: str) -> None:
    """Announce that the screen is being kept awake, if the desktop can show it.

    Goes through ``notify-send`` rather than QtDBus: ``Notify`` takes an
    unsigned id and a string array, PySide6 sends a signed int and a variant
    array, and the server refuses the call. Without notify-send there is
    simply no notification; the request itself stands either way.

    Args:
        reason: What the machine is busy with, shown as the body.
    """
    program = shutil.which("notify-send")
    if program is None:
        return
    try:
        # Output captured so a complaint from notify-send cannot break into
        # the command line's live status line.
        subprocess.run(
            notify_command(Path(program), reason),
            check=False,
            capture_output=True,
            timeout=_NOTIFY_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return


def notify_command(program: Path, reason: str) -> list[str]:
    """Build the notify-send invocation announcing a held request.

    Args:
        program: The notify-send executable.
        reason: What the machine is busy with, shown as the body.

    Returns:
        The argument list, program first.
    """
    return [
        str(program),
        "--app-name=lecture scribe",
        # The icon `scribe launcher` installs; a server that cannot find it
        # shows the notification without one.
        f"--icon={_APP_NAME}",
        f"--expire-time={_NOTIFY_SHOWN_MS}",
        # Transient: it says something true only for as long as the work
        # runs, so it has no place in the notification history afterwards.
        "--transient",
        "Screen kept awake",
        reason,
    ]


def _explain(error_name: str, message: str) -> str:
    """Turn a D-Bus error into a line a person can act on.

    Args:
        error_name: The error's D-Bus name.
        message: The text that came with it.

    Returns:
        One line saying why the desktop cannot be kept awake.
    """
    if error_name in (
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
    ):
        return f"nothing on this desktop serves {_SERVICE}"
    return f"{_SERVICE} refused: {message or error_name}"


def _process_name(pid: int) -> str:
    """Name the program behind a process id, for the doctor report."""
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return f"process {pid}"
