"""Celery application. Configuration comes from Django settings under the CELERY_ prefix."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "prefkit.settings")

app = Celery("prefkit")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
