"""Run the judge over a dataset's human-labelled rows and store its verdicts."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from prefkit import models as db
from prefkit.judge.runner import judge_dataset, reset_verdicts


class Command(BaseCommand):
    help = (
        "Ask the judge model about each human-labelled row of a dataset, in both orders, "
        "and store its verdicts."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("dataset")
        parser.add_argument("--model", default=settings.PREFKIT_JUDGE_MODEL, help="Hugging Face model id.")
        parser.add_argument("--limit", type=int, default=None, help="Judge only the first N rows.")
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete this model's existing verdicts for the dataset before judging.",
        )

    def handle(self, *args, **options) -> None:
        from prefkit.judge.model import TransformersJudge  # imported here: needs torch

        dataset = options["dataset"]
        if not db.Dataset.objects.filter(pk=dataset).exists():
            raise CommandError(f"unknown dataset '{dataset}'. Run sync_datasets first.")

        if options["reset"]:
            self.stdout.write(f"deleted {reset_verdicts(dataset, options['model'])} old verdicts")

        judge = TransformersJudge(options["model"])
        self.stdout.write(f"judging on {judge.device.name} with {options['model']}, in both orders")
        run = judge_dataset(dataset, judge, options["model"], limit=options["limit"])
        self.stdout.write(f"judged={run.judged} no_verdict={run.no_verdict}")
