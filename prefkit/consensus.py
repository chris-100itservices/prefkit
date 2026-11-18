"""Combine several people's labels on one row into one answer, by majority.

Why a majority: one person makes mistakes, as the measured person accuracy shows. When
several people label the same row, a mistake by one is outvoted by the others, so the
majority answer is usually more accurate than any single label.

Why a minimum number of votes: the "majority" of one person is just that person's label,
and two people who disagree have no majority. The default minimum is 3, the smallest
number where one mistake can be outvoted.

Why a strict majority: more than half the votes must agree. With 3 votes that means at
least 2. If the votes split evenly, or every vote differs, the row gets no answer and
needs more votes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from django.db import transaction

from prefkit import models as db
from prefkit import routing

DEFAULT_MIN_VOTES = 3


def majority(choices: Sequence[str], min_votes: int = DEFAULT_MIN_VOTES) -> str | None:
    """The answer chosen by more than half the votes, or None. None also when there are too few."""
    if len(choices) < min_votes or not choices:
        return None
    choice, count = Counter(choices).most_common(1)[0]
    return choice if count * 2 > len(choices) else None


@dataclass(frozen=True)
class ConsensusRun:
    labelled_rows: int  # rows with at least one person label
    enough_votes: int  # rows with at least min_votes labels
    with_majority: int  # rows where more than half the votes agree
    gold: routing.SourceStats  # majority answers on gold rows, against the known winner
    agreement: routing.SourceStats  # majority answers on human-labelled rows, against that label


def build(dataset: str, min_votes: int = DEFAULT_MIN_VOTES) -> ConsensusRun:
    """Compute the majority answer for every labelled row of a dataset, and store them.

    Earlier majority answers for this dataset are replaced, so the stored answers always
    match the current labels and the current minimum.
    """
    votes: dict[str, list[str]] = defaultdict(list)
    for comparison_id, choice in db.PersonLabel.objects.filter(comparison__dataset_id=dataset).values_list(
        "comparison_id", "choice"
    ):
        votes[comparison_id].append(choice)

    rows = {
        row_id: (winner, gold, source)
        for row_id, winner, gold, source in db.Comparison.objects.filter(pk__in=votes).values_list(
            "id", "winner", "gold", "source"
        )
    }

    decided: dict[str, str | None] = {}
    new_labels = []
    for row_id, choices in votes.items():
        if len(choices) < min_votes:
            continue
        answer = majority(choices, min_votes)
        decided[row_id] = answer
        if answer is not None:
            new_labels.append(
                db.ConsensusLabel(
                    comparison_id=row_id,
                    choice=answer,
                    votes=len(choices),
                    agreeing=choices.count(answer),
                    min_votes=min_votes,
                )
            )

    with transaction.atomic():
        db.ConsensusLabel.objects.filter(comparison__dataset_id=dataset).delete()
        db.ConsensusLabel.objects.bulk_create(new_labels)

    # A row with enough votes but no majority counts as wrong: it still has no answer.
    gold_pairs = [
        (answer, rows[row_id][0])
        for row_id, answer in decided.items()
        if rows[row_id][1] and rows[row_id][0] is not None
    ]
    human_pairs = [
        (answer, rows[row_id][0])
        for row_id, answer in decided.items()
        if not rows[row_id][1] and rows[row_id][2] == "human" and rows[row_id][0] is not None
    ]
    return ConsensusRun(
        labelled_rows=len(votes),
        enough_votes=len(decided),
        with_majority=len(new_labels),
        gold=routing.stats_from_pairs("majority", gold_pairs, cost=0.0),
        agreement=routing.stats_from_pairs("majority agreement", human_pairs, cost=0.0),
    )
