"""Hidden gold checks: rows with a known answer, mixed into a person's labelling queue.

Why: on a dataset without gold rows, a person's labels can only be compared with the
dataset's own label. That shows agreement, not accuracy, because both can be wrong.
Mixing in a few rows whose answer is known measures each person's accuracy on any dataset.

Why hidden: if people knew which rows were checks, they could take extra care on those
rows only, and the checks would no longer measure their usual work. A check row looks
exactly like any other row, and the page never says which is which.

Where checks come from: the gold rows of other datasets, such as HumanEval and MBPP. They
measure care and judgement in general. They say less about skill on the dataset's own
subject, because a coding question is not a chat question.

How often: about one row in every PREFKIT_GOLD_CHECK_EVERY (default 10; 0 turns checks
off). Whether the next row is a check is decided by a hash of the person, the dataset and
how many rows they have labelled there. That looks random to the person, so they cannot
predict which row is a check, but it gives the same answer every time for the same inputs,
which makes it testable.
"""

from __future__ import annotations

import hashlib

from django.conf import settings
from django.db.models import Q

from prefkit import models as db

DEFAULT_CHECK_EVERY = 10
SESSION_KEY = "prefkit_gold_check"  # the check row currently on screen, kept in the session


def check_every() -> int:
    return int(getattr(settings, "PREFKIT_GOLD_CHECK_EVERY", DEFAULT_CHECK_EVERY))


def needs_checks(dataset: str) -> bool:
    """Checks are only mixed into datasets that have no gold rows of their own."""
    return not db.Comparison.objects.filter(dataset_id=dataset, gold=True).exists()


def _labelled_here(annotator: str, dataset: str) -> int:
    """Rows this person has labelled on this dataset's page, checks included."""
    return db.PersonLabel.objects.filter(annotator=annotator).filter(
        Q(comparison__dataset_id=dataset, check_for="") | Q(check_for=dataset)
    ).count()


def _roll(annotator: str, dataset: str, count: int) -> int:
    digest = hashlib.sha256(f"{annotator}:{dataset}:{count}".encode()).hexdigest()
    return int(digest[:12], 16)


def _new_check(annotator: str, dataset: str) -> db.Comparison | None:
    every = check_every()
    if every <= 0 or not needs_checks(dataset):
        return None
    roll = _roll(annotator, dataset, _labelled_here(annotator, dataset))
    if roll % every != 0:
        return None
    pool = (
        db.Comparison.objects.filter(gold=True, winner__in=["a", "b"])
        .exclude(dataset_id=dataset)
        .exclude(person_labels__annotator=annotator)
        .order_by("id")
    )
    available = pool.count()
    if available == 0:
        return None
    return pool.select_related("prompt", "a", "b")[(roll // every) % available]


def next_check(session, annotator: str, dataset: str) -> db.Comparison | None:
    """The check row to show now, or None for an ordinary row.

    A check that was shown but not yet labelled is shown again, so reloading the page
    cannot be used to skip a check or to get a different one.
    """
    pending = session.get(SESSION_KEY)
    if pending and pending.get("dataset") == dataset:
        row = (
            db.Comparison.objects.filter(pk=pending.get("row"))
            .exclude(person_labels__annotator=annotator)
            .select_related("prompt", "a", "b")
            .first()
        )
        if row is not None:
            return row
        session.pop(SESSION_KEY, None)

    row = _new_check(annotator, dataset)
    if row is not None:
        session[SESSION_KEY] = {"dataset": dataset, "row": row.pk}
    return row


def pending_for(session, comparison_id: str) -> str | None:
    """If this row is the check on screen, the dataset page it was shown on; else None."""
    pending = session.get(SESSION_KEY)
    if pending and pending.get("row") == comparison_id:
        return pending["dataset"]
    return None


def clear(session) -> None:
    """Forget the check on screen, once its label has been saved."""
    session.pop(SESSION_KEY, None)
