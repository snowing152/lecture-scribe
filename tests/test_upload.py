"""Tests for copying transcripts to a remote through rclone."""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from lecture_scribe.config import UploadConfig, load_config
from lecture_scribe.upload import (
    Upload,
    announce,
    announce_command,
    push_transcript,
    remote_path,
    upload_status,
)

_CONFIG = UploadConfig(remote="gdrive:Lectures", timeout=5.0)


def _completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


@pytest.fixture
def rclone(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Pretend rclone is installed and record every command run."""
    calls: list[list[str]] = []
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/rclone")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return _completed()

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_remote_path_mirrors_the_course_folders(tmp_path: Path) -> None:
    transcript = (
        tmp_path / "Computer_Network" / "2026-09-28_컴넷" / "2026-09-28_컴넷.txt"
    )

    destination = remote_path(transcript, tmp_path, "gdrive:Lectures")

    assert destination == (
        "gdrive:Lectures/Computer_Network/2026-09-28_컴넷/2026-09-28_컴넷.txt"
    )


def test_remote_path_into_the_remote_root_adds_no_slash(tmp_path: Path) -> None:
    transcript = tmp_path / "os" / "os.txt"

    assert remote_path(transcript, tmp_path, "gdrive:") == "gdrive:os/os.txt"


def test_remote_path_tolerates_a_trailing_slash(tmp_path: Path) -> None:
    transcript = tmp_path / "os" / "os.txt"

    assert remote_path(transcript, tmp_path, "gdrive:Lectures/") == (
        "gdrive:Lectures/os/os.txt"
    )


def test_a_transcript_outside_the_root_keeps_its_own_folder(tmp_path: Path) -> None:
    transcript = tmp_path / "elsewhere" / "deep" / "lecture" / "lecture.txt"

    destination = remote_path(transcript, tmp_path / "lectures", "gdrive:Lectures")

    assert destination == "gdrive:Lectures/lecture/lecture.txt"


def test_push_runs_copyto_with_the_mirrored_destination(
    tmp_path: Path, rclone: list[list[str]]
) -> None:
    transcript = tmp_path / "os" / "os.txt"

    upload = push_transcript(transcript, tmp_path, _CONFIG)

    assert upload.ok
    assert upload.destination == "gdrive:Lectures/os/os.txt"
    assert rclone == [
        ["/usr/bin/rclone", "copyto", str(transcript), "gdrive:Lectures/os/os.txt"]
    ]


def test_push_without_rclone_says_how_to_install_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    upload = push_transcript(tmp_path / "os" / "os.txt", tmp_path, _CONFIG)

    assert not upload.ok
    assert upload.error is not None
    assert "rclone.org/install" in upload.error


def test_a_failed_push_reports_rclones_last_line_and_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "rclone")

    def fail(command: list[str], **_kwargs: object) -> None:
        stderr = 'NOTICE: something\nERROR : couldn\'t find remote "gdrive"\n\n'
        raise subprocess.CalledProcessError(1, command, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", fail)

    upload = push_transcript(tmp_path / "os" / "os.txt", tmp_path, _CONFIG)

    assert upload.error is not None
    assert upload.error.startswith('ERROR : couldn\'t find remote "gdrive"')
    assert "rclone copyto" in upload.error


def test_a_failed_push_without_stderr_names_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "rclone")

    def fail(command: list[str], **_kwargs: object) -> None:
        raise subprocess.CalledProcessError(7, command, stderr="")

    monkeypatch.setattr(subprocess, "run", fail)

    upload = push_transcript(tmp_path / "os" / "os.txt", tmp_path, _CONFIG)

    assert upload.error is not None
    assert "exited with 7" in upload.error


def test_a_push_that_hangs_is_given_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "rclone")

    def hang(command: list[str], timeout: float, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(subprocess, "run", hang)

    upload = push_transcript(tmp_path / "os" / "os.txt", tmp_path, _CONFIG)

    assert upload.error is not None
    assert "gave up after 5 s" in upload.error


def test_status_without_rclone_is_fine_when_upload_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    status = upload_status(UploadConfig())

    assert status.rclone is None
    assert status.problem is None


def test_status_without_rclone_is_a_problem_when_upload_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    assert upload_status(_CONFIG).problem is not None


def test_status_finds_the_configured_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "rclone")
    outputs = {
        "version": "rclone v1.75.1\n- os/version: x\n",
        "listremotes": "gdrive:\n",
    }

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(outputs[command[1]])

    monkeypatch.setattr(subprocess, "run", run)

    status = upload_status(_CONFIG)

    assert status.rclone == "rclone v1.75.1"
    assert status.remote_found is True
    assert status.problem is None


def test_status_reports_a_missing_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "rclone")
    # "gdrive2:" must not count as "gdrive:".
    outputs = {"version": "rclone v1.75.1\n", "listremotes": "gdrive2:\nbox:\n"}

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _completed(outputs[command[1]])

    monkeypatch.setattr(subprocess, "run", run)

    status = upload_status(_CONFIG)

    assert status.remote_found is False
    assert status.problem is not None
    assert "rclone config" in status.problem


def test_announce_names_where_the_transcript_went() -> None:
    upload = Upload(Path("os.txt"), "gdrive:Lectures/os/os.txt")

    command = announce_command(Path("/usr/bin/notify-send"), upload)

    assert command[0] == "/usr/bin/notify-send"
    assert command[-2] == "Transcript uploaded"
    assert command[-1] == "gdrive:Lectures/os/os.txt"


def test_a_failed_upload_is_not_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("notify-send must not run")

    monkeypatch.setattr(shutil, "which", lambda _name: "notify-send")
    monkeypatch.setattr(subprocess, "run", refuse)

    announce(Upload(Path("os.txt"), "gdrive:os.txt", error="offline"))


def test_announce_gives_up_on_a_desktop_that_never_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hang(command: list[str], timeout: float, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, timeout)

    monkeypatch.setattr(shutil, "which", lambda _name: "notify-send")
    monkeypatch.setattr(subprocess, "run", hang)

    announce(Upload(Path("os.txt"), "gdrive:os.txt"))


@pytest.mark.manual
def test_a_real_upload_arrives(tmp_path: Path) -> None:
    """Needs rclone and an [upload] section in ./config.toml.

    Leaves ``_scribe-test/upload-check.txt`` in the remote to look at in the
    browser; delete it there by hand.
    """
    config = load_config()
    if not config.upload.enabled:
        pytest.skip("no [upload] section in ./config.toml")
    transcript = tmp_path / "_scribe-test" / "upload-check.txt"
    transcript.parent.mkdir()
    transcript.write_text(f"한국어 업로드 확인 {datetime.now()}\n", encoding="utf-8")

    upload = push_transcript(transcript, tmp_path, config.upload)

    assert upload.ok, upload.error
