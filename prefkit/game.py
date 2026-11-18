"""The annotator game: points for labels, weighted by how accurate the person has shown to be.

Why points depend on accuracy: a game that rewarded the number of labels alone would reward
fast, careless clicking. Here every label is worth POINTS_PER_LABEL times the person's
accuracy on rows with a known answer (gold rows, including hidden gold checks). A careless
person earns fewer points for every label they make, not only for the wrong ones.

Why the pessimistic estimate: the accuracy used is the lower end of its 95% range, as in
label routing. Someone with 3 right out of 3 has not yet shown much, so their points per
label start low and rise as more known answers confirm their accuracy. This rewards steady,
careful work over a lucky streak.

Why rushed labels earn nothing: a label given in under MIN_SECONDS was too fast to have read
both answers. It is still stored, because it is still a label, but it earns no points.

Why there is no feedback per row: telling a person that a row was right or wrong would show
which rows are hidden checks, and the checks would stop measuring their usual work. The
page only shows running totals.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from prefkit import models as db
from prefkit.judge.agreement import wilson_interval

POINTS_PER_LABEL = 10
MIN_SECONDS = 3.0

# (points needed, level name), from the lowest level up.
LEVELS = [(0, "Starter"), (100, "Bronze"), (500, "Silver"), (2000, "Gold"), (5000, "Platinum")]


@dataclass(frozen=True)
class Score:
    annotator: str
    labels: int  # every label the person made
    counted: int  # labels that took at least MIN_SECONDS
    known_right: int  # labels on rows with a known answer that matched it
    known_total: int  # labels on rows with a known answer
    accuracy: float  # lower end of the 95% range of known_right / known_total, 0 when none
    points: int
    level: str
    next_level: str | None  # the next level's name, None at the top
    to_next: int  # points still needed for the next level


def level_for(points: int) -> tuple[str, str | None, int]:
    """The level for a number of points, the next level, and how many points it still needs."""
    name, next_name, to_next = LEVELS[0][1], None, 0
    for i, (needed, level) in enumerate(LEVELS):
        if points >= needed:
            name = level
            if i + 1 < len(LEVELS):
                next_name, to_next = LEVELS[i + 1][1], LEVELS[i + 1][0] - points
            else:
                next_name, to_next = None, 0
    return name, next_name, to_next


def _score(annotator: str, labels: int, counted: int, right: int, total: int) -> Score:
    accuracy = wilson_interval(right, total)[0] if total else 0.0
    points = round(POINTS_PER_LABEL * counted * accuracy)
    level, next_level, to_next = level_for(points)
    return Score(annotator, labels, counted, right, total, accuracy, points, level, next_level, to_next)


def scores(annotator: str | None = None) -> list[Score]:
    """Everyone's score, highest first. With `annotator`, only that person's score."""
    rows = db.PersonLabel.objects.all()
    if annotator is not None:
        rows = rows.filter(annotator=annotator)
    tally = defaultdict(lambda: [0, 0, 0, 0])  # labels, counted, known right, known total
    for name, choice, seconds, gold, winner in rows.values_list(
        "annotator", "choice", "seconds", "comparison__gold", "comparison__winner"
    ):
        t = tally[name]
        t[0] += 1
        if seconds >= MIN_SECONDS:
            t[1] += 1
        if gold and winner is not None:
            t[3] += 1
            if choice == winner:
                t[2] += 1
    result = [_score(name, *t) for name, t in tally.items()]
    if annotator is not None and not result:
        result = [_score(annotator, 0, 0, 0, 0)]
    result.sort(key=lambda s: (-s.points, s.annotator))
    return result


def score_for(annotator: str) -> Score:
    return scores(annotator)[0]


def as_dict(score: Score) -> dict:
    """The score as the labelling page shows it."""
    return {
        "points": score.points,
        "level": score.level,
        "next_level": score.next_level,
        "to_next": score.to_next,
        "labels": score.labels,
        "counted": score.counted,
        "known_right": score.known_right,
        "known_total": score.known_total,
        "accuracy": round(score.accuracy * 100),
    }
