"""Tests for how the command line carries a batch through failures."""

from pathlib import Path

import pytest

from lecture_scribe import cli
from lecture_scribe.config import Config
from lecture_scribe.transcribe import Segment, Transcription


def _lecture(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir()
    wav = folder / f"{name}.wav"
    wav.touch()
    return wav


def test_a_file_that_is_not_a_recording_fails_before_the_model_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    junk = tmp_path / "notes.wav"
    junk.write_bytes(b"\x13\x37" * 4096)

    def never(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("nothing readable, so no model should load")

    monkeypatch.setattr(cli, "_transcribe_all", never)

    assert cli._text(Config(), [junk]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_a_transcript_that_cannot_be_written_stops_the_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocked = _lecture(tmp_path, "blocked")
    # A directory where the transcript belongs makes writing it fail.
    (blocked.parent / "blocked.txt").mkdir()
    later = _lecture(tmp_path, "later")
    result = Transcription(
        segments=[Segment(0.0, 2.0, "안녕하세요.", -0.1)],
        audio_duration=2.0,
        decode_seconds=0.1,
    )
    recognised: list[Path] = []

    def recognise(_model: object, wav: Path, **_kwargs: object) -> Transcription:
        recognised.append(wav)
        return result

    monkeypatch.setattr(cli, "load_model", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "transcribe", recognise)
    # The real one would take Ctrl+C away from pytest for the rest of the run.
    monkeypatch.setattr(cli, "_install_stop_handler", lambda *_args, **_kwargs: None)

    code = cli._transcribe_all(Config(), [blocked, later], failures=0)

    # A full disk would fail the next write too, after a whole recognition.
    assert code == 1
    assert "cannot write" in capsys.readouterr().err
    assert recognised == [blocked]
    assert not (later.parent / "later.txt").exists()
