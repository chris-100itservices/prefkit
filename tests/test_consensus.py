"""Majority answers from several people, and creating accounts for them."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from prefkit import models as db
from prefkit.consensus import majority

# ------------------------------------------------------------------ the majority rule


def test_more_than_half_the_votes_wins():
    assert majority(["a", "a", "b"]) == "a"
    assert majority(["b", "b", "b"]) == "b"
    assert majority(["tie", "tie", "a"]) == "tie"


def test_no_majority_when_votes_split():
    assert majority(["a", "b", "tie"]) is None  # every vote differs
    assert majority(["a", "a", "b", "b"]) is None  # even split


def test_too_few_votes_give_no_answer():
    assert majority(["a", "a"]) is None  # fewer than the default minimum of 3
    assert majority(["a", "a"], min_votes=2) == "a"
    assert majority([], min_votes=1) is None


# ------------------------------------------------------------------ the command

pytestmark_db = pytest.mark.django_db


def _row(dataset, i, winner="a", gold=True, source="execution"):
    prompt = db.Prompt.objects.create(id=f"p{dataset.name}{i}", text=f"question {i}")
    a = db.Response.objects.create(id=f"a{dataset.name}{i}", prompt=prompt, text="first")
    b = db.Response.objects.create(id=f"b{dataset.name}{i}", prompt=prompt, text="second")
    return db.Comparison.objects.create(
        id=f"c{dataset.name}{i}", dataset=dataset, prompt=prompt, a=a, b=b,
        winner=winner, gold=gold, source=source,
    )


def _votes(row, *choices):
    for n, choice in enumerate(choices):
        db.PersonLabel.objects.create(comparison=row, annotator=f"person{n}", choice=choice, seconds=10)


def _consensus(name, *args):
    out = StringIO()
    call_command("consensus", name, *args, stdout=out)
    return out.getvalue()


@pytestmark_db
def test_consensus_stores_majority_answers_and_scores_them_on_gold_rows():
    ds = db.Dataset.objects.create(name="mbpp", manifest_version=1)
    right = _row(ds, 1, winner="a")
    wrong = _row(ds, 2, winner="a")
    split = _row(ds, 3, winner="a")
    few = _row(ds, 4, winner="a")
    _votes(right, "a", "a", "b")  # majority a, correct
    _votes(wrong, "b", "b", "a")  # majority b, wrong
    _votes(split, "a", "b", "tie")  # no majority, counts as wrong
    _votes(few, "a")  # too few votes, left out

    text = _consensus("mbpp")

    assert "4 rows labelled by people, 3 with at least 3 votes, 2 with a majority" in text
    assert "majority on gold rows, against the known winner: 1 of 3" in text
    stored = {c.comparison_id: (c.choice, c.votes, c.agreeing) for c in db.ConsensusLabel.objects.all()}
    assert stored == {right.id: ("a", 3, 2), wrong.id: ("b", 3, 2)}


@pytestmark_db
def test_consensus_reports_agreement_on_rows_without_a_known_answer():
    ds = db.Dataset.objects.create(name="hh_rlhf", manifest_version=1)
    _votes(_row(ds, 1, winner="a", gold=False, source="human"), "a", "a", "a")
    _votes(_row(ds, 2, winner="a", gold=False, source="human"), "b", "b", "a")

    text = _consensus("hh_rlhf")

    assert "against the dataset's own human label (agreement, not accuracy): 1 of 2" in text
    assert "majority on gold rows" not in text


@pytestmark_db
def test_rerunning_replaces_the_old_answers():
    ds = db.Dataset.objects.create(name="mbpp", manifest_version=1)
    row = _row(ds, 1)
    _votes(row, "a", "a")
    _consensus("mbpp", "--min-votes", "2")
    assert db.ConsensusLabel.objects.count() == 1
    _consensus("mbpp")  # back to the default of 3: two votes are now too few
    assert db.ConsensusLabel.objects.count() == 0


@pytestmark_db
def test_consensus_rejects_an_unknown_dataset():
    with pytest.raises(CommandError, match="no dataset"):
        _consensus("nope")


# ------------------------------------------------------------------ accounts


@pytestmark_db
def test_add_annotator_creates_an_account(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "long enough password")
    call_command("add_annotator", "chris", stdout=StringIO())
    user = get_user_model().objects.get(username="chris")
    assert user.check_password("long enough password")
    assert not user.is_staff  # a labeller cannot open the admin pages


@pytestmark_db
def test_add_annotator_refuses_different_passwords(monkeypatch):
    answers = iter(["first password", "second password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with pytest.raises(CommandError, match="different"):
        call_command("add_annotator", "chris", stdout=StringIO())
    assert not get_user_model().objects.exists()


@pytestmark_db
def test_add_annotator_refuses_a_short_password(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "short")
    with pytest.raises(CommandError, match="at least 8"):
        call_command("add_annotator", "chris", stdout=StringIO())


@pytestmark_db
def test_add_annotator_refuses_an_existing_name(monkeypatch):
    get_user_model().objects.create_user(username="chris", password="x" * 10)
    with pytest.raises(CommandError, match="already exists"):
        call_command("add_annotator", "chris", stdout=StringIO())
