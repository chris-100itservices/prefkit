"""Live progress for running ingest jobs, kept in the cache.

Why the cache and not the database: progress is written from inside the ingest
transaction. Other connections cannot see uncommitted rows, so a database column would
show nothing until the end. The cache is visible immediately and holds only short-lived
numbers.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.core.cache import cache

TTL_SECONDS = 24 * 60 * 60


def _key(job_id: UUID | str) -> str:
    return f"prefkit:ingest:progress:{job_id}"


def publish(job_id: UUID | str, snapshot: dict[str, Any]) -> None:
    """Store the latest counts for a job."""
    cache.set(_key(job_id), snapshot, TTL_SECONDS)


def read(job_id: UUID | str) -> dict[str, Any] | None:
    """Return the latest counts for a job, or None if nothing has been published."""
    return cache.get(_key(job_id))
