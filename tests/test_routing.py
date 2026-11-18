"""Tests for label routing. These need no database and no model."""

from prefkit import routing


def test_lower_bound_is_pessimistic_for_small_samples():
    small = routing.SourceStats(name="judge", cost=10, correct=9, measured=10)
    large = routing.SourceStats(name="judge", cost=10, correct=900, measured=1000)
    assert small.lower_bound() < 0.7
    assert large.lower_bound() > 0.85


def test_judge_stats_counts_missing_verdicts_as_wrong():
    pairs = [("a", "a"), ("b", "a"), (None, "a"), ("a", None), (None, None)]
    stats = routing.judge_stats(pairs, cost=10)
    assert stats.measured == 5
    assert stats.correct == 1


def test_judge_stats_with_no_rows_is_unmeasured():
    stats = routing.judge_stats([], cost=10)
    assert stats.measured == 0


def test_unmeasured_source_is_never_chosen():
    unmeasured = routing.SourceStats(name="code_check", cost=1, correct=0, measured=0)
    assert routing.choose([unmeasured]) == routing.PERSON


def test_cheapest_source_meeting_target_wins():
    good_judge = routing.SourceStats(name="judge", cost=10, correct=950, measured=1000)
    good_check = routing.SourceStats(name="code_check", cost=1, correct=990, measured=1000)
    assert routing.choose([good_judge, good_check], target=0.90) == "code_check"


def test_falls_back_to_person_when_no_source_is_good_enough():
    weak_judge = routing.SourceStats(name="judge", cost=10, correct=14, measured=308)
    assert routing.choose([weak_judge], target=0.90) == routing.PERSON


def test_small_sample_is_not_trusted_even_with_perfect_score():
    perfect_but_small = routing.SourceStats(name="judge", cost=10, correct=20, measured=20)
    assert routing.choose([perfect_but_small], target=0.90) == routing.PERSON


def test_the_measured_numbers_from_the_project_route_to_person():
    mbpp_7b = routing.judge_stats(
        [("a", "a")] * 14 + [(None, "a")] * 294,
        cost=10,
    )
    humaneval_7b = routing.judge_stats(
        [("a", "a")] * 21 + [(None, "a")] * 124,
        cost=10,
    )
    assert routing.choose([mbpp_7b]) == routing.PERSON
    assert routing.choose([humaneval_7b]) == routing.PERSON
    