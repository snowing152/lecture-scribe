"""Tests for the pure parts of capture: device choice and folder naming."""

from datetime import datetime
from pathlib import Path

import pytest

from lecture_scribe.audio_capture import (
    AudioDeviceError,
    LoopbackDevice,
    course_slug,
    lecture_dir,
    resolve_device,
)

SPEAKER = LoopbackDevice(name="Ryzen Speaker", id="alsa_output.pci", is_default=True)
HDMI_1 = LoopbackDevice(name="HDMI Output 1", id="alsa_output.hdmi1", is_default=False)
HDMI_2 = LoopbackDevice(name="HDMI Output 2", id="alsa_output.hdmi2", is_default=False)
DEVICES = [SPEAKER, HDMI_1, HDMI_2]


def test_auto_picks_the_system_default() -> None:
    assert resolve_device(DEVICES, "auto") == SPEAKER


def test_substring_picks_one_device_ignoring_case() -> None:
    assert resolve_device(DEVICES, "hdmi output 2") == HDMI_2


def test_no_match_lists_what_is_available() -> None:
    with pytest.raises(AudioDeviceError, match="HDMI Output 1"):
        resolve_device(DEVICES, "bluetooth")


def test_ambiguous_substring_is_refused() -> None:
    with pytest.raises(AudioDeviceError, match="matches several"):
        resolve_device(DEVICES, "HDMI")


def test_empty_device_list_is_refused() -> None:
    with pytest.raises(AudioDeviceError, match="no output devices"):
        resolve_device([], "auto")


def test_auto_without_a_default_is_refused() -> None:
    with pytest.raises(AudioDeviceError, match="no default output"):
        resolve_device([HDMI_1, HDMI_2], "auto")


def test_folder_is_named_after_date_and_course(tmp_path: Path) -> None:
    when = datetime(2026, 9, 9, 10, 4)

    assert lecture_dir(tmp_path, "os", when) == tmp_path / "2026-09-09_os"


def test_second_lecture_the_same_day_gets_its_own_folder(tmp_path: Path) -> None:
    when = datetime(2026, 9, 9, 10, 4)
    (tmp_path / "2026-09-09_os").mkdir()

    assert lecture_dir(tmp_path, "os", when) == tmp_path / "2026-09-09_os-2"

    (tmp_path / "2026-09-09_os-2").mkdir()
    assert lecture_dir(tmp_path, "os", when) == tmp_path / "2026-09-09_os-3"


def test_course_names_survive_korean_and_spaces() -> None:
    assert course_slug("운영체제") == "운영체제"
    assert course_slug("operating systems") == "operating-systems"
    assert course_slug("  os  ") == "os"


def test_course_names_lose_characters_windows_forbids() -> None:
    assert course_slug("os/network") == "osnetwork"
    assert course_slug('os: "intro"') == "os-intro"


def test_course_name_without_usable_characters_is_refused() -> None:
    with pytest.raises(ValueError, match="usable"):
        course_slug(" / ")
