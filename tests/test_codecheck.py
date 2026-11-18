"""Tests for measuring the code check. The sandbox is replaced by a fake, so no Docker is needed.

The fake runs the tiny test programs below in this process. That is safe only because
these programs are written here; real dataset code always goes through the sandbox.
"""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from prefkit import codecheck, sandbox
from prefkit import models as db
from prefkit.datasets import pair_sources
from prefkit.datasets.pair_sources import CodeTask

HEADER = 'def add(a, b):\n    """Return the sum of a and b."""\n'


def add_task(solution: str, test_line: str) -> CodeTask:
    tests = f"\n\ndef check(candidate):\n    {test_line}\n\ncheck(add)\n"
    return CodeTask("t/0", HEADER, solution, lambda body: HEADER + body + tests)


def run_here(program: str, timeout_s: int = 5) -> bool:
    """Fake sandbox: run one of the test programs above. True when it raises nothing."""
    try:
        exec(program, {})
    except Exception:
        return False
    return True


# ------------------------------------------------------------- mutant building


def test_all_mutants_edits_every_occurrence():
    mutants = list(codecheck.all_mutants("a + b + c"))
    assert "a - b + c" in mutants
    assert "a + b - c" in mutants


def test_real_mutants_drop_syntax_errors_and_edits_that_change_nothing():
    task = add_task("    return a <= b  # x < 1\n", "assert candidate(1, 2)")
    # "<=" -> "<" changes the program and is kept.
    # "<" inside "<=" gives "<==", a syntax error, and is dropped.
    # Edits inside the comment do not change the program, and are dropped.
    assert codecheck.real_mutants(task) == ["    return a < b  # x < 1\n"]


def test_real_mutants_respects_the_limit():
    task = add_task("    return a + b + 1 + 0\n", "assert candidate(1, 2) == 4")
    assert len(codecheck.real_mutants(task, limit=2)) == 2


# ------------------------------------------------------------------ measuring


def test_strong_tests_catch_the_wrong_version():
    task = add_task("    return a + b\n", "assert candidate(1, 2) == 3")
    m = codecheck.measure([task], run_here)
    assert (m.tasks, m.skipped, m.mutants, m.caught) == (1, 0, 1, 1)


def test_weak_tests_miss_the_wrong_version():
    # add(0, 0) is 0 for both a + b and a - b, so this test cannot tell them apart.
    task = add_task("    return a + b\n", "assert candidate(0, 0) == 0")
    m = codecheck.measure([task], run_here)
    assert (m.mutants, m.caught) == (1, 0)


def test_task_is_skipped_when_its_own_solution_fails():
    task = add_task("    return a + b\n", "assert candidate(1, 2) == 3")
    m = codecheck.measure([task], lambda program: False)
    assert (m.tasks, m.skipped, m.mutants) == (1, 1, 0)


# ------------------------------------------------------------------- commands


@pytest.fixture
def humaneval(monkeypatch):
    """The humaneval dataset, with two fake problems and a fake, working sandbox."""
    dataset = db.Dataset.objects.create(name="humaneval", manifest_version=1)
    tasks = [
        add_task("    return a + b\n", "assert candidate(1, 2) == 3"),  # caught
        add_task("    return a + b\n", "assert candidate(0, 0) == 0"),  # missed
    ]
    monkeypatch.setitem(pair_sources.CODE_TASK_READERS, "humaneval_mutants", lambda manifest: iter(tasks))
    monkeypatch.setattr(sandbox, "ensure_docker", lambda: None)
    monkeypatch.setattr(sandbox, "passes", run_here)
    return dataset


@pytest.mark.django_db
def test_measure_code_saves_the_result(humaneval):
    out = StringIO()
    call_command("measure_code", "humaneval", stdout=out)
    run = db.CodeCheckRun.objects.get()
    assert (run.tasks, run.skipped, run.mutants, run.caught) == (2, 0, 2, 1)
    assert "caught=1 of 2" in out.getvalue()


@pytest.mark.django_db
def test_measure_code_refuses_without_docker(humaneval, monkeypatch):
    def no_docker():
        raise sandbox.SandboxUnavailable("Docker is not installed.")

    monkeypatch.setattr(sandbox, "ensure_docker", no_docker)
    with pytest.raises(CommandError, match="not installed"):
        call_command("measure_code", "humaneval", stdout=StringIO())
    assert not db.CodeCheckRun.objects.exists()


@pytest.mark.django_db
def test_measure_code_saves_nothing_when_the_sandbox_runs_nothing(humaneval, monkeypatch):
    # A broken sandbox makes every program fail. That must not look like perfect tests.
    monkeypatch.setattr(sandbox, "passes", lambda program, timeout_s=5: False)
    with pytest.raises(CommandError, match="no solution passed"):
        call_command("measure_code", "humaneval", stdout=StringIO())
    assert not db.CodeCheckRun.objects.exists()


@pytest.mark.django_db
def test_measure_code_rejects_a_dataset_without_tests():
    db.Dataset.objects.create(name="demo_local", manifest_version=1)
    with pytest.raises(CommandError, match="not a code dataset"):
        call_command("measure_code", "demo_local", stdout=StringIO())


@pytest.mark.django_db
def test_route_uses_the_latest_code_check_measurement(humaneval):
    db.CodeCheckRun.objects.create(dataset=humaneval, tasks=100, skipped=0, mutants=1000, caught=990)
    out = StringIO()
    call_command("route", "humaneval", "--target", "0.95", stdout=out)
    text = out.getvalue()
    assert "code_check: correct=990 of 1000" in text
    assert text.strip().endswith("chosen source: code_check")


@pytest.mark.django_db
def test_route_without_a_measurement_says_how_to_get_one(humaneval):
    out = StringIO()
    call_command("route", "humaneval", stdout=out)
    assert "run measure_code first" in out.getvalue()
    assert out.getvalue().strip().endswith("chosen source: person")
