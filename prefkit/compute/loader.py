"""Load a judge model onto the selected device.

Quantization stores weights in fewer bits. 4-bit weights need roughly a quarter of the
memory of 16-bit weights, which is what lets a 3-billion-parameter model fit in the
8 GB of an RTX 4060, at a small cost in quality. The loader uses 4-bit only on CUDA,
and only when bitsandbytes is installed.

Heavy imports (torch, transformers) happen inside functions, so importing this module
on a CPU-only machine costs nothing.
"""

from __future__ import annotations

import functools
import gc
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from prefkit.compute.devices import DeviceInfo, requires_device


@functools.cache
def load_judge(model_id: str, device: DeviceInfo) -> tuple[Any, Any]:
    """Return (model, tokenizer) on the device. Cached per model and device."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    kwargs: dict[str, Any] = {
        "torch_dtype": torch.float32 if device.kind == "cpu" else torch.float16,
        "device_map": {"": device.torch_device},
    }

    if device.supports_4bit and _can_quantize():
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_quant_type="nf4",  # NormalFloat 4-bit, suited to normally distributed weights
        )

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
    model.eval()  # disables dropout, so inference is deterministic
    return model, tokenizer


def _can_quantize() -> bool:
    try:
        import bitsandbytes  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def released_memory() -> Iterator[None]:
    """Free cached accelerator memory after a block, even if it raised."""
    try:
        yield
    finally:
        gc.collect()
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif torch.backends.mps.is_available():
            torch.mps.empty_cache()


def judged_on_gpu(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Require a CUDA GPU, and release its memory when the batch ends."""

    @requires_device("cuda")
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with released_memory():
            return fn(*args, **kwargs)

    return wrapper
