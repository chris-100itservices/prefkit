"""Report how the judge's verdicts compare with the human winners."""

from django.conf import settings
from django.core.management.base import BaseCommand

from prefkit.judge.runner import summarise


def _line(label: str, result) -> str:
    return (
        f"{label}: compared={result.compared} agreed={result.agreed} rate={result.rate:.1%} "
        f"95% interval={result.low:.1%} to {result.high:.1%}"
    )


class Command(BaseCommand):
    help = "Compare the judge's verdicts with human winners, with 95% intervals."

    def add_arguments(self, parser) -> None:
        parser.add_argument("dataset")
        parser.add_argument("--model", default=settings.PREFKIT_JUDGE_MODEL)

    def handle(self, *args, **options) -> None:
        summary = summarise(options["dataset"], options["model"])
        if summary.judged == 0:
            self.stdout.write("no judged rows yet. Run the judge command first.")
            return
        self.stdout.write(f"judged={summary.judged} no_verdict={summary.no_verdict}")
        share = summary.ties / summary.judged
        self.stdout.write(f"judge said TIE: {summary.ties} of {summary.judged} ({share:.1%})")
        self.stdout.write(_line("all verdicts, TIE counted as a disagreement", summary.overall))
        self.stdout.write(_line("only rows where the judge chose A or B", summary.choices_only))
        if summary.gold.compared:
            self.stdout.write(_line("gold rows, judge against the known winner", summary.gold))
