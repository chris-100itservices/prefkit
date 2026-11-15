"""End-to-end ingest through the job pipeline. Uses the real database, run eagerly."""

import json
from pathlib import Path

import pytest
from django.urls import reverse

from prefkit import models as db
from prefkit.jobs import tasks
from prefkit.jobs.tasks import ingest_dataset

# on_commit callbacks only run on real commits, so these tests use transaction mode.
pytestmark = pytest.mark.django_db(transaction=True)


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
