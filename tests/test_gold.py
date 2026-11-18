"""Gold items: rows whose winner is known for certain, and how the judge does on them."""

import json

import pytest

from prefkit import models as db
from prefkit.datasets.ingest import map_row
from prefkit.datasets.manifest import load_manifest
from prefkit.judge.runner import judge_dataset, summarise


def _manifest(tmp_path, judged_by):
    data = tmp_path / "x.jsonl"
    data.write_text(json.dumps({"chosen": "u", "rejected": "v"}), encoding="utf-8")
    path = tmp_path / "x.yaml"
    path.write_text(
        f"""\
name: x
version: 1
source:
  type: local
  location: {data}
card: https://example.org
license: mit
judged_by: {judged_by}
mapping:
  prompt_strategy: common_prefix
  response_a_field: chosen
  response_b_field: rejected
  winner_fixed: a
gold:
  kind: none
""",
        encoding="utf-8",
    )
    return load_manifest(path)


def test_execution_rows_are_gold(tmp_path):
    row = {"chosen": "\n\nHuman: q\n\nAssistant: right", "rejected": "\n\nHuman: q\n\nAssistant: wrong"}

    assert map_row(row, _manifest(tmp_path, "execution")).comparison.gold is True


def test_human_rows_are_not_gold_without_a_marker(tmp_path):
    row = {"chosen": "\n\nHuman: q\n\nAssistant: right", "rejected": "\n\nHuman: q\n\nAssistant: wrong"}

    assert map_row(row, _manifest(tmp_path, "human")).comparison.gold is False


def prefers_first_answer(question, a, b):
    return "A" if a == "first answer" else "B"


@pytest.mark.django_db
def test_judge_is_scored_on_gold_rows_against_the_known_winner():
    dataset = db.Dataset.objects.create(name="code", manifest_version=1)
    prompt = db.Prompt.objects.create(id="p1", text="question")
    a = db.Response.objects.create(id="a1", prompt=prompt, text="first answer")
    b = db.Response.objects.create(id="b1", prompt=prompt, text="second answer")
    db.Comparison.objects.create(
        id="g1", dataset=dataset, prompt=prompt, a=a, b=b, winner="a", gold=True, source="execution"
    )
    db.Comparison.objects.create(
        id="g2", dataset=dataset, prompt=prompt, a=a, b=b, winner="b", gold=True, source="execution"
    )

    judge_dataset("code", prefers_first_answer, judge_model="fake")
    summary = summarise("code", "fake")

    assert summary.gold.compared == 2
    assert summary.gold.agreed == 1  # the judge is right on g1 and wrong on g2
    assert summary.overall.compared == 2  # gold rows also count in the overall figure
