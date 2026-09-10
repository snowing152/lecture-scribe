"""Tests for the desktop menu entry."""

from pathlib import Path

from lecture_scribe.launcher import desktop_entry


def _entry(target: str = "/home/u/.local/bin/scribe-gui") -> str:
    return desktop_entry(Path(target), Path("/home/u/Projects/lecture_helper"))


def test_desktop_entry_pins_the_working_directory() -> None:
    # Without this the menu would start the window in the home directory,
    # where there is no config.toml to read.
    assert "Path=/home/u/Projects/lecture_helper\n" in _entry()


def test_desktop_entry_runs_the_given_executable() -> None:
    assert 'Exec="/home/u/.local/bin/scribe-gui"\n' in _entry()


def test_desktop_entry_quotes_a_path_holding_spaces() -> None:
    assert 'Exec="/home/u/my lectures/scribe-gui"\n' in _entry(
        "/home/u/my lectures/scribe-gui"
    )


def test_desktop_entry_doubles_a_percent_in_the_path() -> None:
    # A single percent starts a field code, so a literal one is written twice.
    assert 'Exec="/home/u/100%%/scribe-gui"\n' in _entry("/home/u/100%/scribe-gui")


def test_desktop_entry_escapes_a_backslash_in_the_path() -> None:
    assert 'Exec="/home/u/a\\\\b/scribe-gui"\n' in _entry("/home/u/a\\b/scribe-gui")


def test_desktop_entry_names_the_icon_and_the_window_class() -> None:
    # The two have to agree, or the window shows up without its icon.
    entry = _entry()
    assert "Icon=lecture-scribe\n" in entry
    assert "StartupWMClass=lecture-scribe\n" in entry


def test_desktop_entry_is_an_application_that_opens_no_terminal() -> None:
    entry = _entry()
    assert entry.startswith("[Desktop Entry]\n")
    assert "Type=Application\n" in entry
    assert "Terminal=false\n" in entry
    assert entry.endswith("\n")
