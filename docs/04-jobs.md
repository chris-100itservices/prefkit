---
title: Jobs, queues and the browser
order: 4
---

# Jobs, queues and the browser

Ingesting a large dataset takes minutes. A web request should not hold a connection
open for minutes, so the work runs as a **job** in the background, and the browser asks
for progress.

## The flow

1. The browser sends `POST /api/datasets/<name>/ingest/`.
2. The server creates an `IngestJob` row with status `queued`, commits it, and answers
   **202 Accepted** with a `status_url`. The 202 means "accepted for later".
3. A **worker** picks up the job from the queue and runs it. The job moves to `running`.
4. The browser polls `status_url` every second or so. Polling backs off while nothing
   changes, and pauses while the tab is hidden.
5. The job ends as `succeeded` or `failed`, and the browser stops polling.

The enqueue happens after commit (`transaction.on_commit`). Otherwise the worker could
pick up a job row that does not yet exist.

## Why each piece is there

**Celery** runs the work in worker processes. **Celery** is a standard Python task queue.
A **broker** (Redis here) holds the queued messages between the web server and workers.

**Eager mode** (`PREFKIT_EAGER=1`, the default) skips the broker and runs the task
inside the calling process. It is the setting for debugging, because a breakpoint in
the ingest code is hit directly.

**Processes, not threads.** Mapping rows is CPU-bound Python. Threads share one
interpreter lock, so they cannot run Python code in parallel. Separate worker processes
can. Parallelism in prefkit comes from running several jobs, not from splitting one
dataset, because one dataset must stay in one transaction.

**One transaction per dataset.** An aborted run rolls back completely. The cost is a
long transaction, which is acceptable because only this job writes these rows.

**Idempotent writes.** Every id is a content hash, so a retried or redelivered job
converges to the same rows. Without this, retries would duplicate data.

**Retries are selective.** A transient database error (`OperationalError`) is retried
with backoff. A bad manifest or a mapping that fails the quarantine limit (`AbortIngest`)
is not retried, because retrying cannot fix the data.

**One active job per dataset.** A partial unique constraint in the database rejects a
second queued or running job for the same dataset with HTTP 409. The database enforces
this, so two simultaneous clicks cannot both succeed.

**Progress lives in the cache, not the database.** Progress is written during the
transaction, and other connections cannot see uncommitted rows. The cache is visible
immediately. The database row holds the final, authoritative counts.

## Running a worker

For a real queue, start Redis and set `REDIS_URL` and `PREFKIT_EAGER=0` in `.env`, then:

```bash
celery -A prefkit worker -Q ingest --pool=prefork --concurrency=4 -l info
```

Use `--pool=solo` when the task needs the GPU, so one process owns it.

## Reading a job's status

```bash
curl http://127.0.0.1:8000/api/jobs/<job_id>/
```

Response fields: `status`, `finished`, `accepted`, `quarantined`, `top_reasons`, `error`,
and `live` while running. An unknown id returns 404.

## Roadmap

These pieces are designed but not yet built. They are listed so you can see where the
project is going, and so each can be added without disturbing what exists.

- **Judge batches** on the `judge` queue, running a local model on the device chosen in
  `03-compute.md`.
- **Routing**: choose per item between a human, an LLM judge, and code execution for
  code items, according to measured error and cost.
- **Sandboxed execution** for code gold items, on its own queue, with CPU and memory
  limits and no network access.
- **Game layer** for annotators: gold items for scoring, annotator ratings, and streaks,
  with accuracy tracked so that engagement does not reduce label quality.
