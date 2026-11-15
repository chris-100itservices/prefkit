---
title: Glossary
order: 6
---

# Glossary

**Batch.** A group of rows written to the database together, for efficiency.

**Broker.** The message store between the web server and the workers. Redis here.

**Canonical schema.** The one internal shape for prompts, responses and comparisons.

**Celery.** A Python library for running tasks in background worker processes.

**CUDA.** NVIDIA's platform for running code on GPUs. The `cuda` device kind.

**Comparison.** One judgment: which of two responses to the same prompt is better.

**Content hash.** A fingerprint of data, from a function such as SHA-256. Identical
content always gives the same hash, so ids derived from it are stable.

**Dataset card.** The documentation page for a dataset. It records the license and
known problems.

**Eager mode.** Celery's setting that runs tasks in the calling process instead of
sending them to a broker. Used for debugging.

**Extractor.** A function registered with `@extractor` that reads prompt and responses
from one row layout.

**Gold item.** A comparison with a known correct answer. Used to score annotators.

**HH-RLHF.** Anthropic's public dataset of helpfulness and harmlessness preference pairs.

**Idempotent.** Doing an operation twice has the same effect as doing it once.

**Manifest.** The YAML file that maps one dataset onto the canonical schema.

**Migration.** A generated file that tells the database how to change its tables.

**MPS.** Apple's GPU interface for PyTorch, on Apple Silicon. The `mps` device kind.

**Preference pair.** Two responses to one prompt, with a label saying which is preferred.

**Quantization.** Storing model weights in fewer bits, to save memory.

**Quarantine.** Setting aside a row that cannot be mapped, with its reason, so the run continues.

**Queue.** A list of pending jobs, waiting for a worker.

**Reward model.** A model trained to score how good a response is, from preference pairs.

**Transaction.** A group of database changes that succeed or fail together.

**VRAM.** Video memory: the memory on a GPU card, where model weights are held.
