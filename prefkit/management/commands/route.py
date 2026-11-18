"""Show which label source the router chooses for a dataset, and optionally apply it.

Each source is measured on the dataset's gold rows (rows with a known correct answer):
- the code check, by the latest measure_code run;
- the judge, by its stored verdicts;
- people, by their labels from the labelling page.
The router picks the cheapest of the code check and the judge whose lower bound meets
the target. If neither does, the rows go to a person.

With --apply, the choice is written on every row of the dataset. Rows routed to a person
then appear on the labelling page.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from prefkit import models as db
from prefkit import routing, sources


class Command(BaseCommand):
    help = "Show which label source the router picks for a dataset, from measured accuracy and cost."

    def add_arguments(self, parser):
        parser.add_argument("dataset")
        parser.add_argument("--model", default=settings.PREFKIT_JUDGE_MODEL, help="Judge model to measure.")
        parser.add_argument(
            "--target", type=float, default=routing.DEFAULT_TARGET, help="Accuracy target, 0 to 1."
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the chosen source on every row of the dataset.",
        )

    def handle(self, *args, **options):
        name = options["dataset"]
        if not db.Dataset.objects.filter(pk=name).exists():
            raise CommandError(f"no dataset named '{name}'")
        target = options["target"]
        if not 0 < target <= 1:
            raise CommandError("--target must be between 0 and 1")

        code_check = sources.code_check(name)
        judge = sources.judge(name, options["model"])
        person = sources.person(name)

        gold = db.Comparison.objects.filter(dataset_id=name, gold=True, winner__isnull=False).count()
        self.stdout.write(f"{name}: {gold} gold rows, target {target:.0%}")
        hints = {
            "code_check": "run measure_code first",
            "judge": "run the judge command first",
            routing.PERSON: "label rows on the labelling page",
        }
        for measured in (code_check, judge, person):
            s = measured.stats
            if s.measured == 0:
                self.stdout.write(f"  {s.name}: not measured ({hints[s.name]})")
                continue
            kind = "measured" if measured.cost_measured else "placeholder"
            self.stdout.write(
                f"  {s.name}: correct={s.correct} of {s.measured} "
                f"rate={s.correct / s.measured:.1%} lower bound={s.lower_bound():.1%} "
                f"cost={s.cost:.1f}s per label ({kind})"
            )

        agreement = sources.person_agreement(name)
        if agreement.measured:
            rate = agreement.correct / agreement.measured
            self.stdout.write(
                "  person agreement with the dataset's own label: "
                f"{agreement.correct} of {agreement.measured} rate={rate:.1%} "
                f"lower bound={agreement.lower_bound():.1%} (agreement, not accuracy)"
            )

        checks = sources.person_checks(name)
        if checks.measured:
            rate = checks.correct / checks.measured
            self.stdout.write(
                "  person on hidden gold checks: "
                f"{checks.correct} of {checks.measured} rate={rate:.1%} "
                f"lower bound={checks.lower_bound():.1%}"
            )

        # A person is never compared: it is the fallback when no machine source is good enough.
        choice = routing.choose([code_check.stats, judge.stats], target)
        self.stdout.write(f"chosen source: {choice}")

        if options["apply"]:
            rows = list(db.Comparison.objects.filter(dataset_id=name).values_list("id", flat=True))
            with transaction.atomic():
                db.RouteDecision.objects.filter(comparison__dataset_id=name).delete()
                db.RouteDecision.objects.bulk_create(
                    db.RouteDecision(comparison_id=row, source=choice, target=target) for row in rows
                )
            self.stdout.write(f"applied: {len(rows)} rows routed to {choice}")
