"""Speech recognition of a recorded WAV file.

Step 0 covers readiness reporting only, which is what ``scribe doctor`` needs:
whether the model weights are already on disk and whether a CUDA device is
visible. Neither check touches the network. Decoding lands here in step 2.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Standard faster-whisper model names live under this Hugging Face account.
_REPO_DIR_TEMPLATE = "models--Systran--faster-whisper-{name}"


@dataclass(frozen=True, slots=True)
class ModelStatus:
    """Whether a model is ready to run offline.

    Attributes:
        name: Model name as configured.
        cached: Whether the weights are already on disk.
        location: Directory the weights are expected in.
    """

    name: str
    cached: bool
    location: Path


@dataclass(frozen=True, slots=True)
class GpuStatus:
    """Whether CUDA is usable for decoding.

    Attributes:
        available: Whether at least one CUDA device was found.
        device_count: Number of CUDA devices visible to CTranslate2.
        detail: One line description for the doctor report.
    """

    available: bool
    device_count: int
    detail: str


def compute_type(gpu: bool) -> str:
    """Pick the numeric precision for the decoder.

    Full float16 large-v3 needs roughly 10 GB of VRAM, which most laptop GPUs
    do not have; int8_float16 fits comfortably and costs little accuracy.

    Args:
        gpu: Whether the model runs on CUDA.

    Returns:
        A CTranslate2 compute type name.
    """
    return "int8_float16" if gpu else "int8"


def hub_cache_dir() -> Path:
    """Locate the Hugging Face cache without importing huggingface_hub.

    Returns:
        The directory downloaded models are stored in, whether or not it
        exists yet.
    """
    hub = os.environ.get("HF_HUB_CACHE")
    if hub:
        return Path(hub)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    cache_root = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_root) if cache_root else Path.home() / ".cache"
    return base / "huggingface" / "hub"


def model_status(name: str) -> ModelStatus:
    """Report whether model weights are already downloaded.

    Only the standard model names follow the Hugging Face naming pattern, so
    an unusual name may be reported as missing even when it is cached; the
    first real run downloads or reuses it correctly either way.

    Args:
        name: Model name such as ``large-v3``, or a path to a local model
            directory.

    Returns:
        Where the weights are expected and whether they are there.
    """
    local = Path(name).expanduser()
    if local.is_dir():
        return ModelStatus(
            name=name, cached=(local / "model.bin").is_file(), location=local
        )

    location = hub_cache_dir() / _REPO_DIR_TEMPLATE.format(name=name)
    cached = any(location.glob("snapshots/*/model.bin"))
    return ModelStatus(name=name, cached=cached, location=location)


def gpu_status() -> GpuStatus:
    """Report whether CTranslate2 can see a CUDA device.

    Returns:
        The device count plus a line explaining it, including the reason when
        no device is usable.
    """
    # ctranslate2 ships with faster-whisper; importing it lazily keeps doctor
    # useful on a machine where only the recording half is installed.
    try:
        import ctranslate2
    except ImportError as error:
        return GpuStatus(False, 0, f"unknown, ctranslate2 not importable ({error})")

    try:
        count = int(ctranslate2.get_cuda_device_count())
    except RuntimeError as error:
        return GpuStatus(False, 0, f"unavailable ({error})")

    if count == 0:
        return GpuStatus(False, 0, "no CUDA device, decoding runs on the CPU")
    return GpuStatus(
        True,
        count,
        f"{count} CUDA device(s) visible "
        "(cuBLAS and cuDNN are loaded only when asr.gpu = true)",
    )
