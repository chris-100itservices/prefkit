"""Write a dataset's rows with their final labels to a JSONL file. See prefkit/export.py."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from prefkit import export
from prefkit import models as db

DEFAULT_FOLDER = Path(settings.BASE_DIR) / "var" / "exports"  # var/ is excluded from Git


class Command(BaseCommand):
    help = "Write a dataset's rows, with their final labels and the evidence behind them, to a JSONL file."

    def add_arguments(self, parser):
        parser.add_argument("dataset")
        parser.add_argument("--output", type=Path, help="File to write. Default: var/exports/<dataset>.jsonl")
        parser.add_argument(
            "--model", default=settings.PREFKIT_JUDGE_MODEL, help="Judge model whose verdicts to include."
        )
        parser.add_argument(
            "--skip-unlabelled", action="store_true", help="Leave out rows that have no final label yet."
        )

    def handle(self, *args, **options):
        name = options["dataset"]
        if not db.Dataset.objects.filter(pk=name).exists():
            raise CommandError(f"no dataset named '{name}'")
        path = options["output"] or DEFAULT_FOLDER / f"{name}.jsonl"

        result = export.export(name, path, options["model"], skip_unlabelled=options["skip_unlabelled"])

        self.stdout.write(f"wrote {result.written} rows to {result.path}")
        for source, count in sorted(result.by_source.items()):
            self.stdout.write(f"  label from {source}: {count}")
        if result.skipped:
            self.stdout.write(f"  left out, no label yet: {result.skipped}")
