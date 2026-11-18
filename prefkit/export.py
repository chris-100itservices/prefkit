"""Write a dataset's rows with their final labels to a JSONL file: one JSON object per line.

Which label is final:
- A row that `route --apply` sent to a source gets that source's answer:
  - code_check: the winner found by running the tests at import, stored on gold rows;
  - judge: the judge's verdict, when it gave one in both orders;
  - person: the majority answer of the people who labelled it (see consensus).
  If that source has no answer for the row yet, the label is empty and the source is
  "none", so the file shows which rows still need work instead of guessing.
- A row that was never routed keeps the dataset's own label, with the source "dataset".

Why every line also carries the evidence (the dataset's label, the judge's verdict and the
people's votes): whoever uses the file can check a label, or choose a different rule,
without going back to the database.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from prefkit import models as db
from prefkit import routing

CHUNK = 2000  # rows read from the database at a time, so large datasets do not fill memory


@dataclass
class ExportResult:
    path: Path
    written: int = 0
    skipped: int = 0  # rows left out by skip_unlabelled
    by_source: Counter = field(default_factory=Counter)


def _final_label(row, routed_to, verdict, majority):
    """(label, source) for one row, following the rules in the module docstring."""
    if routed_to is None:
        return row.winner, "dataset" if row.winner is not None else "none"
    if routed_to == "code_check":
        answer = row.winner if row.gold else None
    elif routed_to == "judge":
        answer = verdict
    elif routed_to == routing.PERSON:
        answer = majority.choice if majority else None
    else:
        answer = None
    if answer is None:
        return None, "none"
    return answer, "person_majority" if routed_to == routing.PERSON else routed_to


def rows(dataset: str, judge_model: str) -> Iterator[dict]:
    """Every row of the dataset as a dictionary, ready to write as one JSON line."""
    routes = dict(
        db.RouteDecision.objects.filter(comparison__dataset_id=dataset).values_list("comparison_id", "source")
    )
    verdicts = dict(
        db.JudgeVerdict.objects.filter(comparison__dataset_id=dataset, judge_model=judge_model).values_list(
            "comparison_id", "verdict"
        )
    )
    majorities = {
        c.comparison_id: c for c in db.ConsensusLabel.objects.filter(comparison__dataset_id=dataset)
    }

    queryset = (
        db.Comparison.objects.filter(dataset_id=dataset).select_related("prompt", "a", "b").order_by("id")
    )
    for row in queryset.iterator(chunk_size=CHUNK):
        majority = majorities.get(row.id)
        label, source = _final_label(row, routes.get(row.id), verdicts.get(row.id), majority)
        yield {
            "id": row.id,
            "dataset": dataset,
            "prompt": row.prompt.text,
            "answer_a": row.a.text,
            "answer_b": row.b.text,
            "label": label,
            "label_source": source,
            "routed_to": routes.get(row.id),
            "dataset_label": row.winner,
            "gold": row.gold,
            "judge": {"model": judge_model, "verdict": verdicts[row.id]} if row.id in verdicts else None,
            "people": (
                {"answer": majority.choice, "votes": majority.votes, "agreeing": majority.agreeing}
                if majority
                else None
            ),
        }


def export(dataset: str, path: Path, judge_model: str, skip_unlabelled: bool = False) -> ExportResult:
    """Write the dataset to `path`. The file is written in full, then renamed into place,
    so a failed export never leaves a half-written file under the final name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    result = ExportResult(path=path)
    with partial.open("w", encoding="utf-8", newline="\n") as out:
        for record in rows(dataset, judge_model):
            if skip_unlabelled and record["label"] is None:
                result.skipped += 1
                continue
            # ensure_ascii=False keeps non-English text readable instead of \u escapes.
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            result.written += 1
            result.by_source[record["label_source"]] += 1
    partial.replace(path)
    return result
