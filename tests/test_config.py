"""Tests for defaults, config file parsing and command line precedence."""

from pathlib import Path

import pytest

from lecture_scribe.config import Config, ConfigError, load_config, merge_cli


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_defaults_apply_without_a_file(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")

    assert config.source is None
    assert config.audio.device == "auto"
    assert config.audio.sample_rate == 16000
    assert config.asr.model == "large-v3"
    assert config.asr.gpu is False
    assert config.output.paragraph_gap == pytest.approx(1.2)


def test_file_overrides_defaults(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        [audio]
        sample_rate = 48000

        [asr]
        gpu = true
        """,
    )

    config = load_config(path)

    assert config.source == path
    assert config.audio.sample_rate == 48000
    assert config.asr.gpu is True
    # Untouched keys keep their default.
    assert config.audio.device == "auto"
    assert config.asr.model == "large-v3"


def test_paths_expand_the_home_shortcut(tmp_path: Path) -> None:
    path = _write(tmp_path, '[output]\ndir = "~/somewhere"\n')

    config = load_config(path)

    assert config.output.dir == Path.home() / "somewhere"


def test_integer_is_accepted_where_a_float_is_expected(tmp_path: Path) -> None:
    path = _write(tmp_path, "[output]\nparagraph_gap = 2\n")

    assert load_config(path).output.paragraph_gap == pytest.approx(2.0)


def test_cli_overrides_the_file() -> None:
    config = Config()

    merged = merge_cli(config, device="HDMI", model="medium")

    assert merged.audio.device == "HDMI"
    assert merged.asr.model == "medium"
    # Absent flags change nothing, and the input is left alone.
    assert merge_cli(config).audio.device == "auto"
    assert config.audio.device == "auto"


def test_unknown_key_is_rejected_with_a_hint(tmp_path: Path) -> None:
    path = _write(tmp_path, "[audio]\nsample_rte = 16000\n")

    with pytest.raises(ConfigError, match="sample_rate"):
        load_config(path)


def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, '[audoi]\ndevice = "auto"\n')

    with pytest.raises(ConfigError, match="unknown key 'audoi'"):
        load_config(path)


def test_wrong_type_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, '[audio]\nsample_rate = "16000"\n')

    with pytest.raises(ConfigError, match=r"audio\.sample_rate must be an integer"):
        load_config(path)


def test_boolean_does_not_pass_as_a_number(tmp_path: Path) -> None:
    path = _write(tmp_path, "[audio]\nsample_rate = true\n")

    with pytest.raises(ConfigError, match="must be an integer"):
        load_config(path)


def test_broken_toml_is_reported_as_such(tmp_path: Path) -> None:
    path = _write(tmp_path, "[audio\n")

    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path)


def test_section_must_be_a_table(tmp_path: Path) -> None:
    path = _write(tmp_path, 'audio = "auto"\n')

    with pytest.raises(ConfigError, match=r"\[audio\] must be a table"):
        load_config(path)


def test_upload_is_off_by_default(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing.toml").upload.enabled is False


def test_upload_section_is_read(tmp_path: Path) -> None:
    path = _write(tmp_path, '[upload]\nremote = "gdrive:Lectures"\ntimeout = 30\n')

    upload = load_config(path).upload

    assert upload.enabled is True
    assert upload.remote == "gdrive:Lectures"
    assert upload.timeout == pytest.approx(30.0)


def test_upload_remote_without_a_colon_is_rejected(tmp_path: Path) -> None:
    # rclone would read "gdrive" as a local directory and copy into it.
    path = _write(tmp_path, '[upload]\nremote = "gdrive"\n')

    with pytest.raises(ConfigError, match="<remote>:<folder>"):
        load_config(path)


def test_upload_timeout_must_be_positive(tmp_path: Path) -> None:
    path = _write(tmp_path, '[upload]\nremote = "gdrive:x"\ntimeout = 0\n')

    with pytest.raises(ConfigError, match="positive"):
        load_config(path)
