"""Desktop window for recording a lecture and transcribing it.

A second front end beside :mod:`lecture_scribe.cli`, and like it, holding no
logic of its own: it drives the same capture, recognition and formatting
functions and shows what they report. Recording and recognition each get a
worker thread, because both block for as long as the lecture is long.

Qt's Fusion style is forced on both platforms. The window has to look the
same on Linux and on Windows, and the native styles differ too much in
metrics and colour for one stylesheet to cover both.
"""

import ctypes
import html
import math
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPaintEvent,
    QPixmap,
    QPolygonF,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
    inspect_recording,
    lecture_dir,
    list_loopback_devices,
    record_loopback,
    recording_path,
    resolve_device,
)
from lecture_scribe.config import Config, ConfigError, load_config
from lecture_scribe.format_text import (
    Passage,
    format_timecode,
    read_transcript,
    render_transcript,
)
from lecture_scribe.keep_awake import Inhibition, keep_awake
from lecture_scribe.launcher import APP_USER_MODEL_ID, DESKTOP_ID, ICON
from lecture_scribe.transcribe import (
    Decoding,
    Transcription,
    TranscriptionError,
    compute_type,
    earlier_transcript,
    load_model,
    transcribe,
    transcript_path,
)
from lecture_scribe.upload import Upload, announce, push_transcript

_GLYPH = 10
"""Side of a button's painted icon, in logical pixels."""
_GLYPH_GAP = 6
"""Space between the icon and the label; Qt's own is a cramped 4 px under Fusion."""

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

# Three stacked surfaces instead of outlines: depth comes from how light a
# panel is, so borders are left for focus and for what is switched off. The
# greys lean faintly cool, which keeps them from reading as unconsidered.
_BG = "#0f0f11"
_SURFACE = "#18181b"
_RAISED = "#232327"
_HOVER = "#2d2d32"
_LINE = "#2a2a2f"
_FOCUS = "#55555e"
_TEXT = "#ececee"
_MUTED = "#8c8c94"
_FAINT = "#5a5a62"
# Colour is kept for meaning, never decoration: red for capturing audio,
# amber for a mark, and a lighter red for anything that needs attention.
_LIVE = "#ff5a4f"
_MARK = "#f5b83d"
_MARK_WASH = "#272012"
"""A marked paragraph's background: amber, faint enough to read through."""
_PROBLEM = "#ff8a80"

_STYLESHEET = f"""
QWidget {{ background: {_BG}; color: {_TEXT}; }}
QLabel {{ background: transparent; }}
QLabel#caption {{ color: {_FAINT}; }}
QLabel#title {{ color: {_TEXT}; }}
QLabel#clock {{ color: {_TEXT}; }}
QLabel#levels {{ color: {_MUTED}; }}
QLabel#status {{ color: {_MUTED}; }}
QLabel#status[problem="true"] {{ color: {_PROBLEM}; }}

QLineEdit, QComboBox {{
    background: {_SURFACE};
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 8px 12px;
    selection-background-color: {_HOVER};
    selection-color: {_TEXT};
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {_LINE}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {_FOCUS}; }}
QLineEdit:disabled, QComboBox:disabled {{
    color: {_FAINT}; background: transparent; border-color: {_LINE};
}}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox QAbstractItemView {{
    background: {_RAISED};
    border: 1px solid {_LINE};
    padding: 4px;
    selection-background-color: {_HOVER};
    outline: none;
}}

QPushButton {{
    background: {_RAISED};
    border: 1px solid transparent;
    border-radius: 16px;
    padding: 8px 20px;
    color: {_TEXT};
    font-weight: 500;
}}
QPushButton:hover {{ background: {_HOVER}; }}
QPushButton:pressed {{ background: {_SURFACE}; }}
QPushButton:focus {{ border-color: {_FOCUS}; }}
QPushButton:disabled {{
    background: transparent; border-color: {_LINE}; color: {_FAINT};
}}
QPushButton#primary {{ background: {_TEXT}; color: {_BG}; }}
QPushButton#primary:hover {{ background: #ffffff; }}
QPushButton#primary:pressed {{ background: #c8c8cc; }}
QPushButton#primary:focus {{ border-color: {_MUTED}; }}
QPushButton#primary:disabled {{
    background: transparent; border-color: {_LINE}; color: {_FAINT};
}}
QPushButton#primary[live="true"] {{ background: {_LIVE}; color: #ffffff; }}
QPushButton#primary[live="true"]:hover {{ background: #ff6f65; }}
QPushButton#primary[live="true"]:pressed {{ background: #e0493f; }}
QPushButton#primary[live="true"]:focus {{ border-color: #ffb3ad; }}
QPushButton#primary[live="true"]:disabled {{
    background: transparent; border-color: {_LINE}; color: {_FAINT};
}}

QTextEdit {{
    background: {_SURFACE};
    border: none;
    border-radius: 10px;
    padding: 14px 16px;
    selection-background-color: {_HOVER};
    selection-color: {_TEXT};
}}
QFrame#rule {{ background: {_LINE}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 0; }}
QScrollBar::handle:vertical {{
    background: {_RAISED}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {_FOCUS}; }}
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


def _with_upload(message: str, upload: Upload | None) -> tuple[str, bool]:
    """Add the outcome of an upload to the status line.

    Args:
        message: What recognition reported.
        upload: The upload, or ``None`` when upload is off.

    Returns:
        The line, and whether it needs attention. A failed upload does: the
        transcript is safe, but the copy the user expects on Drive is not.
    """
    if upload is None:
        return message, False
    if upload.ok:
        return f"{message} · uploaded to {upload.destination}", False
    return f"{message} · not uploaded, {upload.error}", True


class _TranscribeWorker(QThread):
    """Loads the model and transcribes one recording off the GUI thread.

    Recognition can be given up part way through, which is what lets the
    window close during the twenty minutes a long lecture takes.
    """

    staged = Signal(str)
    progressed = Signal(Decoding)
    # The last is an Upload, or None when upload is off; Signal has no Optional.
    transcribed = Signal(Path, str, Transcription, object)
    stopped = Signal(str)
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
        self._stop = threading.Event()

    def stop(self) -> None:
        """Ask recognition to give up at the end of the segment it is on."""
        self._stop.set()

    def run(self) -> None:
        """Recognise the recording and write its transcript beside it."""
        asr = self._config.asr
        self.staged.emit(f"loading {asr.model} ({compute_type(asr.gpu)}) ...")
        try:
            model = load_model(asr.model, gpu=asr.gpu)
        except TranscriptionError as error:
            self.failed.emit(str(error))
            return

        # The decoder scans the whole file for speech before the first
        # segment exists, which on a long lecture is a good twenty seconds
        # with nothing moving on screen.
        self.staged.emit("reading the recording and finding the speech in it ...")
        try:
            result = transcribe(
                model,
                self._wav,
                language=asr.language,
                beam_size=asr.beam_size,
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
            title=self._wav.parent.name,
            model=asr.model,
            language=asr.language,
            audio_duration=result.audio_duration,
            paragraph_gap=self._config.output.paragraph_gap,
            paragraph_target=self._config.output.paragraph_target,
            paragraph_max=self._config.output.paragraph_max,
            marks=self._marks,
        )
        path = transcript_path(self._wav)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as error:
            self.failed.emit(f"cannot write {path}: {error}")
            return

        # Uploaded before the result is reported rather than after: the
        # window frees its buttons on that report, and a second transcription
        # started meanwhile would replace this thread while it still runs.
        upload: Upload | None = None
        if self._config.upload.enabled:
            self.staged.emit(f"uploading to {self._config.upload.remote} ...")
            upload = push_transcript(path, self._config.output.dir, self._config.upload)
            announce(upload)
        self.transcribed.emit(path, text, result, upload)


class _Combo(QComboBox):
    """A device list that draws its own arrow.

    Qt stops painting the native arrow as soon as the drop-down is styled at
    all, and the native one arrives with a raised panel and a separator that
    do not belong in a flat window.
    """

    _WIDTH = 9.0
    _HEIGHT = 5.0

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(_MUTED if self.isEnabled() else _FAINT))

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


class _Chip(QWidget):
    """A small pill in the header saying what the window is doing.

    While recording its dot is red and breathes, so a live recording shows
    from across the room even with the clock too small to read.
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
            live: Whether audio is being captured, which turns the dot red.
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
    """The transcript pane: timecodes in a gutter, marks washed in amber.

    Drawn from the paragraphs :func:`read_transcript` finds in the text, so
    a transcript opened from disk shows the same as one just made. What is
    on disk is untouched; this is only how it is shown.
    """

    def __init__(self) -> None:
        """Create an empty pane."""
        super().__init__()
        self.setReadOnly(True)
        self.setPlaceholderText("the transcript appears here")
        font = QFont()
        font.setFamilies(_UI_FAMILIES)
        font.setPointSizeF(10.5)
        self.setFont(font)
        self.document().setDocumentMargin(2)
        self._source = ""

    def show_transcript(self, text: str) -> None:
        """Replace what is shown with a whole transcript.

        Args:
            text: The transcript as written to disk.
        """
        header, passages = read_transcript(text)
        parts = [] if not header else [_header_html(header)]
        parts.extend(_passage_html(passage) for passage in passages)
        self.setHtml("".join(parts))
        self._source = text

    def add_preview(self, timecode: str, text: str) -> None:
        """Append one segment while recognition is still running.

        Appended rather than redrawn: a full lecture runs to a thousand
        segments, and laying the whole document out again for each one
        would slow down the very thing it is watching.

        Args:
            timecode: Where the segment is, without brackets.
            text: What was recognised in it.
        """
        bar = self.verticalScrollBar()
        following = bar.value() == bar.maximum()
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(_passage_html(Passage(timecode, text, marked=False)))
        self._source += f"[{timecode}] {text}\n"
        # Keep the newest line in view, unless the reader scrolled up to read.
        if following:
            bar.setValue(bar.maximum())

    def clear(self) -> None:
        """Empty the pane and forget the text behind it."""
        super().clear()
        self._source = ""

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


class _LevelMeter(QWidget):
    """A rounded bar showing how loud the incoming audio is.

    The fill brightens from left to right, so a louder passage reveals a
    brighter end of the bar and the level reads at a glance without colour,
    which is kept for meaning.
    """

    _HEIGHT = 6
    _FALL = 0.035
    """Share of the bar the level may sink per block, about 1.6 of it a second."""
    _PEAK_FALL = 0.004

    def __init__(self) -> None:
        """Create an empty meter."""
        super().__init__()
        self.setFixedHeight(self._HEIGHT)
        self._level = 0.0
        self._peak = 0.0
        self._progress = False

    def set_rms(self, rms: float) -> None:
        """Show the level of the block just recorded.

        Args:
            rms: Block level from 0.0 to 1.0.
        """
        # Rises at once but sinks gradually, as a hardware meter does. Blocks
        # arrive every 21 ms, and following each one exactly makes the bar
        # flicker between syllables instead of showing speech.
        self._level = max(loudness(rms), self._level - self._FALL)
        # The peak marker sinks slower still, so a brief loud passage stays
        # on screen long enough to be seen.
        self._peak = max(self._level, self._peak - self._PEAK_FALL)
        self._progress = False
        self.update()

    def set_fraction(self, fraction: float) -> None:
        """Fill the bar to a share of its width, with no peak marker.

        The same widget serves as the progress bar while recognition runs.
        It is already a bar that fills from the left, and a second one would
        be furniture for no gain.

        Args:
            fraction: How full to draw it, from 0.0 to 1.0.
        """
        self._level = max(0.0, min(1.0, fraction))
        self._peak = 0.0
        self._progress = True
        self.update()

    def reset(self) -> None:
        """Empty the meter, for when nothing is being recorded."""
        self._level = 0.0
        self._peak = 0.0
        self._progress = False
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        """Draw the track, the fill for the current level, and the peak."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width = float(self.width())
        height = float(self.height())
        radius = height / 2

        painter.setBrush(QColor(_RAISED))
        painter.drawRoundedRect(QRectF(0.0, 0.0, width, height), radius, radius)

        filled = width * self._level
        if filled > 0.0:
            if self._progress:
                painter.setBrush(QColor(_TEXT))
            else:
                ramp = QLinearGradient(0.0, 0.0, width, 0.0)
                ramp.setColorAt(0.0, QColor(_FAINT))
                ramp.setColorAt(1.0, QColor("#ffffff"))
                painter.setBrush(ramp)
            # Never narrower than the bar is tall, or the rounded ends of a
            # nearly silent level fold into a smudge.
            painter.drawRoundedRect(
                QRectF(0.0, 0.0, max(filled, height), height), radius, radius
            )

        if self._peak > self._level:
            painter.setBrush(QColor(_TEXT))
            left = min(width * self._peak, width - 2.0)
            painter.drawRoundedRect(QRectF(left - 1.0, 0.0, 2.0, height), 1.0, 1.0)


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
        # Recording and recognition never run at once, so one request covers both.
        self._awake: Inhibition | None = None
        self._recording = False
        self._transcribing = False
        self._closing = False
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

        header = QHBoxLayout()
        title = QLabel("Lecture Scribe")
        title.setObjectName("title")
        title_font = title.font()
        title_font.setPointSize(11)
        title_font.setWeight(QFont.Weight.DemiBold)
        title.setFont(title_font)
        self._chip = _Chip()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self._chip)
        layout.addLayout(header)
        layout.addSpacing(22)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(5)
        # The device name is the longer of the two by far.
        form.setColumnStretch(0, 1)
        form.setColumnStretch(1, 2)
        self._course = QLineEdit()
        self._course.setPlaceholderText("used for the folder")
        self._course.textChanged.connect(self._refresh)
        self._device_box = _Combo()
        # Without this the longest device name sets the window's minimum width.
        self._device_box.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._device_box.setMinimumContentsLength(12)
        form.addWidget(_caption("Course"), 0, 0)
        form.addWidget(_caption("Device"), 0, 1)
        form.addWidget(self._course, 1, 0)
        form.addWidget(self._device_box, 1, 1)
        layout.addLayout(form)
        layout.addSpacing(34)

        self._clock = QLabel(_clock(0.0))
        self._clock.setObjectName("clock")
        self._clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clock_font = QFont()
        clock_font.setFamilies(_UI_FAMILIES)
        clock_font.setPointSize(44)
        clock_font.setWeight(QFont.Weight.ExtraLight)
        # Proportional digits make the whole clock shuffle sideways each second.
        clock_font.setFeature(QFont.Tag("tnum"), 1)
        clock_font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 96)
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
        self._mark.setIcon(_glyph("dot", _MARK))
        self._mark.setToolTip("flag this moment in the transcript (Ctrl+M)")
        self._mark.clicked.connect(self._mark_moment)
        self._open = QPushButton("open")
        self._open.setToolTip("transcribe a recording made earlier (Ctrl+O)")
        self._open.clicked.connect(self._open_recording)
        self._text = QPushButton("transcribe")
        self._text.clicked.connect(self._start_transcription)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        for button in (self._record, self._mark, self._open, self._text):
            button.setIconSize(QSize(_GLYPH + _GLYPH_GAP, _GLYPH))
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

        self._transcript = _Transcript()
        layout.addWidget(self._transcript, 1)

        mark = QShortcut(QKeySequence("Ctrl+M"), self)
        mark.activated.connect(self._mark_moment)
        open_file = QShortcut(QKeySequence("Ctrl+O"), self)
        open_file.activated.connect(self._open_recording)

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
            self._chip.show_state("stopping")
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

        self._wav = recording_path(folder)
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
        self._awake = keep_awake("recording a lecture")
        self._refresh()
        note = (
            "" if self._awake.held else f" · the screen may lock: {self._awake.detail}"
        )
        self._say(f"recording to {_short_path(self._wav)}{note}", path=self._wav)
        worker.start()

    def _on_progress(self, progress: Progress) -> None:
        """Redraw the clock, meter and level readout."""
        self._elapsed = progress.elapsed
        self._clock.setText(_clock(progress.elapsed))
        self._chip.breathe(progress.elapsed)
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
        if recording.interrupted is not None:
            self._say(recording.interrupted, problem=True)
        elif recording.mean_rms < self._config.audio.silence_rms:
            self._say(
                "the recording is silent from end to end. The audio was kept "
                "anyway; check that the lecture was playing through the "
                "selected device.",
                problem=True,
            )
        else:
            self._say(
                f"stopped after {_clock(recording.duration)} · "
                f"{_short_path(recording.path)}",
                path=recording.path,
            )
        self._refresh()

    def _on_record_failed(self, message: str) -> None:
        """Report a recording that could not start or could not continue."""
        self._recording = False
        self._meter.reset()
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
        self._let_sleep()
        if self._recording:
            self._recording = False
            self._meter.reset()
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
        if not self._recording:
            return
        self._marks.append(self._elapsed)
        self._say(
            f"marked {format_timecode(self._elapsed)} "
            f"({len(self._marks)} in this lecture)"
        )

    def _open_recording(self) -> None:
        """Pick a recording made earlier and make it the one to transcribe.

        Lectures reach this folder without `scribe rec` having made them --
        copied in, renamed, recorded on another machine -- and after a
        restart the window has no recording of its own to work on either.
        """
        if self._recording or self._transcribing:
            return

        chosen, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open a recording",
            str(self._config.output.dir),
            "Recordings (*.wav);;All files (*)",
        )
        if not chosen:
            return

        path = Path(chosen)
        try:
            existing = inspect_recording(path)
        except AudioDeviceError as error:
            self._say(str(error), problem=True)
            return

        self._wav = path
        # Marks belong to the lecture they were pressed during. Carrying them
        # onto a file opened afterwards would put >>> on unrelated sentences.
        self._marks = []
        self._elapsed = 0.0
        self._meter.reset()
        self._clock.setText(_clock(existing.duration))
        self._levels.setText(f"{existing.size_bytes / 1_000_000:.1f} MB")
        self._show_earlier_transcript(existing)
        self._refresh()

    def _show_earlier_transcript(self, existing: ExistingRecording) -> None:
        """Show the transcript beside a recording, when one was made already.

        Args:
            existing: The recording that was just opened.
        """
        transcript = earlier_transcript(existing.path)
        if transcript is None:
            self._transcript.clear()
            self._say(
                f"{_short_path(existing.path)} · not transcribed yet",
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
            f"transcribing again {outcome}",
            path=existing.path,
        )

    def _start_transcription(self) -> None:
        """Start recognition, or give up the one that is running."""
        if self._transcribing and self._transcriber is not None:
            self._say("stopping recognition ...")
            self._chip.show_state("stopping")
            self._text.setEnabled(False)
            self._transcriber.stop()
            return
        if self._wav is None:
            return
        self._transcript.clear()
        worker = _TranscribeWorker(self._config, self._wav, list(self._marks))
        worker.staged.connect(self._say)
        worker.progressed.connect(self._on_decoding)
        worker.transcribed.connect(self._on_transcribed)
        worker.stopped.connect(self._on_transcribe_stopped)
        worker.failed.connect(self._on_transcribe_failed)
        worker.finished.connect(self._release_transcriber)

        self._transcriber = worker
        self._transcribing = True
        self._awake = keep_awake("transcribing a lecture")
        self._refresh()
        worker.start()

    def _on_decoding(self, decoding: Decoding) -> None:
        """Show how far recognition has got, and the text as it arrives.

        Reading can start immediately instead of after a wait that runs into
        double digit minutes on a full lecture. What the pane shows here is
        replaced by the finished transcript once the last segment is in.
        """
        self._clock.setText(_clock(decoding.position))
        self._meter.set_fraction(decoding.fraction)
        self._chip.show_state(f"transcribing {decoding.fraction * 100:.0f}%")
        left = (
            decoding.elapsed / decoding.fraction - decoding.elapsed
            if decoding.fraction > 0.0
            else 0.0
        )
        self._levels.setText(
            f"{decoding.fraction * 100:.0f}%   ·   "
            f"{_plural(decoding.segments, 'segment')}   ·   {_clock(left)} left"
        )
        # The end of the segment rather than its start: near enough for a
        # preview the finished render is about to replace.
        self._transcript.add_preview(
            format_timecode(decoding.position).strip("[]"), decoding.text
        )

    def _on_transcribed(
        self, path: Path, text: str, result: Transcription, upload: Upload | None
    ) -> None:
        """Show the finished transcript, how long decoding took, and the upload."""
        self._transcribing = False
        self._meter.reset()
        self._clock.setText(_clock(result.audio_duration))
        self._transcript.show_transcript(text)
        ratio = (
            result.decode_seconds / result.audio_duration
            if result.audio_duration > 0
            else 0.0
        )
        message = (
            f"wrote {_short_path(path)} · {_plural(len(result.segments), 'segment')} · "
            f"{_clock(result.decode_seconds)} ({ratio:.2f}x audio length)"
        )
        line, problem = _with_upload(message, upload)
        self._say(line, problem=problem, path=path)
        self._refresh()

    def _on_transcribe_stopped(self, message: str) -> None:
        """Report recognition the user gave up on, which is not a failure.

        Args:
            message: What was recognised before it stopped, and what that
                leaves behind.
        """
        self._transcribing = False
        self._meter.reset()
        self._say(message)
        self._refresh()

    def _on_transcribe_failed(self, message: str) -> None:
        """Report a recognition that could not be completed."""
        self._transcribing = False
        self._say(message, problem=True)
        self._refresh()

    def _release_transcriber(self) -> None:
        """Drop the finished worker, never leaving the window mid-recognition.

        The same reasoning as :meth:`_release_recorder`: a thread that dies
        without reporting would otherwise leave every button disabled for
        good.
        """
        if self._transcriber is not None:
            self._transcriber.wait()
            self._transcriber = None
        self._let_sleep()
        if self._transcribing:
            self._transcribing = False
            self._say(
                "recognition ended without saying why; the recording itself "
                "is untouched and can be transcribed again",
                problem=True,
            )
            self._refresh()
        if self._closing:
            # The window is already hidden and the worker is now finished, so
            # there is no thread left to be destroyed under a running loop.
            QApplication.quit()

    def _let_sleep(self) -> None:
        """Hand locking and sleeping back to the desktop once the work is done.

        Called from the ``finished`` slots rather than the result slots, for
        the same reason the worker state is cleared there: a thread that dies
        without reporting would otherwise keep the machine awake for good.
        """
        if self._awake is not None:
            self._awake.release()
            self._awake = None

    def _refresh(self) -> None:
        """Bring every control into line with what the window is doing."""
        idle = not self._recording and not self._transcribing
        named = bool(self._course.text().strip())
        self._record.setText("stop" if self._recording else "record")
        self._record.setIcon(
            _glyph("square", "#ffffff") if self._recording else _glyph("dot", _LIVE)
        )
        self._record.setProperty("live", self._recording)
        _restyle(self._record)
        self._record.setEnabled(
            self._recording or (idle and named and bool(self._devices))
        )
        self._mark.setEnabled(self._recording)
        self._open.setEnabled(idle)
        self._text.setText("stop" if self._transcribing else "transcribe")
        self._text.setIcon(_glyph("square", _TEXT) if self._transcribing else QIcon())
        self._text.setEnabled(self._transcribing or (idle and self._wav is not None))
        self._course.setEnabled(idle)
        self._device_box.setEnabled(idle)
        if self._recording:
            self._chip.show_state("recording", live=True)
        elif self._transcribing:
            self._chip.show_state("transcribing")
        else:
            self._chip.show_state("ready")

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
        if self._transcribing:
            answer = QMessageBox.question(
                self,
                "lecture scribe",
                "Recognition is still running.\n"
                "Stopping it throws away what has been recognised so far; the "
                "recording itself is safe on disk. Close anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._closing = True
            if self._transcriber is not None:
                self._transcriber.stop()
            self.hide()
            event.ignore()
            return

        if self._recorder is not None:
            self._recorder.stop()
            self._recorder.wait()
        self._let_sleep()
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


def _passage_html(passage: Passage) -> str:
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
        f'<td width="100%"><p style="line-height: 150%;">'
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


def _track(label: QLabel, spacing: float) -> None:
    """Letter-space a label, which Qt stylesheets cannot express."""
    font = label.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    label.setFont(font)


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
