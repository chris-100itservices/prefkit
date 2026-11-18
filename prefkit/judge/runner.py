"""Run a judge over the human-labelled comparisons of one dataset, and store its verdicts.

Each pair is asked twice, once in each order. A judge often prefers whichever answer it
reads first or second, so one question per pair measures position as much as content.
A verdict is kept only when both orders give the same answer.

The judge only sees rows that people have already labelled. That is what makes the
agreement check possible: each verdict can be compared with the human winner.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from django.db.models import Q

from prefkit import models as db
from prefkit.judge.agreement import Agreement, agreement
from prefkit.judge.prompting import parse_verdict

# A judge takes (question, first answer, second answer) and returns the raw reply text.
JudgeFn = Callable[[str, str, str], str]

# Translates a verdict given in swapped order back to the original order.
_SWAP = {"a": "b", "b": "a", "tie": "tie"}


@dataclass(frozen=True)
class JudgeRun:
    judged: int  # rows the judge was asked about
    no_verdict: int  # rows where the replies did not name a verdict, or the two orders disagreed


@dataclass(frozen=True)
class JudgeSummary:
    judged: int  # verdicts stored for this model
    no_verdict: int  # verdicts stored as None
    ties: int  # verdicts that are TIE
    overall: Agreement  # every verdict with a value, TIE counted as a disagreement with A or B
    choices_only: Agreement  # only the rows where the judge chose A or B
    gold: Agreement  # gold rows only: the judge's verdict against the known winner


def judge_pair(judge: JudgeFn, question: str, a: str, b: str) -> tuple[str | None, str]:
    """Ask in both orders. Return (verdict or None, both raw replies for the record)."""
    first_reply = judge(question, a, b)
    second_reply = judge(question, b, a)
    raw = f"order A-B: {first_reply.strip()} | order B-A: {second_reply.strip()}"

    first = parse_verdict(first_reply)
    second = parse_verdict(second_reply)
    if first is None or second is None:
        return None, raw
    if first != _SWAP[second]:
        return None, raw  # the judge changed its answer when the order changed
    return first, raw


def judge_dataset(dataset_name: str, judge: JudgeFn, judge_model: str, limit: int | None = None) -> JudgeRun:
    """Ask the judge about each human-labelled row that has no verdict from this model yet."""
    # Human-labelled rows give agreement. Gold rows give accuracy against a known winner.
    pending = (
        db.Comparison.objects.filter(dataset_id=dataset_name, winner__isnull=False)
        .filter(Q(source="human") | Q(gold=True))
        .exclude(verdicts__judge_model=judge_model)
        .select_related("prompt", "a", "b")
        .order_by("id")
    )
    if limit is not None:
        pending = pending[:limit]

    judged = no_verdict = 0
    for comparison in pending:
        # Timed per row, so the router can compare the judge's cost with other sources.
        started = time.perf_counter()
        verdict, raw = judge_pair(judge, comparison.prompt.text, comparison.a.text, comparison.b.text)
        db.JudgeVerdict.objects.create(
            comparison=comparison,
            judge_model=judge_model,
            verdict=verdict,
            raw_reply=raw[:500],
            seconds=time.perf_counter() - started,
        )
        judged += 1
        if verdict is None:
            no_verdict += 1
    return JudgeRun(judged=judged, no_verdict=no_verdict)


def reset_verdicts(dataset_name: str, judge_model: str) -> int:
    """Delete this judge's verdicts for one dataset. Returns how many were deleted."""
    deleted, _ = db.JudgeVerdict.objects.filter(
        comparison__dataset_id=dataset_name, judge_model=judge_model
    ).delete()
    return deleted


def summarise(dataset_name: str, judge_model: str) -> JudgeSummary:
    """Compare this judge's verdicts with the human winners, in three ways."""
    rows = list(
        db.JudgeVerdict.objects.filter(comparison__dataset_id=dataset_name, judge_model=judge_model)
        .filter(Q(comparison__source="human") | Q(comparison__gold=True))
        .values_list("verdict", "comparison__winner", "comparison__gold")
    )
    with_value = [(verdict, winner) for verdict, winner, _ in rows if verdict is not None]
    choices = [(verdict, winner) for verdict, winner in with_value if verdict in ("a", "b")]
    gold = [(verdict, winner) for verdict, winner, is_gold in rows if verdict is not None and is_gold]
    return JudgeSummary(
        judged=len(rows),
        no_verdict=sum(1 for verdict, _, _ in rows if verdict is None),
        ties=sum(1 for verdict, _, _ in rows if verdict == "tie"),
        overall=agreement(with_value),
        choices_only=agreement(choices),
        gold=agreement(gold),
    )
