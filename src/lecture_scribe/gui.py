"""Desktop window for recording a lecture and transcribing it.

A second front end beside :mod:`lecture_scribe.cli`, and like it, holding no
logic of its own: it drives the same capture, recognition and formatting
functions and shows what they report. Recording and recognition each get a
worker thread, because both block for as long as the lecture is long.

Qt's Fusion style is forced on both platforms. The window has to look the
same on Linux and on Windows, and the native styles differ too much in
metrics and colour for one stylesheet to cover both.
"""

import math
import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from lecture_scribe.audio_capture import (
    AudioDeviceError,
    LoopbackDevice,
    Progress,
    Recording,
    lecture_dir,
    list_loopback_devices,
    record_loopback,
    resolve_device,
)
from lecture_scribe.config import Config, ConfigError, load_config
from lecture_scribe.format_text import format_timecode, render_transcript
from lecture_scribe.transcribe import (
    Transcription,
    TranscriptionError,
    compute_type,
    load_model,
    transcribe,
)

_FLOOR_DBFS = -60.0
"""Level shown as an empty meter; below this a lecture is inaudible anyway."""

# Korean is the point of the tool, so a CJK family is named in every stack
# rather than left to whatever Qt happens to fall back to per character.
_UI_FAMILIES = (
    "Inter",
    "Segoe UI",
    "Noto Sans",
    "Malgun Gothic",
    "Noto Sans KR",
    "Noto Sans CJK KR",
    "DejaVu Sans",
)
_MONO_FAMILIES = (
    "JetBrains Mono",
    "Consolas",
    "D2Coding",
    "Noto Sans Mono CJK KR",
    "DejaVu Sans Mono",
    "monospace",
)

_BG = "#101010"
_SURFACE = "#171717"
_LINE = "#262626"
_TEXT = "#e8e8e8"
_MUTED = "#7a7a7a"
_FAINT = "#5f5f5f"

_STYLESHEET = f"""
QWidget {{ background: {_BG}; color: {_TEXT}; }}
QLabel {{ background: transparent; }}
QLabel#caption {{ color: {_FAINT}; }}
QLabel#clock {{ color: {_TEXT}; }}
QLabel#levels {{ color: {_MUTED}; }}
QLabel#status {{ color: {_MUTED}; }}
QLabel#status[problem="true"] {{ color: {_TEXT}; }}

QLineEdit, QComboBox {{
    background: {_SURFACE};
    border: 1px solid {_LINE};
    border-radius: 2px;
    padding: 7px 10px;
    selection-background-color: #3a3a3a;
    selection-color: {_TEXT};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: #4e4e4e; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {_FAINT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{
    background: {_SURFACE};
    border: 1px solid {_LINE};
    selection-background-color: #2c2c2c;
    outline: none;
}}

QPushButton {{
    background: transparent;
    border: 1px solid #333333;
    border-radius: 2px;
    padding: 9px 22px;
    color: #d2d2d2;
}}
QPushButton:hover {{ border-color: #5c5c5c; color: #ffffff; }}
QPushButton:pressed {{ background: #1c1c1c; }}
QPushButton:disabled {{ color: #3c3c3c; border-color: #212121; }}
QPushButton#primary {{ border-color: #585858; color: #ffffff; }}
QPushButton#primary:disabled {{ color: #3c3c3c; border-color: #212121; }}

QPlainTextEdit {{
    background: #131313;
    border: 1px solid {_LINE};
    border-radius: 2px;
    padding: 12px;
    selection-background-color: #3a3a3a;
    selection-color: {_TEXT};
}}
QFrame#rule {{ background: {_LINE}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: #2f2f2f; border-radius: 5px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: #484848; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QMessageBox {{ background: {_SURFACE}; }}
"""


class _RecordWorker(QThread):
    """Runs one recording off the GUI thread.

    ``record_loopback`` blocks until its stop event is set, which for a
    lecture means an hour or more, so it cannot share the thread that has to
    keep redrawing the level meter.
    """

    progressed = Signal(Progress)
    warned = Signal(str)
    recorded = Signal(Recording)
    failed = Signal(str)

    def __init__(
        self,
        device: LoopbackDevice,
        path: Path,
        *,
        sample_rate: int,
        silence_rms: float,
    ) -> None:
        """Prepare a recording without starting it.

        Args:
            device: Output device to capture.
            path: WAV file to create.
            sample_rate: Capture rate in Hz.
            silence_rms: Level below which the opening seconds count as silent.
        """
        super().__init__()
        self._device = device
        self._path = path
        self._sample_rate = sample_rate
        self._silence_rms = silence_rms
        self._stop = threading.Event()

    def stop(self) -> None:
        """Ask the recording to finish and close its file."""
        self._stop.set()

    def run(self) -> None:
        """Record until stopped, then report the finished file."""
        try:
            recording = record_loopback(
                self._device,
                self._path,
                sample_rate=self._sample_rate,
                stop=self._stop,
                silence_rms=self._silence_rms,
                on_progress=self._report,
            )
        except AudioDeviceError as error:
            self.failed.emit(str(error))
            return
        self.recorded.emit(recording)

    def _report(self, progress: Progress) -> None:
        """Forward one block's state to the window as a queued signal."""
        if progress.warning is not None:
            self.warned.emit(progress.warning)
        self.progressed.emit(progress)


class _TranscribeWorker(QThread):
    """Loads the model and transcribes one recording off the GUI thread."""

    staged = Signal(str)
    transcribed = Signal(Path, str, Transcription)
    failed = Signal(str)

    def __init__(self, config: Config, wav: Path, marks: list[float]) -> None:
        """Prepare a transcription without starting it.

        Args:
            config: Effective configuration.
            wav: Recording to transcribe.
            marks: Offsets in seconds flagged during the lecture.
        """
        super().__init__()
        self._config = config
        self._wav = wav
        self._marks = marks

    def run(self) -> None:
        """Recognise the recording and write ``transcript.txt`` beside it."""
        asr = self._config.asr
        self.staged.emit(f"loading {asr.model} ({compute_type(asr.gpu)}) ...")
        try:
            model = load_model(asr.model, gpu=asr.gpu)
        except TranscriptionError as error:
            self.failed.emit(str(error))
            return

        self.staged.emit("decoding, this takes a while ...")
        try:
            result = transcribe(
                model,
                self._wav,
                language=asr.language,
                beam_size=asr.beam_size,
            )
        except TranscriptionError as error:
            self.failed.emit(str(error))
            return

        text = render_transcript(
            result.segments,
            title=self._wav.parent.name,
            model=asr.model,
            language=asr.language,
            audio_duration=result.audio_duration,
            paragraph_gap=self._config.output.paragraph_gap,
            marks=self._marks,
        )
        path = self._wav.parent / "transcript.txt"
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as error:
            self.failed.emit(f"cannot write {path}: {error}")
            return
        self.transcribed.emit(path, text, result)


class _Combo(QComboBox):
    """A device list that draws its own arrow.

    Qt stops painting the native arrow as soon as the drop-down is styled at
    all, and the native one arrives with a raised panel and a separator that
    do not belong in a flat window.
    """

    _WIDTH = 9.0
    _HEIGHT = 5.0

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw the box, then the arrow on top of it."""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_MUTED if self.isEnabled() else "#3c3c3c"))

        right = self.width() - 13.0
        top = (self.height() - self._HEIGHT) / 2
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(right - self._WIDTH, top),
                    QPointF(right, top),
                    QPointF(right - self._WIDTH / 2, top + self._HEIGHT),
                ]
            )
        )


class _LevelMeter(QWidget):
    """A segmented bar showing how loud the incoming audio is.

    Brightness rises across the bar instead of colour changing, so the level
    still reads at a glance without leaving the grey palette.
    """

    _SEGMENTS = 48
    _GAP = 2
    _PEAK_FALL = 0.012

    def __init__(self) -> None:
        """Create an empty meter."""
        super().__init__()
        self.setFixedHeight(9)
        self._level = 0.0
        self._peak = 0.0

    def set_rms(self, rms: float) -> None:
        """Show the level of the block just recorded.

        Args:
            rms: Block level from 0.0 to 1.0.
        """
        self._level = loudness(rms)
        # The peak marker sinks slowly rather than following every block, so a
        # brief loud passage stays on screen long enough to be seen.
        self._peak = max(self._level, self._peak - self._PEAK_FALL)
        self.update()

    def reset(self) -> None:
        """Empty the meter, for when nothing is being recorded."""
        self._level = 0.0
        self._peak = 0.0
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw the segments for the current level and peak."""
        painter = QPainter(self)
        span = self._GAP * (self._SEGMENTS - 1)
        width = (self.width() - span) / self._SEGMENTS
        filled = round(self._SEGMENTS * self._level)
        peak = round(self._SEGMENTS * self._peak) - 1

        for index in range(self._SEGMENTS):
            if index < filled:
                shade = 140 + round(105 * index / (self._SEGMENTS - 1))
            elif index == peak:
                shade = 150
            else:
                shade = 40
            painter.fillRect(
                QRectF(index * (width + self._GAP), 0.0, width, self.height()),
                QColor(shade, shade, shade),
            )


class _Window(QWidget):
    """The single window: record a lecture, then transcribe what was recorded."""

    def __init__(self, config: Config) -> None:
        """Build the window and fill in the device list.

        Args:
            config: Effective configuration.
        """
        super().__init__()
        self._config = config
        self._devices: list[LoopbackDevice] = []
        self._recorder: _RecordWorker | None = None
        self._transcriber: _TranscribeWorker | None = None
        self._recording = False
        self._transcribing = False
        self._wav: Path | None = None
        self._marks: list[float] = []
        self._elapsed = 0.0

        self._build()
        self._load_devices()
        self._refresh()

    def _build(self) -> None:
        """Lay the window out. One column, top to bottom, nothing hidden."""
        self.setWindowTitle("lecture scribe")
        self.setMinimumSize(660, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 26, 34, 26)
        layout.setSpacing(0)

        title = QLabel("LECTURE SCRIBE")
        title.setObjectName("caption")
        _track(title, 2.4)
        layout.addWidget(title)
        layout.addSpacing(26)

        form = QGridLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)
        self._course = QLineEdit()
        self._course.setPlaceholderText("subject name, used for the folder")
        self._course.textChanged.connect(self._refresh)
        self._device_box = _Combo()
        form.addWidget(_caption("COURSE"), 0, 0)
        form.addWidget(self._course, 0, 1)
        form.addWidget(_caption("DEVICE"), 1, 0)
        form.addWidget(self._device_box, 1, 1)
        layout.addLayout(form)
        layout.addSpacing(34)

        self._clock = QLabel(_clock(0.0))
        self._clock.setObjectName("clock")
        self._clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clock_font = QFont()
        clock_font.setFamilies(_MONO_FAMILIES)
        clock_font.setPointSize(38)
        clock_font.setWeight(QFont.Weight.Light)
        self._clock.setFont(clock_font)
        layout.addWidget(self._clock)
        layout.addSpacing(16)

        self._meter = _LevelMeter()
        layout.addWidget(self._meter)
        layout.addSpacing(10)

        self._levels = QLabel("")
        self._levels.setObjectName("levels")
        self._levels.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _track(self._levels, 0.8)
        layout.addWidget(self._levels)
        layout.addSpacing(28)

        self._record = QPushButton("record")
        self._record.setObjectName("primary")
        self._record.clicked.connect(self._toggle_record)
        self._mark = QPushButton("mark")
        self._mark.setToolTip("flag this moment in the transcript (Ctrl+M)")
        self._mark.clicked.connect(self._mark_moment)
        self._text = QPushButton("transcribe")
        self._text.clicked.connect(self._start_transcription)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        for button in (self._record, self._mark, self._text):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addSpacing(28)

        rule = QFrame()
        rule.setObjectName("rule")
        rule.setFixedHeight(1)
        layout.addWidget(rule)
        layout.addSpacing(14)

        self._status = QLabel("ready")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        layout.addSpacing(14)

        self._transcript = QPlainTextEdit()
        self._transcript.setReadOnly(True)
        self._transcript.setPlaceholderText("the transcript appears here")
        transcript_font = QFont()
        transcript_font.setFamilies(_MONO_FAMILIES)
        transcript_font.setPointSize(10)
        self._transcript.setFont(transcript_font)
        layout.addWidget(self._transcript, 1)

        shortcut = QShortcut(QKeySequence("Ctrl+M"), self)
        shortcut.activated.connect(self._mark_moment)

    def _load_devices(self) -> None:
        """Fill the device list and preselect the configured one."""
        try:
            devices = list_loopback_devices()
        except AudioDeviceError as error:
            self._say(str(error), problem=True)
            return

        self._devices = devices
        for device in devices:
            suffix = "  (system default)" if device.is_default else ""
            self._device_box.addItem(f"{device.name}{suffix}")

        try:
            selected = resolve_device(devices, self._config.audio.device)
        except AudioDeviceError as error:
            self._say(str(error), problem=True)
            return
        self._device_box.setCurrentIndex(devices.index(selected))

    def _toggle_record(self) -> None:
        """Start recording, or stop the recording that is running."""
        if self._recording and self._recorder is not None:
            self._say("stopping ...")
            self._record.setEnabled(False)
            self._recorder.stop()
            return
        self._start_recording()

    def _start_recording(self) -> None:
        """Create the lecture folder and hand the capture to a worker."""
        device = self._devices[self._device_box.currentIndex()]
        try:
            folder = lecture_dir(
                self._config.output.dir, self._course.text().strip(), datetime.now()
            )
        except ValueError as error:
            self._say(str(error), problem=True)
            return
        try:
            folder.mkdir(parents=True)
        except OSError as error:
            self._say(f"cannot create {folder}: {error}", problem=True)
            return

        self._wav = folder / "audio.wav"
        self._marks = []
        self._elapsed = 0.0
        self._transcript.clear()

        worker = _RecordWorker(
            device,
            self._wav,
            sample_rate=self._config.audio.sample_rate,
            silence_rms=self._config.audio.silence_rms,
        )
        worker.progressed.connect(self._on_progress)
        worker.warned.connect(self._on_warning)
        worker.recorded.connect(self._on_recorded)
        worker.failed.connect(self._on_record_failed)
        worker.finished.connect(self._release_recorder)

        self._recorder = worker
        self._recording = True
        self._refresh()
        self._say(f"recording to {self._wav}")
        worker.start()

    def _on_progress(self, progress: Progress) -> None:
        """Redraw the clock, meter and level readout."""
        self._elapsed = progress.elapsed
        self._clock.setText(_clock(progress.elapsed))
        self._meter.set_rms(progress.rms)
        self._levels.setText(
            f"{_dbfs(progress.rms)}   ·   {progress.bytes_written / 1_000_000:.1f} MB"
        )

    def _on_warning(self, message: str) -> None:
        """Show a warning raised while recording; the recording continues."""
        self._say(message, problem=True)

    def _on_recorded(self, recording: Recording) -> None:
        """Report the finished file and offer to transcribe it."""
        self._recording = False
        self._meter.reset()
        self._clock.setText(_clock(recording.duration))
        self._levels.setText(
            f"peak {_dbfs(recording.peak)}   ·   "
            f"{recording.path.stat().st_size / 1_000_000:.1f} MB"
        )
        if recording.mean_rms < self._config.audio.silence_rms:
            self._say(
                "the recording is silent from end to end. The audio was kept "
                "anyway; check that the lecture was playing through the "
                "selected device.",
                problem=True,
            )
        else:
            self._say(f"stopped after {_clock(recording.duration)} · {recording.path}")
        self._refresh()

    def _on_record_failed(self, message: str) -> None:
        """Report a recording that could not start or could not continue."""
        self._recording = False
        self._meter.reset()
        self._say(message, problem=True)
        self._refresh()

    def _release_recorder(self) -> None:
        """Drop the finished worker, waiting so it is never freed mid-run."""
        if self._recorder is not None:
            self._recorder.wait()
            self._recorder = None

    def _mark_moment(self) -> None:
        """Flag the current offset so its paragraph is prefixed with ``>>>``."""
        if not self._recording:
            return
        self._marks.append(self._elapsed)
        self._say(
            f"marked {format_timecode(self._elapsed)} "
            f"({len(self._marks)} in this lecture)"
        )

    def _start_transcription(self) -> None:
        """Hand the recording just made to a worker for recognition."""
        if self._wav is None:
            return
        worker = _TranscribeWorker(self._config, self._wav, list(self._marks))
        worker.staged.connect(self._say)
        worker.transcribed.connect(self._on_transcribed)
        worker.failed.connect(self._on_transcribe_failed)
        worker.finished.connect(self._release_transcriber)

        self._transcriber = worker
        self._transcribing = True
        self._refresh()
        worker.start()

    def _on_transcribed(self, path: Path, text: str, result: Transcription) -> None:
        """Show the finished transcript and how long decoding took."""
        self._transcribing = False
        self._transcript.setPlainText(text)
        ratio = (
            result.decode_seconds / result.audio_duration
            if result.audio_duration > 0
            else 0.0
        )
        self._say(
            f"wrote {path} · {len(result.segments)} segments · "
            f"{_clock(result.decode_seconds)} ({ratio:.2f}x audio length)"
        )
        self._refresh()

    def _on_transcribe_failed(self, message: str) -> None:
        """Report a recognition that could not be completed."""
        self._transcribing = False
        self._say(message, problem=True)
        self._refresh()

    def _release_transcriber(self) -> None:
        """Drop the finished worker, waiting so it is never freed mid-run."""
        if self._transcriber is not None:
            self._transcriber.wait()
            self._transcriber = None

    def _refresh(self) -> None:
        """Bring every control into line with what the window is doing."""
        idle = not self._recording and not self._transcribing
        named = bool(self._course.text().strip())
        self._record.setText("stop" if self._recording else "record")
        self._record.setEnabled(
            self._recording or (idle and named and bool(self._devices))
        )
        self._mark.setEnabled(self._recording)
        self._text.setEnabled(idle and self._wav is not None)
        self._course.setEnabled(idle)
        self._device_box.setEnabled(idle)

    def _say(self, message: str, *, problem: bool = False) -> None:
        """Put one line in the status row.

        Args:
            message: Text to show.
            problem: Whether to draw it as something needing attention.
        """
        self._status.setProperty("problem", problem)
        # A property already read by the stylesheet only takes effect on a
        # live widget after the style is applied again.
        style = self._status.style()
        style.unpolish(self._status)
        style.polish(self._status)
        self._status.setText(message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """End a running recording cleanly before the window goes away.

        Recognition cannot be interrupted part way through a file, so closing
        during it is confirmed rather than done quietly.
        """
        if self._transcribing:
            answer = QMessageBox.question(
                self,
                "lecture scribe",
                "Recognition is still running and cannot be resumed.\n"
                "The recording is already safe on disk. Close anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        if self._recorder is not None:
            self._recorder.stop()
            self._recorder.wait()
        event.accept()


def loudness(rms: float) -> float:
    """Map a level to the 0.0 to 1.0 range the meter is drawn in.

    Args:
        rms: Level from 0.0 to 1.0, as reported while recording.

    Returns:
        How full the meter should be, with ``_FLOOR_DBFS`` reading empty.
    """
    if rms <= 0.0:
        return 0.0
    filled = (20 * math.log10(rms) - _FLOOR_DBFS) / -_FLOOR_DBFS
    return max(0.0, min(1.0, filled))


def run(config: Config) -> int:
    """Open the window and run it until it is closed.

    Args:
        config: Effective configuration.

    Returns:
        The process exit code.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("lecture-scribe")
    app.setStyle("Fusion")
    app.setStyleSheet(_STYLESHEET)

    font = QFont()
    font.setFamilies(_UI_FAMILIES)
    font.setPointSize(10)
    app.setFont(font)

    window = _Window(config)
    window.show()
    return app.exec()


def main() -> int:
    """Open the window as its own program, reading ``config.toml`` first.

    Returns:
        The process exit code.
    """
    try:
        config = load_config()
    except ConfigError as error:
        # The Windows launcher has no console behind it, so a configuration
        # mistake has to be shown in a window or it is not shown at all.
        _app = QApplication(sys.argv)
        QMessageBox.critical(None, "lecture scribe", f"config error: {error}")
        return 2
    return run(config)


def _caption(text: str) -> QLabel:
    """Build one of the small tracked-out labels beside a field."""
    label = QLabel(text)
    label.setObjectName("caption")
    _track(label, 1.6)
    return label


def _track(label: QLabel, spacing: float) -> None:
    """Letter-space a label, which Qt stylesheets cannot express."""
    font = label.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    label.setFont(font)


def _clock(seconds: float) -> str:
    """Format a duration as ``H:MM:SS``."""
    whole = int(seconds)
    return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def _dbfs(level: float) -> str:
    """Format a level from 0.0 to 1.0 as decibels below full scale."""
    if level <= 0.0:
        return "-inf dBFS"
    return f"{20 * math.log10(level):.1f} dBFS"


if __name__ == "__main__":
    sys.exit(main())
