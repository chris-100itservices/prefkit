---
title: Installation and first run
order: 1
---

# Installation and first run

This takes about ten minutes on a machine with Python 3.11 or newer.

## 1. Create a virtual environment

A **virtual environment** is an isolated folder of Python packages for this project,
so its dependencies do not clash with other projects.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```

## 2. Create your settings file

```bash
cp .env.example .env
```

The defaults use SQLite (a database held in one file, `var/prefkit.sqlite3`), so there
is nothing else to install. VS Code reads `.env` for every run and debug configuration.

## 3. Create the database tables

A **migration** is a generated file that tells the database which tables to create.
They are generated from the models in `prefkit/models.py`:

```bash
python manage.py makemigrations prefkit
python manage.py migrate
```

Run `makemigrations` once after you clone the project. Run it again whenever you change
a model.

## 4. Register the datasets

```bash
python manage.py sync_datasets
```

This reads every manifest in `prefkit/datasets/manifests/` and creates a `Dataset` row
for each. The `hh_rlhf` and `code_pairs` manifests are registered too, but they will not
run until their data is downloaded (see `02-datasets.md`).

## 5. Run the test suite

```bash
pytest
```

The tests use no network and no GPU. They should all pass on a fresh clone. If one
fails, see `05-debugging.md`.

## 6. Ingest the bundled sample

```bash
python manage.py ingest demo_local
```

Expected output:

```
succeeded: accepted=12 quarantined=1
  1 x winner value '9' is not in winner_map
```

Out of 13 rows, 12 are valid. The 13th has preference value `9`, which the manifest does
not map. It is quarantined with its reason, and the run succeeds. The manifest raised
the quarantine limit to 10% for this reason.

## 7. Open the web page

```bash
python manage.py runserver
```

Visit <http://127.0.0.1:8000/datasets/demo_local/> and press **Ingest dataset**. The page
polls the job status while it runs, and the progress text updates until the job finishes.

## Optional: a local GPU

Check what prefkit can see:

```bash
python manage.py devices
```

To use a GPU for model work, install the GPU extras. Install PyTorch first, using the
wheel that matches your CUDA version from pytorch.org, then:

```bash
pip install -r requirements-gpu.txt
```

On a laptop with an RTX 4060 (8 GB), a 3-billion-parameter judge model in 4-bit fits
with room for batches. Expect the first load to be slow, because the weights are
downloaded.

Apple Silicon uses the `mps` device automatically. Machines with no GPU use `cpu`,
which works but is slow.

To force a choice, set `PREFKIT_DEVICE=cuda`, `mps` or `cpu` in `.env`.

## Optional: PostgreSQL and Redis

For a setup closer to production:

```bash
docker compose up -d
```

Then in `.env`, set `PREFKIT_DB=postgres`, `PREFKIT_EAGER=0` and
`REDIS_URL=redis://localhost:6379/0`. Start a worker in a second terminal:

```bash
celery -A prefkit worker -Q ingest --pool=prefork --concurrency=4 -l info
```

Migrations must be run again against the new database (`python manage.py migrate`).
