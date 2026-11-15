---
title: Overview
order: 0
---

# Overview

**prefkit** is a tool for working with *preference data*. A preference pair is two
responses to the same prompt, plus a label saying which response is better. Models
are trained and evaluated on this kind of data. Reward models, which score how good a
response is, are fitted from many such pairs.

prefkit does four things:

1. **Ingests** public and private preference datasets into one canonical schema,
   described by a short YAML manifest per dataset.
2. **Runs ingests as background jobs**, with live progress in the browser and a
   transaction that rolls back a failed run, so no partial data is left behind.
3. **Chooses compute** (an NVIDIA GPU, Apple Silicon, or CPU) for model work, without
   the calling code knowing which one it is.
4. **Plans to route labels** to the cheapest source that is accurate enough
   (human, LLM judge, or code execution), and to run a game-style interface for
   annotators. These are later milestones; see the roadmap in `docs/04-jobs.md`.

## Vocabulary used throughout

- **Canonical schema**: the one shape prefkit uses internally: a prompt, two responses,
  and a comparison. See `prefkit/canonical.py`.
- **Manifest**: the YAML file that maps one dataset's columns onto the canonical schema.
- **Quarantine**: a row that cannot be mapped is set aside with its reason, instead of
  stopping the run. Too many quarantined rows stop the run.
- **Gold item**: a comparison with a known correct answer, used to score annotators.
- **Eager mode**: Celery runs tasks inside the calling process. This is the default
  for debugging, so breakpoints work.

## Design principles

- **Modularity through registries.** New dataset layouts and new readers are added
  with a decorator (`@extractor`, `@source_reader`, `@backend`). The core code does not change.
- **Fail early, fail loudly.** Manifests are validated when loaded. Unknown YAML keys
  are errors, not warnings. A wrong mapping aborts within a few hundred rows.
- **Reproducible.** Every id is a content hash, and the A/B display order comes from
  the row's content. Re-running an ingest gives identical data.
- **Idempotent retries.** A redelivered or retried job converges to the same rows.

See also: `01-install.md` to get it running, `05-debugging.md` to step through it in VS Code.
