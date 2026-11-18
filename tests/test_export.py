"""Exporting rows with their final labels."""

import json
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from prefkit import models as db

pytestmark = pytest.mark.django_db


def _row(dataset, key, winner="a", gold=False, source="human", routed=None):
    prompt = db.Prompt.objects.create(id=f"p{key}", text=f"question {key} – café")
    a = db.Response.objects.create(id=f"a{key}", prompt=prompt, text="first")
    b = db.Response.objects.create(id=f"b{key}", prompt=prompt, text="second")
    row = db.Comparison.objects.create(
        id=f"c{key}", dataset=dataset, prompt=prompt, a=a, b=b, winner=winner, gold=gold, source=source
    )
    if routed:
        db.RouteDecision.objects.create(comparison=row, source=routed, target=0.9)
    return row


def _export(tmp_path, name, *args):
    path = tmp_path / f"{name}.jsonl"
    out = StringIO()
    call_command("export", name, "--output", str(path), "--model", "judge-x", *args, stdout=out)
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {line["id"]: line for line in lines}, out.getvalue()


@pytest.fixture
def mixed():
    """One dataset with a row for each way a label can be decided."""
    ds = db.Dataset.objects.create(name="mix", manifest_version=1)
    rows = {
        "plain": _row(ds, "1", winner="b"),
        "code": _row(ds, "2", winner="a", gold=True, source="execution", routed="code_check"),
        "judged": _row(ds, "3", routed="judge"),
        "unjudged": _row(ds, "4", routed="judge"),
        "voted": _row(ds, "5", routed="person"),
        "unvoted": _row(ds, "6", routed="person"),
    }
    db.JudgeVerdict.objects.create(comparison=rows["judged"], judge_model="judge-x", verdict="b")
    db.JudgeVerdict.objects.create(comparison=rows["unjudged"], judge_model="judge-x", verdict=None)
    db.JudgeVerdict.objects.create(comparison=rows["judged"], judge_model="other-model", verdict="a")
    db.ConsensusLabel.objects.create(comparison=rows["voted"], choice="tie", votes=3, agreeing=2, min_votes=3)
    return rows


def test_each_row_gets_the_label_of_the_source_it_was_routed_to(tmp_path, mixed):
    lines, _ = _export(tmp_path, "mix")
    assert (lines["c1"]["label"], lines["c1"]["label_source"]) == ("b", "dataset")
    assert (lines["c2"]["label"], lines["c2"]["label_source"]) == ("a", "code_check")
    assert (lines["c3"]["label"], lines["c3"]["label_source"]) == ("b", "judge")
    assert (lines["c5"]["label"], lines["c5"]["label_source"]) == ("tie", "person_majority")


def test_a_routed_row_without_an_answer_is_marked_not_guessed(tmp_path, mixed):
    lines, _ = _export(tmp_path, "mix")
    for key in ("c4", "c6"):
        assert lines[key]["label"] is None
        assert lines[key]["label_source"] == "none"
        assert lines[key]["dataset_label"] == "a"  # the dataset's label is kept as evidence


def test_each_line_carries_the_evidence(tmp_path, mixed):
    lines, _ = _export(tmp_path, "mix")
    assert lines["c3"]["judge"] == {"model": "judge-x", "verdict": "b"}  # only the chosen model
    assert lines["c5"]["people"] == {"answer": "tie", "votes": 3, "agreeing": 2}
    assert lines["c1"]["judge"] is None and lines["c1"]["people"] is None
    assert lines["c1"]["prompt"] == "question 1 – café"  # non-English text is kept as written
    assert set(lines["c1"]) == {
        "id", "dataset", "prompt", "answer_a", "answer_b", "label", "label_source",
        "routed_to", "dataset_label", "gold", "judge", "people",
    }


def test_the_summary_counts_labels_by_source(tmp_path, mixed):
    _, out = _export(tmp_path, "mix")
    assert "wrote 6 rows" in out
    assert "label from none: 2" in out
    assert "label from person_majority: 1" in out


def test_unlabelled_rows_can_be_left_out(tmp_path, mixed):
    lines, out = _export(tmp_path, "mix", "--skip-unlabelled")
    assert set(lines) == {"c1", "c2", "c3", "c5"}
    assert "left out, no label yet: 2" in out


def test_no_partial_file_is_left_behind(tmp_path, mixed):
    _export(tmp_path, "mix")
    assert [p.name for p in tmp_path.iterdir()] == ["mix.jsonl"]


def test_unknown_dataset_is_refused(tmp_path):
    with pytest.raises(CommandError, match="no dataset"):
        call_command("export", "nope", "--output", str(tmp_path / "x.jsonl"), stdout=StringIO())
