"""Tests for the desktop window.

Run on Qt's offscreen platform, so they need no display. The sound backend
and the model are replaced; only the window's own bookkeeping is under test.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from lecture_scribe import gui
from lecture_scribe.audio_capture import (
    LoopbackDevice,
    Recording,
    marks_path,
    read_marks,
)
from lecture_scribe.config import AsrConfig, Config, ModelChoice, OutputConfig
from lecture_scribe.keep_awake import Inhibition
from lecture_scribe.transcribe import GpuStatus, Transcription, TranscriptionError

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
    # The language travels with the recording, as chosen when it was queued.
    job = gui._Job(wav=tmp_path / "os.wav", marks=(), language="kr")
    worker = gui._TranscribeWorker(Config(), gui._JobQueue([job]))

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

    assert window._language_box.current_text() == "Korean"
    assert window._recognition_config().asr.language == "ko"


def test_the_chosen_language_is_the_one_recognised(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)

    window._language_box.select("en")

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
    job = gui._Job(wav=tmp_path / "os.wav", marks=(), language="ko")
    worker = gui._TranscribeWorker(Config(), gui._JobQueue([job]))

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


@pytest.fixture
def idle_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transcription workers are created but never run, nor the desktop asked."""
    monkeypatch.setattr(gui._TranscribeWorker, "start", lambda _self: None)
    monkeypatch.setattr(
        gui, "keep_awake", lambda _reason: Inhibition(held=False, detail="test")
    )


def _stop_recording(window: gui._Window, wav: Path, *, mean_rms: float = 0.1) -> None:
    wav.parent.mkdir(parents=True, exist_ok=True)
    wav.write_bytes(b"")
    window._wav = wav
    window._recording = True
    window._on_recorded(
        Recording(
            path=wav, duration=60.0, peak=0.5, mean_rms=mean_rms, interrupted=None
        )
    )


def _queued(window: gui._Window) -> list[Path]:
    assert window._transcriber is not None
    return window._transcriber.jobs.waiting()


def test_a_stopped_recording_is_queued_at_once(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    wav = tmp_path / "lectures" / "a" / "a.wav"

    _stop_recording(window, wav)

    assert _queued(window) == [wav]
    assert "queued for transcription" in window._status.text()
    # Already in line, so pressing transcribe would only queue it twice.
    assert not window._text.isEnabled()


def test_a_silent_recording_is_not_queued(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)

    _stop_recording(window, tmp_path / "lectures" / "a" / "a.wav", mean_rms=0.0)

    assert window._transcriber is None
    assert "not queued" in window._status.text()


def test_the_next_lecture_can_be_recorded_while_one_is_recognised(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    _stop_recording(window, tmp_path / "lectures" / "a" / "a.wav")
    window._course.setText("os")

    window._refresh()

    assert window._record.isEnabled()
    assert window._language_box.isEnabled()
    assert window._open.isEnabled()
    assert not window._stop_text.isHidden()


def test_a_second_recording_joins_the_running_queue(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    first = tmp_path / "lectures" / "a" / "a.wav"
    second = tmp_path / "lectures" / "b" / "b.wav"
    _stop_recording(window, first)
    worker = window._transcriber

    _stop_recording(window, second)

    # The same worker, so the model is not loaded a second time.
    assert window._transcriber is worker
    assert _queued(window) == [first, second]


def test_the_language_is_the_one_chosen_when_queued(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    _stop_recording(window, tmp_path / "lectures" / "a" / "a.wav")
    window._language_box.select("en")
    _stop_recording(window, tmp_path / "lectures" / "b" / "b.wav")
    assert window._transcriber is not None
    jobs = window._transcriber.jobs

    languages = [job.language for job in (jobs.take(), jobs.take()) if job]

    assert languages == ["ko", "en"]


def test_a_recording_queued_as_the_worker_winds_down_gets_a_worker_of_its_own(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    _stop_recording(window, tmp_path / "lectures" / "a" / "a.wav")
    first = window._transcriber
    assert first is not None
    # The worker has taken the last recording and found the queue empty.
    first.jobs.take()
    assert first.jobs.take() is None
    late = tmp_path / "lectures" / "b" / "b.wav"

    _stop_recording(window, late)
    window._release_transcriber()

    assert window._transcriber is not None
    assert window._transcriber is not first
    assert _queued(window) == [late]


def test_stop_drops_every_recording_still_waiting(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    _stop_recording(window, tmp_path / "lectures" / "a" / "a.wav")
    _stop_recording(window, tmp_path / "lectures" / "b" / "b.wav")

    window._stop_transcription()

    assert _queued(window) == []
    assert not window._stop_text.isEnabled()
    assert "2 queued recordings dropped" in window._queue_line.text()


def test_a_queue_found_empty_refuses_more_work(tmp_path: Path) -> None:
    jobs = gui._JobQueue([])

    assert jobs.take() is None
    assert not jobs.put(gui._Job(wav=tmp_path / "a.wav", marks=(), language="ko"))


@dataclass
class _Model:
    supported_languages: tuple[str, ...] = ("en", "ko")


def test_one_model_serves_the_queue_and_a_failure_does_not_end_it(
    app: QApplication,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = tmp_path / "broken" / "broken.wav"
    fine = tmp_path / "fine" / "fine.wav"
    fine.parent.mkdir()
    jobs = gui._JobQueue(
        [
            gui._Job(wav=broken, marks=(), language="ko"),
            gui._Job(wav=fine, marks=(), language="ko"),
        ]
    )
    worker = gui._TranscribeWorker(Config(), jobs)
    loads: list[str] = []

    def load(name: str, **_kwargs: object) -> _Model:
        loads.append(name)
        return _Model()

    def recognise(_model: object, wav: Path, **_kwargs: object) -> Transcription:
        if wav == broken:
            raise TranscriptionError(f"cannot transcribe {wav}: damaged")
        return Transcription(segments=[], audio_duration=60.0, decode_seconds=6.0)

    monkeypatch.setattr(gui, "load_model", load)
    monkeypatch.setattr(gui, "transcribe", recognise)
    began: list[Path] = []
    failed: list[str] = []
    written: list[Path] = []
    worker.began.connect(began.append)
    worker.failed.connect(failed.append)
    worker.transcribed.connect(lambda wav, _path, _text, _result: written.append(wav))

    worker.run()

    assert loads == ["large-v3"]
    assert began == [broken, fine]
    assert len(failed) == 1
    assert "damaged" in failed[0]
    assert written == [fine]
    assert (fine.parent / "fine.txt").is_file()


def test_a_gpu_that_cannot_decode_fails_the_whole_queue_at_once(
    app: QApplication,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = gui._JobQueue(
        [
            gui._Job(wav=tmp_path / "a.wav", marks=(), language="ko"),
            gui._Job(wav=tmp_path / "b.wav", marks=(), language="ko"),
        ]
    )
    worker = gui._TranscribeWorker(Config(asr=AsrConfig(gpu=True)), jobs)

    def never(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no model should be loaded without a usable GPU")

    monkeypatch.setattr(
        gui, "gpu_status", lambda: GpuStatus(False, 1, "cuBLAS cannot be loaded")
    )
    monkeypatch.setattr(gui, "load_model", never)
    failed: list[str] = []
    worker.failed.connect(failed.append)

    worker.run()

    assert len(failed) == 1
    assert "cuBLAS cannot be loaded" in failed[0]
    assert "2 recordings not transcribed" in failed[0]
    assert jobs.waiting() == []


_OFFERED = (
    ModelChoice(label="large-v3", model="large-v3", note="default"),
    ModelChoice(label="buzz", model="/models/buzz", note="Korean-tuned"),
)


def test_the_model_list_starts_on_the_configured_one_with_its_note(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    config = Config(output=OutputConfig(dir=tmp_path / "lectures"), models=_OFFERED)

    window = gui._Window(config)

    assert window._model_box.texts() == ["large-v3", "buzz"]
    # The note of the model in use is shown beside the list, not hidden in it.
    assert window._model_note.text() == "default"
    assert window._recognition_config().asr.model == "large-v3"


def test_a_configured_model_outside_the_list_is_kept(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    config = Config(
        asr=AsrConfig(model="medium"),
        output=OutputConfig(dir=tmp_path / "lectures"),
        models=_OFFERED,
    )

    window = gui._Window(config)

    assert window._model_box.count() == 3
    assert window._recognition_config().asr.model == "medium"


def test_a_recording_is_queued_with_the_model_chosen_then(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    config = Config(output=OutputConfig(dir=tmp_path / "lectures"), models=_OFFERED)
    window = gui._Window(config)
    window._model_box.select("buzz")

    _stop_recording(window, tmp_path / "lectures" / "a" / "a.wav")
    assert window._transcriber is not None
    job = window._transcriber.jobs.take()

    assert job is not None
    assert job.model == "buzz"


def test_a_model_is_loaded_once_per_run_of_recordings_that_ask_for_it(
    app: QApplication,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wavs = [tmp_path / name / f"{name}.wav" for name in ("a", "b", "c")]
    for wav in wavs:
        wav.parent.mkdir()
    jobs = gui._JobQueue(
        [
            gui._Job(wav=wavs[0], marks=(), language="ko", model="buzz"),
            gui._Job(wav=wavs[1], marks=(), language="ko", model="buzz"),
            gui._Job(wav=wavs[2], marks=(), language="ko", model="large-v3"),
        ]
    )
    worker = gui._TranscribeWorker(Config(models=_OFFERED), jobs)
    loads: list[str] = []

    def load(name: str, **_kwargs: object) -> _Model:
        loads.append(name)
        return _Model()

    def recognise(_model: object, _wav: Path, **_kwargs: object) -> Transcription:
        return Transcription(segments=[], audio_duration=60.0, decode_seconds=6.0)

    monkeypatch.setattr(gui, "load_model", load)
    monkeypatch.setattr(gui, "transcribe", recognise)
    texts: list[str] = []
    worker.transcribed.connect(lambda _wav, _path, text, _result: texts.append(text))

    worker.run()

    # The label is resolved to what it names, and loaded again only on change.
    assert loads == ["/models/buzz", "large-v3"]
    # The transcript header names the model by its label, not its directory.
    assert "buzz" in texts[0].splitlines()[0]
    assert "/models/buzz" not in texts[0]


def test_with_transcribe_after_stop_off_a_recording_waits_in_the_list(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._auto.setChecked(False)
    wav = tmp_path / "lectures" / "a" / "a.wav"

    _stop_recording(window, wav)

    assert window._transcriber is None
    assert "Transcribe after stop is off" in window._status.text()
    assert [lecture.wav for lecture in window._lectures] == [wav]


def test_the_transcripts_column_lists_the_folder_and_searches_it(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    plain = _wav(tmp_path / "lectures" / "2026-11-24_net" / "2026-11-24_net.wav")
    _wav(tmp_path / "lectures" / "2026-11-25_os" / "2026-11-25_os.wav")
    plain.with_suffix(".txt").write_text(
        "2026-11-24_net -- buzz-korean (ko), 0:50:08\n\n"
        "[00:01] 지난 시간에 이어서\n\n[12:40] 패킷 로스가 발생합니다\n",
        encoding="utf-8",
    )
    window = _window(tmp_path)

    assert window._done_col._count.text() == "2"
    window._search.setText("패킷")

    assert window._done_col._count.text() == "1"
    hit = next(iter(window._done_col._cards))
    assert isinstance(hit, tuple)
    assert hit[0].wav == plain


def test_a_recording_without_a_transcript_can_be_queued_from_the_list(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    idle_workers: None,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    wav = _wav(tmp_path / "lectures" / "a" / "a.wav")
    marks_path(wav).write_text("5.0\n", encoding="utf-8")
    window = _window(tmp_path)

    window._transcribe_wav(wav, 0.1)

    assert _queued(window) == [wav]
    assert window._queued[wav].marks == (5.0,)


@pytest.mark.manual
def test_window_opens_and_lists_the_devices() -> None:
    """Needs a display and a working sound backend."""
    from lecture_scribe.config import Config
    from lecture_scribe.gui import _Window

    _app = QApplication.instance() or QApplication([])
    window = _Window(Config())

    assert window.windowTitle() == "lecture scribe"
    assert window._device_box.count() == len(window._devices)


def test_a_mark_is_saved_beside_the_recording_as_it_is_pressed(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._wav = tmp_path / "lecture.wav"
    window._recording = True
    window._elapsed = 42.0

    window._mark_moment()

    assert read_marks(window._wav) == [42.0]
    assert window._marks == [42.0]


def test_a_mark_that_cannot_be_saved_is_still_kept_and_reported(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
) -> None:
    window = _window(tmp_path)
    window._wav = tmp_path / "missing" / "lecture.wav"
    window._recording = True
    window._elapsed = 7.0

    window._mark_moment()

    assert window._marks == [7.0]
    assert "could not be saved" in window._status.text()


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, np.zeros(1600, dtype=np.float32), 16000)
    return path


def test_opening_a_recording_loads_the_marks_made_for_it(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    wav = _wav(tmp_path / "a" / "a.wav")
    marks_path(wav).write_text("5.0\n9.5\n", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(wav), "")
    )

    window._open_recording()

    assert window._marks == [5.0, 9.5]


def test_opening_a_recording_without_marks_drops_the_previous_ones(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    window._marks = [1.0, 2.0]
    wav = _wav(tmp_path / "b" / "b.wav")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(wav), "")
    )

    window._open_recording()

    assert window._marks == []


def test_a_damaged_marks_file_leaves_the_open_recording_as_it_was(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    kept = _wav(tmp_path / "kept" / "kept.wav")
    window._wav = kept
    window._marks = [3.0]
    broken = _wav(tmp_path / "broken" / "broken.wav")
    marks_path(broken).write_text("soon\n", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(broken), "")
    )

    window._open_recording()

    assert window._wav == kept
    assert window._marks == [3.0]
    assert "line 1" in window._status.text()


def test_a_transcript_that_cannot_be_read_stays_a_problem_with_marks(
    app: QApplication,  # noqa: ARG001
    devices: _Devices,  # noqa: ARG001
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path)
    wav = _wav(tmp_path / "c" / "c.wav")
    marks_path(wav).write_text("5.0\n", encoding="utf-8")
    transcript = wav.parent / "c.txt"
    transcript.write_text("text", encoding="utf-8")
    real_read = Path.read_text

    def refuse(path: Path, *args: object, **kwargs: object) -> str:
        if path == transcript:
            raise PermissionError(13, "Permission denied")
        return real_read(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(wav), "")
    )

    window._open_recording()

    assert window._status.property("problem") is True
    assert "cannot read" in window._status.text()
