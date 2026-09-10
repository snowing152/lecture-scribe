"""Tests for device choice, folder naming and how a recording ends."""

import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
import soundfile as sf

from lecture_scribe import audio_capture
from lecture_scribe.audio_capture import (
    BLOCK_FRAMES,
    AudioDeviceError,
    LoopbackDevice,
    Recording,
    course_slug,
    inspect_recording,
    lecture_dir,
    resolve_device,
)

SPEAKER = LoopbackDevice(name="Ryzen Speaker", id="alsa_output.pci", is_default=True)
HDMI_1 = LoopbackDevice(name="HDMI Output 1", id="alsa_output.hdmi1", is_default=False)
HDMI_2 = LoopbackDevice(name="HDMI Output 2", id="alsa_output.hdmi2", is_default=False)
DEVICES = [SPEAKER, HDMI_1, HDMI_2]


def test_auto_picks_the_system_default() -> None:
    assert resolve_device(DEVICES, "auto") == SPEAKER


def test_substring_picks_one_device_ignoring_case() -> None:
    assert resolve_device(DEVICES, "hdmi output 2") == HDMI_2


def test_no_match_lists_what_is_available() -> None:
    with pytest.raises(AudioDeviceError, match="HDMI Output 1"):
        resolve_device(DEVICES, "bluetooth")


def test_ambiguous_substring_is_refused() -> None:
    with pytest.raises(AudioDeviceError, match="matches several"):
        resolve_device(DEVICES, "HDMI")


def test_empty_device_list_is_refused() -> None:
    with pytest.raises(AudioDeviceError, match="no output devices"):
        resolve_device([], "auto")


def test_auto_without_a_default_is_refused() -> None:
    with pytest.raises(AudioDeviceError, match="no default output"):
        resolve_device([HDMI_1, HDMI_2], "auto")


def test_folder_is_named_after_date_and_course(tmp_path: Path) -> None:
    when = datetime(2026, 9, 9, 10, 4)

    assert lecture_dir(tmp_path, "os", when) == tmp_path / "2026-09-09_os"


def test_second_lecture_the_same_day_gets_its_own_folder(tmp_path: Path) -> None:
    when = datetime(2026, 9, 9, 10, 4)
    (tmp_path / "2026-09-09_os").mkdir()

    assert lecture_dir(tmp_path, "os", when) == tmp_path / "2026-09-09_os-2"

    (tmp_path / "2026-09-09_os-2").mkdir()
    assert lecture_dir(tmp_path, "os", when) == tmp_path / "2026-09-09_os-3"


def test_course_names_survive_korean_and_spaces() -> None:
    assert course_slug("운영체제") == "운영체제"
    assert course_slug("operating systems") == "operating-systems"
    assert course_slug("  os  ") == "os"


def test_course_names_lose_characters_windows_forbids() -> None:
    assert course_slug("os/network") == "osnetwork"
    assert course_slug('os: "intro"') == "os-intro"


def test_course_name_without_usable_characters_is_refused() -> None:
    with pytest.raises(ValueError, match="usable"):
        course_slug(" / ")


class _FakeRecorder:
    """Serves a fixed number of blocks, then stops the recording or fails.

    Stands in for soundcard's recorder so the capture loop can be exercised
    with no audio hardware present.
    """

    def __init__(
        self,
        blocks: int,
        stop: threading.Event,
        failure: Exception | None = None,
        levels: list[float] | None = None,
    ) -> None:
        self._blocks = blocks
        self._stop = stop
        self._failure = failure
        # One amplitude per block, the last one holding for whatever follows.
        # Silence arriving part way through is the case worth playing back.
        self._levels = levels
        self._served = 0

    def __enter__(self) -> "_FakeRecorder":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def record(self, numframes: int) -> npt.NDArray[np.float32]:
        if self._served >= self._blocks and self._failure is not None:
            raise self._failure
        self._served += 1
        if self._failure is None and self._served >= self._blocks:
            self._stop.set()
        level = 0.25
        if self._levels is not None:
            level = self._levels[min(self._served - 1, len(self._levels) - 1)]
        return np.full((numframes, 2), level, dtype=np.float32)


class _FakeMicrophone:
    """Hands out one prepared recorder, whatever it is asked for."""

    def __init__(self, recorder: _FakeRecorder) -> None:
        self._recorder = recorder

    def recorder(self, **_kwargs: object) -> _FakeRecorder:
        return self._recorder


def _record(
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    blocks: int,
    failure: Exception | None = None,
) -> Recording:
    stop = threading.Event()
    microphone = _FakeMicrophone(_FakeRecorder(blocks, stop, failure))
    monkeypatch.setattr(
        audio_capture, "_loopback_microphone", lambda _device: microphone
    )
    return audio_capture.record_loopback(SPEAKER, target, sample_rate=16000, stop=stop)


_BLOCK_SECONDS = BLOCK_FRAMES / 16000
"""How much audio one faked block stands for, at the rate the tests record."""


def _warnings_from(
    target: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    levels: list[float],
    blocks: int,
) -> list[str]:
    """Record the given per-block levels and collect the warnings raised.

    The two silence windows are shortened to a couple of blocks each, so a
    lecture going quiet can be played out in microseconds rather than in the
    minutes the real windows cover.
    """
    monkeypatch.setattr(audio_capture, "_SILENCE_CHECK_SECONDS", 2 * _BLOCK_SECONDS)
    monkeypatch.setattr(audio_capture, "_SILENCE_WINDOW_SECONDS", 4 * _BLOCK_SECONDS)

    stop = threading.Event()
    microphone = _FakeMicrophone(_FakeRecorder(blocks, stop, levels=levels))
    monkeypatch.setattr(
        audio_capture, "_loopback_microphone", lambda _device: microphone
    )

    warnings: list[str] = []

    def collect(progress: audio_capture.Progress) -> None:
        if progress.warning is not None:
            warnings.append(progress.warning)

    audio_capture.record_loopback(
        SPEAKER,
        target,
        sample_rate=16000,
        stop=stop,
        silence_rms=0.01,
        on_progress=collect,
    )
    return warnings


def test_a_silent_start_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = _warnings_from(
        tmp_path / "audio.wav", monkeypatch, levels=[0.0], blocks=6
    )

    assert len(warnings) == 1
    assert "first" in warnings[0]
    assert SPEAKER.name in warnings[0]


def test_a_recording_that_goes_quiet_part_way_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two loud blocks pass the opening check, then the sound stops arriving --
    # an output device that changed under a lecture already running.
    warnings = _warnings_from(
        tmp_path / "audio.wav",
        monkeypatch,
        levels=[0.5, 0.5] + [0.0] * 8,
        blocks=10,
    )

    assert len(warnings) == 1
    assert "for the last" in warnings[0]
    assert SPEAKER.name in warnings[0]


def test_the_sound_coming_back_re_arms_the_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = _warnings_from(
        tmp_path / "audio.wav",
        monkeypatch,
        levels=[0.5] * 2 + [0.0] * 4 + [0.5] * 4 + [0.0] * 4,
        blocks=14,
    )

    assert len(warnings) == 2


def test_a_recording_that_stays_loud_is_never_warned_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = _warnings_from(
        tmp_path / "audio.wav", monkeypatch, levels=[0.5], blocks=12
    )

    assert warnings == []


def test_a_recording_that_reaches_the_stop_event_is_not_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = _record(tmp_path / "audio.wav", monkeypatch, blocks=3)

    assert recording.interrupted is None
    assert recording.duration == pytest.approx(3 * BLOCK_FRAMES / 16000)


def test_a_disk_giving_out_keeps_everything_recorded_so_far(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "audio.wav"

    recording = _record(
        target, monkeypatch, blocks=3, failure=OSError(28, "No space left on device")
    )

    # The lecture is what cannot be recreated, so a dying disk must not cost
    # the part that already reached it.
    assert recording.interrupted is not None
    assert "No space left on device" in recording.interrupted
    assert recording.duration == pytest.approx(3 * BLOCK_FRAMES / 16000)
    assert sf.info(target).frames == 3 * BLOCK_FRAMES


def test_a_device_lost_mid_recording_is_reported_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = _record(
        tmp_path / "audio.wav",
        monkeypatch,
        blocks=2,
        failure=RuntimeError("stream has been terminated"),
    )

    assert recording.interrupted is not None
    assert "stream has been terminated" in recording.interrupted


def test_a_failure_before_the_first_block_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing was recorded, so there is no partial lecture worth returning.
    with pytest.raises(AudioDeviceError, match="stream has been terminated"):
        _record(
            tmp_path / "audio.wav",
            monkeypatch,
            blocks=0,
            failure=RuntimeError("stream has been terminated"),
        )


def test_a_file_that_cannot_be_created_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AudioDeviceError, match="cannot record to"):
        _record(tmp_path / "missing" / "audio.wav", monkeypatch, blocks=3)


def test_an_existing_recording_reports_its_length_and_size(tmp_path: Path) -> None:
    wav = tmp_path / "audio.wav"
    sf.write(wav, np.zeros(16000 * 3, dtype=np.int16), 16000, subtype="PCM_16")

    existing = inspect_recording(wav)

    assert existing.path == wav
    assert existing.duration == pytest.approx(3.0)
    assert existing.size_bytes == wav.stat().st_size


def test_a_recording_that_is_not_there_is_refused(tmp_path: Path) -> None:
    with pytest.raises(AudioDeviceError, match="cannot read"):
        inspect_recording(tmp_path / "gone.wav")


def test_a_file_that_is_not_audio_is_refused(tmp_path: Path) -> None:
    not_audio = tmp_path / "transcript.txt"
    not_audio.write_text("안녕하세요", encoding="utf-8")

    with pytest.raises(AudioDeviceError, match="cannot read"):
        inspect_recording(not_audio)
