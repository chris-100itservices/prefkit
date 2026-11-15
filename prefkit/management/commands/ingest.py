"""Run one ingest in this process, synchronously. Set a breakpoint and step through it."""

from django.core.management.base import BaseCommand, CommandError

from prefkit import models as db
from prefkit.jobs.tasks import ingest_dataset


class Command(BaseCommand):
    help = "Ingest one dataset now, in the foreground, so it can be debugged."

    def add_arguments(self, parser) -> None:
        parser.add_argument("name", help="Dataset name, e.g. demo_local")

    def handle(self, *args, **options) -> None:
        try:
            dataset = db.Dataset.objects.get(pk=options["name"])
        except db.Dataset.DoesNotExist as exc:
            raise CommandError(f"unknown dataset '{options['name']}'; run sync_datasets first") from exc

        job = db.IngestJob.objects.create(dataset=dataset)
        # throw=True re-raises unexpected errors here, so the debugger stops at the real fault.
        ingest_dataset.apply(args=[str(job.pk)], throw=True)

        job.refresh_from_db()
        self.stdout.write(f"{job.status}: accepted={job.accepted} quarantined={job.quarantined}")
        if job.top_reasons:
            for reason in job.top_reasons:
                self.stdout.write(f"  {reason['count']} x {reason['reason']}")
        if job.error:
            self.stderr.write(job.error)
