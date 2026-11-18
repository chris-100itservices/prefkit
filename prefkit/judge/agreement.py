"""Measure how often the judge agrees with people, and how sure we can be of that number.

Why not just report the agreement rate: the rate comes from a sample. Ten rows that
agree 7 times say little, while 2,000 rows that agree 1,400 times say a lot. The
confidence interval shows the range the true agreement probably lies in.

The interval used is the Wilson score interval. It behaves well for small samples and
for rates near 0% or 100%, where the simpler normal-approximation interval goes wrong.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

Z_95 = 1.96  # the number of standard deviations that covers 95% of a normal distribution


@dataclass(frozen=True)
class Agreement:
    compared: int  # rows where both the judge and a person gave a verdict
    agreed: int  # rows where they gave the same verdict
    rate: float  # agreed / compared, between 0 and 1
    low: float  # lower end of the 95% interval
    high: float  # upper end of the 95% interval


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """The 95% range for a proportion, given successes out of trials."""
    if trials == 0:
        return 0.0, 1.0
    p = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    centre = (p + z2 / (2 * trials)) / denominator
    spread = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials)) / denominator
    return max(0.0, centre - spread), min(1.0, centre + spread)


def agreement(pairs: Iterable[tuple[str | None, str | None]]) -> Agreement:
    """Compare (judge verdict, human winner) pairs. Pairs with a missing side are skipped."""
    compared = agreed = 0
    for judge_verdict, human_winner in pairs:
        if judge_verdict is None or human_winner is None:
            continue
        compared += 1
        if judge_verdict == human_winner:
            agreed += 1
    low, high = wilson_interval(agreed, compared)
    rate = agreed / compared if compared else 0.0
    return Agreement(compared=compared, agreed=agreed, rate=rate, low=low, high=high)
