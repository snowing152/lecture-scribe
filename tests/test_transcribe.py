"""Tests for the parts of transcribe.py that do not need a real model."""

import errno
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from lecture_scribe import transcribe as transcribe_module
from lecture_scribe.transcribe import (
    Decoding,
    Transcription,
    TranscriptionError,
    check_language,
    compute_type,
    earlier_transcript,
    gpu_status,
    hub_cache_dir,
    load_model,
    model_status,
    pending_recordings,
    shared_transcripts,
    transcribe,
    transcript_path,
    write_transcript,
)


@dataclass
class _Languages:
    """Stands in for a loaded model, as far as its languages go."""

    supported_languages: list[str]


def test_a_language_the_model_knows_passes() -> None:
    check_language(_Languages(["en", "ja", "ko"]), "ko")


def test_an_unknown_language_code_is_refused_with_the_likely_ones() -> None:
    with pytest.raises(TranscriptionError, match="Korean is ko"):
        check_language(_Languages(["en", "ja", "ko"]), "kr")


def test_an_english_only_model_refuses_korean() -> None:
    # faster-whisper itself would switch to English and only log it.
    with pytest.raises(TranscriptionError, match="English only"):
        check_language(_Languages(["en"]), "ko")


def test_compute_type_matches_the_device() -> None:
    assert compute_type(gpu=False) == "int8"
    assert compute_type(gpu=True) == "int8_float16"


def test_transcript_is_named_after_the_folder_as_it_is_now(tmp_path: Path) -> None:
    folder = tmp_path / "2026-09-14_컴넷-2-2"

    assert transcript_path(folder / "audio.wav") == folder / "2026-09-14_컴넷-2-2.txt"


def test_recordings_in_one_folder_share_a_transcript(tmp_path: Path) -> None:
    first = tmp_path / "Downloads" / "a.wav"
    second = tmp_path / "Downloads" / "b.wav"
    alone = tmp_path / "2026-09-14_os" / "2026-09-14_os.wav"

    clashes = shared_transcripts([first, alone, second])

    assert clashes == {(tmp_path / "Downloads" / "Downloads.txt"): [first, second]}


def test_one_folder_spelled_two_ways_is_still_one_folder(tmp_path: Path) -> None:
    (tmp_path / "os").mkdir()
    plain = tmp_path / "os" / "a.wav"
    detour = tmp_path / "os" / ".." / "os" / "b.wav"

    assert list(shared_transcripts([plain, detour]).values()) == [[plain, detour]]


def test_recordings_in_separate_folders_share_nothing(tmp_path: Path) -> None:
    wavs = [tmp_path / "one" / "one.wav", tmp_path / "two" / "two.wav"]

    assert shared_transcripts(wavs) == {}


def test_no_earlier_transcript_beside_a_fresh_recording(tmp_path: Path) -> None:
    assert earlier_transcript(tmp_path / "audio.wav") is None


def test_earlier_transcript_is_found_under_the_current_name_first(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "Row-Reduction"
    folder.mkdir()
    for name in ("Row-Reduction.txt", "2026-09-13_linear.txt", "transcript.txt"):
        (folder / name).write_text("", encoding="utf-8")

    found = earlier_transcript(folder / "2026-09-13_linear.wav")

    assert found == folder / "Row-Reduction.txt"


def test_earlier_transcript_survives_a_renamed_folder(tmp_path: Path) -> None:
    folder = tmp_path / "Row-Reduction"
    folder.mkdir()
    (folder / "2026-09-13_linear.txt").write_text("", encoding="utf-8")

    found = earlier_transcript(folder / "2026-09-13_linear.wav")

    assert found == folder / "2026-09-13_linear.txt"


def test_earlier_transcript_under_the_old_fixed_name_is_found(tmp_path: Path) -> None:
    (tmp_path / "transcript.txt").write_text("", encoding="utf-8")

    assert earlier_transcript(tmp_path / "audio.wav") == tmp_path / "transcript.txt"


def test_uncached_model_is_reported_as_such(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))

    status = model_status("large-v3")

    assert status.cached is False
    assert status.location == tmp_path / "models--Systran--faster-whisper-large-v3"


def test_cached_model_is_found_by_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snapshot = (
        tmp_path / "models--Systran--faster-whisper-large-v3" / "snapshots" / "abc123"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").touch()

    assert model_status("large-v3").cached is True


def test_local_model_directory_is_read_directly(tmp_path: Path) -> None:
    (tmp_path / "model.bin").touch()

    status = model_status(str(tmp_path))

    assert status.cached is True
    assert status.location == tmp_path


class _FakeWhisperModel:
    """Stand-in for faster_whisper.WhisperModel that keeps its arguments."""

    seen: ClassVar[dict[str, object]] = {}
    failure: ClassVar[Exception | None] = None

    def __init__(self, name: str, **kwargs: object) -> None:
        if _FakeWhisperModel.failure is not None:
            raise _FakeWhisperModel.failure
        _FakeWhisperModel.seen = {"name": name, **kwargs}


def _fake_faster_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeWhisperModel.seen = {}
    _FakeWhisperModel.failure = None
    monkeypatch.setattr(
        transcribe_module,
        "_import_faster_whisper",
        lambda: SimpleNamespace(WhisperModel=_FakeWhisperModel),
    )


def _cache_model(root: Path) -> Path:
    snapshot = root / "models--Systran--faster-whisper-large-v3" / "snapshots" / "x"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").touch()
    return root / "models--Systran--faster-whisper-large-v3"


def test_a_cached_model_is_loaded_without_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _cache_model(tmp_path)
    _fake_faster_whisper(monkeypatch)

    load_model("large-v3", gpu=False)

    assert _FakeWhisperModel.seen["local_files_only"] is True


def test_a_model_not_on_disk_is_allowed_to_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _fake_faster_whisper(monkeypatch)

    load_model("large-v3", gpu=False)

    assert _FakeWhisperModel.seen["local_files_only"] is False


def test_a_cached_model_that_will_not_load_suggests_downloading_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    folder = _cache_model(tmp_path)
    _fake_faster_whisper(monkeypatch)
    _FakeWhisperModel.failure = OSError("truncated")

    with pytest.raises(TranscriptionError) as caught:
        load_model("large-v3", gpu=False)

    assert str(folder) in str(caught.value)
    assert "incomplete" in str(caught.value)


def test_a_failed_download_does_not_blame_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _fake_faster_whisper(monkeypatch)
    _FakeWhisperModel.failure = OSError("offline")

    with pytest.raises(TranscriptionError) as caught:
        load_model("large-v3", gpu=False)

    assert "incomplete" not in str(caught.value)


def test_hub_cache_dir_prefers_the_most_specific_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "/xdg")

    assert hub_cache_dir() == Path("/xdg/huggingface/hub")

    monkeypatch.setenv("HF_HOME", "/hf-home")
    assert hub_cache_dir() == Path("/hf-home/hub")

    monkeypatch.setenv("HF_HUB_CACHE", "/exact")
    assert hub_cache_dir() == Path("/exact")


def _pretend_devices(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    """Report a given number of CUDA devices, whatever this machine has."""
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: count)


def test_a_machine_with_no_cuda_device_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pretend_devices(monkeypatch, 0)

    status = gpu_status()

    assert status.available is False
    assert "no CUDA device" in status.detail


def test_a_device_without_cublas_is_not_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The case worth catching in the doctor report: the driver shows a card,
    # so everything looks fine until the decoder asks for the library twenty
    # minutes into a lecture.
    _pretend_devices(monkeypatch, 1)
    monkeypatch.setattr(transcribe_module, "_cublas_available", lambda: False)

    status = gpu_status()

    assert status.available is False
    assert status.device_count == 1
    assert "cannot be loaded" in status.detail


def test_a_device_with_cublas_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _pretend_devices(monkeypatch, 1)
    monkeypatch.setattr(transcribe_module, "_cublas_available", lambda: True)

    status = gpu_status()

    assert status.available is True
    assert status.device_count == 1


@dataclass(frozen=True, slots=True)
class _FakeRawSegment:
    """Stand-in for the object faster_whisper yields per segment."""

    start: float
    end: float
    text: str
    avg_logprob: float


@dataclass(frozen=True, slots=True)
class _FakeInfo:
    duration: float


class _FakeModel:
    """Stand-in for WhisperModel: records the call and returns fixed segments."""

    def __init__(self, segments: list[_FakeRawSegment], duration: float) -> None:
        self._segments = segments
        self._duration = duration
        self.calls: list[dict[str, object]] = []

    def transcribe(
        self, audio: str, **kwargs: object
    ) -> tuple[list[_FakeRawSegment], _FakeInfo]:
        self.calls.append({"audio": audio, **kwargs})
        return self._segments, _FakeInfo(duration=self._duration)


class _GeneratorModel:
    """Stand-in whose segments arrive from a generator, as the real one's do.

    Counting what was pulled from it is how a test can tell that recognition
    really gave up rather than decoding the whole file and discarding the end.
    """

    def __init__(self, segments: list[_FakeRawSegment], duration: float) -> None:
        self._segments = segments
        self._duration = duration
        self.yielded = 0

    def transcribe(
        self,
        audio: str,  # noqa: ARG002
        **kwargs: object,  # noqa: ARG002
    ) -> tuple[Iterator[_FakeRawSegment], _FakeInfo]:
        return self._pull(), _FakeInfo(duration=self._duration)

    def _pull(self) -> Iterator[_FakeRawSegment]:
        for segment in self._segments:
            self.yielded += 1
            yield segment


class _BrokenModel:
    """Stand-in for a model that fails to decode."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def transcribe(self, audio: str, **kwargs: object) -> None:  # noqa: ARG002
        raise self._error


def test_transcribe_materializes_segments_and_strips_whitespace(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _FakeModel(
        segments=[_FakeRawSegment(0.0, 1.5, "  안녕하세요  ", -0.2)],
        duration=90.0,
    )

    result = transcribe(model, wav, language="ko", beam_size=5)

    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment.text == "안녕하세요"
    assert segment.start == pytest.approx(0.0)
    assert segment.end == pytest.approx(1.5)
    assert segment.avg_logprob == pytest.approx(-0.2)
    assert result.audio_duration == pytest.approx(90.0)


def test_hotwords_reach_the_decoder_and_empty_ones_do_not(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _FakeModel(segments=[], duration=0.0)

    transcribe(model, wav, language="ko", beam_size=5, hotwords="  TCP 혼잡 제어 ")
    transcribe(model, wav, language="ko", beam_size=5)
    transcribe(model, wav, language="ko", beam_size=5, hotwords="   ")

    assert model.calls[0]["hotwords"] == "TCP 혼잡 제어"
    assert model.calls[1]["hotwords"] is None
    assert model.calls[2]["hotwords"] is None


def test_transcribe_passes_the_fixed_decoding_settings(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _FakeModel(segments=[], duration=0.0)

    transcribe(model, wav, language="ko", beam_size=5)

    call = model.calls[0]
    assert call["language"] == "ko"
    assert call["beam_size"] == 5
    assert call["vad_filter"] is True
    assert call["vad_parameters"] == {"min_silence_duration_ms": 500}
    assert call["condition_on_previous_text"] is False


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("decoder exploded"),
        OSError("decoder exploded"),
        # How faster-whisper rejects `language = "kr"`, and the base of PyAV's
        # InvalidDataError for audio it cannot decode.
        ValueError("decoder exploded"),
    ],
)
def test_transcribe_wraps_decoder_failures(tmp_path: Path, error: Exception) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()

    with pytest.raises(TranscriptionError, match="decoder exploded"):
        transcribe(_BrokenModel(error), wav, language="ko", beam_size=5)


def test_empty_recording_yields_no_segments(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _FakeModel(segments=[], duration=0.0)

    result = transcribe(model, wav, language="ko", beam_size=5)

    assert result.segments == []
    assert result.audio_duration == pytest.approx(0.0)


def test_progress_is_reported_once_per_segment(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _FakeModel(
        segments=[
            _FakeRawSegment(0.0, 12.0, "하나", -0.2),
            _FakeRawSegment(12.5, 40.0, "둘", -0.2),
            _FakeRawSegment(41.0, 90.0, "셋", -0.2),
        ],
        duration=90.0,
    )
    seen: list[Decoding] = []

    transcribe(model, wav, language="ko", beam_size=5, on_progress=seen.append)

    assert [step.segments for step in seen] == [1, 2, 3]
    assert [step.position for step in seen] == [12.0, 40.0, 90.0]
    assert [step.text for step in seen] == ["하나", "둘", "셋"]
    # The length is known before the first segment, which is the whole point.
    assert {step.audio_duration for step in seen} == {90.0}
    assert seen[-1].fraction == pytest.approx(1.0)


def test_progress_is_optional(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _FakeModel(segments=[_FakeRawSegment(0.0, 1.0, "네", -0.2)], duration=1.0)

    assert len(transcribe(model, wav, language="ko", beam_size=5).segments) == 1


def test_fraction_is_the_share_of_the_recording_done() -> None:
    step = Decoding(
        position=30.0, audio_duration=120.0, elapsed=7.0, segments=4, text="x"
    )

    assert step.fraction == pytest.approx(0.25)


def test_time_left_assumes_the_pace_so_far_holds() -> None:
    # A quarter done in 7 seconds leaves three quarters, 21 seconds.
    step = Decoding(
        position=30.0, audio_duration=120.0, elapsed=7.0, segments=4, text="x"
    )

    assert step.remaining == pytest.approx(21.0)


def test_speed_is_audio_recognised_per_second_of_decoding() -> None:
    step = Decoding(
        position=30.0, audio_duration=120.0, elapsed=7.5, segments=4, text="x"
    )

    assert step.speed == pytest.approx(4.0)


def test_speed_is_zero_before_any_time_has_passed() -> None:
    step = Decoding(
        position=0.0, audio_duration=120.0, elapsed=0.0, segments=1, text=""
    )

    assert step.speed == 0.0


def test_no_time_left_is_guessed_before_any_audio_is_done() -> None:
    step = Decoding(
        position=0.0, audio_duration=120.0, elapsed=3.0, segments=1, text=""
    )

    assert step.remaining == 0.0


def test_decode_ratio_compares_decoding_with_the_audio() -> None:
    # The 81 minute lecture decoded in 3.9 minutes on the GPU.
    result = Transcription(segments=[], audio_duration=4860.0, decode_seconds=234.0)

    assert result.decode_ratio == pytest.approx(0.048, abs=1e-3)


def test_decode_ratio_of_a_recording_with_no_length_is_zero() -> None:
    result = Transcription(segments=[], audio_duration=0.0, decode_seconds=0.2)

    assert result.decode_ratio == 0.0


def test_fraction_of_a_recording_with_no_length_is_finished() -> None:
    # An empty file would otherwise divide by zero on the first segment.
    step = Decoding(position=0.0, audio_duration=0.0, elapsed=0.1, segments=1, text="x")

    assert step.fraction == pytest.approx(1.0)


def test_fraction_never_runs_past_the_end() -> None:
    # VAD restores timestamps, and a segment can end a shade past the
    # duration the decoder reported.
    step = Decoding(
        position=91.0, audio_duration=90.0, elapsed=20.0, segments=9, text="x"
    )

    assert step.fraction == pytest.approx(1.0)


def test_a_stop_ends_recognition_after_the_segment_in_hand(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _GeneratorModel(
        segments=[
            _FakeRawSegment(index * 10.0, index * 10.0 + 9.0, f"line {index}", -0.2)
            for index in range(5)
        ],
        duration=300.0,
    )
    stop = threading.Event()

    def stop_after_two(decoding: Decoding) -> None:
        if decoding.segments == 2:
            stop.set()

    result = transcribe(
        model,
        wav,
        language="ko",
        beam_size=5,
        on_progress=stop_after_two,
        stop=stop,
    )

    assert len(result.segments) == 2
    # The remaining three were never pulled, which is the work being saved.
    assert model.yielded == 2
    assert result.interrupted is not None
    # The second segment ends at 19 seconds, which is how far it got.
    assert "0:19" in result.interrupted


def test_recognition_that_runs_to_the_end_is_not_interrupted(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()
    model = _GeneratorModel(
        segments=[_FakeRawSegment(0.0, 1.0, "only", -0.2)],
        duration=1.0,
    )

    result = transcribe(model, wav, language="ko", beam_size=5, stop=threading.Event())

    assert result.interrupted is None
    assert model.yielded == 1


def test_a_transcript_is_written_whole(tmp_path: Path) -> None:
    target = tmp_path / "os.txt"

    write_transcript(target, "os -- large-v3 (ko), 0:00:02\n\n[00:00] 네.\n")

    assert target.read_text(encoding="utf-8").endswith("[00:00] 네.\n")
    assert list(tmp_path.iterdir()) == [target]


def test_a_failed_write_leaves_the_earlier_transcript_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "os.txt"
    target.write_text("the complete transcript\n", encoding="utf-8")
    real_write = Path.write_text

    def fill_the_disk(path: Path, text: str, **kwargs: str) -> int:
        real_write(path, text[: len(text) // 2], **kwargs)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Path, "write_text", fill_the_disk)

    with pytest.raises(OSError, match="No space left"):
        write_transcript(target, "a new transcript that never fits\n")

    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "the complete transcript\n"
    assert list(tmp_path.iterdir()) == [target]


def _wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def test_pending_recordings_finds_the_ones_without_a_transcript(
    tmp_path: Path,
) -> None:
    done = _wav(tmp_path / "BigData" / "2026-10-28_a" / "2026-10-28_a.wav")
    (done.parent / "2026-10-28_a.txt").touch()
    todo = _wav(tmp_path / "CN" / "2026-11-02_b" / "2026-11-02_b.wav")

    assert pending_recordings(tmp_path) == [todo]


def test_a_transcript_under_an_older_name_counts_as_done(tmp_path: Path) -> None:
    renamed = _wav(tmp_path / "title" / "2026-09-10_os.wav")
    (renamed.parent / "2026-09-10_os.txt").touch()
    old = _wav(tmp_path / "older" / "audio.wav")
    (old.parent / "transcript.txt").touch()

    assert pending_recordings(tmp_path) == []


def test_pending_recordings_come_back_in_path_order(tmp_path: Path) -> None:
    second = _wav(tmp_path / "b" / "b.wav")
    first = _wav(tmp_path / "a" / "a.wav")

    assert pending_recordings(tmp_path) == [first, second]
