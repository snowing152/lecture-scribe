"""Tests for the parts of transcribe.py that do not need a real model."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from lecture_scribe.transcribe import (
    Decoding,
    TranscriptionError,
    compute_type,
    hub_cache_dir,
    model_status,
    transcribe,
)


def test_compute_type_matches_the_device() -> None:
    assert compute_type(gpu=False) == "int8"
    assert compute_type(gpu=True) == "int8_float16"


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


class _BrokenModel:
    """Stand-in for a model that fails to decode."""

    def transcribe(self, audio: str, **kwargs: object) -> None:  # noqa: ARG002
        raise RuntimeError("decoder exploded")


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


def test_transcribe_wraps_decoder_failures(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    wav.touch()

    with pytest.raises(TranscriptionError, match="decoder exploded"):
        transcribe(_BrokenModel(), wav, language="ko", beam_size=5)


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
