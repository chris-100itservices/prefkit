---
title: Debugging in VS Code
order: 5
---

# Debugging in VS Code

This page assumes you have finished `01-install.md`, so `.venv` exists and `pytest`
passes.

## Setup

1. Open the `prefkit-project` folder in VS Code.
2. Open the Command Palette (Ctrl+Shift+P, or Cmd+Shift+P on Mac) and run
   **Python: Select Interpreter**. Choose `.venv`.
3. Install the **Python** and **Ruff** extensions if they are missing.
4. Open the **Run and Debug** panel (Ctrl+Shift+D). The configurations are in
   `.vscode/launch.json`.

The configurations read `.env` through `envFile`, so the same settings apply in the
terminal and in the debugger.

## The configurations

| Name | What it does |
|---|---|
| `runserver (no reload)` | Starts the web server. `--noreload` keeps breakpoints working, since auto-reload restarts the process. |
| `ingest demo_local` | Runs one ingest in the foreground. Best place to learn the pipeline. |
| `list devices` | Prints the compute devices. |
| `Celery worker (solo)` | Starts a worker for the `ingest` queue, for when `PREFKIT_EAGER=0`. |
| `pytest: current file` | Runs the test file you have open, with output visible. |

## Walkthrough: follow one ingest from start to finish

1. Open `prefkit/management/commands/ingest.py`. Put a breakpoint on the line that calls
   `ingest_dataset.apply(...)`.
2. Open `prefkit/jobs/tasks.py` and put a breakpoint on the first line of
   `ingest_dataset`, the line that calls `load_manifest`.
3. Choose **ingest demo_local** and press F5.
4. The debugger stops at the command. Press F10 (step over) to reach the task. Press
   F11 (step into) to enter `tracked_job`, and watch `job.status` change.
5. Continue into `IngestRun.run`, then `map_row`. Hover over `prompt_text` and `winner`
   to see their values. Step through row 13 (`d13`): it raises `RowError`, and the
   quarantine branch records it.
6. Watch `winner` on a swapped row. Because `shuffle_positions` is on, `_show_b_first`
   decides per row whether A and B are exchanged, and `_FLIP` changes the winner to match.

Use the **Variables** pane and the **Debug Console**. In the console you can type an
expression, for example `manifest.mapping`, to inspect it while paused.

## Debugging the web flow

1. Start **runserver (no reload)**.
2. Open `prefkit/views.py` and put a breakpoint inside `start_ingest`.
3. Visit <http://127.0.0.1:8000/datasets/demo_local/> and press **Ingest dataset**.
4. Execution stops at the breakpoint. Step through it. Because `PREFKIT_EAGER=1`,
   `ingest_dataset.delay(...)` runs inside the same request, so you can also step into
   the task body.

## When tests fail

- **`NoReverseMatch` for `prefkit:...`**: the URL configuration lost its namespace. Check
  `prefkit/urls.py`.
- **`no such table`**: migrations are missing or not applied. Run
  `python manage.py makemigrations prefkit` and `python manage.py migrate`.
- **`DisallowedHost`**: add the host to `DJANGO_ALLOWED_HOSTS` in `.env`. The test
  client uses `testserver`, which pytest-django already allows.
- **`FileNotFoundError` for a dataset**: the manifest points at a missing file. The job
  is marked `failed` with a readable message, not a traceback.

## Common mistakes

- **Breakpoints not hit in the worker.** The worker runs in another process. Use
  eager mode for debugging, or attach the debugger to the worker explicitly.
- **Reloading resets state.** With `runserver` auto-reload, a breakpoint can be lost
  when the process restarts. Use `--noreload`.
- **Stale migrations after a model change.** Run `makemigrations` again.
