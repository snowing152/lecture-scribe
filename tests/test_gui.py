"""Tests for the desktop window, which need a display to run."""

import pytest


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
