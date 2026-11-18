"""Hidden gold checks mixed into the labelling queue of datasets without gold rows."""

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from prefkit import goldchecks
from prefkit import models as db

pytestmark = pytest.mark.django_db


def _row(dataset, key, winner="a", gold=False, source="human", routed=False):
    prompt = db.Prompt.objects.create(id=f"p{key}", text=f"question {key}")
    a = db.Response.objects.create(id=f"a{key}", prompt=prompt, text=f"first {key}")
    b = db.Response.objects.create(id=f"b{key}", prompt=prompt, text=f"second {key}")
    row = db.Comparison.objects.create(
        id=f"c{key}", dataset=dataset, prompt=prompt, a=a, b=b, winner=winner, gold=gold, source=source
    )
    if routed:
        db.RouteDecision.objects.create(comparison=row, source="person", target=0.9)
    return row


@pytest.fixture
def setup(settings):
    """hh_rlhf with two rows routed to a person, and humaneval with one gold row to use as a check."""
    settings.PREFKIT_GOLD_CHECK_EVERY = 1  # a check every time one is available
    hh = db.Dataset.objects.create(name="hh_rlhf", manifest_version=1)
    he = db.Dataset.objects.create(name="humaneval", manifest_version=1)
    real = [_row(hh, f"h{i}", routed=True) for i in (1, 2)]
    check = _row(he, "g1", winner="a", gold=True, source="execution")
    return {"real": real, "check": check}


def signed_in(username="ann"):
    user, _ = get_user_model().objects.get_or_create(username=username)
    client = Client()
    client.force_login(user)
    return client


def _next(client, name="hh_rlhf"):
    return client.get(reverse("prefkit:label-next", args=[name])).json()


def _save(client, body, choice="a"):
    return client.post(
        body["save_url"], data=json.dumps({"choice": choice, "seconds": 5}), content_type="application/json"
    )


def test_a_check_looks_exactly_like_an_ordinary_row(setup):
    body = _next(signed_in())
    assert body["prompt"] == "question g1"  # the check was chosen
    assert set(body) == {"done", "remaining", "prompt", "a", "b", "save_url", "score"}
    assert body["remaining"] == 2  # only the real rows are counted


def test_a_saved_check_is_recorded_against_the_page_it_was_shown_on(setup):
    client = signed_in()
    assert _save(client, _next(client)).status_code == 201
    label = db.PersonLabel.objects.get()
    assert (label.comparison_id, label.check_for) == (setup["check"].id, "hh_rlhf")


def test_after_the_check_the_real_rows_follow(setup):
    client = signed_in()
    _save(client, _next(client))
    assert _next(client)["prompt"] == "question h1"  # no checks left for this person


def test_reloading_shows_the_same_check_again(setup):
    client = signed_in()
    assert _next(client)["prompt"] == "question g1"
    assert _next(client)["prompt"] == "question g1"


def test_an_invalid_answer_does_not_skip_the_check(setup):
    client = signed_in()
    body = _next(client)
    assert _save(client, body, choice="maybe").status_code == 400
    assert _next(client)["prompt"] == "question g1"


def test_a_check_row_cannot_be_labelled_unless_it_was_shown(setup):
    url = reverse("prefkit:label-save", args=[setup["check"].pk])
    body = json.dumps({"choice": "a", "seconds": 5})
    response = signed_in().post(url, data=body, content_type="application/json")
    assert response.status_code == 409


def test_checks_can_be_turned_off(setup, settings):
    settings.PREFKIT_GOLD_CHECK_EVERY = 0
    assert _next(signed_in())["prompt"] == "question h1"


def test_datasets_with_their_own_gold_rows_get_no_checks(setup):
    he = db.Dataset.objects.get(name="humaneval")
    _row(he, "g2", gold=True, source="execution", routed=True)
    _row(db.Dataset.objects.create(name="mbpp", manifest_version=1), "m1", gold=True, source="execution")
    assert not goldchecks.needs_checks("humaneval")
    assert _next(signed_in(), name="humaneval")["prompt"] in {"question g1", "question g2"}
    assert not db.PersonLabel.objects.exists()


def test_no_checks_once_the_real_rows_are_done(setup):
    client = signed_in()
    for row in setup["real"]:
        db.PersonLabel.objects.create(comparison=row, annotator="ann", choice="a", seconds=1)
    body = _next(client)
    assert (body["done"], body["remaining"]) == (True, 0)


def test_route_reports_accuracy_on_hidden_checks(setup):
    right = signed_in("ann")
    _save(right, _next(right), choice="a")  # the known winner is a
    wrong = signed_in("bob")
    _save(wrong, _next(wrong), choice="b")
    out = StringIO()
    call_command("route", "hh_rlhf", stdout=out)
    assert "person on hidden gold checks: 1 of 2" in out.getvalue()


def test_about_one_row_in_ten_is_a_check_by_default(settings):
    settings.PREFKIT_GOLD_CHECK_EVERY = 10
    hits = sum(goldchecks._roll("ann", "hh_rlhf", n) % 10 == 0 for n in range(2000))
    assert 150 < hits < 250  # close to 200, the expected one in ten
