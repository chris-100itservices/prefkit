"""HTTP views. One page lists every dataset. The /api/ routes are for its JavaScript.

Starting a job returns at once with 202, and the browser then polls for progress.
Only one import may run at a time, so the server refuses a second start even if the
page's buttons were not greyed out, for example when two browser tabs are open.

Who may do what:
- Every page needs a signed-in user. LoginRequiredMiddleware in settings enforces this.
- Starting and cancelling imports needs an administrator (a "staff" account).
- Labelling needs any signed-in user.
"""

import json
import logging
from collections.abc import Callable
from functools import wraps
from pathlib import Path

from django.contrib.auth.decorators import login_not_required, login_required
from django.core.cache import cache
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from prefkit import game, goldchecks, routing, sandbox
from prefkit import models as db
from prefkit.datasets.ingest import PROJECT_ROOT
from prefkit.datasets.manifest import ManifestError, load_manifest
from prefkit.datasets.pair_sources import CODE_TASK_READERS
from prefkit.jobs import progress, tasks
from prefkit.jobs.tasks import ingest_dataset

logger = logging.getLogger(__name__)


def _signed_out() -> JsonResponse:
    """The API answer when nobody is signed in. JSON, so the page can show the message."""
    return JsonResponse({"error": "Your session has ended. Reload the page and sign in again."}, status=401)


def api_view(staff: bool = False) -> Callable:
    """For views the page's JavaScript calls. Answers with JSON when the user may not use it.

    The site-wide sign-in rule answers a signed-out request with a redirect to the sign-in
    page. JavaScript cannot use a redirect to an HTML page, so these views opt out of that
    rule and check here instead: 401 when nobody is signed in, and 403 when the view needs
    an administrator and the signed-in user is not one.
    """

    def decorate(view: Callable) -> Callable:
        @login_not_required
        @wraps(view)
        def wrapper(request: HttpRequest, *args, **kwargs):
            if not request.user.is_authenticated:
                return _signed_out()
            if staff and not request.user.is_staff:
                return JsonResponse({"error": "Only an administrator can do this."}, status=403)
            return view(request, *args, **kwargs)

        return wrapper

    return decorate


def _active_job() -> db.IngestJob | None:
    """The import that is queued or running right now, if any. There is at most one."""
    return (
        db.IngestJob.objects.filter(status__in=db.IngestJob.ACTIVE)
        .select_related("dataset")
        .first()
    )


def _active_summary(job: db.IngestJob) -> dict[str, str]:
    """What the page needs to watch and cancel a running import."""
    return {
        "job_id": str(job.pk),
        "dataset": job.dataset_id,
        "status_url": reverse("prefkit:job-status", args=[job.pk]),
        "cancel_url": reverse("prefkit:cancel-job", args=[job.pk]),
    }


def _busy_response(dataset_name: str) -> JsonResponse:
    return JsonResponse(
        {"error": f"'{dataset_name}' cannot start because another import is still running. "
                  "Wait for it to finish, or press Cancel import."},
        status=409,
    )


DOCKER_STATUS_KEY = "prefkit:docker-running"
DOCKER_STATUS_SECONDS = 60  # how long the page reuses one answer from Docker


def _docker_running() -> bool:
    """Whether Docker runs now. Asked at most once a minute, because asking takes a moment."""
    running = cache.get(DOCKER_STATUS_KEY)
    if running is None:
        running = sandbox.docker_running()
        cache.set(DOCKER_STATUS_KEY, running, DOCKER_STATUS_SECONDS)
    return running


def _import_readiness(manifest) -> tuple[bool, str]:
    """Whether the dataset's data is available and its import can run now, and why."""
    source = manifest.source
    if source.type == "local":
        path = Path(source.location)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            return False, f"Not ready: its data file {source.location} is not on this computer yet."
        return True, "Ready: its data file is on this computer."
    if source.type in CODE_TASK_READERS:
        if not _docker_running():
            return False, "Not ready: it runs code in the sandbox, and Docker is not running."
        return True, "Ready: Docker is running. The data downloads from the Hugging Face Hub the first time."
    return True, "Ready: the data downloads from the Hugging Face Hub the first time."


def _dataset_cards(username: str) -> list[dict]:
    """Each dataset with its help text, and whether its buttons are ready to use.

    The title, description and optional flag come from the dataset's manifest. A missing or
    broken manifest must not break the page, so the dataset's name is used instead.
    Optional datasets come last.
    """
    routed_rows = db.Comparison.objects.filter(route__source=routing.PERSON)
    routed = dict(routed_rows.values("dataset_id").annotate(n=Count("id")).values_list("dataset_id", "n"))
    mine = dict(
        routed_rows.filter(person_labels__annotator=username)
        .values("dataset_id")
        .annotate(n=Count("id"))
        .values_list("dataset_id", "n")
    )
    cards = []
    for ds in db.Dataset.objects.all():
        try:
            manifest = load_manifest(tasks.MANIFEST_DIR / f"{ds.name}.yaml")
        except ManifestError:
            title, description, optional = ds.name, "", False
            ready, status = False, "Not ready: its manifest could not be read."
        else:
            title, description, optional = manifest.title or ds.name, manifest.description, manifest.optional
            ready, status = _import_readiness(manifest)
        waiting = routed.get(ds.name, 0) - mine.get(ds.name, 0)
        cards.append(
            {
                "name": ds.name,
                "title": title,
                "description": description,
                "optional": optional,
                "ready": ready,
                "status": status,
                "routed": routed.get(ds.name, 0),
                "waiting": waiting,
            }
        )
    cards.sort(key=lambda card: (card["optional"], card["name"]))
    return cards


def index(request: HttpRequest):
    """The one page: a button per dataset, the running import, and recent jobs."""
    active = _active_job()
    return render(
        request,
        "prefkit/index.html",
        {
            "datasets": _dataset_cards(request.user.get_username()),
            "active": _active_summary(active) if active else None,
            "recent": db.IngestJob.objects.select_related("dataset")[:15],
        },
    )


def dataset_page(request: HttpRequest, name: str):
    """The old address of one dataset's page. It now sends you to the single page."""
    get_object_or_404(db.Dataset, pk=name)
    return redirect("prefkit:index")


@api_view(staff=True)
@require_POST
def start_ingest(request: HttpRequest, name: str) -> JsonResponse:
    """Create a job, enqueue it after commit, and answer 202 with a status URL."""
    dataset = get_object_or_404(db.Dataset, pk=name)

    try:
        with transaction.atomic():
            busy = _active_job()
            if busy is not None:
                return _busy_response(busy.dataset_id)
            job = db.IngestJob.objects.create(dataset=dataset)
    except IntegrityError:
        # The partial unique constraint rejected a second active job for this dataset.
        return JsonResponse(
            {"error": f"an ingest for '{name}' is already queued or running"},
            status=409,
        )
    except OperationalError:
        # SQLite reports "database is locked" when another connection holds the write lock.
        # Answer with JSON, so the browser can show this message instead of failing to parse HTML.
        logger.warning("ingest for '%s' could not start: database is busy", name, exc_info=True)
        return JsonResponse(
            {"error": "The database is busy because another import or program is using it. "
                      "Stop the other process, wait a few seconds, then try again."},
            status=503,
            headers={"Retry-After": "5"},
        )

    # Enqueue only after the job row is committed, so the worker always sees it.
    transaction.on_commit(lambda: ingest_dataset.delay(str(job.pk)))

    status_url = reverse("prefkit:job-status", args=[job.pk])
    return JsonResponse(
        {"job_id": str(job.pk), "status_url": status_url},
        status=202,
        headers={"Location": status_url},
    )


@api_view(staff=True)
@require_POST
def cancel_job(request: HttpRequest, job_id: str) -> JsonResponse:
    """Ask a running import to stop. It stops at its next check, and nothing it saved is kept."""
    job = get_object_or_404(db.IngestJob, pk=job_id)
    if job.status not in db.IngestJob.ACTIVE:
        return JsonResponse(
            {"error": "This import is not running, so there is nothing to cancel."},
            status=409,
        )
    progress.request_cancel(job.pk)
    return JsonResponse({"job_id": str(job.pk), "cancel_requested": True}, status=202)


@api_view()
@never_cache
@require_GET
def active_job(request: HttpRequest) -> JsonResponse:
    """Report the running import, if any. The page uses this to grey out buttons."""
    job = _active_job()
    return JsonResponse({"active": _active_summary(job) if job else None})


@api_view()
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


# ------------------------------------------------------------------ person labelling
#
# Rows that `route --apply` sent to a person are shown here one at a time. The page never
# shows the dataset's own winner, so a person's label is their own judgement. Labels on
# gold rows then measure that person, the same way the judge is measured.
#
# A person must be signed in. Each label is stored under their username, so nobody can
# label under someone else's name, and each person is measured separately.
#
# On datasets without gold rows, hidden gold checks from other datasets are mixed in, so
# each person's accuracy can be measured there too. See prefkit/goldchecks.py.

MAX_LABEL_SECONDS = 3600.0  # a longer time means the page was left open, not reading time


def _person_queue(dataset_name: str, annotator: str):
    """Rows routed to a person that this person has not labelled yet, in a fixed order."""
    return (
        db.Comparison.objects.filter(dataset_id=dataset_name, route__source=routing.PERSON)
        .exclude(person_labels__annotator=annotator)
        .order_by("id")
    )


@login_required
def label_page(request: HttpRequest, name: str):
    """The labelling page for one dataset. Someone who is not signed in is sent to sign in first."""
    dataset = get_object_or_404(db.Dataset, pk=name)
    routed = db.Comparison.objects.filter(dataset=dataset, route__source=routing.PERSON).count()
    return render(request, "prefkit/label.html", {"dataset": dataset, "routed": routed})


@api_view()
@never_cache
@require_GET
def next_for_person(request: HttpRequest, name: str) -> JsonResponse:
    """The next row for this person, without its winner. Answers done=true when none are left."""
    get_object_or_404(db.Dataset, pk=name)

    annotator = request.user.get_username()
    queue = _person_queue(name, annotator)
    row = queue.select_related("prompt", "a", "b").first()
    score = game.as_dict(game.score_for(annotator))  # the running totals, never per-row results
    if row is None:
        return JsonResponse({"done": True, "remaining": 0, "score": score})
    # Sometimes a hidden gold check is shown instead. The answer looks the same, so the
    # person cannot tell which rows are checks. "remaining" counts only the real rows.
    row = goldchecks.next_check(request.session, annotator, name) or row
    return JsonResponse(
        {
            "done": False,
            "remaining": queue.count(),
            "prompt": row.prompt.text,
            "a": row.a.text,
            "b": row.b.text,
            "save_url": reverse("prefkit:label-save", args=[row.pk]),
            "score": score,
        }
    )


@login_required
def leaderboard(request: HttpRequest):
    """Everyone's points, highest first, with the rules that produce them."""
    return render(
        request,
        "prefkit/leaderboard.html",
        {
            "scores": game.scores(),
            "me": request.user.get_username(),
            "points_per_label": game.POINTS_PER_LABEL,
            "min_seconds": int(game.MIN_SECONDS),
            "levels": game.LEVELS,
        },
    )


@api_view()
@require_POST
def save_person_label(request: HttpRequest, comparison_id: str) -> JsonResponse:
    """Store the signed-in person's choice. Body: {"choice": "a"|"b"|"tie", "seconds": number}."""
    comparison = get_object_or_404(db.Comparison, pk=comparison_id)
    try:
        body = json.loads(request.body)
        choice = body["choice"]
        seconds = float(body["seconds"])
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"error": "The label could not be read."}, status=400)

    if choice not in ("a", "b", "tie"):
        return JsonResponse({"error": "The choice must be A, B or Tie."}, status=400)
    # A row may be labelled when it was shown as a hidden check, or when it is routed to a person.
    check_for = goldchecks.pending_for(request.session, comparison.pk)
    routed = db.RouteDecision.objects.filter(comparison=comparison, source=routing.PERSON).exists()
    if check_for is None and not routed:
        return JsonResponse({"error": "This row is not routed to a person."}, status=409)

    try:
        with transaction.atomic():
            db.PersonLabel.objects.create(
                comparison=comparison,
                annotator=request.user.get_username(),
                choice=choice,
                seconds=min(max(seconds, 0.0), MAX_LABEL_SECONDS),
                check_for=check_for or "",
            )
    except IntegrityError:
        return JsonResponse({"error": "You have already labelled this row."}, status=409)
    if check_for is not None:
        goldchecks.clear(request.session)
    return JsonResponse({"saved": True}, status=201)
