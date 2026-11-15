# prefkit

Preference-data tooling: ingest preference datasets through YAML manifests, run
ingests as background jobs with live progress, choose local compute (NVIDIA, Apple
Silicon or CPU), and build a manual from the docs.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate                # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
python manage.py makemigrations prefkit  # only needed if you change models
python manage.py migrate
python manage.py sync_datasets
pytest
python manage.py ingest demo_local
python manage.py runserver               # then open http://127.0.0.1:8000/datasets/demo_local/
```

The full walkthrough, with expected output, is in `docs/01-install.md`.

## Debugging in VS Code

Open the folder, select the `.venv` interpreter, and press F5 on a configuration in the
Run and Debug panel. `docs/05-debugging.md` explains how to step through one ingest.

## Commands

| Command | Purpose |
|---|---|
| `python manage.py sync_datasets` | Register every manifest as a dataset. |
| `python manage.py ingest <name>` | Run one ingest in the foreground. |
| `python manage.py devices` | List CUDA, MPS and CPU devices. |
| `python manage.py runserver` | Start the web server. |
| `pytest` | Run the test suite (no network or GPU needed). |
| `ruff check . && ruff format --check .` | Lint and format check. |
| `python scripts/build_docs.py` | Build `build/docs/MANUAL.md` (and PDF if Pandoc is installed). |

## Layout

```
prefkit/
  settings.py, urls.py, views.py, models.py   Django project and app
  celery.py                                    background task configuration
  canonical.py                                 the one internal data shape
  datasets/   manifest.py (validation), ingest.py (row mapping), manifests/
  jobs/       tasks.py (job lifecycle), writer.py (upserts), progress.py
  compute/    devices.py (detection), loader.py (judge model on device)
  management/commands/                         sync_datasets, ingest, devices
  static/prefkit/ingest.js                     AJAX client with backoff polling
  templates/prefkit/dataset.html               the ingest page
tests/        pytest suite
docs/         handwritten manual pages, numbered for order
scripts/      build_docs.py
data/         bundled sample data
```

## Configuration

Everything is set in `.env`. The defaults run without any of it: SQLite, eager Celery,
no Redis. Set `PREFKIT_DB=postgres` and `REDIS_URL` for the production-like setup, and
`PREFKIT_DEVICE=cuda|mps|cpu|auto` to choose compute. See `.env.example`.

## Status

Built and tested: manifests, row mapping with quarantine, the job pipeline with
transactional ingest and idempotent writes, the AJAX status page, device detection, and
the manual build.

Designed but not yet built (see `docs/04-jobs.md`): judge batches on the GPU, label
routing, sandboxed code execution, and the annotator game.

## Licence

Add a licence before publishing. The bundled sample data is project-owned, but check
each external dataset's licence before use.
