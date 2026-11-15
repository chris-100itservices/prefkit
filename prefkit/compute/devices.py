"""Detect and describe the compute devices prefkit can run inference on.

Each hardware family registers a detector with the @backend decorator. Other code asks
select_device() for the best available device and never checks hardware directly.
Adding an accelerator means writing one detector.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

log = logging.getLogger(__name__)

Kind = Literal["cuda", "mps", "cpu"]

# Environment variable that forces a choice, e.g. PREFKIT_DEVICE=cpu for debugging.
DEVICE_ENV: Final = "PREFKIT_DEVICE"


class DeviceUnavailable(RuntimeError):
    """The requested device kind is not present on this machine."""


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """What prefkit knows about one device.

    Attributes:
        kind: Hardware family, used for routing and for choosing a dtype.
        name: Human-readable name, for logs and the dashboard.
        memory_gb: Dedicated memory in GB. For CUDA this is VRAM. For MPS (Apple unified
            memory, shared with the system) it is None, because the figure is not fixed.
        index: Device index for multi-GPU machines; 0 for single-GPU systems.
    """

    kind: Kind
    name: str
    memory_gb: float | None
    index: int = 0

    @property
    def torch_device(self) -> str:
        return "cpu" if self.kind == "cpu" else f"{self.kind}:{self.index}"

    @property
    def supports_4bit(self) -> bool:
        # 4-bit loading needs bitsandbytes, which runs only on NVIDIA CUDA.
        return self.kind == "cuda"


Detector = Callable[[], DeviceInfo | None]

# kind -> (priority, detector). Lower priority number is preferred.
_DETECTORS: dict[Kind, tuple[int, Detector]] = {}


def backend(kind: Kind, priority: int) -> Callable[[Detector], Detector]:
    """Register a device detector.

    A detector returns a DeviceInfo when its hardware is usable, or None when it is not.
    It must not raise for a missing driver: a machine without a GPU is normal.
    """

    def register(fn: Detector) -> Detector:
        if kind in _DETECTORS:
            raise RuntimeError(f"backend '{kind}' is already registered")
        _DETECTORS[kind] = (priority, fn)
        return fn

    return register


@functools.cache
def _torch():
    # Imported lazily: a CPU-only install (tests, the web server) need not carry torch.
    import torch

    return torch


@backend("cuda", priority=10)
def _detect_cuda() -> DeviceInfo | None:
    try:
        torch = _torch()
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    props = torch.cuda.get_device_properties(0)
    return DeviceInfo(kind="cuda", name=props.name, memory_gb=round(props.total_memory / 1024**3, 1))


@backend("mps", priority=20)
def _detect_mps() -> DeviceInfo | None:
    try:
        torch = _torch()
    except ImportError:
        return None
    if not torch.backends.mps.is_available():
        return None
    return DeviceInfo(kind="mps", name="Apple Silicon GPU (MPS)", memory_gb=None)


@backend("cpu", priority=100)
def _detect_cpu() -> DeviceInfo:
    return DeviceInfo(kind="cpu", name=f"CPU ({os.cpu_count()} logical cores)", memory_gb=None)


def available_devices() -> list[DeviceInfo]:
    """Every usable device on this machine, most preferred first."""
    found: list[DeviceInfo] = []
    for _, (_, detect) in sorted(_DETECTORS.items(), key=lambda item: item[1][0]):
        try:
            info = detect()
        except Exception:  # deliberate: a broken driver must not hide the other devices
            log.exception("device detector failed")
            continue
        if info is not None:
            found.append(info)
    return found


def select_device(preference: str | None = None) -> DeviceInfo:
    """Choose a device.

    Args:
        preference: "cuda", "mps", "cpu", or "auto". Defaults to the PREFKIT_DEVICE
            environment variable, then to "auto".

    Raises:
        DeviceUnavailable: the explicit preference is not present on this machine.
    """
    wanted = (preference or os.environ.get(DEVICE_ENV, "auto")).lower()
    devices = available_devices()

    if wanted == "auto":
        return devices[0]

    for device in devices:
        if device.kind == wanted:
            return device
    raise DeviceUnavailable(
        f"device '{wanted}' requested but not available; found: " + ", ".join(d.name for d in devices)
    )


def requires_device(kind: Kind) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Refuse to run a function unless a given device kind is present.

    This turns a confusing CUDA error deep inside a model load into one clear message
    at the entry point.
    """

    def decorate(fn: Callable[..., object]) -> Callable[..., object]:
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            if not any(d.kind == kind for d in available_devices()):
                raise DeviceUnavailable(f"{fn.__qualname__} needs a '{kind}' device")
            return fn(*args, **kwargs)

        return wrapper

    return decorate
