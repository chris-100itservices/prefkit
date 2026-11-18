"""Who may do what: signed out, a labeller, or an administrator."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from prefkit import models as db

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset():
    return db.Dataset.objects.create(name="demo", manifest_version=1)


def signed_in(username: str, staff: bool = False) -> Client:
    user = get_user_model().objects.create_user(username=username, password="x" * 10, is_staff=staff)
    client = Client()
    client.force_login(user)
    return client


# ------------------------------------------------------------------ signed out


def test_every_page_sends_a_signed_out_visitor_to_sign_in(client, dataset):
    for url in (reverse("prefkit:index"), reverse("prefkit:label", args=["demo"]), "/accounts/password/"):
        response = client.get(url)
        assert response.status_code == 302, url
        assert response["Location"].startswith("/accounts/login/"), url


def test_the_sign_in_page_is_open(client):
    assert client.get("/accounts/login/").status_code == 200


def test_the_api_answers_a_signed_out_visitor_with_json(client, dataset):
    response = client.get(reverse("prefkit:active-job"))
    assert response.status_code == 401
    assert "sign in" in response.json()["error"]
    assert client.post(reverse("prefkit:start-ingest", args=["demo"])).status_code == 401
    assert not db.IngestJob.objects.exists()


# ------------------------------------------------------------------ labeller


def test_a_labeller_sees_the_page_without_import_buttons(dataset):
    page = signed_in("lab").get(reverse("prefkit:index")).content.decode()
    assert "Signed in as <strong>lab</strong>" in page
    assert 'class="start btn' not in page
    assert 'id="cancel"' not in page
    assert "Only an administrator can import" in page
    assert reverse("prefkit:label", args=["demo"]) in page


def test_a_labeller_cannot_start_or_cancel_an_import(dataset):
    lab = signed_in("lab")
    response = lab.post(reverse("prefkit:start-ingest", args=["demo"]))
    assert response.status_code == 403
    assert not db.IngestJob.objects.exists()
    job = db.IngestJob.objects.create(dataset=dataset, status="running")
    assert lab.post(reverse("prefkit:cancel-job", args=[job.pk])).status_code == 403


def test_a_labeller_can_follow_progress(dataset):
    assert signed_in("lab").get(reverse("prefkit:active-job")).json() == {"active": None}


# ------------------------------------------------------------------ administrator


def test_an_administrator_sees_the_import_buttons(dataset):
    page = signed_in("boss", staff=True).get(reverse("prefkit:index")).content.decode()
    assert "(administrator)" in page
    assert 'class="start btn' in page


# ------------------------------------------------------------------ passwords and accounts


def test_a_signed_in_user_can_change_their_password():
    user = get_user_model().objects.create_user(username="lab", password="old password 1")
    client = Client()
    client.force_login(user)
    response = client.post(
        "/accounts/password/",
        {
            "old_password": "old password 1",
            "new_password1": "new pass phrase 2",
            "new_password2": "new pass phrase 2",
        },
    )
    assert response.status_code == 302
    user.refresh_from_db()
    assert "Back to datasets" in client.get("/accounts/password/").content.decode()  # prefkit's page
    assert user.check_password("new pass phrase 2")


def test_add_annotator_staff_makes_an_administrator(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "long enough password")
    out = StringIO()
    call_command("add_annotator", "boss", "--staff", stdout=out)
    assert get_user_model().objects.get(username="boss").is_staff
    assert "administrator" in out.getvalue()
