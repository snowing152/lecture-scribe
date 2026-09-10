"""Adding the window to the desktop's application menu.

The entry pins a working directory rather than leaving it to the desktop.
``config.toml`` is read from the current directory, and a menu starts a
program from the home directory, so without the pin the window would quietly
run on the built-in defaults.
"""

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DESKTOP_ID = "lecture-scribe"
"""Basename of the desktop entry, and the app id the window reports.

A Wayland compositor pairs a window with its menu entry by this name, so the
two have to agree for the icon to show up in a task switcher.
"""

_NAME = "lecture scribe"
_COMMENT = "Record a lecture playing on this computer and transcribe it"
_ICON_FILE = "lecture-scribe.svg"


class LauncherError(Exception):
    """Raised when the desktop entry cannot be written or removed."""


@dataclass(frozen=True, slots=True)
class Launcher:
    """What was written, and what it points at.

    Attributes:
        entry: The desktop entry or Start Menu shortcut itself.
        target: Executable the entry runs.
        workdir: Directory the window will start in.
        icon: Icon file that was installed, or ``None`` on a platform that
            takes the icon from the executable instead.
    """

    entry: Path
    target: Path
    workdir: Path
    icon: Path | None


def install(workdir: Path) -> Launcher:
    """Write the desktop entry for whichever platform this is.

    Args:
        workdir: Directory the window should start in, so that it reads the
            ``config.toml`` sitting there.

    Returns:
        What was written.

    Raises:
        LauncherError: This platform has no supported menu, ``scribe-gui``
            is not installed, or the entry could not be written.
    """
    target = executable()
    entry = entry_path()
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise LauncherError(f"cannot create {entry.parent}: {error}") from error

    if platform.system() == "Windows":
        _write_shortcut(entry, target, workdir)
        return Launcher(entry=entry, target=target, workdir=workdir, icon=None)

    icon = _install_icon()
    try:
        entry.write_text(desktop_entry(target, workdir), encoding="utf-8")
    except OSError as error:
        raise LauncherError(f"cannot write {entry}: {error}") from error
    return Launcher(entry=entry, target=target, workdir=workdir, icon=icon)


def remove() -> list[Path]:
    """Delete the desktop entry, and the icon that was installed with it.

    Returns:
        What was actually deleted, empty when there was nothing to delete.

    Raises:
        LauncherError: A file was there but could not be deleted.
    """
    candidates = [entry_path()]
    if platform.system() == "Linux":
        candidates.append(_icon_path())

    deleted: list[Path] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            path.unlink()
        except OSError as error:
            raise LauncherError(f"cannot delete {path}: {error}") from error
        deleted.append(path)
    return deleted


def entry_path() -> Path:
    """Locate where this platform keeps its menu entries.

    Returns:
        The file to write, whether or not it exists yet.

    Raises:
        LauncherError: This platform has no menu the tool knows how to use.
    """
    system = platform.system()
    if system == "Linux":
        return _data_home() / "applications" / f"{DESKTOP_ID}.desktop"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise LauncherError("APPDATA is not set, so the Start Menu is not findable")
        return (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / f"{_NAME}.lnk"
        )
    raise LauncherError(f"no desktop menu is supported on {system}")


def executable() -> Path:
    """Locate the ``scribe-gui`` launcher the entry should run.

    Returns:
        The executable to point the entry at.

    Raises:
        LauncherError: No ``scribe-gui`` could be found.
    """
    name = "scribe-gui.exe" if platform.system() == "Windows" else "scribe-gui"

    # The sibling of the running interpreter comes first: it belongs to the
    # same install as the `scribe` being used right now, whether that is a
    # `uv run` inside the project or a `uv tool install`. Whatever is on PATH
    # may well be a different, older one.
    sibling = Path(sys.executable).parent / name
    if sibling.is_file():
        return sibling

    found = shutil.which(name)
    if found is not None:
        return Path(found)

    raise LauncherError(
        f"{name} was not found. Run 'uv tool install --editable . --force' to "
        "put it on the PATH, or 'uv sync' to get it inside the project's .venv"
    )


def desktop_entry(target: Path, workdir: Path) -> str:
    """Render the contents of the ``.desktop`` file.

    Args:
        target: Executable the entry runs.
        workdir: Directory the window starts in.

    Returns:
        The complete file, ending in a newline.
    """
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={_NAME}\n"
        f"Comment={_COMMENT}\n"
        f"Exec={_quote_exec(target)}\n"
        f"Path={workdir}\n"
        f"Icon={DESKTOP_ID}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Audio;Recorder;\n"
        f"StartupWMClass={DESKTOP_ID}\n"
    )


def _quote_exec(target: Path) -> str:
    """Quote a program path for a desktop entry's ``Exec`` key.

    The key is not a plain string: it is split into words, a backslash keeps
    its escaping meaning inside quotes, and a percent sign introduces a field
    code. A path holding any of those has to be quoted and escaped.

    Args:
        target: Executable to run.

    Returns:
        The quoted, escaped value.
    """
    escaped = str(target).replace("\\", "\\\\").replace('"', '\\"')
    # A literal percent is written twice; a single one starts a field code.
    return '"' + escaped.replace("%", "%%") + '"'


def _install_icon() -> Path | None:
    """Copy the icon into the user's theme so the entry can name it.

    Returns:
        Where the icon landed, or ``None`` when the package holds no icon.

    Raises:
        LauncherError: The icon could not be copied.
    """
    source = Path(__file__).parent / _ICON_FILE
    if not source.is_file():
        return None

    destination = _icon_path()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    except OSError as error:
        raise LauncherError(f"cannot install the icon: {error}") from error
    return destination


def _icon_path() -> Path:
    """Locate the themed icon file, whether or not it exists yet."""
    return (
        _data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{DESKTOP_ID}.svg"
    )


def _data_home() -> Path:
    """Locate the directory holding desktop entries and icons."""
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path.home() / ".local" / "share"


def _write_shortcut(entry: Path, target: Path, workdir: Path) -> None:
    """Create a Start Menu shortcut through the Windows scripting host.

    PowerShell writes the shortcut rather than a COM binding, so that adding
    a menu entry costs the tool no dependency: a ``.lnk`` is a binary format
    that cannot simply be written out.

    Args:
        entry: Shortcut file to create.
        target: Executable the shortcut runs.
        workdir: Directory the shortcut starts it in.

    Raises:
        LauncherError: PowerShell is missing or refused to write the file.
    """
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut({_powershell_string(str(entry))}); "
        f"$link.TargetPath = {_powershell_string(str(target))}; "
        f"$link.WorkingDirectory = {_powershell_string(str(workdir))}; "
        f"$link.Description = {_powershell_string(_COMMENT)}; "
        "$link.Save()"
    )
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise LauncherError(f"cannot run powershell: {error}") from error

    if done.returncode != 0:
        raise LauncherError(
            f"powershell could not write {entry}: {done.stderr.strip()}"
        )


def _powershell_string(value: str) -> str:
    """Quote a value as a PowerShell single quoted string literal."""
    return "'" + value.replace("'", "''") + "'"
