"""Tests for listing and searching the lectures on disk."""

import os
from pathlib import Path

import numpy as np
import soundfile as sf

from lecture_scribe.library import list_lectures, search

_HEADER = "{name} -- {model} (ko), 0:01:05\n\n"


def _lecture(
    root: Path,
    name: str,
    *,
    mtime: float,
    transcript: str | None = None,
    seconds: int = 2,
) -> Path:
    folder = root / name
    folder.mkdir()
    wav = folder / f"{name}.wav"
    sf.write(wav, np.zeros(16_000 * seconds, dtype="float32"), 16_000)
    os.utime(wav, (mtime, mtime))
    if transcript is not None:
        (folder / f"{name}.txt").write_text(transcript, encoding="utf-8")
    return wav


def _transcript(name: str, body: str, model: str = "large-v3") -> str:
    return _HEADER.format(name=name, model=model) + body + "\n"


def test_lectures_come_newest_first_with_ties_by_name(tmp_path: Path) -> None:
    _lecture(tmp_path, "b", mtime=1000)
    _lecture(tmp_path, "a", mtime=1000)
    _lecture(tmp_path, "old", mtime=10)
    _lecture(tmp_path, "new", mtime=2000)

    assert [x.name for x in list_lectures(tmp_path)] == ["new", "a", "b", "old"]


def test_a_missing_root_gives_an_empty_list(tmp_path: Path) -> None:
    assert list_lectures(tmp_path / "nope") == []


def test_a_transcript_is_found_and_its_header_read(tmp_path: Path) -> None:
    _lecture(
        tmp_path,
        "net",
        mtime=1,
        transcript=_transcript("net", "[00:01] hello"),
    )
    _lecture(tmp_path, "raw", mtime=0, seconds=3)

    done, raw = list_lectures(tmp_path)
    assert done.transcript == tmp_path / "net" / "net.txt"
    assert done.model == "large-v3"
    assert done.seconds == 65.0
    assert raw.transcript is None
    assert raw.model is None
    assert raw.seconds == 3.0


def test_an_unparseable_header_falls_back_to_the_wav(tmp_path: Path) -> None:
    _lecture(tmp_path, "odd", mtime=1, transcript="just some text\n", seconds=4)

    (lecture,) = list_lectures(tmp_path)
    assert lecture.transcript is not None
    assert lecture.model is None
    assert lecture.seconds == 4.0


def test_a_damaged_marks_file_gives_none_and_good_ones_are_counted(
    tmp_path: Path,
) -> None:
    bad = _lecture(tmp_path, "bad", mtime=2)
    bad.with_suffix(".marks").write_text("1.5\nnot a number\n")
    good = _lecture(tmp_path, "good", mtime=1)
    good.with_suffix(".marks").write_text("1.5\n2.5\n")

    bad_lecture, good_lecture = list_lectures(tmp_path)
    assert bad_lecture.marks is None
    assert good_lecture.marks == 2


def test_a_damaged_wav_does_not_break_the_list(tmp_path: Path) -> None:
    folder = tmp_path / "junk"
    folder.mkdir()
    (folder / "junk.wav").write_bytes(b"\x13\x37" * 100)
    _lecture(tmp_path, "fine", mtime=0)

    by_name = {x.name: x for x in list_lectures(tmp_path)}
    assert by_name["junk"].seconds is None
    assert by_name["fine"].seconds == 2.0


def test_search_matches_the_name_only_without_a_passage(tmp_path: Path) -> None:
    _lecture(tmp_path, "operating-systems", mtime=1)
    _lecture(tmp_path, "networks", mtime=0)

    (hit,) = search(list_lectures(tmp_path), "SYSTEMS")
    assert hit.lecture.name == "operating-systems"
    assert hit.passage is None
    assert hit.timecode is None
    assert hit.excerpt == ""


def test_a_korean_word_matches_through_its_particle(tmp_path: Path) -> None:
    body = "[00:05] 첫 문단입니다.\n\n[01:10] 이 프로토콜의 헤더를 봅니다."
    _lecture(tmp_path, "net", mtime=1, transcript=_transcript("net", body))

    (hit,) = search(list_lectures(tmp_path), "프로토콜")
    assert hit.passage == 1
    assert hit.timecode == "01:10"
    assert "프로토콜의" in hit.excerpt


def test_latin_matching_ignores_case(tmp_path: Path) -> None:
    body = "[00:05] We study TCP congestion control."
    _lecture(tmp_path, "net", mtime=1, transcript=_transcript("net", body))

    (hit,) = search(list_lectures(tmp_path), "tcp")
    assert hit.passage == 0


def test_a_blank_query_returns_every_lecture_in_order(tmp_path: Path) -> None:
    _lecture(tmp_path, "a", mtime=2)
    _lecture(tmp_path, "b", mtime=1)
    lectures = list_lectures(tmp_path)

    hits = search(lectures, "  ")
    assert [h.lecture for h in hits] == lectures
    assert all(h.passage is None for h in hits)


def test_the_first_match_wins_and_the_excerpt_is_short(tmp_path: Path) -> None:
    filler = "가" * 200
    body = f"[00:05] {filler} needle here\n\n[02:00] second needle"
    _lecture(tmp_path, "net", mtime=1, transcript=_transcript("net", body))

    (hit,) = search(list_lectures(tmp_path), "needle")
    assert hit.passage == 0
    assert hit.timecode == "00:05"
    assert "needle" in hit.excerpt
    assert len(hit.excerpt) <= 80


def test_an_unreadable_transcript_still_matches_by_name(tmp_path: Path) -> None:
    wav = _lecture(tmp_path, "net", mtime=1, transcript="x")
    (wav.parent / "net.txt").write_bytes(b"\xff\xfe\x00bad")

    (hit,) = search(list_lectures(tmp_path), "net")
    assert hit.passage is None


def test_a_title_holding_the_separator_still_gives_the_model(tmp_path: Path) -> None:
    # Review finding: a lecture title written by the user may contain " -- ".
    wav = tmp_path / "os -- notes" / "os -- notes.wav"
    wav.parent.mkdir()
    sf.write(wav, np.zeros(1600, dtype=np.float32), 16000)
    wav.with_suffix(".txt").write_text(
        "os -- notes -- large-v3 (ko), 0:50:00\n\n[00:01] text\n", encoding="utf-8"
    )

    (lecture,) = list_lectures(tmp_path)

    assert lecture.model == "large-v3"
    assert lecture.seconds == 3000.0
