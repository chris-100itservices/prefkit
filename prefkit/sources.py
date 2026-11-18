"""Measure each label source for one dataset, from what is stored in the database.

Accuracy is always measured on gold rows: rows whose correct answer is known.
Cost is measured as seconds of work per label. When a source has not been timed yet,
its cost is the placeholder in routing.DEFAULT_COSTS, and `cost_measured` says so.

Why seconds: it is the one unit all three sources share. The code check and the judge
use computer time, a person uses their own time. A person's second usually costs more
money than a computer's, but that does not change the router's choice: a person is only
ever the fallback, so the person's cost is reported, never compared.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Avg

from prefkit import models as db
from prefkit import routing


@dataclass(frozen=True)
class Measured:
    stats: routing.SourceStats
    cost_measured: bool  # False when the cost is a placeholder


def _gold_rows(dataset: str):
    return db.Comparison.objects.filter(dataset_id=dataset, gold=True, winner__isnull=False)


def code_check(dataset: str) -> Measured:
    """The latest measure_code run: wrong versions caught, out of wrong versions run.

    Cost: labelling a pair means running both solutions against the tests, so the cost
    per label is twice the average time of one program run.
    """
    run = db.CodeCheckRun.objects.filter(dataset_id=dataset).first()
    if run is None:
        return Measured(routing.SourceStats("code_check", routing.DEFAULT_COSTS["code_check"], 0, 0), False)
    programs = run.tasks + run.mutants  # every problem's own solution, plus each wrong version
    if run.seconds and programs:
        cost, measured = 2 * run.seconds / programs, True
    else:
        cost, measured = routing.DEFAULT_COSTS["code_check"], False
    return Measured(routing.SourceStats("code_check", cost, run.caught, run.mutants), measured)


def judge(dataset: str, model: str) -> Measured:
    """The judge's verdicts on gold rows. A row with no verdict counts as wrong."""
    gold = list(_gold_rows(dataset).values_list("id", "winner"))
    verdicts = dict(
        db.JudgeVerdict.objects.filter(
            comparison__dataset_id=dataset, comparison__gold=True, judge_model=model
        ).values_list("comparison_id", "verdict")
    )
    average = db.JudgeVerdict.objects.filter(
        comparison__dataset_id=dataset, judge_model=model, seconds__isnull=False
    ).aggregate(avg=Avg("seconds"))["avg"]
    cost = average if average is not None else routing.DEFAULT_COSTS["judge"]
    stats = routing.stats_from_pairs("judge", ((verdicts.get(cid), winner) for cid, winner in gold), cost)
    return Measured(stats, average is not None)


def person(dataset: str) -> Measured:
    """Every person label on a gold row, against the known winner. Each label counts once."""
    pairs = db.PersonLabel.objects.filter(
        comparison__dataset_id=dataset, comparison__gold=True, comparison__winner__isnull=False
    ).values_list("choice", "comparison__winner")
    average = db.PersonLabel.objects.filter(comparison__dataset_id=dataset).aggregate(avg=Avg("seconds"))[
        "avg"
    ]
    cost = average if average is not None else routing.DEFAULT_COSTS[routing.PERSON]
    return Measured(routing.stats_from_pairs(routing.PERSON, pairs, cost), average is not None)


def person_agreement(dataset: str) -> routing.SourceStats:
    """Person labels on rows that are not gold, against the dataset's own human label.

    This is agreement, not accuracy: the dataset's label is one person's opinion, so two
    labels can agree and both be wrong. It is reported, but the router never uses it.
    """
    pairs = db.PersonLabel.objects.filter(
        comparison__dataset_id=dataset,
        comparison__gold=False,
        comparison__source="human",
        comparison__winner__isnull=False,
    ).values_list("choice", "comparison__winner")
    return routing.stats_from_pairs("person agreement", pairs, cost=0.0)


def person_checks(dataset: str) -> routing.SourceStats:
    """People's labels on hidden gold checks shown on this dataset's page, against the known winner.

    This is accuracy, because the check rows have a known answer. The check rows come from
    other datasets, so it measures care and judgement rather than skill on this subject.
    """
    pairs = db.PersonLabel.objects.filter(check_for=dataset).values_list("choice", "comparison__winner")
    return routing.stats_from_pairs("person on hidden checks", pairs, cost=0.0)
