"""Write mapped rows in batched, idempotent upserts.

Every id is a content hash, so writing the same row twice updates it in place instead
of duplicating it. A retried or re-run ingest therefore converges to the same data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from prefkit import models as db
from prefkit.datasets.ingest import Mapped

BATCH_SIZE = 1000


def scoped_id(dataset_name: str, comparison_id: str) -> str:
    """Scope a comparison id to its dataset.

    The same pair of responses in two datasets stays two separate judgments.
    """
    return hashlib.sha256(f"{dataset_name}\x1f{comparison_id}".encode()).hexdigest()[:16]


class BatchWriter:
    def __init__(
        self,
        dataset: db.Dataset,
        *,
        batch_size: int = BATCH_SIZE,
        on_flush: Callable[[], None] | None = None,
    ) -> None:
        self._dataset = dataset
        self._batch_size = batch_size
        self._on_flush = on_flush
        self._pending: list[Mapped] = []

    def add(self, mapped: Mapped) -> None:
        self._pending.append(mapped)
        if len(self._pending) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return

        # Deduplicate within the batch first. Postgres rejects an upsert that touches the
        # same row twice in one statement, and a dataset can repeat a prompt or response.
        prompts: dict[str, db.Prompt] = {}
        responses: dict[str, db.Response] = {}
        comparisons: dict[str, db.Comparison] = {}

        for mapped in self._pending:
            prompts[mapped.prompt.id] = db.Prompt(id=mapped.prompt.id, text=mapped.prompt.text)
            for resp in mapped.responses:
                responses[resp.id] = db.Response(
                    id=resp.id,
                    prompt_id=resp.prompt_id,
                    model=resp.model or "",
                    text=resp.text,
                )
            cmp = mapped.comparison
            row_id = scoped_id(self._dataset.name, cmp.id)
            comparisons[row_id] = db.Comparison(
                id=row_id,
                dataset=self._dataset,
                prompt_id=cmp.prompt_id,
                a_id=cmp.a_id,
                b_id=cmp.b_id,
                winner=cmp.winner,
                gold=cmp.gold,
                source=cmp.source,
            )

        # Parents first: prompts, then responses, then the comparisons that reference them.
        db.Prompt.objects.bulk_create(
            prompts.values(),
            update_conflicts=True,
            unique_fields=["id"],
            update_fields=["text"],
        )
        db.Response.objects.bulk_create(
            responses.values(),
            update_conflicts=True,
            unique_fields=["id"],
            update_fields=["model", "text"],
        )
        db.Comparison.objects.bulk_create(
            comparisons.values(),
            update_conflicts=True,
            unique_fields=["id"],
            update_fields=["winner", "gold", "source"],
        )

        self._pending.clear()
        if self._on_flush is not None:
            self._on_flush()
