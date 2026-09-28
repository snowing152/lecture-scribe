"""Turning recognised segments into the readable transcript.

Everything here is pure: it takes segments and returns a string, or takes
that string and reads it back, and never touches the disk.
"""

import re
import textwrap
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from lecture_scribe.transcribe import Segment

_LINE_WIDTH = 80
_AN_HOUR = 3600.0

# What a segment's text ends with when the speaker finished a thought there.
# Whisper punctuates Korean with the ASCII marks: over four measured lectures
# no segment ended on a full width one.
_SENTENCE_ENDS = (".", "?", "!")

_MARKER = ">>> "
# How a rendered paragraph opens: an optional marker, then the timecode.
_OPENING = re.compile(rf"({re.escape(_MARKER)})?\[((?:\d+:)?\d\d:\d\d)\] ")


@dataclass(frozen=True, slots=True)
class Passage:
    """One paragraph of a transcript, read back from the text on disk.

    Attributes:
        timecode: Where the paragraph starts, as written but without the
            brackets, such as ``12:30``. Empty for a block that has none,
            such as ``(no speech recognised)`` or a line added by hand.
        text: The paragraph unwrapped onto one line.
        marked: Whether it was flagged with ``>>>`` during the lecture.
    """

    timecode: str
    text: str
    marked: bool


@dataclass(frozen=True, slots=True)
class _Paragraph:
    """A run of segments the transcript prints as one block.

    Attributes:
        start: Start time of the first segment, in seconds. This is the whole
            of a paragraph's position: its timecode is written from it, and a
            mark is attached by it.
        text: Segment texts joined into one block, not yet wrapped.
    """

    start: float
    text: str


def render_transcript(
    segments: Sequence[Segment],
    *,
    title: str,
    model: str,
    language: str,
    audio_duration: float,
    paragraph_gap: float,
    paragraph_target: float,
    paragraph_max: float,
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
        paragraph_target: Length in seconds after which a paragraph is ended
            at the next sentence, whether or not the speaker paused.
        paragraph_max: Length in seconds after which a paragraph ends at
            the next segment, sentence or not. A segment is only a few
            seconds long, so a paragraph overruns this by that much at most.
            Either of the two turns off at zero.
        marks: Timestamps flagged as important during the lecture, in
            seconds. The paragraph that was running when a mark was pressed
            is prefixed with ``>>>``.

    Returns:
        The complete transcript text, header included, ending in a newline.
    """
    header = f"{title} -- {model} ({language}), {_plain_duration(audio_duration)}\n"
    if not segments:
        return f"{header}\n(no speech recognised)\n"

    paragraphs = _group_paragraphs(
        segments,
        paragraph_gap=paragraph_gap,
        paragraph_target=paragraph_target,
        paragraph_max=paragraph_max,
    )
    marked = _marked_paragraphs(paragraphs, marks)
    # One width of timecode for the whole file, so the text beside them keeps
    # a single left edge rather than stepping over two columns at the hour. A
    # segment can end a shade past the audio's own length, so the last
    # paragraph has a say in this as well.
    with_hour = audio_duration >= _AN_HOUR or paragraphs[-1].start >= _AN_HOUR
    body = "\n\n".join(
        _render_paragraph(paragraph, marked=index in marked, with_hour=with_hour)
        for index, paragraph in enumerate(paragraphs)
    )
    return f"{header}\n{body}\n"


def read_transcript(text: str) -> tuple[str, list[Passage]]:
    """Split a transcript back into its header and paragraphs.

    Works from the text rather than from segments, because a transcript
    opened from disk has none behind it. Anything that does not look like a
    rendered paragraph is kept as a passage without a timecode rather than
    dropped, so a transcript edited by hand still shows whole.

    Args:
        text: A transcript as :func:`render_transcript` writes it.

    Returns:
        The header line, empty when the first block is already a paragraph,
        and the paragraphs in order.
    """
    blocks = [block for block in re.split(r"\n\s*\n", text.strip()) if block]
    header = ""
    if blocks and not _OPENING.match(blocks[0]):
        header = " ".join(line.strip() for line in blocks.pop(0).splitlines())

    passages = []
    for block in blocks:
        # The wrap broke lines at spaces only, so a single space rejoins them.
        joined = " ".join(line.strip() for line in block.splitlines())
        opening = _OPENING.match(joined)
        if opening is None:
            passages.append(Passage(timecode="", text=joined, marked=False))
            continue
        passages.append(
            Passage(
                timecode=opening.group(2),
                text=joined[opening.end() :],
                marked=opening.group(1) is not None,
            )
        )
    return header, passages


def format_timecode(seconds: float, *, with_hour: bool = False) -> str:
    """Format an offset into the transcript's inline timecode style.

    The hour is dropped for anything under an hour, so a typical lecture
    reads ``[12:30]`` rather than the more cluttered ``[0:12:30]``.

    Args:
        seconds: Offset from the start of the recording.
        with_hour: Whether to show the hour even under one hour. A transcript
            of a recording that runs past an hour asks for this, so that its
            timecodes are all one width; a live readout of a moment does not.

    Returns:
        ``[MM:SS]``, or ``[H:MM:SS]`` once the offset reaches an hour or when
        the hour was asked for.
    """
    whole = int(seconds)
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours or with_hour:
        return f"[{hours}:{minutes:02d}:{secs:02d}]"
    return f"[{minutes:02d}:{secs:02d}]"


def _group_paragraphs(
    segments: Sequence[Segment],
    *,
    paragraph_gap: float,
    paragraph_target: float,
    paragraph_max: float,
) -> list[_Paragraph]:
    """Group segments into paragraphs.

    A pause ends a paragraph, which is all a lecturer who pauses needs. One
    who runs sentences together leaves nothing to break on: measured on an 81
    minute lecture, 92 of the 97 gaps between segments were zero and the
    largest in ten minutes was 2.0 seconds, so the whole file came out as 24
    paragraphs, one of them 15 minutes and 69 lines long. A paragraph that
    has run past ``paragraph_target`` therefore ends at the next sentence,
    and one that reaches no sentence ends at ``paragraph_max`` regardless.
    Both land on a segment boundary, the only place a paragraph can be cut.
    """
    start = segments[0].start
    pieces = [segments[0].text]
    paragraphs: list[_Paragraph] = []

    for previous, segment in pairwise(segments):
        run = previous.end - start
        if (
            segment.start - previous.end > paragraph_gap
            or (0.0 < paragraph_target < run and _ends_a_sentence(previous.text))
            or 0.0 < paragraph_max < run
        ):
            paragraphs.append(_Paragraph(start, " ".join(pieces)))
            start = segment.start
            pieces = []
        pieces.append(segment.text)

    paragraphs.append(_Paragraph(start, " ".join(pieces)))
    return paragraphs


def _marked_paragraphs(
    paragraphs: Sequence[_Paragraph], marks: Sequence[float]
) -> set[int]:
    """Decide which paragraphs carry a ``>>>``.

    A mark is a reaction: the key is pressed after the sentence it refers to,
    and often in the pause that follows it -- which is the same pause that
    ends the paragraph. Asking which paragraph covers a mark therefore loses
    exactly the marks a listener is most likely to press, so a mark is
    carried back to the last paragraph that had begun when it was pressed.

    Args:
        paragraphs: Paragraphs in chronological order.
        marks: Timestamps flagged during the lecture, in seconds.

    Returns:
        Indices into ``paragraphs``. A mark from before the first paragraph
        began lands on that first paragraph: a mark is never dropped, since
        nothing in the transcript would show that one went missing.
    """
    starts = [paragraph.start for paragraph in paragraphs]
    return {max(bisect_right(starts, mark) - 1, 0) for mark in marks}


def _ends_a_sentence(text: str) -> bool:
    """Whether a segment's text ends where a paragraph could.

    Whisper cuts continuous speech into segments wherever its thirty second
    window lands rather than where the speaker finished a thought: on a
    measured lecture only 18% of segments ended on punctuation. Ending an
    over-long paragraph at one of those few is what keeps the break off the
    middle of a sentence.

    Args:
        text: One segment's recognised text, already stripped.

    Returns:
        Whether it closes a sentence.
    """
    return text.endswith(_SENTENCE_ENDS)


def _render_paragraph(paragraph: _Paragraph, *, marked: bool, with_hour: bool) -> str:
    """Wrap one paragraph, adding its timecode and marker prefix."""
    lead = _MARKER if marked else ""
    prefix = f"{lead}{format_timecode(paragraph.start, with_hour=with_hour)} "
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
