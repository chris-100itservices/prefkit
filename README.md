# prefkit

prefkit imports preference datasets (a question, two answers, and which answer people
preferred) into one shared format, checks every row, and helps decide who labels new
rows: a code check, a language-model judge, or a person.

Built with Django 5, Celery and SQLite. It runs on Windows, macOS and Linux.

**Manual:** [read it in the browser (PDF)](docs/prefkit-documentation.pdf) or
[download the Word file](docs/prefkit-documentation.docx).

## What it can do

- Import preference datasets from files on your computer or from the Hugging Face Hub.
- Describe each dataset with one short YAML manifest, so adding a dataset needs no code.
- Convert every dataset into one shared format: a question, two answers and the winner.
- Check every row and set broken rows aside with a reason, instead of guessing.
- Cancel an import when more than 1% of its rows fail, so a wrong manifest is caught at once.
- Repeat an import safely: the same data always gives the same records, with no duplicates.
- Build answer pairs for HumanEval, MBPP, oasst2 and UltraFeedback from tests, rankings and ratings.
- Run all dataset code inside an isolated Docker sandbox, with no network and strict limits.
- Start, follow and cancel imports on one web page, with live progress.
- Ask a language model, the judge, which answer is better, in both orders to catch position bias.
- Measure how often the judge agrees with people, and its accuracy on rows with a known answer.
- Measure how well a code dataset's tests catch wrong solutions.
- Choose the cheapest label source that is accurate enough: a code check, the judge or a person.
- Let people label rows on a web page, with hidden gold checks to measure each person's accuracy.
- Combine several people's labels into one majority answer.
- Reward careful labelling with points, levels and a leaderboard.
- Export rows with their final labels, and the evidence behind them, to a JSONL file.
- Require sign-in on every page, with administrators who import and labellers who label.

## Quick start

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
copy .env.example .env
python manage.py migrate
python manage.py sync_datasets
python manage.py add_annotator yourname --staff
pytest
python manage.py ingest demo_local
python manage.py runserver
```

macOS and Linux (Terminal): the same, but create and activate the environment with
`python3.12 -m venv .venv` and `source .venv/bin/activate`, and copy the settings with
`cp .env.example .env`.

Then open http://127.0.0.1:8000/ and sign in with the account you created.

Optional:

- **Docker Desktop**, to import HumanEval and MBPP and to measure the code check. Their
  test code only ever runs in the sandbox.
- **An NVIDIA graphics card**, for the judge. Install PyTorch with CUDA and
  `requirements-gpu.txt`, as described in the manual.

## Commands

| Command | Purpose |
|---|---|
| `python manage.py sync_datasets` | Register every manifest as a dataset. |
| `python manage.py ingest <name>` | Run one import in the foreground. |
| `python manage.py devices` | List CUDA, MPS and CPU devices. |
| `python manage.py judge <name>` | Ask the judge model which answer is better, for each row. |
| `python manage.py agreement <name>` | Compare the judge's answers with the dataset's labels. |
| `python manage.py measure_code <name>` | Measure how well a code dataset's tests reject wrong solutions. |
| `python manage.py route <name>` | Show which label source the router chooses. `--apply` sends the rows. |
| `python manage.py consensus <name>` | Store the majority answer of several people's labels. |
| `python manage.py export <name>` | Write rows with their final labels to `var/exports/<name>.jsonl`. |
| `python manage.py add_annotator <name>` | Create an account. Add `--staff` for an administrator. |
| `python manage.py runserver` | Start the development web server. |
| `pytest` | Run the test suite. It needs no network, GPU or Docker. |

## Documentation

The full manual is [docs/prefkit-documentation.pdf](docs/prefkit-documentation.pdf),
also as a [Word file](docs/prefkit-documentation.docx). It covers setup on each system,
writing a manifest, the judge, gold items, the sandbox, label routing, the annotator game,
exporting, and running prefkit on a server.

## Datasets and licences

The bundled sample `data/demo_local.jsonl` belongs to this project. The other datasets
are downloaded from their sources and keep their own licences. Each manifest records the
licence from the dataset card; read it before you publish results based on the data.

## Licence

MIT. See `LICENSE`.
