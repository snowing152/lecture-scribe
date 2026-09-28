"""Loading and validation of ``config.toml``.

Settings come from three places, each overriding the previous one: the
defaults written down in this module, ``config.toml`` in the current working
directory, and command line arguments. The tool therefore runs with no config
file present at all.

Unknown keys are rejected rather than ignored: a silently swallowed typo would
leave the tool running with a default the user believes they changed.
"""

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from difflib import get_close_matches
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.toml"


class ConfigError(Exception):
    """Raised when ``config.toml`` cannot be parsed or contains bad values."""


@dataclass(frozen=True, slots=True)
class AudioConfig:
    """Sound capture settings.

    Attributes:
        device: Substring of the output device to record from, or ``"auto"``
            to follow whatever the system is currently playing through.
        sample_rate: Capture rate in Hz.
        silence_rms: Blocks with a lower RMS than this count as silence.
    """

    device: str = "auto"
    sample_rate: int = 16000
    silence_rms: float = 0.001


@dataclass(frozen=True, slots=True)
class AsrConfig:
    """Speech recognition settings.

    Attributes:
        model: faster-whisper model name or a path to a local model directory.
        gpu: Whether to run the model on CUDA instead of the CPU.
        language: Spoken language, fixed rather than auto detected.
        beam_size: Beam search width passed to the decoder.
    """

    model: str = "large-v3"
    gpu: bool = False
    language: str = "ko"
    beam_size: int = 5


@dataclass(frozen=True, slots=True)
class OutputConfig:
    """Where results are written and how the transcript is shaped.

    Attributes:
        dir: Root directory holding one folder per lecture.
        paragraph_gap: Pause in seconds that starts a new paragraph.
        paragraph_target: Length in seconds after which a paragraph is ended
            at the next sentence, for a lecturer who leaves no pauses to
            break on.
        paragraph_max: Length in seconds after which a paragraph ends at
            the next segment, sentence or not. Either of the two turns off
            at zero.
    """

    dir: Path = field(default_factory=lambda: Path("~/lectures").expanduser())
    paragraph_gap: float = 1.2
    paragraph_target: float = 90.0
    paragraph_max: float = 180.0


@dataclass(frozen=True, slots=True)
class UploadConfig:
    """Copying finished transcripts to cloud storage through rclone.

    Attributes:
        remote: rclone destination as ``<remote>:<folder>``, such as
            ``"gdrive:Lectures"``. Empty turns upload off.
        timeout: Seconds one upload may take before it is given up.
    """

    remote: str = ""
    timeout: float = 120.0

    @property
    def enabled(self) -> bool:
        """Whether transcripts are uploaded at all."""
        return self.remote != ""


@dataclass(frozen=True, slots=True)
class Config:
    """The complete effective configuration.

    Attributes:
        audio: Capture settings.
        asr: Recognition settings.
        output: Result location and transcript shape.
        upload: Where finished transcripts are copied, if anywhere.
        source: File the settings were read from, or ``None`` for defaults.
    """

    audio: AudioConfig = AudioConfig()
    asr: AsrConfig = AsrConfig()
    output: OutputConfig = OutputConfig()
    upload: UploadConfig = UploadConfig()
    source: Path | None = None


def load_config(path: Path | None = None) -> Config:
    """Read the configuration file, falling back to the built-in defaults.

    Args:
        path: File to read. Defaults to ``config.toml`` in the current working
            directory. A missing file is not an error.

    Returns:
        The parsed configuration.

    Raises:
        ConfigError: The file is not valid TOML, holds an unknown section or
            key, or a value has the wrong type.
    """
    config_path = Path(CONFIG_FILENAME) if path is None else path
    if not config_path.is_file():
        return Config()

    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{config_path} is not valid TOML: {error}") from error
    except OSError as error:
        raise ConfigError(f"cannot read {config_path}: {error}") from error

    sections = ("audio", "asr", "output", "upload")
    _reject_unknown("config.toml", raw, sections)
    return Config(
        audio=_audio(_section(raw, "audio")),
        asr=_asr(_section(raw, "asr")),
        output=_output(_section(raw, "output")),
        upload=_upload(_section(raw, "upload")),
        source=config_path,
    )


def merge_cli(
    config: Config,
    *,
    device: str | None = None,
    model: str | None = None,
) -> Config:
    """Apply command line overrides on top of the file settings.

    Args:
        config: Settings as loaded from ``config.toml``.
        device: Value of ``--device``, or ``None`` when the flag was absent.
        model: Value of ``--model``, or ``None`` when the flag was absent.

    Returns:
        A new configuration; the input is left untouched.
    """
    result = config
    if device is not None:
        result = replace(result, audio=replace(result.audio, device=device))
    if model is not None:
        result = replace(result, asr=replace(result.asr, model=model))
    return result


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    """Return one table of the config file, or an empty mapping when absent."""
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table, got {type(value).__name__}")
    return value


def _audio(raw: Mapping[str, Any]) -> AudioConfig:
    """Build the ``[audio]`` section."""
    _reject_unknown("audio", raw, _names(AudioConfig))
    default = AudioConfig()
    return AudioConfig(
        device=_str("audio", raw, "device", default.device),
        sample_rate=_int("audio", raw, "sample_rate", default.sample_rate),
        silence_rms=_float("audio", raw, "silence_rms", default.silence_rms),
    )


def _asr(raw: Mapping[str, Any]) -> AsrConfig:
    """Build the ``[asr]`` section."""
    _reject_unknown("asr", raw, _names(AsrConfig))
    default = AsrConfig()
    return AsrConfig(
        model=_str("asr", raw, "model", default.model),
        gpu=_bool("asr", raw, "gpu", default.gpu),
        language=_str("asr", raw, "language", default.language),
        beam_size=_int("asr", raw, "beam_size", default.beam_size),
    )


def _output(raw: Mapping[str, Any]) -> OutputConfig:
    """Build the ``[output]`` section."""
    _reject_unknown("output", raw, _names(OutputConfig))
    default = OutputConfig()
    return OutputConfig(
        dir=_path("output", raw, "dir", default.dir),
        paragraph_gap=_float("output", raw, "paragraph_gap", default.paragraph_gap),
        paragraph_target=_float(
            "output", raw, "paragraph_target", default.paragraph_target
        ),
        paragraph_max=_float("output", raw, "paragraph_max", default.paragraph_max),
    )


def _upload(raw: Mapping[str, Any]) -> UploadConfig:
    """Build the ``[upload]`` section."""
    _reject_unknown("upload", raw, _names(UploadConfig))
    default = UploadConfig()
    remote = _str("upload", raw, "remote", default.remote)
    # Without a colon rclone takes the destination for a local path and
    # "uploads" into a folder named after it, reporting success.
    if remote and ":" not in remote:
        raise ConfigError(
            f'upload.remote must look like "<remote>:<folder>", got "{remote}"'
        )
    timeout = _float("upload", raw, "timeout", default.timeout)
    if timeout <= 0:
        raise ConfigError(f"upload.timeout must be positive, got {timeout}")
    return UploadConfig(remote=remote, timeout=timeout)


def _names(cls: type) -> tuple[str, ...]:
    """List the keys a section accepts, taken from its dataclass."""
    return tuple(f.name for f in fields(cls))


def _reject_unknown(where: str, raw: Mapping[str, Any], known: Sequence[str]) -> None:
    """Fail on the first key the tool does not understand.

    Args:
        where: Section name used in the error message.
        raw: Table read from the file.
        known: Accepted keys.

    Raises:
        ConfigError: ``raw`` holds a key outside ``known``.
    """
    for key in raw:
        if key in known:
            continue
        close = get_close_matches(key, known, n=1)
        hint = f", did you mean '{close[0]}'?" if close else ""
        raise ConfigError(f"unknown key '{key}' in {where}{hint}")


def _wrong_type(section: str, key: str, expected: str, value: object) -> ConfigError:
    """Build the error raised when a value has the wrong TOML type."""
    return ConfigError(
        f"{section}.{key} must be {expected}, got {type(value).__name__}"
    )


def _str(section: str, raw: Mapping[str, Any], key: str, default: str) -> str:
    """Read a string value."""
    value = raw.get(key, default)
    if not isinstance(value, str):
        raise _wrong_type(section, key, "a string", value)
    return value


def _bool(section: str, raw: Mapping[str, Any], key: str, default: bool) -> bool:
    """Read a boolean value."""
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise _wrong_type(section, key, "true or false", value)
    return value


def _int(section: str, raw: Mapping[str, Any], key: str, default: int) -> int:
    """Read an integer value."""
    value = raw.get(key, default)
    # bool is a subclass of int, so `gpu = true` must not pass as a number.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _wrong_type(section, key, "an integer", value)
    return value


def _float(section: str, raw: Mapping[str, Any], key: str, default: float) -> float:
    """Read a number, accepting the integer spelling of it."""
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _wrong_type(section, key, "a number", value)
    return float(value)


def _path(section: str, raw: Mapping[str, Any], key: str, default: Path) -> Path:
    """Read a filesystem path, expanding a leading ``~``."""
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise _wrong_type(section, key, "a path string", value)
    return Path(value).expanduser()
