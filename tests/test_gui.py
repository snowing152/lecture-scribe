"""Tests for the desktop window's pure logic."""

from pathlib import Path

import pytest

from lecture_scribe.gui import _with_upload, loudness
from lecture_scribe.upload import Upload


def test_loudness_of_silence_is_an_empty_meter() -> None:
    assert loudness(0.0) == 0.0


def test_loudness_of_full_scale_fills_the_meter() -> None:
    assert loudness(1.0) == 1.0


def test_loudness_at_the_floor_is_an_empty_meter() -> None:
    # 0.001 is -60 dBFS, the level the meter is drawn from.
    assert loudness(0.001) == pytest.approx(0.0)


def test_loudness_is_half_way_at_half_the_decibel_range() -> None:
    assert loudness(0.0316227766) == pytest.approx(0.5, abs=1e-6)


def test_loudness_clamps_below_the_floor() -> None:
    assert loudness(1e-9) == 0.0


def test_loudness_clamps_above_full_scale() -> None:
    # A block that clipped should pin the meter rather than overrun it.
    assert loudness(2.5) == 1.0


@pytest.mark.manual
def test_window_opens_and_lists_the_devices() -> None:
    """Needs a display and a working sound backend."""
    from PySide6.QtWidgets import QApplication

    from lecture_scribe.config import Config
    from lecture_scribe.gui import _Window

    _app = QApplication([])
    window = _Window(Config())

    assert window.windowTitle() == "lecture scribe"
    assert window._device_box.count() == len(window._devices)


def test_status_line_is_unchanged_with_upload_off() -> None:
    assert _with_upload("wrote x.txt", None) == ("wrote x.txt", False)


def test_status_line_names_where_the_transcript_went() -> None:
    upload = Upload(Path("x.txt"), "gdrive:Lectures/x/x.txt")

    line, problem = _with_upload("wrote x.txt", upload)

    assert line == "wrote x.txt · uploaded to gdrive:Lectures/x/x.txt"
    assert not problem


def test_a_failed_upload_needs_attention_but_keeps_the_write() -> None:
    upload = Upload(Path("x.txt"), "gdrive:x.txt", error="rclone is not found")

    line, problem = _with_upload("wrote x.txt", upload)

    assert line.startswith("wrote x.txt")
    assert "not uploaded, rclone is not found" in line
    assert problem
