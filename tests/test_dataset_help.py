"""The help boxes on the main page's dataset buttons."""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from prefkit import models as db
from prefkit import sandbox
from prefkit.datasets.manifest import load_manifest
from prefkit.jobs import tasks

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def docker(monkeypatch):
    """Docker is replaced by a switch, so these tests do not depend on Docker running."""
    state = {"running": True}
    monkeypatch.setattr(sandbox, "docker_running", lambda timeout_s=5: state["running"])
    cache.clear()
    yield state
    cache.clear()
MANIFESTS = Path(__file__).resolve().parent.parent / "prefkit" / "datasets" / "manifests"


def admin_page() -> str:
    user = get_user_model().objects.create_user(username="boss", password="x" * 10, is_staff=True)
    client = Client()
    client.force_login(user)
    return client.get(reverse("prefkit:index")).content.decode()


def test_every_bundled_manifest_has_a_title_and_description():
    for path in MANIFESTS.glob("*.yaml"):
        manifest = load_manifest(path)
        assert manifest.title, path.name
        assert manifest.description, path.name


def test_title_and_description_are_optional(tmp_path):
    text = (MANIFESTS / "demo_local.yaml").read_text(encoding="utf-8")
    optional = ("title:", "description:", "  13 example")
    lines = [line for line in text.splitlines() if not line.startswith(optional)]
    path = tmp_path / "demo_local.yaml"
    path.write_text("\n".join(lines), encoding="utf-8")
    manifest = load_manifest(path)
    assert (manifest.title, manifest.description) == ("", "")


def test_both_rows_are_buttons_with_help_from_the_manifest():
    db.Dataset.objects.create(name="humaneval", manifest_version=1)
    page = admin_page()
    assert 'class="start btn ready" data-name="humaneval"' in page
    assert f'class="btn" href="{reverse("prefkit:label", args=["humaneval"])}"' in page
    assert page.count("<strong>OpenAI HumanEval</strong>") == 2  # one help box per row
    assert "164 Python programming problems" in page
    assert "Press to import this dataset." in page


def test_the_label_help_says_how_many_rows_wait_for_a_person():
    ds = db.Dataset.objects.create(name="humaneval", manifest_version=1)
    prompt = db.Prompt.objects.create(id="p1", text="q")
    a = db.Response.objects.create(id="a1", prompt=prompt, text="x")
    b = db.Response.objects.create(id="b1", prompt=prompt, text="y")
    row = db.Comparison.objects.create(id="c1", dataset=ds, prompt=prompt, a=a, b=b, winner="a")
    assert "No rows of this dataset are sent to a person yet." in admin_page()
    db.RouteDecision.objects.create(comparison=row, source="person", target=0.9)
    get_user_model().objects.all().delete()
    page = admin_page()
    assert "Press to label: 1 row waits for you." in page
    assert f'class="btn ready" href="{reverse("prefkit:label", args=["humaneval"])}"' in page
    db.PersonLabel.objects.create(comparison=row, annotator="boss", choice="a", seconds=5)
    get_user_model().objects.all().delete()
    page = admin_page()
    assert "You have labelled all 1 row sent to a person." in page
    assert f'class="btn" href="{reverse("prefkit:label", args=["humaneval"])}"' in page


def test_a_dataset_without_a_manifest_still_shows(monkeypatch, tmp_path):
    monkeypatch.setattr(tasks, "MANIFEST_DIR", tmp_path)  # no manifest files here
    db.Dataset.objects.create(name="orphan", manifest_version=1)
    page = admin_page()
    assert "<strong>orphan</strong>" in page


def test_a_code_dataset_is_only_ready_while_docker_runs(docker):
    db.Dataset.objects.create(name="humaneval", manifest_version=1)
    docker["running"] = False
    page = admin_page()
    assert 'class="start btn" data-name="humaneval"' in page
    assert "Docker is not running" in page


def test_a_local_dataset_is_ready_only_when_its_file_exists():
    db.Dataset.objects.create(name="demo_local", manifest_version=1)  # its file ships with the project
    db.Dataset.objects.create(name="code_pairs", manifest_version=1)  # its file does not
    page = admin_page()
    assert 'class="start btn ready" data-name="demo_local"' in page
    assert 'class="start btn" data-name="code_pairs"' in page
    assert "its data file data/code_pairs.jsonl is not on this computer yet" in page


def test_optional_datasets_come_last_and_are_marked():
    for name in ("code_pairs", "demo_local", "shp"):
        db.Dataset.objects.create(name=name, manifest_version=1)
    page = admin_page()
    order = [page.index(f'data-name="{name}"') for name in ("demo_local", "shp", "code_pairs")]
    assert order == sorted(order)
    assert 'code_pairs<span class="optional">optional</span>' in page
    assert "Code pairs (template) (optional)" in page
