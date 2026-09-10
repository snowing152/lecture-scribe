"""Tests for the parts of transcribe.py that do not need a real model."""

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from lecture_scribe import transcribe as transcribe_module
from lecture_scribe.transcribe import (
    Decoding,
    TranscriptionError,
    compute_type,
    gpu_status,
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
