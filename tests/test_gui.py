"""Tests for the desktop window.

Run on Qt's offscreen platform, so they need no display. The sound backend
and the model are replaced; only the window's own bookkeeping is under test.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from lecture_scribe import gui
from lecture_scribe.audio_capture import LoopbackDevice, Recording
from lecture_scribe.config import AsrConfig, Config, OutputConfig

_SPEAKERS = LoopbackDevice(name="Speakers", id="alsa.speakers", is_default=True)
_HDMI = LoopbackDevice(name="HDMI", id="alsa.hdmi", is_default=False)
_HEADPHONES = LoopbackDevice(name="Headphones", id="usb.headphones", is_default=True)


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    mp = pytest.MonkeyPatch()
    mp.setenv("QT_QPA_PLATFORM", "offscreen")
    existing = QApplication.instance()
    yield existing if isinstance(existing, QApplication) else QApplication([])
    mp.undo()


class _Devices:
    """Stands in for the sound backend; tests change what it reports."""

    def __init__(self, devices: list[LoopbackDevice]) -> None:
        self.devices = devices

    def __call__(self) -> list[LoopbackDevice]:
        return list(self.devices)


@pytest.fixture
def devices(monkeypatch: pytest.MonkeyPatch) -> _Devices:
    backend = _Devices([_SPEAKERS, _HDMI])
    monkeypatch.setattr(gui, "list_loopback_devices", backend)
    return backend


def _window(tmp_path: Path) -> gui._Window:
    return gui._Window(Config(output=OutputConfig(dir=tmp_path / "lectures")))


def test_auto_follows_the_default_to_where_it_moved(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    assert window._device_box.currentText().startswith("Speakers")

    # Headphones plugged in after the window opened.
    devices.devices = [
        LoopbackDevice(name="Speakers", id="alsa.speakers", is_default=False),
        _HDMI,
        _HEADPHONES,
    ]

    assert window._load_devices() == _HEADPHONES
    assert window._device_box.count() == 3


def test_a_device_picked_by_hand_stays_picked(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._on_device_picked(1)

    devices.devices = [_HEADPHONES, _SPEAKERS, _HDMI]

    assert window._load_devices() == _HDMI
    assert window._device_box.currentText() == "HDMI"


def test_a_picked_device_that_is_gone_does_not_record(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._on_device_picked(1)
    window._course.setText("os")
    devices.devices = [_SPEAKERS]

    window._start_recording()

    # Nothing started on a device other than the one on screen.
    assert window._recorder is None
    assert not (tmp_path / "lectures").exists()
    assert "'HDMI' is gone" in window._status.text()
    # The list fell back to the configured choice, ready for a second press.
    assert window._load_devices() == _SPEAKERS


def test_a_recording_that_never_started_is_not_offered_for_transcription(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._wav = tmp_path / "lectures" / "2026-11-09_os" / "2026-11-09_os.wav"
    window._recording = True

    window._on_record_failed("cannot record")

    assert window._wav is None
    assert not window._text.isEnabled()


def test_a_failed_start_takes_back_its_empty_folder(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    folder = tmp_path / "lectures" / "2026-11-10_os"
    folder.mkdir(parents=True)
    window._wav = folder / "2026-11-10_os.wav"
    window._recording = True

    window._on_record_failed("cannot record")

    assert not folder.exists()


def test_a_failed_start_keeps_a_folder_that_holds_audio(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    folder = tmp_path / "lectures" / "2026-11-10_os"
    folder.mkdir(parents=True)
    window._wav = folder / "2026-11-10_os.wav"
    window._wav.write_bytes(b"RIFF")
    window._recording = True

    window._on_record_failed("cannot record")

    assert (folder / "2026-11-10_os.wav").is_file()


def test_a_recording_stopped_before_any_audio_is_not_offered(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    wav = tmp_path / "os.wav"
    wav.touch()
    window._wav = wav
    window._recording = True

    window._on_recorded(
        Recording(path=wav, duration=0.0, peak=0.0, mean_rms=0.0, interrupted=None)
    )

    assert window._wav is None
    assert not window._text.isEnabled()
    assert "nothing to transcribe" in window._status.text()
    # The file itself is a recording like any other and stays where it is.
    assert wav.exists()


def test_opening_the_list_does_not_ask_for_a_second_record_press(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._on_device_picked(1)
    devices.devices = [_SPEAKERS]

    window._on_device_list_opening()

    assert "'HDMI' is gone" in window._status.text()
    assert "press record" not in window._status.text()


def test_closing_during_recognition_defaults_to_no(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    window._transcribing = True
    asked: list[object] = []

    def question(
        _parent: QWidget, _title: str, text: str, **kwargs: object
    ) -> QMessageBox.StandardButton:
        asked.append(kwargs.get("defaultButton"))
        assert "Recognition is still running" in text
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    event = QCloseEvent()

    window.closeEvent(event)

    assert asked == [QMessageBox.StandardButton.No]
    assert not event.isAccepted()


def test_an_unknown_language_fails_before_the_recording_is_read(
    app: QApplication,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Config(asr=AsrConfig(language="kr"))
    worker = gui._TranscribeWorker(config, tmp_path / "os.wav", [])

    @dataclass
    class Model:
        supported_languages: tuple[str, ...] = ("en", "ko")

    def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the recording should not be read")

    monkeypatch.setattr(gui, "load_model", lambda *_args, **_kwargs: Model())
    monkeypatch.setattr(gui, "transcribe", never)
    failed: list[str] = []
    worker.failed.connect(failed.append)

    worker.run()

    assert len(failed) == 1
    assert "'kr' is not a language code" in failed[0]


def test_closing_during_a_recording_asks_and_defaults_to_no(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    window._recording = True
    asked: list[object] = []

    def question(
        _parent: QWidget, _title: str, text: str, **kwargs: object
    ) -> QMessageBox.StandardButton:
        asked.append(kwargs.get("defaultButton"))
        assert "being recorded" in text
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    event = QCloseEvent()

    window.closeEvent(event)

    assert asked == [QMessageBox.StandardButton.No]
    assert not event.isAccepted()


def test_the_language_starts_on_the_configured_one(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)

    assert window._language_box.currentText() == "Korean"
    assert window._recognition_config().asr.language == "ko"


def test_the_chosen_language_is_the_one_recognised(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)

    window._language_box.setCurrentIndex(window._language_box.findText("English"))

    assert window._recognition_config().asr.language == "en"
    # Chosen for this run only: the configuration itself is left alone.
    assert window._config.asr.language == "ko"


def test_a_configured_language_outside_the_list_is_kept(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    config = Config(
        asr=AsrConfig(language="ja"), output=OutputConfig(dir=tmp_path / "lectures")
    )

    window = gui._Window(config)

    assert window._language_box.count() == 3
    assert window._recognition_config().asr.language == "ja"


def test_the_language_is_locked_while_working(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._recording = True
    window._recorder = gui._RecordWorker(
        _SPEAKERS, tmp_path / "x.wav", sample_rate=16000, silence_rms=0.0
    )

    window._refresh()

    assert not window._language_box.isEnabled()


def test_a_stop_while_the_model_loads_skips_recognition(
    app: QApplication,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = gui._TranscribeWorker(Config(), tmp_path / "os.wav", [])

    def load(*_args: object, **_kwargs: object) -> object:
        # The window closing while a first download is still running.
        worker.stop()
        return object()

    def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("recognition should not start after a stop")

    monkeypatch.setattr(gui, "load_model", load)
    monkeypatch.setattr(gui, "transcribe", never)
    stopped: list[str] = []
    worker.stopped.connect(stopped.append)

    worker.run()

    assert len(stopped) == 1
    assert "before it began" in stopped[0]


@pytest.mark.manual
def test_window_opens_and_lists_the_devices() -> None:
    """Needs a display and a working sound backend."""
    from lecture_scribe.config import Config
    from lecture_scribe.gui import _Window

    _app = QApplication.instance() or QApplication([])
    window = _Window(Config())

    assert window.windowTitle() == "lecture scribe"
    assert window._device_box.count() == len(window._devices)
