"""Database models. Prompts, responses and comparisons mirror prefkit.canonical."""

import uuid

from django.db import models
from django.db.models import Q


class Dataset(models.Model):
    """A registered dataset. The primary key equals the manifest name."""

    name = models.SlugField(primary_key=True)
    manifest_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


class IngestJob(models.Model):
    """One run of ingesting a dataset, from request to terminal state."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    ACTIVE = (Status.QUEUED, Status.RUNNING)
    TERMINAL = (Status.SUCCEEDED, Status.FAILED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="jobs")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    task_id = models.CharField(max_length=155, blank=True)

    accepted = models.PositiveIntegerField(default=0)
    quarantined = models.PositiveIntegerField(default=0)
    top_reasons = models.JSONField(default=list)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # At most one queued or running ingest per dataset. The database enforces
            # this, so two simultaneous requests cannot both pass a check.
            models.UniqueConstraint(
                fields=["dataset"],
                condition=Q(status__in=["queued", "running"]),
                name="one_active_ingest_per_dataset",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.dataset_id} {self.status} {self.pk}"


class Prompt(models.Model):
    id = models.CharField(primary_key=True, max_length=16)
    text = models.TextField()


class Response(models.Model):
    id = models.CharField(primary_key=True, max_length=16)
    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE, related_name="responses")
    model = models.CharField(max_length=200, blank=True, default="")
    text = models.TextField()


class Comparison(models.Model):
    """One judgment: which of two responses is better, from one dataset."""

    id = models.CharField(primary_key=True, max_length=16)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="comparisons")
    prompt = models.ForeignKey(Prompt, on_delete=models.CASCADE)
    a = models.ForeignKey(Response, on_delete=models.CASCADE, related_name="as_a")
    b = models.ForeignKey(Response, on_delete=models.CASCADE, related_name="as_b")
    winner = models.CharField(max_length=4, choices=[("a", "A"), ("b", "B"), ("tie", "Tie")], null=True)
    gold = models.BooleanField(default=False)
    source = models.CharField(max_length=20, default="human")

    class Meta:
        indexes = [models.Index(fields=["dataset", "gold"])]
