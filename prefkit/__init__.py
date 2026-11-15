"""prefkit: preference-data routing, evaluation and annotation.

The Celery app is imported here so that `@shared_task` decorators bind to it
as soon as Django starts.
"""

from prefkit.celery import app as celery_app

__all__ = ("celery_app",)
