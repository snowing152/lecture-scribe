"""Tests for the parts of transcribe.py that do not need a real model."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from lecture_scribe.transcribe import (
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

    transcribe(model, wav, language="ko", beam_size=5, initial_prompt="문맥 교환")

    call = model.calls[0]
    assert call["language"] == "ko"
    assert call["beam_size"] == 5
    assert call["vad_filter"] is True
    assert call["vad_parameters"] == {"min_silence_duration_ms": 500}
    assert call["condition_on_previous_text"] is False
    assert call["initial_prompt"] == "문맥 교환"


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
