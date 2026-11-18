"""End-to-end ingest through the job pipeline. Uses the real database, run eagerly."""

import json
import re
from pathlib import Path

import pytest
from django.urls import reverse

from prefkit import models as db
from prefkit.jobs import progress, tasks
from prefkit.jobs.tasks import ingest_dataset

# on_commit callbacks only run on real commits, so these tests use transaction mode.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client(client, django_user_model):
    """In this file, the browser is signed in as an administrator, who may import."""
    admin = django_user_model.objects.create_user(username="admin", password="x" * 10, is_staff=True)
    client.force_login(admin)
    return client


@pytest.fixture
def manifest_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "MANIFEST_DIR", tmp_path)
    return tmp_path


def write_dataset(tmp_path: Path, name: str, rows: list[dict]) -> None:
    data = tmp_path / f"{name}.jsonl"
    data.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (tmp_path / f"{name}.yaml").write_text(
        f"""\
name: {name}
version: 1
source:
  type: local
  location: {data}
card: https://example.org/card
license: mit
mapping:
  prompt_strategy: common_prefix
  response_a_field: chosen
  response_b_field: rejected
  winner_fixed: a
  shuffle_positions: false
gold:
  kind: none
""",
        encoding="utf-8",
    )


def hh(i: int) -> dict:
    prefix = f"\n\nHuman: question {i}"
    return {
        "chosen": f"{prefix}\n\nAssistant: good {i}",
        "rejected": f"{prefix}\n\nAssistant: bad {i}",
    }


@pytest.fixture
def demo_dataset(manifest_dir):
    write_dataset(manifest_dir, "demo", [hh(i) for i in range(50)])
    return db.Dataset.objects.create(name="demo", manifest_version=1)


def test_start_returns_202_and_job_succeeds(client, demo_dataset):
    response = client.post(reverse("prefkit:start-ingest", args=["demo"]))

    assert response.status_code == 202
    job = db.IngestJob.objects.get(pk=response.json()["job_id"])
    assert job.status == db.IngestJob.Status.SUCCEEDED
    assert job.accepted == 50
    assert db.Comparison.objects.filter(dataset=demo_dataset).count() == 50


def test_rerun_is_idempotent(client, demo_dataset):
    client.post(reverse("prefkit:start-ingest", args=["demo"]))
    client.post(reverse("prefkit:start-ingest", args=["demo"]))

    assert db.Comparison.objects.filter(dataset=demo_dataset).count() == 50
    assert db.Prompt.objects.count() == 50


def test_aborted_run_rolls_back_everything(client, manifest_dir):
    write_dataset(manifest_dir, "broken", [{"chosen": "x", "rejected": "y"}] * 300)
    db.Dataset.objects.create(name="broken", manifest_version=1)

    response = client.post(reverse("prefkit:start-ingest", args=["broken"]))

    job = db.IngestJob.objects.get(pk=response.json()["job_id"])
    assert job.status == db.IngestJob.Status.FAILED
    assert db.Comparison.objects.count() == 0  # the transaction rolled back


def test_second_active_job_is_rejected(client, demo_dataset):
    db.IngestJob.objects.create(dataset=demo_dataset, status=db.IngestJob.Status.QUEUED)

    response = client.post(reverse("prefkit:start-ingest", args=["demo"]))

    assert response.status_code == 409


def test_finished_job_is_not_rerun_on_redelivery(demo_dataset):
    job = db.IngestJob.objects.create(dataset=demo_dataset, status=db.IngestJob.Status.SUCCEEDED, accepted=50)

    # Simulate a redelivered message after success.
    ingest_dataset.apply(args=[str(job.pk)], throw=True)

    job.refresh_from_db()
    assert job.accepted == 50
    assert db.Comparison.objects.count() == 0  # nothing re-ingested


def test_status_endpoint_reports_terminal_state(client, demo_dataset):
    job = db.IngestJob.objects.create(
        dataset=demo_dataset,
        status=db.IngestJob.Status.SUCCEEDED,
        accepted=50,
        quarantined=0,
    )

    response = client.get(reverse("prefkit:job-status", args=[job.pk]))

    assert response.status_code == 200
    body = response.json()
    assert body["finished"] is True
    assert body["accepted"] == 50
    assert "live" not in body


def test_shipped_demo_dataset_ingests_with_quarantine(client):
    """The bundled demo has one bad row, which must be quarantined, not fatal."""
    db.Dataset.objects.create(name="demo_local", manifest_version=1)

    response = client.post(reverse("prefkit:start-ingest", args=["demo_local"]))

    job = db.IngestJob.objects.get(pk=response.json()["job_id"])
    assert job.status == db.IngestJob.Status.SUCCEEDED
    assert job.accepted == 12
    assert job.quarantined == 1


def test_second_dataset_is_refused_while_one_is_running(client, demo_dataset, manifest_dir):
    """Only one import at a time, even across different datasets."""
    write_dataset(manifest_dir, "other", [hh(i) for i in range(5)])
    db.Dataset.objects.create(name="other", manifest_version=1)
    db.IngestJob.objects.create(dataset=demo_dataset, status=db.IngestJob.Status.RUNNING)

    response = client.post(reverse("prefkit:start-ingest", args=["other"]))

    assert response.status_code == 409
    assert "another import is still running" in response.json()["error"]


def test_active_endpoint_reports_the_running_import(client, demo_dataset):
    assert client.get(reverse("prefkit:active-job")).json() == {"active": None}

    job = db.IngestJob.objects.create(dataset=demo_dataset, status=db.IngestJob.Status.RUNNING)

    active = client.get(reverse("prefkit:active-job")).json()["active"]
    assert active["job_id"] == str(job.pk)
    assert active["dataset"] == "demo"


def test_cancel_before_start_marks_job_cancelled_and_saves_nothing(demo_dataset):
    job = db.IngestJob.objects.create(dataset=demo_dataset)
    progress.request_cancel(job.pk)

    ingest_dataset.apply(args=[str(job.pk)], throw=True)

    job.refresh_from_db()
    assert job.status == db.IngestJob.Status.CANCELLED
    assert db.Comparison.objects.count() == 0
    assert not progress.cancel_requested(job.pk)  # the flag is cleared once the job ends


def test_cancel_during_run_rolls_back_everything(demo_dataset, monkeypatch):
    """A cancel seen mid-run leaves no rows behind, because the transaction is rolled back."""
    monkeypatch.setattr(tasks, "CANCEL_CHECK_EVERY", 10)
    real_cancel_requested = progress.cancel_requested
    calls = {"n": 0}

    def cancel_on_second_check(job_id):
        calls["n"] += 1
        return calls["n"] >= 2 or real_cancel_requested(job_id)

    monkeypatch.setattr(progress, "cancel_requested", cancel_on_second_check)
    job = db.IngestJob.objects.create(dataset=demo_dataset)

    ingest_dataset.apply(args=[str(job.pk)], throw=True)

    job.refresh_from_db()
    assert job.status == db.IngestJob.Status.CANCELLED
    assert db.Comparison.objects.count() == 0


def test_cancel_endpoint_refuses_when_nothing_is_running(client, demo_dataset):
    job = db.IngestJob.objects.create(dataset=demo_dataset, status=db.IngestJob.Status.SUCCEEDED)

    response = client.post(reverse("prefkit:cancel-job", args=[job.pk]))

    assert response.status_code == 409


def test_cancel_endpoint_sets_the_flag_for_a_running_job(client, demo_dataset):
    job = db.IngestJob.objects.create(dataset=demo_dataset, status=db.IngestJob.Status.RUNNING)

    response = client.post(reverse("prefkit:cancel-job", args=[job.pk]))

    assert response.status_code == 202
    assert progress.cancel_requested(job.pk)


def test_one_page_lists_every_dataset(client, demo_dataset):
    response = client.get(reverse("prefkit:index"))

    assert response.status_code == 200
    button = re.search(rb'<button[^>]*data-name="demo"[^>]*>', response.content).group(0)
    assert b"disabled" not in button  # nothing is running, so the button is not greyed out
