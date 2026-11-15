"""Device selection logic, with hardware faked. Runs on any machine."""

import pytest

from prefkit.compute import devices
from prefkit.compute.devices import DeviceInfo, DeviceUnavailable, select_device

CUDA = DeviceInfo(kind="cuda", name="RTX 4060", memory_gb=8.0)
CPU = DeviceInfo(kind="cpu", name="CPU", memory_gb=None)


@pytest.fixture
def fake_hardware(monkeypatch):
    def install(*infos: DeviceInfo):
        monkeypatch.setattr(devices, "available_devices", lambda: list(infos))

    return install


def test_auto_prefers_first_available(fake_hardware):
    fake_hardware(CUDA, CPU)
    assert select_device("auto") == CUDA


def test_explicit_cpu_is_honoured_even_with_gpu(fake_hardware):
    fake_hardware(CUDA, CPU)
    assert select_device("cpu") == CPU


def test_missing_device_fails_clearly(fake_hardware):
    fake_hardware(CPU)
    with pytest.raises(DeviceUnavailable, match="not available"):
        select_device("cuda")


def test_environment_variable_is_used(fake_hardware, monkeypatch):
    fake_hardware(CUDA, CPU)
    monkeypatch.setenv("PREFKIT_DEVICE", "cpu")
    assert select_device().kind == "cpu"


def test_only_cuda_supports_4bit():
    assert CUDA.supports_4bit
    assert not DeviceInfo(kind="mps", name="m", memory_gb=None).supports_4bit


def test_real_detection_never_raises():
    """On any machine, including one with no GPU or no torch, detection returns a list."""
    found = devices.available_devices()
    assert isinstance(found, list)
    assert any(d.kind == "cpu" for d in found)
