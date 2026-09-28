"""Tests for turning segments into the readable transcript."""

from collections.abc import Sequence

from lecture_scribe.format_text import (
    Passage,
    format_timecode,
    read_transcript,
    render_transcript,
)
from lecture_scribe.transcribe import Segment


def _render(
    segments: list[Segment],
    *,
    audio_duration: float = 4.0,
    paragraph_gap: float = 1.2,
    paragraph_target: float = 0.0,
    paragraph_max: float = 0.0,
    marks: Sequence[float] = (),
) -> str:
    """Render with the header these tests do not care about filled in.

    The length cap is off unless a test asks for it, so that a test about
    pauses is not quietly answering a question about paragraph length.
    """
    return render_transcript(
        segments,
        title="lecture",
        model="large-v3",
        language="ko",
        audio_duration=audio_duration,
        paragraph_gap=paragraph_gap,
        paragraph_target=paragraph_target,
        paragraph_max=paragraph_max,
        marks=marks,
    )


def test_format_timecode_drops_the_hour_under_one_hour() -> None:
    assert format_timecode(47) == "[00:47]"
    assert format_timecode(90) == "[01:30]"


def test_format_timecode_shows_the_hour_past_one_hour() -> None:
    assert format_timecode(3725) == "[1:02:05]"


def test_format_timecode_can_be_asked_for_the_hour_under_one() -> None:
    assert format_timecode(47, with_hour=True) == "[0:00:47]"


def _continuous(count: int, *, sentences_at: Sequence[int] = ()) -> list[Segment]:
    """Ten second segments running back to back, with no pause anywhere.

    This is what continuous speech looks like coming out of the decoder: on a
    measured lecture 92 of 97 gaps between segments were zero.
    """
    return [
        Segment(
            start=index * 10.0,
            end=index * 10.0 + 10.0,
            text=f"piece {index}" + ("." if index in sentences_at else ""),
            avg_logprob=-0.1,
        )
        for index in range(count)
    ]


def _paragraphs(transcript: str) -> list[str]:
    """The transcript's paragraphs, header dropped."""
    return [block.strip() for block in transcript.split("\n\n")[1:] if block.strip()]


def test_a_paragraph_past_the_target_breaks_at_the_next_sentence() -> None:
    segments = _continuous(10, sentences_at=[4, 7])
    transcript = _render(
        segments, audio_duration=100.0, paragraph_target=25.0, paragraph_max=1000.0
    )

    paragraphs = _paragraphs(transcript)
    assert len(paragraphs) == 3
    # Both breaks waited for a sentence rather than landing mid-thought.
    assert paragraphs[0].endswith("piece 4.")
    assert paragraphs[1].endswith("piece 7.")


def test_a_paragraph_that_reaches_no_sentence_breaks_at_the_maximum() -> None:
    segments = _continuous(12)
    transcript = _render(
        segments, audio_duration=120.0, paragraph_target=25.0, paragraph_max=45.0
    )

    paragraphs = _paragraphs(transcript)
    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("[00:00] ")
    assert paragraphs[1].startswith("[00:50] ")
    assert paragraphs[2].startswith("[01:40] ")


def test_a_paragraph_under_the_target_is_left_whole() -> None:
    segments = _continuous(4, sentences_at=[1])
    transcript = _render(
        segments, audio_duration=40.0, paragraph_target=90.0, paragraph_max=180.0
    )

    assert len(_paragraphs(transcript)) == 1


def test_the_length_cap_is_off_at_zero() -> None:
    segments = _continuous(20, sentences_at=range(20))
    transcript = _render(
        segments, audio_duration=200.0, paragraph_target=0.0, paragraph_max=0.0
    )

    assert len(_paragraphs(transcript)) == 1


def test_a_transcript_past_an_hour_gives_every_timecode_the_hour() -> None:
    segments = [
        Segment(start=10.0, end=11.0, text="early", avg_logprob=-0.1),
        Segment(start=3700.0, end=3701.0, text="late", avg_logprob=-0.1),
    ]
    transcript = _render(segments, audio_duration=3800.0)

    paragraphs = _paragraphs(transcript)
    assert paragraphs[0].startswith("[0:00:10] ")
    assert paragraphs[1].startswith("[1:01:40] ")


def test_a_transcript_under_an_hour_keeps_the_short_timecode() -> None:
    segments = [Segment(start=10.0, end=11.0, text="early", avg_logprob=-0.1)]
    transcript = _render(segments, audio_duration=100.0)

    assert _paragraphs(transcript)[0].startswith("[00:10] ")


def test_a_segment_past_the_hour_widens_a_shorter_recording_too() -> None:
    # The VAD can put a segment a shade past the audio's own length, and one
    # timecode two columns wider than the rest is the thing being avoided.
    segments = [
        Segment(start=10.0, end=11.0, text="early", avg_logprob=-0.1),
        Segment(start=3600.5, end=3601.0, text="late", avg_logprob=-0.1),
    ]
    transcript = _render(segments, audio_duration=3599.0)

    widths = {len(paragraph.split(" ")[0]) for paragraph in _paragraphs(transcript)}
    assert widths == {len("[0:00:10]")}


def test_render_transcript_of_no_segments_says_so() -> None:
    transcript = _render(
        [],
        audio_duration=12.0,
    )
    assert transcript == "lecture -- large-v3 (ko), 0:00:12\n\n(no speech recognised)\n"


def test_render_transcript_starts_a_new_paragraph_after_a_long_pause() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=3.0, end=4.0, text="second", avg_logprob=-0.1),
    ]
    transcript = _render(
        segments,
        audio_duration=4.0,
    )
    assert transcript == (
        "lecture -- large-v3 (ko), 0:00:04\n\n[00:00] first\n\n[00:03] second\n"
    )


def test_render_transcript_keeps_a_short_pause_in_one_paragraph() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=1.5, end=2.0, text="second", avg_logprob=-0.1),
    ]
    transcript = _render(
        segments,
        audio_duration=2.0,
    )
    assert transcript == ("lecture -- large-v3 (ko), 0:00:02\n\n[00:00] first second\n")


def test_render_transcript_marks_a_flagged_moment() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=3.0, end=4.0, text="second", avg_logprob=-0.1),
    ]
    transcript = _render(
        segments,
        audio_duration=4.0,
        marks=[3.5],
    )
    assert "[00:00] first" in transcript
    assert ">>> [00:03] second" in transcript


def test_a_mark_pressed_in_a_pause_belongs_to_the_paragraph_before_it() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=3.0, end=4.0, text="second", avg_logprob=-0.1),
    ]
    # 2.0 falls in the pause that ended the first paragraph -- which is where
    # a listener reacting to what was just said presses the key.
    transcript = _render(
        segments,
        audio_duration=4.0,
        marks=[2.0],
    )
    assert ">>> [00:00] first" in transcript
    assert ">>> [00:03]" not in transcript


def test_a_mark_pressed_after_the_last_word_belongs_to_the_last_paragraph() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=3.0, end=4.0, text="second", avg_logprob=-0.1),
    ]
    transcript = _render(
        segments,
        audio_duration=20.0,
        marks=[9.0],
    )
    assert ">>> [00:03] second" in transcript


def test_a_mark_pressed_before_the_first_word_is_not_dropped() -> None:
    segments = [Segment(start=5.0, end=6.0, text="first", avg_logprob=-0.1)]
    transcript = _render(
        segments,
        audio_duration=6.0,
        marks=[0.5],
    )
    assert ">>> [00:05] first" in transcript


def test_two_marks_in_one_paragraph_prefix_it_once() -> None:
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=3.0, end=4.0, text="second", avg_logprob=-0.1),
    ]
    transcript = _render(
        segments,
        audio_duration=4.0,
        marks=[3.2, 3.8],
    )
    assert transcript.count(">>>") == 1


def test_render_transcript_wraps_long_paragraphs() -> None:
    text = ("word " * 20).strip()
    segments = [Segment(start=0.0, end=1.0, text=text, avg_logprob=-0.1)]

    transcript = _render(
        segments,
        audio_duration=1.0,
    )

    body = transcript.split("\n\n", 1)[1].rstrip("\n")
    lines = body.splitlines()
    assert len(lines) > 1
    assert lines[0].startswith("[00:00] ")
    assert all(line.startswith(" " * len("[00:00] ")) for line in lines[1:])


def test_read_transcript_gives_back_what_render_wrote() -> None:
    long = ("넷째 " * 30).strip()
    segments = [
        Segment(start=0.0, end=1.0, text="first", avg_logprob=-0.1),
        Segment(start=5.0, end=6.0, text=long, avg_logprob=-0.1),
    ]

    header, passages = read_transcript(
        _render(segments, audio_duration=6.0, marks=[5.5])
    )

    assert header == "lecture -- large-v3 (ko), 0:00:06"
    assert passages == [
        Passage(timecode="00:00", text="first", marked=False),
        Passage(timecode="00:05", text=long, marked=True),
    ]


def test_read_transcript_keeps_the_hour_of_a_long_recording() -> None:
    segments = [Segment(start=3700.0, end=3701.0, text="late", avg_logprob=-0.1)]

    _header, passages = read_transcript(_render(segments, audio_duration=3701.0))

    assert passages == [Passage(timecode="1:01:40", text="late", marked=False)]


def test_read_transcript_keeps_a_block_without_a_timecode() -> None:
    header, passages = read_transcript(_render([], audio_duration=6.0))

    assert header == "lecture -- large-v3 (ko), 0:00:06"
    assert passages == [
        Passage(timecode="", text="(no speech recognised)", marked=False)
    ]


def test_read_transcript_of_text_with_no_header_starts_with_a_paragraph() -> None:
    header, passages = read_transcript("[00:01] only\n")

    assert header == ""
    assert passages == [Passage(timecode="00:01", text="only", marked=False)]


def test_read_transcript_of_nothing_is_empty() -> None:
    assert read_transcript("") == ("", [])
