"""Turning recognised segments into the readable transcript.

Everything here is pure: it takes segments and returns a string, and never
touches the disk.
"""

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from lecture_scribe.transcribe import Segment

_LINE_WIDTH = 80


@dataclass(frozen=True, slots=True)
class _Paragraph:
    """A run of segments with no long pause between them.

    Attributes:
        start: Start time of the first segment, in seconds.
        end: End time of the last segment, in seconds.
        text: Segment texts joined into one block, not yet wrapped.
    """

    start: float
    end: float
    text: str


def render_transcript(
    segments: Sequence[Segment],
    *,
    title: str,
    model: str,
    language: str,
    audio_duration: float,
    paragraph_gap: float,
    marks: Sequence[float] = (),
) -> str:
    """Render recognised segments into the readable .txt transcript.

    Args:
        segments: Recognised speech, in chronological order.
        title: Lecture identifier shown in the header, normally the output
            folder's name.
        model: ASR model name, shown in the header.
        language: Spoken language code, shown in the header.
        audio_duration: Length of the source recording in seconds, shown in
            the header even when no speech was recognised in it.
        paragraph_gap: Pause in seconds that starts a new paragraph.
        marks: Timestamps flagged as important during the lecture, in
            seconds. A paragraph covering one is prefixed with ``>>>``.

    Returns:
        The complete transcript text, header included, ending in a newline.
    """
    header = f"{title} -- {model} ({language}), {_plain_duration(audio_duration)}\n"
    if not segments:
        return f"{header}\n(no speech recognised)\n"

    paragraphs = _group_paragraphs(segments, paragraph_gap=paragraph_gap)
    body = "\n\n".join(_render_paragraph(paragraph, marks) for paragraph in paragraphs)
    return f"{header}\n{body}\n"


def format_timecode(seconds: float) -> str:
    """Format an offset into the transcript's inline timecode style.

    The hour is dropped for anything under an hour, so a typical lecture
    reads ``[12:30]`` rather than the more cluttered ``[0:12:30]``.

    Args:
        seconds: Offset from the start of the recording.

    Returns:
        ``[MM:SS]``, or ``[H:MM:SS]`` once the offset reaches an hour.
    """
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"[{hours}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes:02d}:{secs:02d}]"


def _group_paragraphs(
    segments: Sequence[Segment], *, paragraph_gap: float
) -> list[_Paragraph]:
    """Group segments into paragraphs, breaking on a long enough pause."""
    start = segments[0].start
    end = segments[0].end
    pieces = [segments[0].text]
    paragraphs: list[_Paragraph] = []

    for previous, segment in pairwise(segments):
        if segment.start - previous.end > paragraph_gap:
            paragraphs.append(_Paragraph(start, end, " ".join(pieces)))
            start = segment.start
            pieces = []
        pieces.append(segment.text)
        end = segment.end

    paragraphs.append(_Paragraph(start, end, " ".join(pieces)))
    return paragraphs


def _render_paragraph(paragraph: _Paragraph, marks: Sequence[float]) -> str:
    """Wrap one paragraph, adding its timecode and marker prefix."""
    marked = any(paragraph.start <= mark <= paragraph.end for mark in marks)
    lead = ">>> " if marked else ""
    prefix = f"{lead}{format_timecode(paragraph.start)} "
    return textwrap.fill(
        paragraph.text,
        width=_LINE_WIDTH,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )


def _plain_duration(seconds: float) -> str:
    """Format a duration as ``H:MM:SS``, hour always shown."""
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"
