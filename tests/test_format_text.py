"""Tests for turning segments into the readable transcript."""

from lecture_scribe.format_text import format_timecode, render_transcript
from lecture_scribe.transcribe import Segment


def test_format_timecode_drops_the_hour_under_one_hour() -> None:
    assert format_timecode(47) == "[00:47]"
    assert format_timecode(90) == "[01:30]"


def test_format_timecode_shows_the_hour_past_one_hour() -> None:
    assert format_timecode(3725) == "[1:02:05]"


def test_render_transcript_of_no_segments_says_so() -> None:
    transcript = render_transcript(
        [],
        title="lecture",
        model="large-v3",
        language="ko",
        audio_duration=12.0,
        paragraph_gap=1.2,
    )
    assert transcript == "lecture -- large-v3 (ko), 0:00:12\n\n(no speech recognised)\n"


def test_render_transcript_starts_a_new_paragraph_after_a_long_pause() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=3.0, end=4.0, text="second", avg_logprob=-0.1),
    ]
    transcript = render_transcript(
        segments,
        title="lecture",
        model="large-v3",
        language="ko",
        audio_duration=4.0,
        paragraph_gap=1.2,
    )
    assert transcript == (
        "lecture -- large-v3 (ko), 0:00:04\n\n[00:00] first\n\n[00:03] second\n"
    )


def test_render_transcript_keeps_a_short_pause_in_one_paragraph() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=1.5, end=2.0, text="second", avg_logprob=-0.1),
    ]
    transcript = render_transcript(
        segments,
        title="lecture",
        model="large-v3",
        language="ko",
        audio_duration=2.0,
        paragraph_gap=1.2,
    )
    assert transcript == ("lecture -- large-v3 (ko), 0:00:02\n\n[00:00] first second\n")


def test_render_transcript_marks_a_flagged_moment() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=3.0, end=4.0, text="second", avg_logprob=-0.1),
    ]
    transcript = render_transcript(
        segments,
        title="lecture",
        model="large-v3",
        language="ko",
        audio_duration=4.0,
        paragraph_gap=1.2,
        marks=[3.5],
    )
    assert "[00:00] first" in transcript
    assert ">>> [00:03] second" in transcript


def test_render_transcript_wraps_long_paragraphs() -> None:
    text = ("word " * 20).strip()
    segments = [Segment(start=0.0, end=1.0, text=text, avg_logprob=-0.1)]

    transcript = render_transcript(
        segments,
        title="lecture",
        model="large-v3",
        language="ko",
        audio_duration=1.0,
        paragraph_gap=1.2,
    )

    body = transcript.split("\n\n", 1)[1].rstrip("\n")
    lines = body.splitlines()
    assert len(lines) > 1
    assert lines[0].startswith("[00:00] ")
    assert all(line.startswith(" " * len("[00:00] ")) for line in lines[1:])
