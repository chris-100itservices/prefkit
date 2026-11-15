"""HTTP views. Starting a job returns at once; the browser polls for progress."""

from django.db import IntegrityError, transaction
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from prefkit import models as db
from prefkit.jobs import progress
from prefkit.jobs.tasks import ingest_dataset


def dataset_page(request: HttpRequest, name: str):
    """HTML page with the ingest button, live status and recent jobs."""
    dataset = get_object_or_404(db.Dataset, pk=name)
    return render(
        request,
        "prefkit/dataset.html",
        {"dataset": dataset, "jobs": dataset.jobs.all()[:10]},
    )


@require_POST
def start_ingest(request: HttpRequest, name: str) -> JsonResponse:
    """Create a job, enqueue it after commit, and answer 202 with a status URL."""
    dataset = get_object_or_404(db.Dataset, pk=name)

    try:
        with transaction.atomic():
            job = db.IngestJob.objects.create(dataset=dataset)
    except IntegrityError:
        # The partial unique constraint rejected a second active job for this dataset.
        return JsonResponse(
            {"error": f"an ingest for '{name}' is already queued or running"},
            status=409,
        )

    # Enqueue only after the job row is committed, so the worker always sees it.
    transaction.on_commit(lambda: ingest_dataset.delay(str(job.pk)))

    status_url = reverse("prefkit:job-status", args=[job.pk])
    return JsonResponse(
        {"job_id": str(job.pk), "status_url": status_url},
        status=202,
        headers={"Location": status_url},
    )


@never_cache
@require_GET
def job_status(request: HttpRequest, job_id: str) -> JsonResponse:
    """Report a job's status. Live counts are included while it runs."""
    job = get_object_or_404(db.IngestJob, pk=job_id)

    payload = {
        "job_id": str(job.pk),
        "status": job.status,
        "finished": job.status in {s.value for s in db.IngestJob.TERMINAL},
        "accepted": job.accepted,
        "quarantined": job.quarantined,
        "top_reasons": job.top_reasons,
        "error": job.error,
    }
    if job.status == db.IngestJob.Status.RUNNING:
        payload["live"] = progress.read(job.pk)

    return JsonResponse(payload)
