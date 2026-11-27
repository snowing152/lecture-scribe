"""Desktop window for recording a lecture and transcribing it.

A second front end beside :mod:`lecture_scribe.cli`, and like it, holding no
logic of its own: it drives the same capture, recognition and formatting
functions and shows what they report. Recording and recognition each get a
worker thread, because both block for as long as the lecture is long, and
they may run at once: the next lecture can be recorded while the queue of
earlier ones is recognised.

Qt's Fusion style is forced on both platforms. The window has to look the
same on Linux and on Windows, and the native styles differ too much in
metrics and colour for one stylesheet to cover both.
"""

import ctypes
import html
import math
import sys
import threading
from collections import deque
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Literal, cast

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QMimeData,
    QParallelAnimationGroup,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lecture_scribe.audio_capture import (
    AudioDeviceError,
    ExistingRecording,
    LoopbackDevice,
    Progress,
    Recording,
    append_mark,
    dbfs,
    inspect_recording,
    lecture_dir,
    list_loopback_devices,
    loudness,
    read_marks,
    record_loopback,
    recording_path,
    remove_if_empty,
    resolve_device,
)
from lecture_scribe.config import (
    Config,
    ConfigError,
    load_config,
    merge_cli,
    model_source,
)
from lecture_scribe.format_text import (
    Passage,
    format_duration,
    format_timecode,
    read_transcript,
    render_transcript,
)
from lecture_scribe.keep_awake import Inhibition, keep_awake
from lecture_scribe.launcher import APP_USER_MODEL_ID, DESKTOP_ID, ICON
from lecture_scribe.library import Hit, Lecture, list_lectures, search
from lecture_scribe.transcribe import (
    Decoding,
    Transcription,
    TranscriptionError,
    check_language,
    compute_type,
    earlier_transcript,
    gpu_status,
    load_model,
    transcribe,
    transcript_path,
    write_transcript,
)

_LANGUAGES = (("ko", "Korean"), ("en", "English"))
"""What the language list offers, as Whisper code and name.

No automatic entry: Whisper guesses once from the opening thirty seconds and
holds the whole file to that guess. Measured here, it took Korean for
Japanese, and a Korean lecture recognised as English comes out as a loose
English translation. Any other code still works from ``config.toml``.
"""

_BOARD = 0
_READER = 1
"""Pages of the area under the recorder: the queue, or one transcript."""

_STACK_BELOW = 900
"""Window width under which the queue's three columns stack."""

_UNLIMITED = 16777215
"""Qt's QWIDGETSIZE_MAX: no maximum height."""

_GLYPH = 10
"""Side of a button's painted icon, in logical pixels."""
_GLYPH_GAP = 6
"""Space between the icon and the label; Qt's own is a cramped 4 px under Fusion."""

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

# Warm greys after Monokai Pro, three surfaces deep: depth comes from how
# light a panel is, so borders are left for focus and for cards.
_BG = "#221f22"
_SURFACE = "#2d2a2e"
_RAISED = "#363337"
_HOVER = "#403e41"
_LINE = "#403e41"
_DASH = "#5b595c"
_TEXT = "#fcfcfa"
# Lighter than Monokai's own #939293, which falls under 4.5:1 on the cards.
_MUTED = "#c1c0c0"
_FAINT = "#727072"
# Each colour has one meaning (D-30): yellow is what can be pressed or is in
# progress, pink-red a recording that is live, orange a mark, green done.
_ACCENT = "#ffd866"
_ACCENT_HOVER = "#ffe599"
_INK = "#221f22"
"""Text on yellow and on pink-red, which white would fail on."""
_LIVE = "#ff6188"
_MARK = "#fc9867"
_MARK_WASH = "#3b2d27"
"""A marked paragraph's background: orange, faint enough to read through."""
_DONE = "#a9dc76"
_PROBLEM = "#ff8aa6"

_STYLESHEET = f"""
QWidget {{ color: {_TEXT}; }}
QWidget#window {{ background: {_BG}; }}
QLabel {{ background: transparent; }}
QLabel#caption {{ color: {_MUTED}; }}
QLabel#sub {{ color: {_MUTED}; }}
QLabel#title {{ color: {_TEXT}; }}
QLabel#clock {{ color: {_TEXT}; }}
QLabel#status {{ color: {_MUTED}; }}
QLabel#status[problem="true"] {{ color: {_PROBLEM}; }}
QLabel#badge {{
    background: {_HOVER}; border-radius: 9px; padding: 1px 8px; color: {_TEXT};
}}
QLabel#empty {{
    border: 1px dashed {_DASH}; border-radius: 12px; padding: 16px; color: {_MUTED};
}}
QLabel#cardtitle {{ color: {_TEXT}; }}

QFrame#bar, QFrame#column {{ background: {_SURFACE}; border-radius: 16px; }}
QFrame#strip {{
    background: {_SURFACE}; border: 1px solid {_LINE}; border-radius: 20px;
}}
QFrame#card {{
    background: {_RAISED}; border: 1px solid {_LINE}; border-radius: 12px;
}}
QFrame#card[live="true"] {{ border-color: rgba(255, 216, 102, 140); }}
QFrame#card[problem="true"] {{ border-color: {_LIVE}; }}
QFrame#pills {{
    background: {_RAISED}; border: 1px solid {_LINE}; border-radius: 16px;
}}

QLineEdit, QComboBox {{
    background: {_RAISED};
    border: 1px solid {_LINE};
    border-radius: 15px;
    padding: 6px 12px;
    selection-background-color: {_ACCENT};
    selection-color: {_INK};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {_ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{
    color: {_FAINT}; background: transparent;
}}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox QAbstractItemView {{
    background: {_RAISED};
    border: 1px solid {_LINE};
    padding: 4px;
    selection-background-color: {_HOVER};
    outline: none;
}}

QPushButton {{
    background: {_RAISED};
    border: 1px solid {_LINE};
    border-radius: 15px;
    padding: 6px 14px;
    color: {_TEXT};
    font-weight: 500;
}}
QPushButton:hover {{ background: {_HOVER}; }}
QPushButton:focus {{ border-color: {_ACCENT}; }}
QPushButton:disabled {{
    background: transparent; border-color: {_LINE}; color: {_FAINT};
}}
QPushButton#accent {{ background: {_ACCENT}; border-color: {_ACCENT}; color: {_INK}; }}
QPushButton#accent:hover {{ background: {_ACCENT_HOVER}; }}
QPushButton#accent:disabled {{
    background: transparent; border-color: {_LINE}; color: {_FAINT};
}}
QPushButton#pill {{
    background: transparent; border: none; border-radius: 13px;
    padding: 5px 13px; color: {_MUTED};
}}
QPushButton#pill:hover {{ color: {_TEXT}; }}
QPushButton#pill:checked {{ background: {_ACCENT}; color: {_INK}; }}
QPushButton#pill:disabled {{ color: {_FAINT}; }}
QPushButton#pill:checked:disabled {{ background: {_HOVER}; color: {_MUTED}; }}
QPushButton#pill:focus {{ border: 1px solid {_ACCENT}; }}
QPushButton#mark {{
    background: {_MARK_WASH}; border: 1px solid {_MARK_WASH}; border-radius: 18px;
    padding: 9px 16px; color: {_MARK};
}}
QPushButton#mark:hover {{ border-color: {_MARK}; }}
QPushButton#mark:disabled {{
    background: transparent; border-color: {_LINE}; color: {_FAINT};
}}
QPushButton#chip {{
    background: {_MARK_WASH}; border: 1px solid {_MARK_WASH}; border-radius: 11px;
    padding: 2px 10px; color: {_MARK};
}}
QPushButton#chip:hover {{ border-color: {_MARK}; }}
QCheckBox {{ color: {_MUTED}; spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border-radius: 4px;
    border: 1px solid {_DASH}; background: {_RAISED};
}}
QCheckBox::indicator:checked {{ background: {_ACCENT}; border-color: {_ACCENT}; }}
QCheckBox:focus {{ color: {_TEXT}; }}
QPushButton#link {{
    background: transparent; border: none; padding: 2px 4px; color: {_ACCENT};
}}
QPushButton#link:hover {{ color: {_ACCENT_HOVER}; }}
QPushButton#link:disabled {{ color: {_FAINT}; }}

QTextEdit {{
    background: {_SURFACE};
    border: none;
    border-radius: 14px;
    padding: 14px 16px;
    selection-background-color: {_ACCENT};
    selection-color: {_INK};
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QStackedWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 0; }}
QScrollBar::handle:vertical {{
    background: {_HOVER}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {_DASH}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QToolTip {{
    background: {_RAISED}; color: {_TEXT}; border: 1px solid {_LINE}; padding: 4px 8px;
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


@dataclass(frozen=True, slots=True)
class _Job:
    """One recording waiting to be transcribed, with what it was queued with.

    Attributes:
        wav: Recording to transcribe.
        marks: Offsets in seconds flagged during the lecture.
        language: Spoken language, fixed when the recording was queued so
            that choosing another one for the next lecture leaves it alone.
        model: Model to recognise it with, as named in the window or in
            ``asr.model``; fixed when queued, like the language.
        seconds: Length of the recording, for its card on the board.
    """

    wav: Path
    marks: tuple[float, ...]
    language: str
    model: str = "large-v3"
    seconds: float = 0.0


class _JobQueue:
    """Recordings waiting for the transcription worker, shared by two threads.

    Once the worker has found it empty the queue closes, and a recording
    offered after that is refused rather than left behind by a worker that is
    already on its way out; the window then starts a new worker for it.
    """

    def __init__(self, jobs: Sequence[_Job]) -> None:
        """Hold the first recordings.

        Args:
            jobs: Recordings to transcribe, in order.
        """
        self._lock = threading.Lock()
        self._jobs: deque[_Job] = deque(jobs)
        self._closed = False

    def put(self, job: _Job) -> bool:
        """Add a recording at the back.

        Returns:
            Whether the worker will reach it; ``False`` once the queue closed.
        """
        with self._lock:
            if self._closed:
                return False
            self._jobs.append(job)
            return True

    def take(self) -> _Job | None:
        """Hand the worker the next recording, closing the queue when none is left."""
        with self._lock:
            if not self._jobs:
                self._closed = True
                return None
            return self._jobs.popleft()

    def drop(self) -> int:
        """Empty and close the queue.

        Returns:
            How many recordings were waiting.
        """
        with self._lock:
            dropped = len(self._jobs)
            self._jobs.clear()
            self._closed = True
            return dropped

    def waiting(self) -> list[Path]:
        """The recordings not yet started, in order."""
        with self._lock:
            return [job.wav for job in self._jobs]

    def jobs(self) -> list[_Job]:
        """The jobs not yet started, in order."""
        with self._lock:
            return list(self._jobs)


class _TranscribeWorker(QThread):
    """Transcribes queued recordings one by one, loading each model once.

    One after another rather than side by side: two copies of the model would
    fit in 8 GB of video memory, but each would decode slower and the margin
    left would be thin. A model stays loaded for as long as the recordings in
    a row ask for it, since loading alone can take longer than a short
    recording; only a recording queued with another model replaces it.
    Recognition can be given up part way through, which is what lets the
    window close during the twenty minutes a long lecture takes.
    """

    staged = Signal(str)
    began = Signal(Path)
    progressed = Signal(Decoding)
    transcribed = Signal(Path, Path, str, Transcription)
    stopped = Signal(str)
    failed = Signal(str)

    def __init__(self, config: Config, jobs: _JobQueue) -> None:
        """Prepare the transcriptions without starting them.

        Args:
            config: Effective configuration; each recording's own language
                and model replace the ones in it.
            jobs: Recordings to transcribe, which may grow while it runs.
        """
        super().__init__()
        self._config = config
        self.jobs = jobs
        self._stop = threading.Event()

    def stop(self) -> None:
        """Give up the recording being decoded and every one still waiting."""
        self._stop.set()
        self.jobs.drop()

    def run(self) -> None:
        """Recognise each queued recording and write its transcript beside it."""
        gpu = self._config.asr.gpu
        # Checked first because a missing cuBLAS only shows once decoding has
        # begun, and it would then fail every recording in the queue in turn.
        if gpu:
            status = gpu_status()
            if not status.available:
                self._fail_all(status.detail, taken=0)
                return

        loaded: str | None = None
        model: object = None
        while (job := self.jobs.take()) is not None:
            if job.model != loaded:
                # The previous model is let go first: two at once would
                # crowd 8 GB of video memory.
                model = None
                self.staged.emit(f"loading {job.model} ({compute_type(gpu)}) ...")
                try:
                    model = load_model(
                        model_source(merge_cli(self._config, model=job.model)),
                        gpu=gpu,
                    )
                except TranscriptionError as error:
                    self._fail_all(str(error), taken=1)
                    return
                loaded = job.model
                # A stop that arrived while the model loaded -- the window
                # closed during a first download, say -- has emptied the queue
                # already, so the loop would end without a word.
                if self._stop.is_set():
                    self.stopped.emit(
                        "recognition was stopped before it began; the recordings "
                        "themselves are untouched and can be transcribed again"
                    )
                    return
            self.began.emit(job.wav)
            self._run_one(model, job)
            if self._stop.is_set():
                return

    def _run_one(self, model: object, job: _Job) -> None:
        """Transcribe one recording, reporting a failure without ending the queue."""
        config = merge_cli(self._config, language=job.language, model=job.model)
        asr = config.asr
        try:
            check_language(model, asr.language)
        except TranscriptionError as error:
            self.failed.emit(str(error))
            return

        # The decoder scans the whole file for speech before the first
        # segment exists, which on a long lecture is a good twenty seconds
        # with nothing moving on screen.
        self.staged.emit(
            f"{job.wav.parent.name}: reading the recording and finding the "
            "speech in it ..."
        )
        try:
            result = transcribe(
                model,
                job.wav,
                language=asr.language,
                beam_size=asr.beam_size,
                hotwords=asr.hotwords,
                on_progress=self.progressed.emit,
                stop=self._stop,
            )
        except TranscriptionError as error:
            self.failed.emit(str(error))
            return

        if result.interrupted is not None:
            # Nothing is written. Half a lecture in a transcript would read
            # as a whole one, and would replace a complete transcript that an
            # earlier run had left there.
            self.stopped.emit(result.interrupted)
            return

        text = render_transcript(
            result.segments,
            title=job.wav.parent.name,
            model=asr.model,
            language=asr.language,
            audio_duration=result.audio_duration,
            paragraph_gap=config.output.paragraph_gap,
            paragraph_target=config.output.paragraph_target,
            paragraph_max=config.output.paragraph_max,
            marks=list(job.marks),
        )
        path = transcript_path(job.wav)
        try:
            write_transcript(path, text)
        except OSError as error:
            self.failed.emit(f"cannot write {path}: {error}")
            return
        self.transcribed.emit(job.wav, path, text, result)

    def _fail_all(self, reason: str, *, taken: int) -> None:
        """Report a failure that would repeat for every recording, and drop them.

        Args:
            reason: What went wrong.
            taken: Recordings already out of the queue and not transcribed,
                counted among those left behind.
        """
        dropped = self.jobs.drop() + taken
        self.failed.emit(
            f"{reason} · {_plural(dropped, 'recording')} not transcribed, safe on disk"
        )


class _Combo(QComboBox):
    """A device list that draws its own arrow.

    Qt stops painting the native arrow as soon as the drop-down is styled at
    all, and the native one arrives with a raised panel and a separator that
    do not belong in a flat window.
    """

    _WIDTH = 9.0
    _HEIGHT = 5.0

    opening = Signal()
    """Sent just before the list drops down, while it can still be refilled."""

    def showPopup(self) -> None:  # noqa: N802 (Qt override)
        """Let the window bring the list up to date, then drop it down."""
        self.opening.emit()
        super().showPopup()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw the box, an elided label, then the arrow on top of it."""
        # QComboBox's own paintEvent, except that the label is elided. Device
        # names run long enough to hide under the arrow otherwise.
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        option.currentText = self.fontMetrics().elidedText(
            option.currentText, Qt.TextElideMode.ElideRight, field.width()
        )
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_ACCENT if self.isEnabled() else _FAINT))

        right = self.width() - 14.0
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


class _Pills(QFrame):
    """A row of choices of which exactly one holds, the chosen one in yellow.

    Used where every option should stay in sight -- two languages, a few
    models -- since a drop-down would hide the alternatives behind a click.
    """

    changed = Signal(str)
    """Sent with the data of an option the user clicked; not sent by :meth:`select`."""

    def __init__(self) -> None:
        """Create an empty row."""
        super().__init__()
        self.setObjectName("pills")
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(3, 3, 3, 3)
        self._row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_clicked)
        self._data: list[str] = []

    def add(self, text: str, data: str, *, tooltip: str = "") -> None:
        """Append one option.

        Args:
            text: What the option says.
            data: What :meth:`current_data` returns while it is chosen.
            tooltip: A longer explanation shown on hover.
        """
        button = QPushButton(text)
        button.setObjectName("pill")
        button.setCheckable(True)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._group.addButton(button, len(self._data))
        self._data.append(data)
        self._row.addWidget(button)

    def select(self, data: str) -> None:
        """Choose the option holding ``data``; an unknown value changes nothing."""
        if data in self._data:
            button = self._group.button(self._data.index(data))
            button.setChecked(True)

    def current_data(self) -> str:
        """The data of the chosen option, or an empty string when none is."""
        index = self._group.checkedId()
        return self._data[index] if index >= 0 else ""

    def current_text(self) -> str:
        """What the chosen option says, or an empty string when none is."""
        button = self._group.checkedButton()
        return "" if button is None else button.text()

    def count(self) -> int:
        """How many options there are."""
        return len(self._data)

    def texts(self) -> list[str]:
        """What every option says, in order."""
        return [self._group.button(i).text() for i in range(len(self._data))]

    def _on_clicked(self, index: int) -> None:
        self.changed.emit(self._data[index])


class _RecordButton(QPushButton):
    """The record and stop button, painted so that it can pulse while live.

    The pulse is a ring that grows and fades around the pill every 1.8
    seconds. It runs only while recording, so an idle window costs no redraws.
    """

    _MARGIN = 10.0
    """Room around the pill for the pulse to grow into."""
    _HEIGHT = 46.0
    _PAD = 24.0
    _DOT = 13.0
    _GAP = 10.0

    def __init__(self) -> None:
        """Create a button that says Record."""
        super().__init__("Record")
        self._live = False
        self._pulse = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(1800)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._on_pulse)
        font = self.font()
        font.setPointSizeF(11.5)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedSize(self.sizeHint())

    def set_live(self, live: bool) -> None:
        """Switch between Record and a pulsing Stop.

        Args:
            live: Whether a recording is running.
        """
        self.setText("Stop" if live else "Record")
        if live == self._live:
            return
        self._live = live
        if live:
            self._animation.start()
        else:
            self._animation.stop()
            self._pulse = 0.0
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        """Fit the longer of the two labels, so the button never changes width."""
        metrics = self.fontMetrics()
        text = max(
            metrics.horizontalAdvance("Record"), metrics.horizontalAdvance("Stop")
        )
        width = 2 * self._PAD + self._DOT + self._GAP + text + 2 * self._MARGIN
        return QSize(round(width), round(self._HEIGHT + 2 * self._MARGIN))

    def _on_pulse(self, value: object) -> None:
        self._pulse = float(cast(float, value))
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw the pulse, the pill, the glyph and the label."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = self._MARGIN
        pill = QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)
        radius = pill.height() / 2
        enabled = self.isEnabled()

        painter.setPen(Qt.PenStyle.NoPen)
        if self._live and self._pulse > 0.0:
            grow = self._pulse * margin
            ring = QColor(_LIVE)
            ring.setAlphaF(0.5 * (1.0 - self._pulse))
            painter.setBrush(ring)
            painter.drawRoundedRect(
                pill.adjusted(-grow, -grow, grow, grow), radius + grow, radius + grow
            )

        if enabled:
            fill = _LIVE if self._live else _ACCENT
            if self.underMouse() and not self._live:
                fill = _ACCENT_HOVER
            painter.setBrush(QColor(fill))
        else:
            painter.setPen(QPen(QColor(_LINE), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(pill, radius, radius)
        if enabled and self.hasFocus():
            painter.setPen(QPen(QColor(_TEXT), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(pill.adjusted(-3, -3, 3, 3), radius + 3, radius + 3)

        text_width = self.fontMetrics().horizontalAdvance(self.text())
        left = pill.center().x() - (self._DOT + self._GAP + text_width) / 2
        glyph = QRectF(left, pill.center().y() - self._DOT / 2, self._DOT, self._DOT)
        painter.setPen(Qt.PenStyle.NoPen)
        if not enabled:
            painter.setBrush(QColor(_FAINT))
        elif self._live:
            painter.setBrush(QColor(_INK))
        else:
            painter.setBrush(QColor(_LIVE))
        if self._live:
            painter.drawRoundedRect(glyph.adjusted(1, 1, -1, -1), 2.5, 2.5)
        else:
            painter.drawEllipse(glyph)

        painter.setPen(QColor(_INK if enabled else _FAINT))
        painter.drawText(
            QRectF(
                left + self._DOT + self._GAP, pill.top(), text_width + 2, pill.height()
            ),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


class _Waveform(QWidget):
    """The last ten seconds of sound, one bar per captured block, newest on the right.

    Real levels rather than decoration: a flat line says at a glance that the
    lecture is not reaching the recording. It moves only as blocks arrive, so
    there is no timer to stop when nothing is recorded.
    """

    _BAR = 3.0
    _GAP = 2.0
    _HISTORY = 160
    """Blocks kept: 1024 frames each, 64 ms at 16 kHz, so about ten seconds."""

    def __init__(self) -> None:
        """Create a flat line."""
        super().__init__()
        self.setMinimumSize(120, 52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._levels: deque[float] = deque(maxlen=self._HISTORY)
        self._live = False

    def set_rms(self, rms: float) -> None:
        """Add the level of the block just recorded.

        Args:
            rms: Block level from 0.0 to 1.0.
        """
        self._levels.append(loudness(rms))
        self.update()

    def set_live(self, live: bool) -> None:
        """Draw in yellow while recording, in grey once it has stopped."""
        self._live = live
        self.update()

    def reset(self) -> None:
        """Forget the history, for a new recording."""
        self._levels.clear()
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw one rounded bar per block, centred on the middle line."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_ACCENT if self._live else _DASH))
        step = self._BAR + self._GAP
        count = int(self.width() // step)
        recent = list(self._levels)[-count:] if count else []
        # Unfilled history shows as the floor, so the newest block always
        # sits at the right edge.
        levels = [0.0] * (count - len(recent)) + recent
        height = float(self.height())
        for index, level in enumerate(levels):
            bar = max(3.0, level * (height - 4.0))
            painter.drawRoundedRect(
                QRectF(index * step, (height - bar) / 2, self._BAR, bar), 1.5, 1.5
            )


class _StripeBar(QWidget):
    """Recognition progress: a yellow fill whose diagonal stripes keep moving.

    The stripes show the work is alive between progress reports, which on a
    long segment can be seconds apart. Their timer runs only while the
    recording on the card is being decoded.
    """

    _HEIGHT = 8
    _PERIOD = 14.0

    def __init__(self) -> None:
        """Create an empty bar that is standing still."""
        super().__init__()
        self.setFixedHeight(self._HEIGHT)
        self._fraction = 0.0
        self._offset = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def set_fraction(self, fraction: float) -> None:
        """Fill the bar to a share of its width.

        Args:
            fraction: How full to draw it, from 0.0 to 1.0.
        """
        self._fraction = max(0.0, min(1.0, fraction))
        self.update()

    def set_running(self, running: bool) -> None:
        """Start or stop the stripes."""
        if running and not self._timer.isActive():
            self._timer.start()
        elif not running:
            self._timer.stop()

    def _advance(self) -> None:
        self._offset = (self._offset + 1.0) % self._PERIOD
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw the track, the fill, and the stripes clipped to the fill."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width = float(self.width())
        height = float(self.height())
        radius = height / 2
        painter.setBrush(QColor(_HOVER))
        painter.drawRoundedRect(QRectF(0.0, 0.0, width, height), radius, radius)
        if self._fraction <= 0.0:
            return
        # Never narrower than the bar is tall, or the rounded ends of a bar
        # that has barely started fold into a smudge.
        fill = QRectF(0.0, 0.0, max(width * self._fraction, height), height)
        painter.setBrush(QColor(_ACCENT))
        painter.drawRoundedRect(fill, radius, radius)
        clip = QPainterPath()
        clip.addRoundedRect(fill, radius, radius)
        painter.setClipPath(clip)
        stripe = QColor(_INK)
        stripe.setAlphaF(0.22)
        painter.setBrush(stripe)
        x = self._offset - self._PERIOD - height
        while x < fill.width():
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(x, height),
                        QPointF(x + height, 0.0),
                        QPointF(x + height + self._PERIOD / 2, 0.0),
                        QPointF(x + self._PERIOD / 2, height),
                    ]
                )
            )
            x += self._PERIOD


class _JobCard(QFrame):
    """One recording on the board: its name, its settings, how far it got."""

    opened = Signal()
    """Sent when the card's link is clicked."""

    def __init__(
        self,
        title: str,
        meta: str,
        *,
        right: str = "",
        progress: bool = False,
        action: str = "",
        problem: bool = False,
    ) -> None:
        """Lay a card out.

        Args:
            title: The lecture folder's name.
            meta: Model, language and length, on one line.
            right: A short state in the top right corner.
            progress: Whether to show a progress bar, for the card being decoded.
            action: Text of a link at the bottom; none when empty.
            problem: Whether the card reports a failure, which draws its edge
                in pink-red.
        """
        super().__init__()
        self.setObjectName("card")
        self.setProperty("live", progress)
        self.setProperty("problem", problem)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(title)
        name.setObjectName("cardtitle")
        name_font = name.font()
        name_font.setWeight(QFont.Weight.DemiBold)
        name.setFont(name_font)
        self._right = QLabel(right)
        self._right.setObjectName("sub")
        mono = QFont()
        mono.setFamilies(_MONO_FAMILIES)
        mono.setPointSizeF(9)
        self._right.setFont(mono)
        top.addWidget(name, 1)
        top.addWidget(self._right)
        column.addLayout(top)

        details = QLabel(meta)
        details.setObjectName("sub")
        details.setWordWrap(True)
        column.addWidget(details)

        self.bar: _StripeBar | None = None
        if progress:
            self.bar = _StripeBar()
            column.addWidget(self.bar)
        if action:
            link = QPushButton(action)
            link.setObjectName("link")
            link.setCursor(Qt.CursorShape.PointingHandCursor)
            link.clicked.connect(self.opened.emit)
            row = QHBoxLayout()
            row.addWidget(link)
            row.addStretch(1)
            column.addLayout(row)

    def set_right(self, text: str) -> None:
        """Change the state in the top right corner."""
        self._right.setText(text)

    def set_progress(self, fraction: float, text: str) -> None:
        """Advance the bar and say how far it got.

        Args:
            fraction: Share of the recording decoded, from 0.0 to 1.0.
            text: Percentage and time left, for the corner.
        """
        self._right.setText(text)
        if self.bar is not None:
            self.bar.set_fraction(fraction)
            self.bar.set_running(True)


class _Column(QFrame):
    """One stage of the queue: a heading with a count, then a card per recording.

    Cards are matched to their recordings by key, so a card that stays put is
    kept as it is, and one that arrives -- from the column before, or new --
    unfolds and fades in, which reads as it having moved across.
    """

    def __init__(self, title: str, colour: str, empty: str) -> None:
        """Lay out an empty column.

        Args:
            title: The stage's name.
            colour: Its dot, in the colour of what the stage means.
            empty: What the column says while it holds no cards.
        """
        super().__init__()
        self.setObjectName("column")
        self.setMinimumWidth(220)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 14, 14, 14)
        column.setSpacing(10)

        self._head = QHBoxLayout()
        self._head.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(9, 9)
        dot.setStyleSheet(f"background: {colour}; border-radius: 4px;")
        name = QLabel(title)
        name_font = name.font()
        name_font.setWeight(QFont.Weight.DemiBold)
        name_font.setPointSizeF(10.5)
        name.setFont(name_font)
        self._count = QLabel("0")
        self._count.setObjectName("badge")
        self._head.addWidget(dot)
        self._head.addWidget(name)
        self._head.addWidget(self._count)
        self._head.addStretch(1)
        column.addLayout(self._head)

        self.subtitle = QLabel("")
        self.subtitle.setObjectName("sub")
        self.subtitle.setWordWrap(True)
        self.subtitle.hide()
        column.addWidget(self.subtitle)

        self._empty = QLabel(empty)
        self._empty.setObjectName("empty")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self._empty)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self._list = QVBoxLayout(body)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(10)
        self._list.addStretch(1)
        scroll.setWidget(body)
        column.addWidget(scroll, 1)
        self._cards: dict[Hashable, _JobCard] = {}

    def add_action(self, widget: QWidget) -> None:
        """Put a button or a toggle at the right end of the heading."""
        self._head.addWidget(widget)

    def add_below_heading(self, widget: QWidget) -> None:
        """Put a widget between the heading and the cards, such as a search field."""
        layout = cast(QVBoxLayout, self.layout())
        # After the heading and the subtitle, before the empty note and cards.
        layout.insertWidget(2, widget)

    def set_empty_text(self, text: str) -> None:
        """Change what the column says while it holds no cards."""
        self._empty.setText(text)

    def set_subtitle(self, text: str) -> None:
        """Say what the stage is doing under its heading; hidden when empty."""
        self.subtitle.setText(text)
        self.subtitle.setVisible(bool(text))

    def card(self, key: Hashable) -> _JobCard | None:
        """The card shown for a key, if there is one."""
        return self._cards.get(key)

    def sync(self, items: Sequence[tuple[Hashable, Callable[[], _JobCard]]]) -> None:
        """Show exactly these cards, in this order.

        Args:
            items: A key per card and a function that builds the card, called
                only for a key the column does not already show.
        """
        keys = {key for key, _build in items}
        for key in [key for key in self._cards if key not in keys]:
            gone = self._cards.pop(key)
            self._list.removeWidget(gone)
            gone.deleteLater()
        for index, (key, build) in enumerate(items):
            card = self._cards.get(key)
            if card is None:
                card = build()
                self._cards[key] = card
                self._list.insertWidget(index, card)
                self._appear(card)
            elif self._list.indexOf(card) != index:
                self._list.removeWidget(card)
                self._list.insertWidget(index, card)
        self._count.setText(str(len(items)))
        self._empty.setVisible(not items)

    @staticmethod
    def _appear(card: _JobCard) -> None:
        """Unfold a new card from nothing while it fades in."""
        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(0.0)
        card.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", card)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        unfold = QPropertyAnimation(card, b"maximumHeight", card)
        unfold.setStartValue(0)
        unfold.setEndValue(card.sizeHint().height())
        group = QParallelAnimationGroup(card)
        for animation in (fade, unfold):
            animation.setDuration(320)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(animation)

        def settle() -> None:
            # Unlimited again, so the card can grow if its text wraps, and
            # without the effect, which would keep repainting it offscreen.
            card.setMaximumHeight(_UNLIMITED)
            # The stubs ask for an effect, but None is how Qt removes one.
            card.setGraphicsEffect(None)  # type: ignore[arg-type]

        group.finished.connect(settle)
        group.start()


class _Chip(QWidget):
    """A small pill in the header saying what the window is doing.

    While recording its dot is pink-red and breathes, so a live recording
    shows from across the room even with the clock too small to read.
    """

    _DOT = 7.0
    _PAD = 10.0
    _GAP = 7.0
    _BREATH = 1.6
    """Seconds for one full pulse of the dot."""

    def __init__(self) -> None:
        """Create a chip saying ``ready``."""
        super().__init__()
        self._text = "ready"
        self._live = False
        self._glow = 1.0
        font = self.font()
        font.setFamilies(_MONO_FAMILIES)
        font.setPointSize(9)
        self.setFont(font)
        self.setFixedHeight(self.fontMetrics().height() + 10)

    def show_state(self, text: str, *, live: bool = False) -> None:
        """Change what the chip says.

        Args:
            text: A word or two, lower case.
            live: Whether audio is being captured, which turns the dot pink-red.
        """
        self._text = text
        self._live = live
        self._glow = 1.0
        self.updateGeometry()
        self.update()

    def breathe(self, elapsed: float) -> None:
        """Advance the pulse of the dot to a point in the recording.

        Driven by the recording's own clock rather than a timer: progress
        arrives every block anyway, and the pulse stops if the capture does.

        Args:
            elapsed: Seconds recorded so far.
        """
        self._glow = 0.6 + 0.4 * math.cos(2 * math.pi * elapsed / self._BREATH)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        """Fit the text, the dot and the padding."""
        width = self.fontMetrics().horizontalAdvance(self._text)
        return QSize(
            round(2 * self._PAD + self._DOT + self._GAP + width), self.height()
        )

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw the pill, the dot and the text."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        radius = self.height() / 2
        painter.setBrush(QColor(_SURFACE))
        painter.drawRoundedRect(QRectF(self.rect()), radius, radius)

        dot = QColor(_LIVE if self._live else _FAINT)
        dot.setAlphaF(self._glow)
        painter.setBrush(dot)
        painter.drawEllipse(
            QRectF(self._PAD, (self.height() - self._DOT) / 2, self._DOT, self._DOT)
        )

        painter.setPen(QColor(_TEXT if self._live else _MUTED))
        text = QRectF(self.rect()).adjusted(self._PAD + self._DOT + self._GAP, 0, 0, 0)
        painter.drawText(text, Qt.AlignmentFlag.AlignVCenter, self._text)


class _Transcript(QTextEdit):
    """The reading pane: timecodes in a gutter, marks washed in orange.

    Drawn from the paragraphs :func:`read_transcript` finds in the text, so
    a transcript opened from disk shows the same as one just made. What is
    on disk is untouched; this is only how it is shown.
    """

    def __init__(self) -> None:
        """Create an empty pane."""
        super().__init__()
        self.setReadOnly(True)
        self.setPlaceholderText("not transcribed yet")
        font = QFont()
        font.setFamilies(_UI_FAMILIES)
        font.setPointSizeF(10.5)
        self.setFont(font)
        self.document().setDocumentMargin(2)
        self._source = ""
        self._marked: list[tuple[int, str]] = []

    def show_transcript(self, text: str) -> None:
        """Replace what is shown with a whole transcript.

        Args:
            text: The transcript as written to disk.
        """
        header, passages = read_transcript(text)
        parts = [] if not header else [_header_html(header)]
        parts.extend(_passage_html(passage, i) for i, passage in enumerate(passages))
        self.setHtml("".join(parts))
        self._source = text
        self._marked = [
            (i, passage.timecode)
            for i, passage in enumerate(passages)
            if passage.marked
        ]

    def marked(self) -> list[tuple[int, str]]:
        """The marked paragraphs shown, as their index and timecode."""
        return list(self._marked)

    def scroll_to(self, index: int) -> None:
        """Bring a paragraph to the top of the pane.

        Args:
            index: The paragraph's position among those of the transcript.
        """
        self.scrollToAnchor(f"p{index}")

    def clear(self) -> None:
        """Empty the pane and forget the text behind it."""
        super().clear()
        self._source = ""
        self._marked = []

    def createMimeDataFromSelection(self) -> QMimeData:  # noqa: N802 (Qt override)
        """Copy the text as it is on disk when everything is selected.

        The gutter is a table underneath, and a table copies as one cell per
        line. Select all then copy is how a transcript leaves the window, so
        that one case hands over the file's own text instead.
        """
        cursor = self.textCursor()
        whole = (
            cursor.selectionStart() == 0
            and cursor.selectionEnd() >= self.document().characterCount() - 1
        )
        if not whole or not self._source:
            return super().createMimeDataFromSelection()
        data = QMimeData()
        data.setText(self._source)
        return data


@dataclass(frozen=True, slots=True)
class _Done:
    """A recording that failed in this session, shown under Transcripts.

    Successes need no record of their own: the folder holds their
    transcript, and the column lists the folder.

    Attributes:
        key: Tells two failures of one recording apart on the board.
        wav: The recording.
        note: What went wrong, in a few words.
    """

    key: int
    wav: Path
    note: str


class _Window(QWidget):
    """The single window: options, the recorder, and the queue in three columns."""

    def __init__(self, config: Config) -> None:
        """Build the window and fill in the device list.

        Args:
            config: Effective configuration.
        """
        super().__init__()
        self.setObjectName("window")
        self._config = config
        self._devices: list[LoopbackDevice] = []
        # The device chosen by hand from the list, kept across refills of it.
        # None while the configured choice applies, as it does at startup.
        self._picked: LoopbackDevice | None = None
        self._recorder: _RecordWorker | None = None
        self._transcriber: _TranscribeWorker | None = None
        # Recordings queued after the worker found its queue empty and began
        # winding down; they get a worker of their own once it has finished.
        self._backlog: list[_Job] = []
        # The recording being decoded, None between recordings.
        self._current: Path | None = None
        # Every recording queued in this session, for its card.
        self._queued: dict[Path, _Job] = {}
        # Recordings that failed in this session; the rest of the Transcripts
        # column is read from the folder.
        self._finished: list[_Done] = []
        self._lectures: list[Lecture] = []
        # The transcript being read, for Open in editor.
        self._reading: Path | None = None
        self._next_key = 0
        # One request covers recording and recognition, which may overlap;
        # it is let go only once neither is running.
        self._awake: Inhibition | None = None
        self._recording = False
        self._transcribing = False
        self._stopping_recognition = False
        self._closing = False
        # The recording in view: the last one made, or one opened.
        self._wav: Path | None = None
        self._marks: list[float] = []
        self._elapsed = 0.0

        self._build()
        self._load_devices()
        self._load_languages()
        self._load_models()
        self._reload_library()
        self._refresh()

    def _build(self) -> None:
        """Lay the window out: options, the recorder, then the queue."""
        self.setWindowTitle("lecture scribe")
        self.setMinimumSize(760, 680)
        self.resize(1180, 800)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 20, 26, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Lecture Scribe")
        title.setObjectName("title")
        title_font = title.font()
        title_font.setPointSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        self._chip = _Chip()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._chip)
        layout.addLayout(header)

        layout.addWidget(self._build_options())
        layout.addWidget(self._build_recorder())

        self._status = QLabel("ready")
        self._status.setObjectName("status")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_board())
        self._pages.addWidget(self._build_reader())
        layout.addWidget(self._pages, 1)

        mark = QShortcut(QKeySequence("Ctrl+M"), self)
        mark.activated.connect(self._mark_moment)
        open_file = QShortcut(QKeySequence("Ctrl+O"), self)
        open_file.activated.connect(self._open_recording)

    def _build_options(self) -> QFrame:
        """The bar of settings: where to record from, and how to recognise it."""
        bar = QFrame()
        bar.setObjectName("bar")
        rows = QVBoxLayout(bar)
        rows.setContentsMargins(14, 10, 14, 10)
        rows.setSpacing(8)

        first = QHBoxLayout()
        first.setSpacing(8)
        self._course = QLineEdit()
        self._course.setPlaceholderText("for example os")
        self._course.setToolTip("names the folder the recording goes into")
        self._course.setFixedWidth(160)
        self._course.textChanged.connect(self._refresh)
        self._device_box = _Combo()
        # Without this the longest device name sets the window's minimum width.
        self._device_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._device_box.setMinimumContentsLength(14)
        # `activated` rather than `currentIndexChanged`: only a choice made by
        # hand counts, not the list being refilled underneath.
        self._device_box.activated.connect(self._on_device_picked)
        self._device_box.opening.connect(self._on_device_list_opening)
        self._language_box = _Pills()
        self._language_box.setToolTip(
            "the language the lecture is spoken in; fixed for a recording when "
            "it is queued"
        )
        first.addWidget(_caption("Course"))
        first.addWidget(self._course)
        first.addSpacing(10)
        first.addWidget(_caption("Device"))
        first.addWidget(self._device_box, 1)
        first.addSpacing(10)
        first.addWidget(_caption("Language"))
        first.addWidget(self._language_box)
        rows.addLayout(first)

        second = QHBoxLayout()
        second.setSpacing(8)
        self._model_box = _Pills()
        self._model_box.changed.connect(self._on_model_picked)
        self._model_note = QLabel("")
        self._model_note.setObjectName("sub")
        self._model_note.setWordWrap(True)
        second.addWidget(_caption("Model"))
        second.addWidget(self._model_box)
        second.addSpacing(6)
        second.addWidget(self._model_note, 1)
        rows.addLayout(second)
        return bar

    def _build_recorder(self) -> QFrame:
        """The recording strip: the button, the clock, the sound, the mark."""
        strip = QFrame()
        strip.setObjectName("strip")
        row = QHBoxLayout(strip)
        row.setContentsMargins(10, 8, 20, 8)
        row.setSpacing(18)

        self._record = _RecordButton()
        self._record.clicked.connect(self._toggle_record)
        row.addWidget(self._record)

        clock_column = QVBoxLayout()
        clock_column.setSpacing(2)
        self._clock = QLabel(format_duration(0.0))
        self._clock.setObjectName("clock")
        clock_font = QFont()
        clock_font.setFamilies(_MONO_FAMILIES)
        clock_font.setPointSize(26)
        self._clock.setFont(clock_font)
        self._levels = QLabel("not recording")
        self._levels.setObjectName("sub")
        clock_column.addWidget(self._clock)
        clock_column.addWidget(self._levels)
        row.addLayout(clock_column)

        self._wave = _Waveform()
        row.addWidget(self._wave, 1)

        self._mark = QPushButton("Mark")
        self._mark.setObjectName("mark")
        self._mark.setIcon(_glyph("dot", _MARK))
        self._mark.setIconSize(QSize(_GLYPH + _GLYPH_GAP, _GLYPH))
        self._mark.setToolTip("flag this moment in the transcript (Ctrl+M)")
        self._mark.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mark.clicked.connect(self._mark_moment)
        row.addWidget(self._mark)
        return strip

    def _build_board(self) -> QWidget:
        """The queue: waiting, being transcribed, and every lecture in the folder."""
        board = QWidget()
        self._board = QBoxLayout(QBoxLayout.Direction.LeftToRight, board)
        self._board.setContentsMargins(0, 0, 0, 0)
        self._board.setSpacing(14)

        self._waiting_col = _Column(
            "Waiting", _FAINT, "A recording lands here when you press stop."
        )
        self._working_col = _Column("Transcribing", _ACCENT, "The GPU is free.")
        self._done_col = _Column(
            "Transcripts", _DONE, "No recordings in the lecture folder yet."
        )
        self._queue_line = self._working_col.subtitle

        self._auto = QCheckBox("Transcribe after stop")
        self._auto.setChecked(True)
        self._auto.setToolTip(
            "queue a recording the moment it stops; turned off, it waits under "
            "Transcripts with a Transcribe button"
        )
        self._waiting_col.add_action(self._auto)

        self._stop_text = QPushButton("Stop all")
        self._stop_text.setToolTip(
            "stop recognition and clear the queue; the recordings stay on disk"
        )
        self._stop_text.clicked.connect(self._stop_transcription)
        self._working_col.add_action(self._stop_text)

        self._open = QPushButton("Open file…")
        self._open.setObjectName("link")
        self._open.setToolTip("transcribe a recording from outside the folder (Ctrl+O)")
        self._open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._open.clicked.connect(self._open_recording)
        self._done_col.add_action(self._open)

        self._search = QLineEdit()
        self._search.setPlaceholderText("search names and text")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._sync_board)
        self._done_col.add_below_heading(self._search)

        for column in (self._waiting_col, self._working_col, self._done_col):
            self._board.addWidget(column, 1)
        return board

    def _build_reader(self) -> QWidget:
        """The reading view, shown in place of the queue."""
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        back = QPushButton("← Back to queue")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._show_board)
        self._reader_title = QLabel("")
        reader_font = self._reader_title.font()
        reader_font.setWeight(QFont.Weight.DemiBold)
        self._reader_title.setFont(reader_font)
        self._editor = QPushButton("Open in editor")
        self._editor.setToolTip("open the .txt in the program the system uses for text")
        self._editor.setCursor(Qt.CursorShape.PointingHandCursor)
        self._editor.clicked.connect(self._open_in_editor)
        self._text = QPushButton("Transcribe")
        self._text.setObjectName("accent")
        self._text.setToolTip("queue this recording for transcription")
        self._text.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text.clicked.connect(self._start_transcription)
        top.addWidget(back)
        top.addWidget(self._reader_title, 1)
        top.addWidget(self._editor)
        top.addWidget(self._text)
        column.addLayout(top)

        self._chips = QWidget()
        self._chip_row = QHBoxLayout(self._chips)
        self._chip_row.setContentsMargins(0, 0, 0, 0)
        self._chip_row.setSpacing(6)
        column.addWidget(self._chips)
        self._chips.hide()

        self._transcript = _Transcript()
        column.addWidget(self._transcript, 1)
        return page

    def event(self, event: QEvent) -> bool:
        """Read the folder again whenever the window comes back to the front.

        A lecture folder renamed or a transcript made by `scribe text` while
        the window sat behind others would otherwise show as it was.
        """
        if event.type() == QEvent.Type.WindowActivate:
            self._reload_library()
        return super().event(event)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 (Qt override)
        """Stack the queue's columns when the window is too narrow for three."""
        super().resizeEvent(event)
        narrow = event.size().width() < _STACK_BELOW
        self._board.setDirection(
            QBoxLayout.Direction.TopToBottom
            if narrow
            else QBoxLayout.Direction.LeftToRight
        )

    def _load_devices(self, *, starting: bool = False) -> LoopbackDevice | None:
        """Read the output devices afresh and select the one to record from.

        Read again before every recording rather than once at startup:
        plugging in headphones renames or replaces the system's output, and
        a list from when the window opened would record the old one. A
        device picked by hand stays picked for as long as it exists;
        otherwise the configured choice applies, ``auto`` following the
        system default to wherever it has moved.

        Args:
            starting: Whether record was just pressed, which is the only
                time a vanished device needs a second press to be explained.

        Returns:
            The selected device, or ``None`` when there is none to record
            from, with the reason already on the status line. A device picked
            by hand that has since gone also gives ``None``, so that a
            recording never starts on a device other than the one shown when
            record was pressed.
        """
        self._device_box.clear()
        try:
            devices = list_loopback_devices()
        except AudioDeviceError as error:
            self._devices = []
            self._say(str(error), problem=True)
            return None

        self._devices = devices
        for device in devices:
            suffix = "  (system default)" if device.is_default else ""
            self._device_box.addItem(f"{device.name}{suffix}")

        picked = self._picked
        if picked is not None:
            for index, device in enumerate(devices):
                if device.id == picked.id:
                    self._device_box.setCurrentIndex(index)
                    return device
            self._picked = None

        try:
            selected = resolve_device(devices, self._config.audio.device)
        except AudioDeviceError as error:
            self._say(str(error), problem=True)
            return None
        self._device_box.setCurrentIndex(devices.index(selected))
        if picked is not None:
            advice = "; press record again to use it" if starting else ""
            self._say(
                f"'{picked.name}' is gone, so the list is back on "
                f"'{selected.name}'{advice}",
                problem=True,
            )
            return None
        return selected

    def _load_languages(self) -> None:
        """Offer the languages and select the configured one.

        A code from ``config.toml`` that the list does not offer is added
        under its own name rather than dropped, so the window never quietly
        transcribes in a language other than the one configured.
        """
        configured = self._config.asr.language
        for code, name in _LANGUAGES:
            self._language_box.add(name, code)
        if all(code != configured for code, _name in _LANGUAGES):
            self._language_box.add(configured, configured)
        self._language_box.select(configured)

    def _load_models(self) -> None:
        """Offer the models of ``[models]`` and select ``asr.model``.

        The chosen model's note shows beside the list, which is the point of
        it: which model suits what. A configured model that is not in
        ``[models]`` is added under its own name, as a language code is.
        """
        configured = self._config.asr.model
        labels = [choice.label for choice in self._config.models]
        if configured not in labels:
            self._model_box.add(configured, configured, tooltip=configured)
        for choice in self._config.models:
            self._model_box.add(
                choice.label, choice.label, tooltip=choice.note or choice.model
            )
        self._model_box.select(configured)
        self._on_model_picked(configured)

    def _on_model_picked(self, label: str) -> None:
        """Show the note of the model just chosen."""
        note = next(
            (choice.note for choice in self._config.models if choice.label == label),
            "",
        )
        self._model_note.setText(note)

    def _recognition_config(self) -> Config:
        """The configuration a transcription runs with, language and model as chosen.

        Returns:
            The configuration from ``config.toml``, with the language and
            model choices applied the way ``--language`` and ``--model``
            apply them on the command line. Nothing is written back: the
            next start begins from the file again.
        """
        return merge_cli(
            self._config,
            language=self._language_box.current_data(),
            model=self._model_box.current_data(),
        )

    def _on_device_picked(self, index: int) -> None:
        """Remember a device chosen by hand, so a refill of the list keeps it."""
        if 0 <= index < len(self._devices):
            self._picked = self._devices[index]

    def _on_device_list_opening(self) -> None:
        """Bring the list up to date before it drops down."""
        self._load_devices()
        self._refresh()

    def _toggle_record(self) -> None:
        """Start recording, or stop the recording that is running."""
        if self._recording and self._recorder is not None:
            self._say("stopping ...")
            self._chip.show_state("stopping")
            self._record.setEnabled(False)
            self._recorder.stop()
            return
        self._start_recording()

    def _start_recording(self) -> None:
        """Create the lecture folder and hand the capture to a worker.

        Recognition of earlier recordings may carry on meanwhile: capture
        barely touches the processor and decoding runs on the GPU.
        """
        if self._recorder is not None:
            return
        device = self._load_devices(starting=True)
        if device is None:
            self._refresh()
            return
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

        self._wav = recording_path(folder)
        self._marks = []
        self._elapsed = 0.0
        self._wave.reset()
        self._wave.set_live(True)
        self._clock.setText(format_duration(0.0))

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
        awake = self._hold_awake("recording a lecture")
        self._refresh()
        note = "" if awake.held else f" · the screen may lock: {awake.detail}"
        self._say(f"recording to {_short_path(self._wav)}{note}", path=self._wav)
        worker.start()

    def _on_progress(self, progress: Progress) -> None:
        """Redraw the clock, the waveform and the level readout."""
        self._elapsed = progress.elapsed
        self._clock.setText(format_duration(progress.elapsed))
        self._chip.breathe(progress.elapsed)
        self._wave.set_rms(progress.rms)
        folder = "" if self._wav is None else f"{self._wav.parent.name}   ·   "
        self._levels.setText(
            f"{folder}{dbfs(progress.rms)}   ·   "
            f"{progress.bytes_written / 1_000_000:.1f} MB"
        )

    def _on_warning(self, message: str) -> None:
        """Show a warning raised while recording; the recording continues."""
        self._say(message, problem=True)

    def _on_recorded(self, recording: Recording) -> None:
        """Report the finished file and queue it for recognition.

        Queued at once, so the next lecture can be recorded while this one is
        recognised. A silent recording is not: it would cost minutes of
        decoding to produce an empty transcript.
        """
        self._recording = False
        self._wave.set_live(False)
        self._clock.setText(format_duration(recording.duration))
        self._levels.setText(
            f"peak {dbfs(recording.peak)}   ·   "
            f"{recording.path.stat().st_size / 1_000_000:.1f} MB"
        )
        if recording.duration == 0.0:
            # Stopped before the first block arrived. The empty file stays on
            # disk like any recording, but there is nothing in it to recognise.
            self._wav = None
            self._say(
                "stopped before any audio arrived, so there is nothing to "
                f"transcribe · {_short_path(recording.path)}",
                path=recording.path,
            )
        elif recording.mean_rms < self._config.audio.silence_rms:
            self._say(
                "the recording is silent from end to end, so it was not queued "
                "for transcription. The audio was kept anyway; check that the "
                "lecture was playing through the selected device.",
                problem=True,
            )
        else:
            # What was captured before an interruption is valid audio, so it
            # is queued like a recording that was stopped by hand.
            if self._auto.isChecked():
                queued = self._enqueue(
                    recording.path, self._marks, seconds=recording.duration
                )
                outcome = " · queued for transcription" if queued else ""
            else:
                outcome = " · kept for later: Transcribe after stop is off"
            if recording.interrupted is not None:
                self._say(f"{recording.interrupted}{outcome}", problem=True)
            else:
                self._say(
                    f"stopped after {format_duration(recording.duration)} · "
                    f"{_short_path(recording.path)}{outcome}",
                    path=recording.path,
                )
        self._reload_library()
        self._refresh()

    def _on_record_failed(self, message: str) -> None:
        """Report a recording that could not start at all.

        A recording that fails part way returns rather than raises, so this
        only ever follows one that captured nothing. Its file holds no audio,
        so it is no longer offered for transcription.
        """
        self._recording = False
        if self._wav is not None:
            # Only an empty folder goes: one holding even a header-only WAV
            # stays, since audio is never deleted automatically.
            remove_if_empty(self._wav.parent)
        self._wav = None
        self._wave.set_live(False)
        self._wave.reset()
        self._clock.setText(format_duration(0.0))
        self._say(message, problem=True)
        self._refresh()

    def _release_recorder(self) -> None:
        """Drop the finished worker, never leaving the window mid-recording.

        A worker's own signals are skipped entirely if its thread dies on an
        exception, while `finished` arrives either way. Clearing the state
        here rather than only in the slots means an unreported death leaves
        the window honest instead of frozen with a stop button and a dead
        clock -- where the next press would quietly start a second recording.
        """
        if self._recorder is not None:
            self._recorder.wait()
            self._recorder = None
        if self._transcriber is None:
            self._let_sleep()
        if self._recording:
            self._recording = False
            self._wave.set_live(False)
            kept = (
                f"; whatever was captured is at {_short_path(self._wav)}"
                if self._wav
                else ""
            )
            self._say(
                f"the recording ended without saying why{kept}",
                problem=True,
                path=self._wav,
            )
        self._refresh()

    def _mark_moment(self) -> None:
        """Flag the current offset so its paragraph is prefixed with ``>>>``."""
        if not self._recording or self._wav is None:
            return
        self._marks.append(self._elapsed)
        message = (
            f"marked {format_timecode(self._elapsed)} "
            f"({len(self._marks)} in this lecture)"
        )
        self._refresh()
        try:
            append_mark(self._wav, self._elapsed)
        except OSError as error:
            # Kept in memory regardless, so this window still uses it.
            self._say(
                f"{message}, but it could not be saved beside the recording: {error}",
                problem=True,
            )
            return
        self._say(message)

    def _open_recording(self) -> None:
        """Pick a recording made earlier and show it, ready to transcribe.

        Lectures reach this folder without `scribe rec` having made them --
        copied in, renamed, recorded on another machine -- and after a
        restart the window has no recording of its own to work on either.
        Not offered during a recording, whose marks still belong to the
        lecture being captured.
        """
        if self._recorder is not None:
            return

        chosen, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open a recording",
            str(self._config.output.dir),
            "Recordings (*.wav);;All files (*)",
        )
        if chosen:
            self._view_recording(Path(chosen))

    def _view_recording(self, path: Path) -> None:
        """Make a recording the one in view and show its transcript, if any.

        Args:
            path: The recording, picked from a dialog or from a card.
        """
        try:
            existing = inspect_recording(path)
        except AudioDeviceError as error:
            self._say(str(error), problem=True)
            return

        # Read before anything changes, so a damaged marks file leaves the
        # recording that was in view as it was.
        try:
            marks = read_marks(path)
        except (ValueError, OSError) as error:
            self._say(str(error), problem=True)
            return

        self._wav = path
        # The marks of this recording, from the file made as they were
        # pressed; an empty list for a recording with none. Carrying the last
        # lecture's over would put >>> on unrelated sentences.
        self._marks = marks
        self._elapsed = 0.0
        self._reader_title.setText(
            f"{path.parent.name}   ·   {format_duration(existing.duration)}"
        )
        self._show_earlier_transcript(existing, marks=len(marks))
        self._show_reader(earlier_transcript(path))

    def _show_earlier_transcript(
        self, existing: ExistingRecording, *, marks: int
    ) -> None:
        """Show the transcript beside a recording, when one was made already.

        Args:
            existing: The recording that was just opened.
            marks: How many marks were read for it, named on the status line.
        """
        counted = f" · {_plural(marks, 'mark')}" if marks else ""
        transcript = earlier_transcript(existing.path)
        if transcript is None:
            self._transcript.clear()
            self._say(
                f"{_short_path(existing.path)} · not transcribed yet{counted}",
                path=existing.path,
            )
            return

        try:
            text = transcript.read_text(encoding="utf-8")
        except OSError as error:
            self._transcript.clear()
            self._say(f"cannot read {transcript}: {error}", problem=True)
            return

        self._transcript.show_transcript(text)
        target = transcript_path(existing.path)
        # A transcript under an older name is left where it is, so saying it
        # gets replaced would be untrue: the new one lands beside it.
        outcome = (
            "replaces it" if transcript == target else f"writes {target.name} beside it"
        )
        self._say(
            f"{_short_path(existing.path)} · showing the transcript made earlier, "
            f"transcribing again {outcome}{counted}",
            path=existing.path,
        )

    def _read_only(self, lecture: Lecture) -> None:
        """Show a transcript without making its recording the one in view.

        For a card clicked during a recording: the recording in view is the
        one being captured, and its marks must stay where they are.
        """
        if lecture.transcript is None:
            return
        try:
            text = lecture.transcript.read_text(encoding="utf-8")
        except OSError as error:
            self._say(f"cannot read {lecture.transcript}: {error}", problem=True)
            return
        self._reader_title.setText(lecture.name)
        self._transcript.show_transcript(text)
        self._show_reader(lecture.transcript)

    def _show_reader(self, transcript: Path | None) -> None:
        """Switch to the reading view, with a jump link for every mark in it.

        Args:
            transcript: The file shown, for Open in editor; ``None`` when the
                recording has none yet.
        """
        self._reading = transcript
        while (item := self._chip_row.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        marked = self._transcript.marked()
        for index, timecode in marked:
            chip = QPushButton(timecode)
            chip.setObjectName("chip")
            chip.setToolTip("jump to this mark")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(partial(self._transcript.scroll_to, index))
            self._chip_row.addWidget(chip)
        self._chip_row.addStretch(1)
        self._chips.setVisible(bool(marked))
        self._pages.setCurrentIndex(_READER)
        self._refresh()

    def _open_in_editor(self) -> None:
        """Hand the transcript to the system's own program for text files."""
        if self._reading is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._reading))):
            self._say(
                f"no program opened {_short_path(self._reading)}; it is a plain "
                "UTF-8 text file",
                problem=True,
                path=self._reading,
            )

    def _open_lecture(self, hit: Hit) -> None:
        """Read a lecture from the list, at the paragraph the search found."""
        if self._recorder is not None:
            self._read_only(hit.lecture)
        else:
            self._view_recording(hit.lecture.wav)
        if hit.passage is not None:
            self._transcript.scroll_to(hit.passage)

    def _transcribe_wav(self, wav: Path, seconds: float | None) -> None:
        """Queue a recording from the list, with the marks saved beside it.

        Allowed during a recording too: its marks come from its own file,
        never from the lecture being captured.
        """
        try:
            marks = read_marks(wav)
        except (ValueError, OSError) as error:
            self._say(str(error), problem=True)
            return
        if self._enqueue(wav, marks, seconds=seconds or 0.0):
            self._say(f"{_short_path(wav)} · queued for transcription", path=wav)
        self._refresh()

    def _show_board(self) -> None:
        """Go back from reading to the queue."""
        self._pages.setCurrentIndex(_BOARD)
        self._reload_library()

    def _start_transcription(self) -> None:
        """Queue the recording in view, behind any already being recognised."""
        if self._wav is None or self._recorder is not None:
            return
        try:
            seconds = inspect_recording(self._wav).duration
        except AudioDeviceError as error:
            self._say(str(error), problem=True)
            return
        if self._enqueue(self._wav, self._marks, seconds=seconds):
            self._say(f"{_short_path(self._wav)} · queued for transcription")
            self._show_board()
        self._refresh()

    def _enqueue(self, wav: Path, marks: Sequence[float], *, seconds: float) -> bool:
        """Put a recording in line for recognition, starting a worker if none runs.

        A running worker is never replaced: a QThread destroyed while it
        still runs aborts the process, and the stale ``finished`` would
        release the next job's keep-awake. A worker past its last recording
        but not yet finished -- freeing a GPU model takes a moment -- refuses
        new work, which then waits in the backlog for a worker of its own.

        Args:
            wav: Recording to transcribe.
            marks: Offsets in seconds flagged during the lecture.
            seconds: How long the recording is, for its card.

        Returns:
            Whether it was queued; ``False`` when it already was, or the
            window is closing.
        """
        if self._closing:
            return False
        if self._is_queued(wav):
            self._say(f"{_short_path(wav)} is already queued for transcription")
            return False
        chosen = self._recognition_config().asr
        job = _Job(
            wav=wav,
            marks=tuple(marks),
            language=chosen.language,
            model=chosen.model,
            seconds=seconds,
        )
        self._queued[wav] = job
        if self._transcriber is None:
            self._start_worker([job])
        elif not self._transcriber.jobs.put(job):
            self._backlog.append(job)
        self._sync_board()
        return True

    def _start_worker(self, jobs: list[_Job]) -> None:
        """Hand a list of recordings to a new transcription worker."""
        worker = _TranscribeWorker(self._config, _JobQueue(jobs))
        worker.staged.connect(self._on_staged)
        worker.began.connect(self._on_began)
        worker.progressed.connect(self._on_decoding)
        worker.transcribed.connect(self._on_transcribed)
        worker.stopped.connect(self._on_transcribe_stopped)
        worker.failed.connect(self._on_transcribe_failed)
        worker.finished.connect(self._release_transcriber)

        self._transcriber = worker
        self._transcribing = True
        self._stopping_recognition = False
        self._hold_awake("transcribing a lecture")
        self._refresh()
        worker.start()

    def _is_queued(self, wav: Path) -> bool:
        """Whether a recording is being recognised or waiting to be."""
        return wav == self._current or wav in self._waiting()

    def _waiting(self) -> list[Path]:
        """The recordings queued but not yet started, in order."""
        return [job.wav for job in self._waiting_jobs()]

    def _waiting_jobs(self) -> list[_Job]:
        """The jobs queued but not yet started, in order."""
        queued = [] if self._transcriber is None else self._transcriber.jobs.jobs()
        return queued + list(self._backlog)

    def _reload_library(self) -> None:
        """Read the lecture folder again and redraw the board."""
        self._lectures = list_lectures(self._config.output.dir)
        self._sync_board()

    def _sync_board(self) -> None:
        """Bring the three columns into line with the queue and the folder."""
        self._waiting_col.sync(
            [
                (job.wav, partial(self._job_card, job, working=False))
                for job in self._waiting_jobs()
            ]
        )
        working: list[tuple[Hashable, Callable[[], _JobCard]]] = []
        current = self._current
        job = None if current is None else self._queued.get(current)
        if current is not None and job is not None:
            working.append((current, partial(self._job_card, job, working=True)))
        self._working_col.sync(working)

        done: list[tuple[Hashable, Callable[[], _JobCard]]] = [
            (("failed", item.key), partial(self._done_card, item))
            for item in self._finished
        ]
        # The lecture being captured is not offered: its file is still growing.
        shown = [
            lecture
            for lecture in self._lectures
            if not (self._recording and lecture.wav == self._wav)
        ]
        query = self._search.text()
        for hit in search(shown, query):
            queued = self._is_queued(hit.lecture.wav)
            key = (hit.lecture, queued, hit.passage, query.strip() != "")
            done.append((key, partial(self._lecture_card, hit, queued)))
        self._done_col.sync(done)
        self._done_col.set_empty_text(
            f"Nothing matches '{query.strip()}'."
            if query.strip()
            else "No recordings in the lecture folder yet."
        )

    def _job_card(self, job: _Job, *, working: bool) -> _JobCard:
        """Build the card of a recording that is waiting or being decoded."""
        language = dict(_LANGUAGES).get(job.language, job.language)
        return _JobCard(
            job.wav.parent.name,
            f"{job.model}   ·   {language}   ·   {round(job.seconds / 60)} min",
            right="starting" if working else "next",
            progress=working,
        )

    def _done_card(self, done: _Done) -> _JobCard:
        """Build the card of a recording that failed in this session."""
        card = _JobCard(
            done.wav.parent.name, done.note, action="Try again", problem=True
        )
        card.opened.connect(partial(self._transcribe_wav, done.wav, None))
        return card

    def _lecture_card(self, hit: Hit, queued: bool) -> _JobCard:
        """Build the card of a lecture in the folder, transcribed or not."""
        lecture = hit.lecture
        parts = [
            "length unknown"
            if lecture.seconds is None
            else f"{round(lecture.seconds / 60)} min",
            lecture.model or "transcribed"
            if lecture.transcript is not None
            else "not transcribed",
        ]
        if lecture.marks:
            parts.append(_plural(lecture.marks, "mark"))
        meta = "   ·   ".join(parts)
        if hit.timecode is not None:
            meta += f"\n{hit.timecode}   {hit.excerpt}"
        if queued:
            return _JobCard(lecture.name, meta, right="queued")
        if lecture.transcript is None:
            card = _JobCard(lecture.name, meta, action="Transcribe")
            card.opened.connect(
                partial(self._transcribe_wav, lecture.wav, lecture.seconds)
            )
            return card
        card = _JobCard(lecture.name, meta, action="Open transcript")
        card.opened.connect(partial(self._open_lecture, hit))
        return card

    def _stop_transcription(self) -> None:
        """Give up the recording being recognised and every one still waiting."""
        if self._transcriber is None:
            return
        dropped = len(self._waiting())
        self._backlog.clear()
        self._stopping_recognition = True
        self._transcriber.stop()
        left = f", {_plural(dropped, 'queued recording')} dropped" if dropped else ""
        self._working_col.set_subtitle(f"stopping recognition{left} ...")
        self._sync_board()
        self._refresh()

    def _on_staged(self, message: str) -> None:
        """Show what the worker is doing before decoding has begun."""
        if not self._stopping_recognition:
            self._working_col.set_subtitle(message)

    def _on_began(self, wav: Path) -> None:
        """Move the recording now being recognised to the middle column."""
        self._current = wav
        self._sync_board()
        self._refresh()

    def _on_decoding(self, decoding: Decoding) -> None:
        """Advance the card being decoded and the chip.

        No text is shown while it runs: the transcript is read once it is
        written, so the card carries only how far it got.
        """
        if self._stopping_recognition or self._current is None:
            return
        self._working_col.set_subtitle("")
        card = self._working_col.card(self._current)
        if card is not None:
            card.set_progress(
                decoding.fraction,
                f"{decoding.fraction * 100:.0f}% · "
                f"{format_duration(decoding.remaining)} left",
            )
        if not self._recording:
            self._chip.show_state(f"transcribing {decoding.fraction * 100:.0f}%")

    def _on_transcribed(
        self, wav: Path, path: Path, text: str, result: Transcription
    ) -> None:
        """Move the card to Transcripts and say how long decoding took.

        The outcome goes on the status line even during a recording: the
        clock and the chip already say that one is running, while a finished
        transcript would otherwise go unseen.
        """
        self._current = None
        took = (
            f"took {format_duration(result.decode_seconds)} "
            f"({result.decode_ratio:.2f}x audio length)   ·   "
            f"{_plural(len(result.segments), 'segment')}"
        )
        self._reload_library()
        if self._pages.currentIndex() == _READER and wav == self._wav:
            self._transcript.show_transcript(text)
            self._show_reader(path)
        self._say(f"wrote {_short_path(path)} · {took}", path=path)
        self._refresh()

    def _on_transcribe_stopped(self, message: str) -> None:
        """Report recognition the user gave up on, which is not a failure.

        Args:
            message: What was recognised before it stopped, and what that
                leaves behind.
        """
        self._finish_one(message, problem=False)

    def _on_transcribe_failed(self, message: str) -> None:
        """Report a recording that could not be recognised; the queue goes on."""
        self._finish_one(message, problem=True)

    def _finish_one(self, message: str, *, problem: bool) -> None:
        """Clear the recording that ended without a transcript, and say why."""
        if problem and self._current is not None:
            self._finish(self._current, "failed: see the status line")
        self._current = None
        self._sync_board()
        self._say(message, problem=problem)
        self._refresh()

    def _finish(self, wav: Path, note: str) -> None:
        """Put a recording that failed at the top of the Transcripts column."""
        self._finished.insert(0, _Done(self._next_key, wav, note))
        self._next_key += 1
        self._sync_board()

    def _release_transcriber(self) -> None:
        """Drop the finished worker, and start one for anything queued since.

        The same reasoning as :meth:`_release_recorder`: a thread that dies
        without reporting would otherwise leave the window showing a
        recognition that is no longer running.
        """
        if self._transcriber is not None:
            self._transcriber.wait()
            self._transcriber = None
        if self._current is not None:
            self._say(
                f"recognition of {self._current.parent.name} ended without "
                "saying why; the recording itself is untouched and can be "
                "transcribed again",
                problem=True,
            )
            self._current = None
        self._transcribing = False
        self._stopping_recognition = False
        self._working_col.set_subtitle("")
        if self._closing:
            self._backlog.clear()
        if self._backlog:
            jobs, self._backlog = self._backlog, []
            self._start_worker(jobs)
        elif self._recorder is None:
            self._let_sleep()
        self._sync_board()
        self._refresh()
        if self._closing:
            # The window is already hidden and the worker is now finished, so
            # there is no thread left to be destroyed under a running loop.
            QApplication.quit()

    def _hold_awake(self, reason: str) -> Inhibition:
        """Keep the desktop from locking or sleeping, once for all the work.

        Args:
            reason: Shown by the desktop; the first job to ask names it.

        Returns:
            The request in force, new or already held.
        """
        if self._awake is None:
            self._awake = keep_awake(reason)
        return self._awake

    def _let_sleep(self) -> None:
        """Hand locking and sleeping back to the desktop once the work is done.

        Called from the ``finished`` slots rather than the result slots, for
        the same reason the worker state is cleared there: a thread that dies
        without reporting would otherwise keep the machine awake for good.
        The caller checks that the other worker is not still running.
        """
        if self._awake is not None:
            self._awake.release()
            self._awake = None

    def _refresh(self) -> None:
        """Bring every control into line with what the window is doing."""
        free = self._recorder is None
        named = bool(self._course.text().strip())
        self._record.set_live(self._recording)
        self._record.setEnabled(
            self._recording or (free and named and bool(self._devices))
        )
        self._mark.setEnabled(self._recording)
        self._mark.setText(f"Mark · {len(self._marks)}" if self._recording else "Mark")
        self._open.setEnabled(free)
        self._editor.setEnabled(self._reading is not None)
        self._text.setEnabled(
            free
            and not self._closing
            and self._wav is not None
            and not self._is_queued(self._wav)
        )
        running = self._transcriber is not None
        self._stop_text.setVisible(running)
        self._stop_text.setEnabled(running and not self._stopping_recognition)
        self._course.setEnabled(free)
        self._device_box.setEnabled(free)
        self._language_box.setEnabled(free)
        self._model_box.setEnabled(free)
        if self._recording:
            self._chip.show_state("recording", live=True)
        elif self._transcribing:
            self._chip.show_state("transcribing")
        else:
            self._chip.show_state("ready")
            self._levels.setText(self._levels.text() or "not recording")

    def _say(
        self, message: str, *, problem: bool = False, path: Path | None = None
    ) -> None:
        """Put one line in the status row.

        Args:
            message: Text to show.
            problem: Whether to draw it as something needing attention.
            path: The file the line is about, shown in full on hover. The
                line itself names it by folder and file only, since the home
                directory in front of every path wrapped it onto two lines.
        """
        self._status.setToolTip("" if path is None else str(path))
        self._status.setProperty("problem", problem)
        _restyle(self._status)
        self._status.setText(message)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """End what is running before the window goes away.

        Recognition is asked to give up rather than waited for on this
        thread. A stop lands at the end of the segment being decoded, and
        before the first segment exists the decoder is scanning the file for
        speech -- 21 seconds on a measured 81 minute lecture. Holding the
        window on screen for that long would be indistinguishable from a
        hang, so it disappears at once and the process leaves once the worker
        has really finished. Waiting for that matters: a QThread destroyed
        while it is still running takes the whole process down with it.
        """
        if self._recording:
            # Asked with No as the default: the rest of a lecture cannot be
            # recorded again, so a stray Enter must not be what ends it.
            answer = QMessageBox.question(
                self,
                "lecture scribe",
                "A lecture is being recorded.\n"
                "Closing stops the recording; everything captured so far is "
                "kept on disk, but the rest of the lecture is not recorded. "
                "Close anyway?",
                defaultButton=QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self._transcribing:
            waiting = len(self._waiting())
            queued = (
                f" {_plural(waiting, 'more recording')} waiting will not be "
                "started either."
                if waiting
                else ""
            )
            answer = QMessageBox.question(
                self,
                "lecture scribe",
                "Recognition is still running.\n"
                "Stopping it throws away what has been recognised so far."
                f"{queued} The recordings themselves are safe on disk, and "
                "`scribe text` on their folder transcribes them later. "
                "Close anyway?",
                # No here too, so that Enter means the same in both prompts.
                defaultButton=QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        # From here on nothing new is queued: a recording stopped below still
        # reports itself, and would otherwise start a worker on the way out.
        self._closing = True
        self._backlog.clear()
        if self._recorder is not None:
            self._recorder.stop()
            self._recorder.wait()
            # Finished now, so the transcriber's `finished` slot is free to
            # let the desktop sleep again rather than wait for this one.
            self._recorder = None
        if self._transcriber is not None:
            # Running, or past its result and still freeing the model: either
            # way the window goes now and the process once `finished` arrives.
            self._transcriber.stop()
            self.hide()
            event.ignore()
            return
        self._let_sleep()
        event.accept()


def run(config: Config) -> int:
    """Open the window and run it until it is closed.

    Args:
        config: Effective configuration.

    Returns:
        The process exit code.
    """
    if sys.platform == "win32":
        # Has to happen before the first window exists, or the taskbar has
        # already filed it under pythonw.exe and shows Python's icon.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)

    app = QApplication(sys.argv)
    app.setApplicationName("lecture-scribe")
    # Wayland pairs a window with its menu entry by this name, which is how
    # the window gets the icon `scribe launcher` installed.
    app.setDesktopFileName(DESKTOP_ID)
    # Windows and X11 take the icon from the window itself.
    app.setWindowIcon(QIcon(str(ICON)))
    app.setStyle("Fusion")
    # The palette covers what the stylesheet does not reach, such as the
    # message boxes and the file dialog where Qt draws its own.
    palette = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, _BG),
        (QPalette.ColorRole.WindowText, _TEXT),
        (QPalette.ColorRole.Base, _RAISED),
        (QPalette.ColorRole.AlternateBase, _SURFACE),
        (QPalette.ColorRole.Text, _TEXT),
        (QPalette.ColorRole.Button, _RAISED),
        (QPalette.ColorRole.ButtonText, _TEXT),
        (QPalette.ColorRole.Highlight, _ACCENT),
        (QPalette.ColorRole.HighlightedText, _INK),
        (QPalette.ColorRole.ToolTipBase, _RAISED),
        (QPalette.ColorRole.ToolTipText, _TEXT),
        (QPalette.ColorRole.PlaceholderText, _FAINT),
    ):
        palette.setColor(role, QColor(colour))
    app.setPalette(palette)
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
    """Build one of the small labels above a field."""
    label = QLabel(text)
    label.setObjectName("caption")
    font = label.font()
    font.setPointSize(9)
    label.setFont(font)
    return label


def _header_html(header: str) -> str:
    """Draw a transcript's header line, quieter than the text below it."""
    return (
        f'<p style="font-family: {_css_families(_MONO_FAMILIES)}; font-size: 9pt; '
        f'color: {_MUTED}; margin-bottom: 8px;">{html.escape(header)}</p>'
    )


def _passage_html(passage: Passage, index: int) -> str:
    """Draw one paragraph as a row: timecode in the gutter, text beside it.

    A table because Qt's rich text has no hanging indent that holds when the
    pane is resized, and a wrapped line has to stay clear of the timecode.
    """
    wash = f' bgcolor="{_MARK_WASH}"' if passage.marked else ""
    stamp = _MARK if passage.marked else _FAINT
    return (
        f'<table width="100%" cellspacing="0" cellpadding="5"{wash} '
        f'style="margin-bottom: 6px;"><tr>'
        f'<td valign="top" style="font-family: {_css_families(_MONO_FAMILIES)}; '
        f'font-size: 9pt; color: {stamp}; padding-right: 12px; white-space: nowrap;">'
        f"{html.escape(passage.timecode)}</td>"
        # All the spare width goes to the text, which shrinks the gutter to
        # its timecode. A fixed pixel width is only a hint to Qt's table
        # layout, and rows of different lengths came out with different
        # gutters. Every timecode in one transcript is the same width.
        f'<td width="100%"><a name="p{index}"></a><p style="line-height: 150%;">'
        f"{html.escape(passage.text)}</p></td></tr></table>"
    )


def _css_families(families: tuple[str, ...]) -> str:
    """Quote a fallback stack for a rich text ``font-family``."""
    return ", ".join(f"'{family}'" for family in families)


def _restyle(widget: QWidget) -> None:
    """Apply the stylesheet again after a property it reads has changed.

    A dynamic property only takes effect on a live widget once the style has
    been taken off and put back on.
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _glyph(shape: Literal["dot", "square"], colour: str) -> QIcon:
    """Paint a small button icon, so shapes cost no image files.

    Args:
        shape: A dot for record and mark, a square for stop.
        colour: Fill while the button is enabled; disabled is always faint.

    Returns:
        An icon with its own disabled look, since Qt's automatic one only
        greys out the fill rather than matching the faint button text.
    """
    icon = QIcon()
    for mode, fill in ((QIcon.Mode.Normal, colour), (QIcon.Mode.Disabled, _FAINT)):
        # Drawn at twice the size so it stays crisp on a scaled display.
        pixmap = QPixmap((_GLYPH + _GLYPH_GAP) * 2, _GLYPH * 2)
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(fill))
        box = QRectF(1.0, 1.0, _GLYPH - 2.0, _GLYPH - 2.0)
        if shape == "dot":
            painter.drawEllipse(box)
        else:
            painter.drawRoundedRect(box, 1.5, 1.5)
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def _short_path(path: Path) -> str:
    """Name a file by its lecture folder and its own name.

    The folder is the lecture's date and course, so the two together say
    which lecture it is without the directories above them.
    """
    return f"{path.parent.name}/{path.name}"


def _plural(count: int, noun: str) -> str:
    """Count a noun, keeping it singular when there is only one of it.

    The first progress report of every transcription carries exactly one
    segment, so "1 segments" would be on screen every single time.

    Args:
        count: How many there are.
        noun: The singular noun, pluralised by adding an s.

    Returns:
        The count and the noun together.
    """
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


if __name__ == "__main__":
    sys.exit(main())
