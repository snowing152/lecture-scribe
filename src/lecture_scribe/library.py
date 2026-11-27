"""The lectures on disk, listed and searched. Pure reporting, nothing written."""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lecture_scribe.audio_capture import AudioDeviceError, inspect_recording, read_marks
from lecture_scribe.format_text import read_transcript
from lecture_scribe.transcribe import earlier_transcript

# The header as `render_transcript` writes it: `<title> -- <model> (<lang>), H:MM:SS`.
# The title is matched greedily, so the model is what follows the last " --":
# a lecture title is typed by the user and may contain " -- ", a model label
# never does.
_HEADER = re.compile(
    r"^.* -- (?P<model>.+) \([^()]*\), (?P<h>\d+):(?P<m>\d\d):(?P<s>\d\d)$"
)

_EXCERPT_WIDTH = 80
_EXCERPT_LEAD = 30


@dataclass(frozen=True, slots=True)
class Lecture:
    """One recording on disk and what is known about it.

    Attributes:
        wav: The recording.
        transcript: Its transcript, or None when it has not been transcribed.
        model: Model named in the transcript's header, or None when there is
            no transcript or its header cannot be read.
        seconds: Length from the transcript's header if present, otherwise
            from the WAV; None when neither can be read.
        marks: Number of marks pressed during the recording, or None when the
            marks file is damaged.
        modified: Modification time of the WAV, used for ordering.
    """

    wav: Path
    transcript: Path | None
    model: str | None
    seconds: float | None
    marks: int | None
    modified: float

    @property
    def name(self) -> str:
        """The lecture folder's name."""
        return self.wav.parent.name


@dataclass(frozen=True, slots=True)
class Hit:
    """A lecture that matched a search.

    Attributes:
        lecture: The lecture.
        passage: Index into the transcript's passages of the first one that
            matches, or None when only the name matched.
        timecode: That passage's timecode, or None.
        excerpt: About 80 characters of the passage around the match with
            whitespace collapsed; empty for a name-only match.
    """

    lecture: Lecture
    passage: int | None
    timecode: str | None
    excerpt: str


def list_lectures(root: Path) -> list[Lecture]:
    """List every recording under a folder, newest first.

    Discovery follows :func:`pending_recordings`, except that recordings
    with a transcript are listed too. One unreadable file shows up with
    missing fields instead of breaking the list.

    Args:
        root: Directory to search, however deeply the lectures are nested.

    Returns:
        Lectures by modification time, newest first, ties by name. Empty when
        ``root`` does not exist.
    """
    if not root.is_dir():
        return []
    wavs = sorted(
        path for path in root.rglob("*.wav", case_sensitive=False) if path.is_file()
    )
    per_folder = Counter(wav.parent for wav in wavs)

    lectures: list[Lecture] = []
    for wav in wavs:
        try:
            modified = wav.stat().st_mtime
        except OSError:
            continue
        lectures.append(
            _describe(wav, shared=per_folder[wav.parent] > 1, modified=modified)
        )
    lectures.sort(key=lambda lecture: lecture.name)
    lectures.sort(key=lambda lecture: lecture.modified, reverse=True)
    return lectures


def search(lectures: Sequence[Lecture], query: str) -> list[Hit]:
    """Find the lectures whose name or transcript contains some text.

    Plain case-insensitive substring matching: Korean words carry particles,
    so ``프로토콜`` has to match ``프로토콜의``, which a word index would miss.

    Args:
        lectures: Lectures to search, as from :func:`list_lectures`.
        query: Text to look for. Blank matches every lecture.

    Returns:
        One hit per matching lecture, in input order.
    """
    needle = query.strip().casefold()
    if not needle:
        return [Hit(lecture, None, None, "") for lecture in lectures]

    hits: list[Hit] = []
    for lecture in lectures:
        hit = _first_passage(lecture, needle)
        if hit is not None:
            hits.append(hit)
        elif needle in lecture.name.casefold():
            hits.append(Hit(lecture, None, None, ""))
    return hits


def _describe(wav: Path, *, shared: bool, modified: float) -> Lecture:
    """Gather what is known about one recording, never raising."""
    if shared:
        own = wav.with_suffix(".txt")
        transcript = own if own.is_file() else None
    else:
        transcript = earlier_transcript(wav)

    model: str | None = None
    seconds: float | None = None
    if transcript is not None:
        model, seconds = _read_header(transcript)
    if seconds is None:
        try:
            seconds = inspect_recording(wav).duration
        except AudioDeviceError:
            seconds = None

    try:
        marks: int | None = len(read_marks(wav))
    except (ValueError, OSError):
        marks = None
    return Lecture(wav, transcript, model, seconds, marks, modified)


def _read_header(transcript: Path) -> tuple[str | None, float | None]:
    """Read the model and length from a transcript's first line."""
    try:
        with transcript.open(encoding="utf-8") as handle:
            first = handle.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None, None
    found = _HEADER.match(first)
    if found is None:
        return None, None
    seconds = int(found["h"]) * 3600 + int(found["m"]) * 60 + int(found["s"])
    return found["model"], float(seconds)


def _first_passage(lecture: Lecture, needle: str) -> Hit | None:
    """Find the first passage of a lecture's transcript containing ``needle``."""
    if lecture.transcript is None:
        return None
    try:
        text = lecture.transcript.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for index, passage in enumerate(read_transcript(text)[1]):
        flat = " ".join(passage.text.split())
        at = flat.casefold().find(needle)
        if at < 0:
            continue
        return Hit(lecture, index, passage.timecode, _excerpt(flat, at))
    return None


def _excerpt(flat: str, at: int) -> str:
    """Cut about 80 characters of ``flat`` starting a little before ``at``."""
    start = max(0, at - _EXCERPT_LEAD)
    return flat[start : start + _EXCERPT_WIDTH].strip()
