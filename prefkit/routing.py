"""Choose the cheapest label source that is accurate enough for a kind of row.

Why the router uses the lower end of the interval: a source measured on 20 rows can
show 90% accuracy while its true accuracy is much lower. Trusting only the lower end
means a source is used only when even the pessimistic estimate meets the target.

Why person is the fallback: a person is the source we trust without measuring the
source first. If no measured source is good enough, the row goes to a person.

Why the rule is strict about judge misses: a row the judge gives no verdict on still
has to be labelled by someone else. So a missing verdict counts as wrong, and the
judge's accuracy is measured over every row it was given, not only the rows it decided.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from prefkit.judge.agreement import wilson_interval

PERSON = "person"
DEFAULT_TARGET = 0.90

# Cost per label, in seconds of work. These placeholders are used only until a source has
# been timed: measure_code times the code check, the judge command times the judge, and the
# labelling page times each person.
DEFAULT_COSTS = {"code_check": 1.0, "judge": 10.0, PERSON: 100.0}


@dataclass(frozen=True)
class SourceStats:
    name: str
    cost: float  # relative cost per row
    correct: int  # gold rows this source labelled correctly
    measured: int  # gold rows this source was given, right or wrong

    def lower_bound(self) -> float:
        """The pessimistic end of the 95% interval for this source's accuracy."""
        low, _ = wilson_interval(self.correct, self.measured)
        return low


def stats_from_pairs(name: str, pairs: Iterable[tuple[str | None, str | None]], cost: float) -> SourceStats:
    """Measure a source on gold rows. Each pair is (the source's label, known winner).

    A label of None means the source gave no usable answer. That counts as wrong,
    because the row would still need another source.
    """
    correct = measured = 0
    for label, winner in pairs:
        measured += 1
        if label is not None and label == winner:
            correct += 1
    return SourceStats(name=name, cost=cost, correct=correct, measured=measured)


def judge_stats(pairs: Iterable[tuple[str | None, str | None]], cost: float) -> SourceStats:
    """Measure the judge on gold rows. Each pair is (judge verdict, known winner)."""
    return stats_from_pairs("judge", pairs, cost)


def choose(stats: Sequence[SourceStats], target: float = DEFAULT_TARGET) -> str:
    """Return the cheapest measured source whose lower bound meets the target, else person.

    A source with no measurements (measured == 0) is never chosen.
    """
    eligible = [s for s in stats if s.measured > 0 and s.lower_bound() >= target]
    if not eligible:
        return PERSON
    return min(eligible, key=lambda s: s.cost).name
