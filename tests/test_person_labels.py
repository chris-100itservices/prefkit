"""Person labelling, sign-in, measured costs and `route --apply`. No model and no Docker needed."""

import json
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from prefkit import models as db
from prefkit.judge.runner import judge_dataset

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset():
    return db.Dataset.objects.create(name="humaneval", manifest_version=1)


def signed_in(username="ann"):
    """A browser client signed in as `username`. The account is created if needed."""
    user, _ = get_user_model().objects.get_or_create(username=username)
    client = Client()
    client.force_login(user)
    return client


def _row(dataset, i, winner="a", gold=True, source="execution"):
    prompt = db.Prompt.objects.create(id=f"p{i}", text=f"question {i}")
    a = db.Response.objects.create(id=f"a{i}", prompt=prompt, text="first answer")
    b = db.Response.objects.create(id=f"b{i}", prompt=prompt, text="second answer")
    return db.Comparison.objects.create(
        id=f"c{i}", dataset=dataset, prompt=prompt, a=a, b=b, winner=winner, gold=gold, source=source
    )


def _route_to_person(*rows):
    for row in rows:
        db.RouteDecision.objects.create(comparison=row, source="person", target=0.9)


def _next(client, name="humaneval"):
    return client.get(reverse("prefkit:label-next", args=[name]))


def _save(client, row, choice="a", seconds=12.5):
    return client.post(
        reverse("prefkit:label-save", args=[row.pk]),
        data=json.dumps({"choice": choice, "seconds": seconds}),
        content_type="application/json",
    )


# ------------------------------------------------------------------ signing in


def test_label_page_sends_a_signed_out_person_to_sign_in(client, dataset):
    response = client.get(reverse("prefkit:label", args=["humaneval"]))
    assert response.status_code == 302
    assert response["Location"].startswith("/accounts/login/?next=/label/humaneval/")


def test_sign_in_page_is_shown(client):
    page = client.get("/accounts/login/").content.decode()
    assert "Sign in" in page
    assert "add_annotator" in page


def test_the_api_refuses_a_signed_out_person(client, dataset):
    row = _row(dataset, 1)
    _route_to_person(row)
    assert _next(client).status_code == 401
    assert _save(client, row).status_code == 401
    assert not db.PersonLabel.objects.exists()


def test_label_is_stored_under_the_signed_in_username(dataset):
    row = _row(dataset, 1)
    _route_to_person(row)
    assert _save(signed_in("chris"), row).status_code == 201
    assert db.PersonLabel.objects.get().annotator == "chris"


# ------------------------------------------------------------------ the page and API


def test_next_shows_the_row_but_never_its_winner(dataset):
    _route_to_person(_row(dataset, 1))
    body = _next(signed_in()).json()
    assert body["done"] is False
    assert body["prompt"] == "question 1"
    assert "winner" not in body
    assert "gold" not in body


def test_only_rows_routed_to_a_person_are_shown(dataset):
    _row(dataset, 1)  # not routed
    body = _next(signed_in()).json()
    assert (body["done"], body["remaining"]) == (True, 0)


def test_saving_moves_on_to_the_next_row(dataset):
    first, second = _row(dataset, 1), _row(dataset, 2)
    _route_to_person(first, second)
    client = signed_in()
    assert _save(client, first).status_code == 201
    body = _next(client).json()
    assert body["remaining"] == 1
    assert body["prompt"] == "question 2"


def test_a_person_labels_each_row_once_but_others_can_too(dataset):
    row = _row(dataset, 1)
    _route_to_person(row)
    ann = signed_in("ann")
    assert _save(ann, row).status_code == 201
    assert _save(ann, row).status_code == 409
    assert _save(signed_in("bob"), row).status_code == 201
    assert db.PersonLabel.objects.count() == 2


def test_bad_labels_are_refused(dataset):
    row = _row(dataset, 1)
    _route_to_person(row)
    client = signed_in()
    assert _save(client, row, choice="maybe").status_code == 400
    assert _save(client, row, seconds="soon").status_code == 400
    assert not db.PersonLabel.objects.exists()


def test_a_row_not_routed_to_a_person_cannot_be_labelled(dataset):
    row = _row(dataset, 1)
    assert _save(signed_in(), row).status_code == 409


def test_absurd_times_are_capped(dataset):
    row = _row(dataset, 1)
    _route_to_person(row)
    _save(signed_in(), row, seconds=999999)
    assert db.PersonLabel.objects.get().seconds == 3600.0


def test_label_page_explains_what_to_do_when_nothing_is_routed(dataset):
    page = signed_in().get(reverse("prefkit:label", args=["humaneval"])).content.decode()
    assert "route humaneval --apply" in page
    assert "Signed in as" in page


# ------------------------------------------------------------------ route command


def _route(*args, name="humaneval"):
    out = StringIO()
    call_command("route", name, *args, stdout=out)
    return out.getvalue()


def test_route_measures_people_against_gold_rows(dataset):
    right, wrong = _row(dataset, 1, winner="a"), _row(dataset, 2, winner="a")
    _route_to_person(right, wrong)
    client = signed_in()
    _save(client, right, choice="a", seconds=20)
    _save(client, wrong, choice="b", seconds=40)
    text = _route()
    assert "person: correct=1 of 2" in text
    assert "cost=30.0s per label (measured)" in text


def test_route_reports_agreement_on_rows_without_a_known_answer():
    hh = db.Dataset.objects.create(name="hh_rlhf", manifest_version=1)
    same, different = (_row(hh, i, winner="a", gold=False, source="human") for i in (1, 2))
    _route_to_person(same, different)
    client = signed_in()
    _save(client, same, choice="a")
    _save(client, different, choice="b")
    text = _route(name="hh_rlhf")
    assert "person: not measured" in text  # no gold rows, so no accuracy
    assert "person agreement with the dataset's own label: 1 of 2" in text
    assert "(agreement, not accuracy)" in text


def test_route_apply_writes_one_decision_per_row_and_replaces_old_ones(dataset):
    _row(dataset, 1)
    _row(dataset, 2)
    assert "applied: 2 rows routed to person" in _route("--apply")
    _route("--apply")
    assert db.RouteDecision.objects.count() == 2
    assert set(db.RouteDecision.objects.values_list("source", flat=True)) == {"person"}


def test_route_without_apply_changes_nothing(dataset):
    _row(dataset, 1)
    _route()
    assert not db.RouteDecision.objects.exists()


def test_code_check_cost_comes_from_its_timed_run(dataset):
    # 100 problems + 900 wrong versions = 1000 programs in 500 s: 0.5 s per program,
    # and a label runs both solutions, so 1.0 s per label.
    db.CodeCheckRun.objects.create(
        dataset=dataset, tasks=100, skipped=0, mutants=900, caught=890, seconds=500
    )
    assert "cost=1.0s per label (measured)" in _route()


def test_untimed_code_check_run_uses_the_placeholder_cost(dataset):
    db.CodeCheckRun.objects.create(dataset=dataset, tasks=100, skipped=0, mutants=900, caught=890)
    assert "(placeholder)" in _route()


# ------------------------------------------------------------------ timing


def test_judge_records_seconds_per_row(dataset):
    _row(dataset, 1)
    judge_dataset("humaneval", lambda question, a, b: "A" if a == "first answer" else "B", "fake")
    verdict = db.JudgeVerdict.objects.get()
    assert verdict.seconds is not None and verdict.seconds >= 0
    assert "judge: correct=1 of 1" in _route("--model", "fake")
