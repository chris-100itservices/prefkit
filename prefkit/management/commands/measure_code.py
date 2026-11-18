"""Measure how well a code dataset's tests reject wrong solutions, inside the sandbox.

Every program runs in the Docker sandbox, never directly on this computer. The result is
saved, and the route command uses it as the code check's accuracy.
"""

import time
from itertools import islice

from django.core.management.base import BaseCommand, CommandError

from prefkit import codecheck, routing, sandbox
from prefkit import models as db
from prefkit.datasets.manifest import load_manifest
from prefkit.datasets.pair_sources import CODE_TASK_READERS, TEST_TIMEOUT_SECONDS
from prefkit.jobs.tasks import MANIFEST_DIR

PROGRESS_EVERY = 10  # print a progress line after this many problems


class Command(BaseCommand):
    help = "Run wrong versions of each solution in the sandbox and count how many the tests reject."

    def add_arguments(self, parser):
        parser.add_argument("dataset")
        parser.add_argument("--limit", type=int, help="Read only the first N problems.")
        parser.add_argument(
            "--max-mutants",
            type=int,
            default=codecheck.DEFAULT_MAX_MUTANTS,
            help="Wrong versions to run per problem.",
        )

    def handle(self, *args, **options):
        name = options["dataset"]
        dataset = db.Dataset.objects.filter(pk=name).first()
        if dataset is None:
            raise CommandError(f"no dataset named '{name}'")
        manifest = load_manifest(MANIFEST_DIR / f"{name}.yaml")
        reader = CODE_TASK_READERS.get(manifest.source.type)
        if reader is None:
            raise CommandError(f"'{name}' is not a code dataset, so it has no tests to measure")

        # Check Docker before reading anything, so a missing sandbox fails at once.
        try:
            sandbox.ensure_docker()
        except sandbox.SandboxUnavailable as exc:
            raise CommandError(str(exc)) from exc

        tasks = reader(manifest)
        if options["limit"]:
            tasks = islice(tasks, options["limit"])

        def passes(program: str) -> bool:
            return sandbox.passes(program, timeout_s=TEST_TIMEOUT_SECONDS)

        def progress(m: codecheck.Measurement) -> None:
            if m.tasks % PROGRESS_EVERY == 0:
                self.stdout.write(f"  {m.tasks} problems, {m.caught} of {m.mutants} wrong versions caught")

        self.stdout.write(f"measuring {name} in the sandbox, up to {options['max_mutants']} per problem")
        started = time.perf_counter()
        result = codecheck.measure(tasks, passes, options["max_mutants"], on_task=progress)
        seconds = time.perf_counter() - started  # used for the cost per label

        if result.tasks == result.skipped:
            raise CommandError(
                "no solution passed its own tests in the sandbox, so nothing was measured. "
                "Check that Docker works with the sandbox check in section 8.3."
            )
        if result.mutants == 0:
            raise CommandError("no wrong versions could be made, so nothing was measured")

        db.CodeCheckRun.objects.create(
            dataset=dataset,
            tasks=result.tasks,
            skipped=result.skipped,
            mutants=result.mutants,
            caught=result.caught,
            seconds=seconds,
        )
        stats = routing.SourceStats("code_check", 0, result.caught, result.mutants)
        self.stdout.write(
            f"problems={result.tasks} skipped={result.skipped} "
            f"caught={result.caught} of {result.mutants} "
            f"rate={result.caught / result.mutants:.1%} lower bound={stats.lower_bound():.1%}"
        )
