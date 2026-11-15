---
title: Datasets and manifests
order: 2
---

# Datasets and manifests

A **dataset** in prefkit is a collection of preference pairs. Public datasets use
different field names. One uses `chosen` and `rejected`; another uses `response_a` and
`response_b`. A **manifest** states the mapping once, so the rest of prefkit never sees
those differences.

## The manifest, field by field

Every manifest is a YAML file in `prefkit/datasets/manifests/`. The file name must match
the `name` field.

| Key | Meaning |
|---|---|
| `name` | Lowercase identifier. Used as the dataset's primary key and in URLs. |
| `version` | Integer. Increase it when the mapping changes. |
| `source.type` | `hf` for a Hugging Face Hub dataset, `local` for a file on disk. |
| `source.location` | Hub id (for example `Anthropic/hh-rlhf`), or a path relative to the project root. |
| `source.split` | Required for `hf`, for example `train`. |
| `card` | Link to the dataset card. Must start with `https://`. |
| `license` | The license from the dataset card. Check it before use. |
| `mapping.prompt_strategy` | `field` or `common_prefix` (see below). |
| `mapping.winner_field` / `winner_fixed` | Exactly one. A column with the label, or one label for every row. |
| `mapping.winner_map` | Translates raw labels such as `1`, `2`, `tie` into `a`, `b`, `tie`. |
| `mapping.shuffle_positions` | `true` shows responses in a content-determined order, which removes position bias. |
| `gold.kind` | `none`, `field` (a column marks gold items) or `execution` (run unit tests). |
| `max_quarantine_rate` | Share of rows allowed to fail before the run aborts. Default 1%. |

Unknown keys are errors. A misspelt key such as `winner_feild` fails when the manifest
loads, not silently later.

## The two prompt strategies

**`field`**: the prompt is stored in one column. Use it for datasets where each row
already has a separate prompt, like `demo_local`.

**`common_prefix`**: each response is a full transcript, and both share the leading
turns. HH-RLHF works this way. The shared turns become the prompt, and the first
differing turn starts each response. A row is quarantined if the transcripts share no
turns, or if the first difference is not an assistant turn.

Worked example, from HH-RLHF:

```
chosen:   "\n\nHuman: How do I boil water?\n\nAssistant: Use a kettle."
rejected: "\n\nHuman: How do I boil water?\n\nAssistant: No idea."
```

The shared turn `Human: How do I boil water?` becomes the prompt. `Use a kettle.`
and `No idea.` become the two responses. The label is `a` because `chosen` is always the
preferred response.

## Adding a dataset

1. Find the dataset and read its card. Record the license.
2. Look at a few rows, for example with `python -c "from datasets import load_dataset; print(next(iter(load_dataset('org/name', split='train', streaming=True))))"`.
3. Write `prefkit/datasets/manifests/<name>.yaml`.
4. Run `python manage.py sync_datasets`, then `python manage.py ingest <name>`.
5. Read the quarantine reasons. If one reason dominates, the mapping is probably wrong.

No Python code changes are needed unless the row layout is new. A new layout is one
function with the `@extractor` decorator in `prefkit/datasets/ingest.py`.

## Dataset links

These are well-known public datasets. Open each card and check its license before use.

- **Anthropic HH-RLHF**: https://huggingface.co/datasets/Anthropic/hh-rlhf (the `hh_rlhf` manifest)
- **Stanford SHP**: https://huggingface.co/datasets/stanfordnlp/SHP
- **OpenAssistant oasst2**: https://huggingface.co/datasets/OpenAssistant/oasst2
- **UltraFeedback**: https://huggingface.co/datasets/openbmb/UltraFeedback
- **HumanEval** (164 Python problems with tests): https://huggingface.co/datasets/openai/openai_humaneval
- **MBPP** (about 1,000 Python problems with tests): https://huggingface.co/datasets/google-research-datasets/mbpp

To download one for offline use:

```bash
pip install -U "huggingface_hub[cli]"
huggingface-cli download Anthropic/hh-rlhf --repo-type dataset --local-dir data/hh-rlhf
```

Then point a `local` manifest at the files. Note that the `hh_rlhf` manifest reads from
the Hub. It downloads through `datasets` at ingest time, so it needs network access the
first time.

## Limits

The manifest checks structure, not truth. It cannot tell that a license is wrong or that
a column exists in the file. The first ingest of any new dataset is the real check, and
the quarantine reasons show what did not match.
