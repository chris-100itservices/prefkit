"""Register every manifest in datasets/manifests as a Dataset row."""

from django.core.management.base import BaseCommand

from prefkit import models as db
from prefkit.datasets.manifest import ManifestError, load_manifest
from prefkit.jobs.tasks import MANIFEST_DIR


class Command(BaseCommand):
    help = "Create or update a Dataset row for every manifest file."

    def handle(self, *args, **options) -> None:
        for path in sorted(MANIFEST_DIR.glob("*.yaml")):
            try:
                manifest = load_manifest(path)
            except ManifestError as exc:
                self.stderr.write(f"skipped: {exc}")
                continue
            _, created = db.Dataset.objects.update_or_create(
                name=manifest.name,
                defaults={"manifest_version": manifest.version},
            )
            verb = "created" if created else "updated"
            self.stdout.write(f"{verb}: {manifest.name} (v{manifest.version})")
