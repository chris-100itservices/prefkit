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


def _cancel_key(job_id: UUID | str) -> str:
    return f"prefkit:ingest:cancel:{job_id}"


def request_cancel(job_id: UUID | str) -> None:
    """Ask a running job to stop. The job checks this flag between batches of rows.

    The flag lives in the cache, not the database, on purpose. The running import holds
    the database's write lock until it finishes, so a cancel that wrote to the database
    would be blocked. The cache does not need that lock.
    """
    cache.set(_cancel_key(job_id), True, TTL_SECONDS)


def cancel_requested(job_id: UUID | str) -> bool:
    """Return True when someone has asked this job to stop."""
    return bool(cache.get(_cancel_key(job_id)))


def clear_cancel(job_id: UUID | str) -> None:
    """Remove the cancel flag once the job has ended."""
    cache.delete(_cancel_key(job_id))
