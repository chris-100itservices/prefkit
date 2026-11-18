"""Measure how well a code dataset's tests catch wrong solutions.

The code check labels a pair by running both solutions against the problem's tests.
That label is only right if the tests reject the wrong solution. To measure this, we
make many small wrong versions of each known-good solution, called mutants, and count
how many the tests reject. The share rejected is the code check's measured accuracy.

Why mutants are filtered before running:
- An edit that breaks the syntax, such as "<" turned into "<=" inside "<=" giving "<==",
  would be rejected by any test. Counting it would make weak tests look strong.
- An edit that does not change the program, such as one inside a comment, cannot be
  caught by any test. Counting it would make good tests look weak.
So only mutants that still parse, and whose parsed program differs from the original,
are run.

Limits of the number:
- Some mutants change the code without changing its behaviour, for example an edit in a
  docstring. No test can catch those, so the rate is slightly pessimistic.
- Mutants of one problem share the same tests, so they are not fully independent. The
  95% interval is therefore somewhat narrower than it should be.
"""

from __future__ import annotations

import ast
import warnings
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from prefkit.datasets.pair_sources import MUTATIONS, CodeTask

DEFAULT_MAX_MUTANTS = 10  # per problem, to keep a run to a sensible length


def all_mutants(code: str) -> Iterator[str]:
    """Yield one variant per occurrence of each edit token, not only the first."""
    for old, new in MUTATIONS:
        start = code.find(old)
        while start != -1:
            yield code[:start] + new + code[start + len(old) :]
            start = code.find(old, start + 1)


def _parsed(program: str) -> str | None:
    """The program's syntax tree as text, or None when it does not parse.

    Parsing only reads the code; it never runs it. Some dataset solutions contain
    strings like "\\." that Python 3.12 warns about. The warnings are silenced here,
    because they say nothing about whether the program is correct.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            return ast.dump(ast.parse(program))
        except (SyntaxError, ValueError):
            return None


def real_mutants(task: CodeTask, limit: int = DEFAULT_MAX_MUTANTS) -> list[str]:
    """Mutants that parse and change the program, without duplicates, at most `limit`."""
    original = _parsed(task.program_for(task.solution))
    seen: set[str] = set()
    kept: list[str] = []
    for mutant in all_mutants(task.solution):
        if len(kept) >= limit:
            break
        tree = _parsed(task.program_for(mutant))
        if tree is None or tree == original or tree in seen:
            continue
        seen.add(tree)
        kept.append(mutant)
    return kept


@dataclass
class Measurement:
    tasks: int = 0  # problems read
    skipped: int = 0  # problems whose own solution did not pass in the sandbox
    mutants: int = 0  # wrong versions run
    caught: int = 0  # wrong versions the tests rejected


def measure(
    tasks: Iterable[CodeTask],
    passes: Callable[[str], bool],
    max_mutants: int = DEFAULT_MAX_MUTANTS,
    on_task: Callable[[Measurement], None] | None = None,
) -> Measurement:
    """Run each task's mutants through `passes` and count how many the tests reject.

    `passes(program)` must run the program safely and return True when it exits cleanly.
    A task whose own solution does not pass is skipped. Without this check, a broken
    sandbox would make every mutant fail and report perfect tests.
    """
    result = Measurement()
    for task in tasks:
        result.tasks += 1
        if not passes(task.program_for(task.solution)):
            result.skipped += 1
        else:
            for mutant in real_mutants(task, max_mutants):
                result.mutants += 1
                if not passes(task.program_for(mutant)):
                    result.caught += 1
        if on_task is not None:
            on_task(result)
    return result
