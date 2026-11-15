---
title: Compute and devices
order: 3
---

# Compute and devices

Model work (running a judge model over many response pairs) needs hardware. prefkit
supports three kinds:

| Kind | Hardware | Memory | Notes |
|---|---|---|---|
| `cuda` | NVIDIA GPU, e.g. RTX 4060 | Dedicated VRAM, 8 GB on that card | Only kind that supports 4-bit quantization here |
| `mps` | Apple Silicon | Shared with system RAM | Automatic on Apple Silicon Macs |
| `cpu` | Any processor | System RAM | Always available, slow for models |

## Why detection is a registry

Hardware differs across machines, and the rest of the code should not care. Each device
family registers a **detector** with the `@backend` decorator in
`prefkit/compute/devices.py`. A detector returns a `DeviceInfo` if its hardware works,
or `None` if it does not. Callers ask `select_device()` and get the most preferred
device available.

A detector must not raise when a driver is missing. A machine without a GPU is normal,
not an error. Detection itself is guarded, so one broken driver cannot hide the others.

## Choosing a device

```python
from prefkit.compute.devices import select_device

device = select_device()          # "auto": most preferred available
device = select_device("cpu")     # explicit: fails clearly if absent
```

The choice can also be fixed through the `PREFKIT_DEVICE` environment variable (in
`.env`). An explicit request is honoured even when a faster device exists. That is
useful for debugging: set `cpu` to rule out GPU-specific bugs.

Run `python manage.py devices` to see what is visible.

## Quantization in one paragraph

A model's **weights** are numbers it has learned. A typical model stores each in 16
bits. **4-bit quantization** stores each in 4 bits, a quarter of the memory, with a
small loss of accuracy. A 3-billion-parameter model is about 6 GB at 16 bits and about
2 GB at 4 bits, which is why it can fit on an 8 GB card with room for batches. The
loader uses 4-bit only on CUDA, because the `bitsandbytes` library is built for NVIDIA.

## Running work on a GPU

Use `judged_on_gpu` in `prefkit/compute/loader.py` for batches that must run on CUDA. It
fails at the entry point if no GPU is present, and it frees GPU memory afterward, even
when the batch raised an error.

Use one worker process per GPU. Each process holds its own copy of the model in
memory, so two workers on one 8 GB card will run out of memory. The worker command in
`04-jobs.md` uses `--pool=solo` for this reason.

## A PC on your network

A second machine can join the same job queue over a private network such as Tailscale.
Keep the database and Redis on the control machine, bound to the private address and
password-protected. Never expose them to the public internet. Pin the same git commit
on both machines, so both apply the same mapping.

## Limits

Your judgments will reflect the model you run. A 3-billion-parameter judge is weaker
than a frontier model. Report that alongside any result, and measure the judge against
human labels before trusting it (see the roadmap in `04-jobs.md`).
