"""The judge: prompt, reply parsing, agreement statistics, and storing verdicts.

These tests use a fake judge, so they run without torch or a model download.
"""

import pytest

from prefkit import models as db
from prefkit.judge.agreement import agreement, wilson_interval
from prefkit.judge.prompting import MAX_ANSWER_CHARS, build_prompt, parse_verdict
from prefkit.judge.runner import judge_dataset, judge_pair, reset_verdicts, summarise


def test_parse_verdict_reads_the_first_word_only():
    assert parse_verdict("A") == "a"
    assert parse_verdict("b") == "b"
    assert parse_verdict("TIE.") == "tie"
    assert parse_verdict("B, because it is clearer") == "b"


def test_parse_verdict_refuses_to_guess():
    assert parse_verdict("I think maybe the first one") is None  # first word is not a verdict
    assert parse_verdict("") is None
    assert parse_verdict("   ") is None


def test_long_answers_are_cut_so_the_prompt_stays_small():
    prompt = build_prompt("question", "x" * (MAX_ANSWER_CHARS + 500), "short")

    assert "[cut short]" in prompt
    assert "x" * (MAX_ANSWER_CHARS + 1) not in prompt


def test_wilson_interval_for_200_rows_at_65_percent():
    low, high = wilson_interval(130, 200)

    assert round(low, 2) == 0.58
    assert round(high, 2) == 0.71


def test_wilson_interval_for_20_rows_is_wide():
    low, high = wilson_interval(13, 20)

    assert round(low, 2) == 0.43
    assert round(high, 2) == 0.82


def test_agreement_skips_rows_with_a_missing_side():
    result = agreement([("a", "a"), ("b", "a"), (None, "a"), ("tie", None)])

    assert result.compared == 2
    assert result.agreed == 1
    assert result.rate == 0.5


def prefers_first_answer(question, a, b):
    """A judge that is consistent: it always picks the answer whose text is "first answer"."""
    return "A" if a == "first answer" else "B"


def _comparison(dataset, i, winner, source="human"):
    prompt = db.Prompt.objects.create(id=f"p{i}", text=f"question {i}")
    a = db.Response.objects.create(id=f"a{i}", prompt=prompt, text="first answer")
    b = db.Response.objects.create(id=f"b{i}", prompt=prompt, text="second answer")
    return db.Comparison.objects.create(
        id=f"c{i}", dataset=dataset, prompt=prompt, a=a, b=b, winner=winner, source=source
    )


@pytest.mark.django_db
def test_judge_stores_verdicts_and_leaves_human_labels_alone():
    dataset = db.Dataset.objects.create(name="demo", manifest_version=1)
    _comparison(dataset, 1, "a")
    _comparison(dataset, 2, "b")
    _comparison(dataset, 3, None)  # unlabelled rows are not judged
    _comparison(dataset, 4, "a", source="execution")  # only human rows are judged

    run = judge_dataset("demo", prefers_first_answer, judge_model="fake")

    assert run.judged == 2
    assert db.Comparison.objects.get(pk="c2").winner == "b"  # human label untouched
    pairs = db.JudgeVerdict.objects.values_list("verdict", "comparison__winner")
    assert sorted(pairs) == [("a", "a"), ("a", "b")]


@pytest.mark.django_db
def test_rerunning_the_judge_does_not_judge_twice():
    dataset = db.Dataset.objects.create(name="demo", manifest_version=1)
    _comparison(dataset, 1, "a")

    judge_dataset("demo", prefers_first_answer, judge_model="fake")
    second = judge_dataset("demo", prefers_first_answer, judge_model="fake")

    assert second.judged == 0
    assert db.JudgeVerdict.objects.count() == 1


@pytest.mark.django_db
def test_unparsed_replies_are_counted_and_kept_as_no_verdict():
    dataset = db.Dataset.objects.create(name="demo", manifest_version=1)
    _comparison(dataset, 1, "a")

    run = judge_dataset("demo", lambda q, a, b: "I am not sure", judge_model="fake")

    assert run.no_verdict == 1
    verdict = db.JudgeVerdict.objects.get()
    assert verdict.verdict is None
    assert "I am not sure" in verdict.raw_reply
    assert summarise("demo", "fake").overall.compared == 0


@pytest.mark.django_db
def test_limit_judges_only_the_first_rows():
    dataset = db.Dataset.objects.create(name="demo", manifest_version=1)
    for i in range(5):
        _comparison(dataset, i, "a")

    run = judge_dataset("demo", prefers_first_answer, judge_model="fake", limit=2)

    assert run.judged == 2


def test_a_judge_that_flips_with_position_gives_no_verdict():
    """Always answering A is position bias, not a judgment, so the verdict is withheld."""
    result = judge_pair(lambda q, a, b: "A", "q", "x", "y")
    assert result == (None, "order A-B: A | order B-A: A")


def test_tie_is_kept_when_both_orders_say_tie():
    verdict, _ = judge_pair(lambda q, a, b: "TIE", "q", "x", "y")

    assert verdict == "tie"


@pytest.mark.django_db
def test_summary_splits_ties_from_choices():
    dataset = db.Dataset.objects.create(name="demo", manifest_version=1)
    _comparison(dataset, 1, "a")  # judge will choose a: agrees
    _comparison(dataset, 2, "b")  # judge will choose a: disagrees
    _comparison(dataset, 3, "a")  # judge will say tie
    judge_dataset("demo", prefers_first_answer, judge_model="fake")
    db.JudgeVerdict.objects.filter(comparison_id="c3").update(verdict="tie")

    summary = summarise("demo", "fake")

    assert summary.judged == 3
    assert summary.ties == 1
    assert summary.overall.compared == 3
    assert summary.choices_only.compared == 2
    assert summary.choices_only.agreed == 1


@pytest.mark.django_db
def test_reset_removes_only_that_models_verdicts():
    dataset = db.Dataset.objects.create(name="demo", manifest_version=1)
    _comparison(dataset, 1, "a")
    judge_dataset("demo", prefers_first_answer, judge_model="fake")
    judge_dataset("demo", prefers_first_answer, judge_model="other")

    assert reset_verdicts("demo", "fake") == 1
    assert db.JudgeVerdict.objects.filter(judge_model="other").count() == 1
