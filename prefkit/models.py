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
        CANCELLED = "cancelled", "Cancelled"

    ACTIVE = (Status.QUEUED, Status.RUNNING)
    TERMINAL = (Status.SUCCEEDED, Status.FAILED, Status.CANCELLED)

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


class CodeCheckRun(models.Model):
    """One measurement of how well a code dataset's tests reject wrong solutions.

    The router uses the most recent run per dataset as the code check's accuracy:
    caught out of mutants.
    """

    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="code_checks")
    tasks = models.PositiveIntegerField()  # problems read
    skipped = models.PositiveIntegerField()  # problems whose own solution failed in the sandbox
    mutants = models.PositiveIntegerField()  # wrong versions run
    caught = models.PositiveIntegerField()  # wrong versions the tests rejected
    # Total running time in seconds, used to work out the cost per label. Empty for runs
    # made before timing was added.
    seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class PersonLabel(models.Model):
    """One person's choice on one comparison, made on the labelling page.

    Stored apart from Comparison.winner, so the dataset's own label is never overwritten,
    and each person can be measured separately against the gold rows.
    """

    comparison = models.ForeignKey(Comparison, on_delete=models.CASCADE, related_name="person_labels")
    annotator = models.CharField(max_length=150)  # the signed-in person's username
    choice = models.CharField(max_length=4, choices=[("a", "A"), ("b", "B"), ("tie", "Tie")])
    seconds = models.FloatField()  # time from showing the pair to the click
    # When the row was a hidden gold check, the dataset whose labelling page showed it.
    # Empty for an ordinary label. See prefkit/goldchecks.py.
    check_for = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # A person labels each row once, so one person cannot count twice on a row.
            models.UniqueConstraint(
                fields=["comparison", "annotator"],
                name="one_label_per_person_per_row",
            ),
        ]


class ConsensusLabel(models.Model):
    """The majority answer of the people who labelled one row. Written by the consensus command.

    Only rows with a clear majority get one. Stored apart from Comparison.winner, so the
    dataset's own label is never overwritten.
    """

    comparison = models.OneToOneField(Comparison, on_delete=models.CASCADE, related_name="consensus")
    choice = models.CharField(max_length=4, choices=[("a", "A"), ("b", "B"), ("tie", "Tie")])
    votes = models.PositiveIntegerField()  # people who labelled the row
    agreeing = models.PositiveIntegerField()  # people who chose the majority answer
    min_votes = models.PositiveIntegerField()  # the minimum the majority was computed with
    updated_at = models.DateTimeField(auto_now=True)


class RouteDecision(models.Model):
    """Which source the router chose to label a row. Written by `route --apply`."""

    comparison = models.OneToOneField(Comparison, on_delete=models.CASCADE, related_name="route")
    source = models.CharField(max_length=20)  # "code_check", "judge" or "person"
    target = models.FloatField()  # the accuracy target the choice was made for
    decided_at = models.DateTimeField(auto_now=True)


class JudgeVerdict(models.Model):
    """What one judge model said about one comparison.

    Stored apart from Comparison.winner, so the human label is never overwritten.
    """

    comparison = models.ForeignKey(Comparison, on_delete=models.CASCADE, related_name="verdicts")
    judge_model = models.CharField(max_length=200)
    verdict = models.CharField(max_length=4, choices=[("a", "A"), ("b", "B"), ("tie", "Tie")], null=True)
    raw_reply = models.TextField(blank=True, default="")
    # Seconds the judge took for this row, both orders together. Empty for older verdicts.
    seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # One verdict per judge per row, so re-running the judge does not duplicate it.
            models.UniqueConstraint(
                fields=["comparison", "judge_model"],
                name="one_verdict_per_judge_per_row",
            ),
        ]
