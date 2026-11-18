"""The ingest task, and the decorator that owns each job's lifecycle.

The decorator sets every status transition in one place, so the task body contains
only the work. It also makes redelivery safe: with acks_late, a worker that dies after
finishing but before acknowledging causes the same message to arrive again, and a
finished job must not run twice.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from celery import shared_task
from django.db import OperationalError, transaction
from django.utils import timezone

from prefkit import models as db
from prefkit.datasets.ingest import AbortIngest, IngestRun, read_rows
from prefkit.datasets.manifest import load_manifest
from prefkit.jobs import progress
from prefkit.jobs.writer import BatchWriter

MANIFEST_DIR = Path(__file__).resolve().parents[1] / "datasets" / "manifests"
MAX_ERROR_CHARS = 4000
# How many rows pass between checks for a cancel request. Each check is a cache read,
# so checking every row would be wasteful; 500 rows is well under a second of work.
CANCEL_CHECK_EVERY = 500
CANCELLED_MESSAGE = "Cancelled by the user. Nothing was saved."

JobBody = Callable[[Any, db.IngestJob], IngestRun]


class JobCancelled(Exception):
    """Raised inside the import when the user has asked it to stop."""


def tracked_job(fn: JobBody) -> Callable[[Any, str], None]:
    """Own the status transitions of an ingest job around the task body.

    Outcomes:
      - success:          SUCCEEDED, with counts and top quarantine reasons.
      - AbortIngest:      FAILED, not retried. The data is wrong; retrying cannot fix it.
      - OperationalError: left RUNNING and re-raised, so autoretry runs it again. The
                          transaction rolled back, so the retry starts clean.
      - anything else:    FAILED, then re-raised so Celery records the traceback.
    """

    @functools.wraps(fn)
    def wrapper(task: Any, job_id: str) -> None:
        job = db.IngestJob.objects.select_related("dataset").get(pk=job_id)
        if job.status == db.IngestJob.Status.SUCCEEDED:
            return  # redelivered after success: do nothing
        if progress.cancel_requested(job.pk):
            # Cancelled while still queued, before any work started.
            _finish(job, db.IngestJob.Status.CANCELLED, error=CANCELLED_MESSAGE)
            return

        db.IngestJob.objects.filter(pk=job.pk).update(
            status=db.IngestJob.Status.RUNNING,
            task_id=task.request.id or "",
            started_at=timezone.now(),
        )

        try:
            run = fn(task, job)
        except OperationalError:
            raise
        except AbortIngest as exc:
            _finish(job, db.IngestJob.Status.FAILED, error=str(exc))
            return
        except JobCancelled:
            # The transaction has rolled back, so no rows from this run were kept.
            _finish(job, db.IngestJob.Status.CANCELLED, error=CANCELLED_MESSAGE)
            return
        except Exception as exc:
            _finish(job, db.IngestJob.Status.FAILED, error=f"{type(exc).__name__}: {exc}")
            raise

        _finish(
            job,
            db.IngestJob.Status.SUCCEEDED,
            accepted=run.accepted,
            quarantined=run.quarantined,
            top_reasons=[
                {"reason": reason, "count": count} for reason, count in run.reason_counts.most_common(10)
            ],
        )

    return wrapper


def _finish(job: db.IngestJob, status: str, **fields: Any) -> None:
    if "error" in fields:
        fields["error"] = fields["error"][:MAX_ERROR_CHARS]
    db.IngestJob.objects.filter(pk=job.pk).update(
        status=status,
        finished_at=timezone.now(),
        **fields,
    )
    progress.clear_cancel(job.pk)


@shared_task(
    bind=True,
    name="prefkit.ingest_dataset",
    autoretry_for=(OperationalError,),
    retry_backoff=5,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
    soft_time_limit=6 * 60 * 60,
    time_limit=6 * 60 * 60 + 300,
)
@tracked_job
def ingest_dataset(task: Any, job: db.IngestJob) -> IngestRun:
    """Ingest one dataset in a single transaction. Returns the run's counts."""
    manifest = load_manifest(MANIFEST_DIR / f"{job.dataset_id}.yaml")
    run = IngestRun(manifest)
    writer = BatchWriter(
        job.dataset,
        on_flush=lambda: progress.publish(job.pk, run.snapshot()),
    )

    # One transaction for the whole run. If the run aborts partway, everything rolls back
    # and no partial dataset is left behind. Progress is published from inside the
    # transaction, so it can show counts that later roll back; the job status is the
    # source of truth.
    with transaction.atomic():
        for count, mapped in enumerate(run.run(read_rows(manifest)), start=1):
            writer.add(mapped)
            if count % CANCEL_CHECK_EVERY == 0 and progress.cancel_requested(job.pk):
                raise JobCancelled()  # leaves the transaction, which rolls everything back
        writer.flush()

    return run
