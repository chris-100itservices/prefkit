"""The annotator game: points, levels and the leaderboard."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from prefkit import game
from prefkit import models as db

pytestmark = pytest.mark.django_db


@pytest.fixture
def rows():
    """Ten gold rows whose known answer is a, and two rows with no known answer."""
    ds = db.Dataset.objects.create(name="mbpp", manifest_version=1)
    made = []
    for i in range(12):
        prompt = db.Prompt.objects.create(id=f"p{i}", text=f"q{i}")
        a = db.Response.objects.create(id=f"a{i}", prompt=prompt, text="x")
        b = db.Response.objects.create(id=f"b{i}", prompt=prompt, text="y")
        made.append(db.Comparison.objects.create(
            id=f"c{i:02d}", dataset=ds, prompt=prompt, a=a, b=b, winner="a", gold=i < 10, source="execution"))
    return made


def label(row, who, choice="a", seconds=10.0):
    db.PersonLabel.objects.create(comparison=row, annotator=who, choice=choice, seconds=seconds)


# ------------------------------------------------------------------ the rules


def test_no_known_answers_means_no_points_yet(rows):
    label(rows[10], "ann")
    label(rows[11], "ann")
    s = game.score_for("ann")
    assert (s.labels, s.counted, s.known_total, s.points) == (2, 2, 0, 0)


def test_points_rise_as_more_known_answers_prove_right(rows):
    for row in rows[:3]:
        label(row, "few")
    for row in rows[:10]:
        label(row, "many")
    few, many = game.score_for("few"), game.score_for("many")
    assert few.known_right == 3 and many.known_right == 10
    # Both are 100% right, but more evidence earns more points per label.
    assert few.points / few.counted < many.points / many.counted


def test_careless_labels_earn_fewer_points_per_label(rows):
    for row in rows[:10]:
        label(row, "careful", "a")
    for i, row in enumerate(rows[:10]):
        label(row, "careless", "a" if i < 6 else "b")
    careful, careless = game.score_for("careful"), game.score_for("careless")
    assert careless.points < careful.points
    assert careless.labels == careful.labels  # same amount of work


def test_rushed_labels_earn_nothing(rows):
    for row in rows[:10]:
        label(row, "slow", seconds=10)
    for row in rows[:10]:
        label(row, "rushed", seconds=1)
    assert game.score_for("rushed").points == 0
    assert game.score_for("rushed").counted == 0
    assert game.score_for("slow").points > 0


def test_points_follow_the_formula(rows):
    for row in rows[:10]:
        label(row, "ann")
    s = game.score_for("ann")
    assert s.points == round(game.POINTS_PER_LABEL * 10 * s.accuracy)


def test_levels():
    assert game.level_for(0) == ("Starter", "Bronze", 100)
    assert game.level_for(99) == ("Starter", "Bronze", 1)
    assert game.level_for(100) == ("Bronze", "Silver", 400)
    assert game.level_for(9999) == ("Platinum", None, 0)


def test_scores_are_ranked_highest_first(rows):
    for row in rows[:10]:
        label(row, "best")
    for row in rows[:4]:
        label(row, "second")
    assert [s.annotator for s in game.scores()] == ["best", "second"]


# ------------------------------------------------------------------ the pages


def signed_in(username):
    user, _ = get_user_model().objects.get_or_create(username=username)
    client = Client()
    client.force_login(user)
    return client


def test_the_labelling_answer_carries_running_totals_only(rows):
    for row in rows:
        db.RouteDecision.objects.create(comparison=row, source="person", target=0.9)
    client = signed_in("ann")
    body = client.get(reverse("prefkit:label-next", args=["mbpp"])).json()
    assert body["score"]["points"] == 0
    answer = json.dumps({"choice": "a", "seconds": 8})
    client.post(body["save_url"], data=answer, content_type="application/json")
    score = client.get(reverse("prefkit:label-next", args=["mbpp"])).json()["score"]
    assert (score["labels"], score["known_right"], score["known_total"]) == (1, 1, 1)
    assert "correct" not in score and "right" not in score  # no result for the single row


def test_the_leaderboard_lists_everyone_and_highlights_you(rows):
    for row in rows[:10]:
        label(row, "ann")
    label(rows[0], "bob")
    page = signed_in("bob").get(reverse("prefkit:leaderboard")).content.decode()
    assert page.index("<td>ann</td>") < page.index("<td>bob</td>")
    assert '<tr class="me">' in page
    assert "How points work" in page


def test_the_leaderboard_needs_sign_in(client):
    response = client.get(reverse("prefkit:leaderboard"))
    assert response.status_code == 302
