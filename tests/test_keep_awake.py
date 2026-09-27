"""Tests for keeping the desktop from locking and sleeping."""

import shutil
import subprocess
import sys
from pathlib import PurePosixPath

import pytest

from lecture_scribe.keep_awake import (
    Inhibition,
    _explain,
    _notify,
    awake_status,
    keep_awake,
    notify_command,
)


def test_release_runs_once_however_often_it_is_called() -> None:
    calls: list[None] = []
    inhibition = Inhibition(True, "test", release=lambda: calls.append(None))

    inhibition.release()
    inhibition.release()

    assert len(calls) == 1


def test_leaving_the_with_block_releases() -> None:
    calls: list[None] = []
    with Inhibition(True, "test", release=lambda: calls.append(None)) as inhibition:
        assert calls == []

    assert len(calls) == 1
    # A second release after the block, as a front end's cleanup would make.
    inhibition.release()
    assert len(calls) == 1


def test_a_request_never_held_releases_harmlessly() -> None:
    Inhibition(False, "nothing to ask").release()


def test_an_unsupported_platform_goes_ahead_without_holding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    inhibition = keep_awake("recording a lecture")

    assert not inhibition.held
    assert "darwin" in inhibition.detail
    inhibition.release()


def test_an_unsupported_platform_reports_itself_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    status = awake_status()

    assert not status.available
    assert "darwin" in status.detail


@pytest.mark.parametrize(
    "error_name",
    [
        "org.freedesktop.DBus.Error.ServiceUnknown",
        "org.freedesktop.DBus.Error.NameHasNoOwner",
    ],
)
def test_explain_a_desktop_with_no_screensaver_service(error_name: str) -> None:
    assert _explain(error_name, "whatever the bus said") == (
        "nothing on this desktop serves org.freedesktop.ScreenSaver"
    )


def test_explain_passes_any_other_refusal_through() -> None:
    detail = _explain("org.freedesktop.DBus.Error.AccessDenied", "not allowed")
    assert detail == "org.freedesktop.ScreenSaver refused: not allowed"


def test_explain_falls_back_to_the_error_name_without_a_message() -> None:
    detail = _explain("org.example.Error.Odd", "")
    assert detail == "org.freedesktop.ScreenSaver refused: org.example.Error.Odd"


def test_notify_command_names_the_app_and_says_why() -> None:
    command = notify_command(
        PurePosixPath("/usr/bin/notify-send"), "recording a lecture"
    )

    assert command[0] == "/usr/bin/notify-send"
    assert "--app-name=lecture scribe" in command
    assert command[-2:] == ["Screen kept awake", "recording a lecture"]


def test_notify_command_keeps_the_notification_out_of_the_history() -> None:
    assert "--transient" in notify_command(
        PurePosixPath("notify-send"), "recording a lecture"
    )


def test_notify_without_notify_send_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no program to run")

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(subprocess, "run", refuse)

    _notify("recording a lecture")


def test_notify_gives_up_on_a_desktop_that_never_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def hang(command: list[str], **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(command, 2.0)

    monkeypatch.setattr(shutil, "which", lambda _name: "notify-send")
    monkeypatch.setattr(subprocess, "run", hang)

    _notify("recording a lecture")


@pytest.mark.manual
def test_the_desktop_takes_and_gives_back_the_request() -> None:
    """Needs a Linux desktop session serving org.freedesktop.ScreenSaver."""
    assert awake_status().available

    with keep_awake("manual test") as inhibition:
        assert inhibition.held, inhibition.detail
