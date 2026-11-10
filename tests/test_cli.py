"""Tests for how the command line carries a batch through failures."""

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from lecture_scribe import cli
from lecture_scribe.config import AsrConfig, Config
from lecture_scribe.transcribe import Segment, Transcription


class _Model:
    """Stands in for a loaded multilingual model."""

    supported_languages = ("en", "ja", "ko")


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


@pytest.mark.parametrize(
    ("flags", "language"), [([], "ko"), (["--language", "en"], "en")]
)
def test_the_language_flag_reaches_recognition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    language: str,
) -> None:
    # Away from the checkout's own config.toml, so the default is the default.
    monkeypatch.chdir(tmp_path)
    used: list[Config] = []

    def collect(config: Config, _wavs: list[Path]) -> int:
        used.append(config)
        return 0

    monkeypatch.setattr(cli, "_text", collect)

    assert cli.main(["text", *flags, "lecture.wav"]) == 0
    assert used[0].asr.language == language


def test_recordings_that_would_share_a_transcript_are_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    folder = tmp_path / "Downloads"
    folder.mkdir()
    first, second = folder / "a.wav", folder / "b.wav"
    for wav in (first, second):
        sf.write(wav, np.zeros(1600, dtype=np.float32), 16000)
    own = _lecture(tmp_path, "os")
    sf.write(own, np.zeros(1600, dtype=np.float32), 16000)
    handed: list[list[Path]] = []

    def collect(_config: Config, wavs: list[Path], failures: int) -> int:
        handed.append(wavs)
        return 1 if failures else 0

    monkeypatch.setattr(cli, "_transcribe_all", collect)

    code = cli._text(Config(), [first, own, second])

    # Only the recording with a folder of its own goes on to be recognised.
    assert code == 1
    assert handed == [[own]]
    assert "Downloads.txt" in capsys.readouterr().err


def test_a_file_named_twice_is_transcribed_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    own = _lecture(tmp_path, "os")
    sf.write(own, np.zeros(1600, dtype=np.float32), 16000)
    # The same file reached by a second, longer path.
    again = own.parent / ".." / "os" / own.name
    handed: list[list[Path]] = []

    def collect(_config: Config, wavs: list[Path], failures: int) -> int:
        handed.append(wavs)
        return 1 if failures else 0

    monkeypatch.setattr(cli, "_transcribe_all", collect)

    assert cli._text(Config(), [own, again]) == 0
    assert handed == [[own]]
    assert capsys.readouterr().err == ""


def test_an_unknown_language_fails_once_before_any_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def never(*_args: object, **_kwargs: object) -> Transcription:
        raise AssertionError("no file should be read with a language that fails")

    monkeypatch.setattr(cli, "load_model", lambda *_args, **_kwargs: _Model())
    monkeypatch.setattr(cli, "transcribe", never)
    monkeypatch.setattr(cli, "_install_stop_handler", lambda *_args, **_kwargs: None)
    config = Config(asr=AsrConfig(language="kr"))

    code = cli._transcribe_all(
        config, [_lecture(tmp_path, "os"), _lecture(tmp_path, "db")], failures=0
    )

    assert code == 1
    assert capsys.readouterr().err.count("'kr' is not a language code") == 1


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

    monkeypatch.setattr(cli, "load_model", lambda *_args, **_kwargs: _Model())
    monkeypatch.setattr(cli, "transcribe", recognise)
    # The real one would take Ctrl+C away from pytest for the rest of the run.
    monkeypatch.setattr(cli, "_install_stop_handler", lambda *_args, **_kwargs: None)

    code = cli._transcribe_all(Config(), [blocked, later], failures=0)

    # A full disk would fail the next write too, after a whole recognition.
    assert code == 1
    assert "cannot write" in capsys.readouterr().err
    assert recognised == [blocked]
    assert not (later.parent / "later.txt").exists()


def test_a_damaged_marks_file_fails_before_the_model_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wav = _lecture(tmp_path, "os")
    sf.write(wav, np.zeros(1600, dtype=np.float32), 16000)
    wav.with_suffix(".marks").write_text("1.0\nsoon\n", encoding="utf-8")

    def never(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("the model must not be loaded")

    monkeypatch.setattr(cli, "_transcribe_all", never)

    assert cli._text(Config(), [wav]) == 1
    assert "line 2" in capsys.readouterr().err


def test_marks_reach_the_transcript_written_by_the_command_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wav = _lecture(tmp_path, "os")
    wav.with_suffix(".marks").write_text("4.0\n", encoding="utf-8")
    result = Transcription(
        segments=[
            Segment(0.0, 2.0, "첫 문장.", -0.1),
            Segment(10.0, 12.0, "둘째 문장.", -0.1),
        ],
        audio_duration=12.0,
        decode_seconds=0.1,
    )
    monkeypatch.setattr(cli, "load_model", lambda *_args, **_kwargs: _Model())
    monkeypatch.setattr(cli, "transcribe", lambda *_a, **_k: result)
    monkeypatch.setattr(cli, "_install_stop_handler", lambda *_args, **_kwargs: None)

    assert cli._transcribe_all(Config(), [wav], failures=0) == 0

    text = (wav.parent / "os.txt").read_text(encoding="utf-8")
    assert ">>> [00:00] 첫 문장." in text
    assert ">>> [00:10]" not in text


def test_a_folder_with_nothing_left_succeeds_without_loading_the_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    done = _lecture(tmp_path, "os")
    sf.write(done, np.zeros(1600, dtype=np.float32), 16000)
    (done.parent / "os.txt").write_text("done", encoding="utf-8")

    def never(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("the model must not be loaded")

    monkeypatch.setattr(cli, "_transcribe_all", never)

    assert cli._text(Config(), [tmp_path]) == 0
    out = capsys.readouterr().out
    assert "0 recording(s) without a transcript" in out
    assert "nothing left to transcribe" in out


def test_a_folder_hands_on_only_the_recordings_without_a_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = _lecture(tmp_path, "done")
    todo = _lecture(tmp_path, "todo")
    for wav in (done, todo):
        sf.write(wav, np.zeros(1600, dtype=np.float32), 16000)
    (done.parent / "done.txt").write_text("done", encoding="utf-8")
    handed: list[list[Path]] = []

    def collect(_config: Config, wavs: list[Path], failures: int) -> int:
        handed.append(wavs)
        return 1 if failures else 0

    monkeypatch.setattr(cli, "_transcribe_all", collect)

    assert cli._text(Config(), [tmp_path]) == 0
    assert handed == [[todo]]


def test_a_file_named_outright_is_transcribed_even_with_a_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = _lecture(tmp_path, "done")
    sf.write(done, np.zeros(1600, dtype=np.float32), 16000)
    (done.parent / "done.txt").write_text("done", encoding="utf-8")
    handed: list[list[Path]] = []

    def collect(_config: Config, wavs: list[Path], failures: int) -> int:
        handed.append(wavs)
        return 1 if failures else 0

    monkeypatch.setattr(cli, "_transcribe_all", collect)

    assert cli._text(Config(), [done]) == 0
    assert handed == [[done]]


def test_a_missing_path_is_still_an_error_beside_a_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def never(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("the model must not be loaded")

    monkeypatch.setattr(cli, "_transcribe_all", never)

    assert cli._text(Config(), [tmp_path, tmp_path / "nope.wav"]) == 1
    assert "does not exist" in capsys.readouterr().err
