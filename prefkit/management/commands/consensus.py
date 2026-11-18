"""Combine the people's labels for a dataset into one majority answer per row, and store them."""

from django.core.management.base import BaseCommand, CommandError

from prefkit import consensus
from prefkit import models as db


class Command(BaseCommand):
    help = "Store the majority answer of the people's labels for each row of a dataset."

    def add_arguments(self, parser):
        parser.add_argument("dataset")
        parser.add_argument(
            "--min-votes",
            type=int,
            default=consensus.DEFAULT_MIN_VOTES,
            help="People who must have labelled a row before it gets a majority answer.",
        )

    def handle(self, *args, **options):
        name = options["dataset"]
        if not db.Dataset.objects.filter(pk=name).exists():
            raise CommandError(f"no dataset named '{name}'")
        if options["min_votes"] < 1:
            raise CommandError("--min-votes must be at least 1")

        run = consensus.build(name, options["min_votes"])
        self.stdout.write(
            f"{name}: {run.labelled_rows} rows labelled by people, "
            f"{run.enough_votes} with at least {options['min_votes']} votes, "
            f"{run.with_majority} with a majority"
        )
        for stats, what in (
            (run.gold, "majority on gold rows, against the known winner"),
            (run.agreement, "majority against the dataset's own human label (agreement, not accuracy)"),
        ):
            if stats.measured:
                self.stdout.write(
                    f"  {what}: {stats.correct} of {stats.measured} "
                    f"rate={stats.correct / stats.measured:.1%} lower bound={stats.lower_bound():.1%}"
                )
        self.stdout.write(f"saved: {run.with_majority} majority answers")
