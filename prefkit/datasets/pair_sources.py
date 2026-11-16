"""Source readers for public datasets that are not stored as preference pairs.

Each reader turns the dataset into rows shaped {"prompt", "reply_a", "reply_b"}, where
reply_a is the better reply. The manifest then maps these rows like any other, with
winner_fixed: a. Rows that cannot form a pair are skipped and logged, not guessed.

Registered with @source_reader, so a manifest selects one with source.type.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from prefkit.datasets.ingest import source_reader
from prefkit.datasets.manifest import Manifest

log = logging.getLogger(__name__)

# Small edits that usually make code wrong. The first edit that makes the tests fail is used.
# An edit can leave the behaviour unchanged, for example in dead code; the tests then pass
# and the edit is tried next.
MUTATIONS: tuple[tuple[str, str], ...] = (
    ("<=", "<"),
    ("<", "<="),
    (">=", ">"),
    (">", ">="),
    ("==", "!="),
    ("+", "-"),
    ("-", "+"),
    ("*", "+"),
    ("True", "False"),
    ("False", "True"),
    ("0", "1"),
    ("1", "0"),
)

# A program that runs longer than this is treated as failing, so a wrong answer that
# loops forever still counts as wrong.
TEST_TIMEOUT_SECONDS = 10

# UltraFeedback rates each answer on four dimensions. This one decides which answer is best.
ULTRAFEEDBACK_DIMENSION = "helpfulness"


# ---------------------------------------------------------------- code datasets


def _passes(program: str) -> bool:
    """Run one Python program in a separate, isolated process. True when it exits cleanly."""
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", program],
            capture_output=True,
            timeout=TEST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0


def _mutants(code: str) -> Iterator[str]:
    """Yield code variants, each with one edit at the first place its token appears."""
    for old, new in MUTATIONS:
        index = code.find(old)
        if index != -1:
            yield code[:index] + new + code[index + len(old) :]


def _wrong_version(code: str, program_for: Callable[[str], str]) -> str | None:
    """Return the first mutant whose program fails, or None when no mutant fails."""
    for candidate in _mutants(code):
        if not _passes(program_for(candidate)):
            return candidate
    return None


def humaneval_pairs(rows: Iterable[Mapping[str, Any]]) -> Iterator[dict[str, str]]:
    """Pair each HumanEval solution (passes its tests) with a broken variant of it."""
    for row in rows:
        header = row["prompt"]
        tests = f"\n\n{row['test']}\n\ncheck({row['entry_point']})\n"

        def program_for(body: str, header: str = header, tests: str = tests) -> str:
            return header + body + tests

        if not _passes(program_for(row["canonical_solution"])):
            log.info("%s skipped: its own solution fails the tests", row["task_id"])
            continue
        broken = _wrong_version(row["canonical_solution"], program_for)
        if broken is None:
            log.info("%s skipped: no small edit makes its tests fail", row["task_id"])
            continue
        yield {"prompt": header, "reply_a": row["canonical_solution"], "reply_b": broken}


def mbpp_pairs(rows: Iterable[Mapping[str, Any]]) -> Iterator[dict[str, str]]:
    """Pair each MBPP solution (passes its tests) with a broken variant of it."""
    for row in rows:
        setup = row.get("test_setup_code") or ""
        asserts = "\n".join(row["test_list"])

        def program_for(code: str, setup: str = setup, asserts: str = asserts) -> str:
            return f"{setup}\n{code}\n{asserts}\n"

        if not _passes(program_for(row["code"])):
            log.info("%s skipped: its own solution fails the tests", row["task_id"])
            continue
        broken = _wrong_version(row["code"], program_for)
        if broken is None:
            log.info("%s skipped: no small edit makes its tests fail", row["task_id"])
            continue
        yield {"prompt": row["text"], "reply_a": row["code"], "reply_b": broken}


# ------------------------------------------------------ conversation datasets


def oasst_pairs(rows: Iterable[Mapping[str, Any]]) -> Iterator[dict[str, str]]:
    """Pair the two best-ranked replies to each prompt in OpenAssistant oasst2.

    Replies to one prompt are separate rows that point at it through parent_id. Rank 0
    is the best reply. The whole file is read first, because replies and prompts are
    linked across rows.
    """
    live = {m["message_id"]: m for m in rows if not m["deleted"]}
    replies: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for message in live.values():
        parent = live.get(message["parent_id"]) if message["parent_id"] else None
        if message["role"] == "assistant" and message["rank"] is not None and parent is not None:
            if parent["role"] == "prompter":
                replies[parent["message_id"]].append(message)

    for parent_id, children in replies.items():
        if len(children) < 2:
            continue
        children.sort(key=lambda m: m["rank"])
        yield {
            "prompt": live[parent_id]["text"],
            "reply_a": children[0]["text"],
            "reply_b": children[1]["text"],
        }


def _rating(completion: Mapping[str, Any]) -> int | None:
    """Return the integer rating on the chosen dimension, or None when it is missing."""
    try:
        return int(completion["annotations"][ULTRAFEEDBACK_DIMENSION]["Rating"])
    except (KeyError, TypeError, ValueError):
        return None


def ultrafeedback_pairs(rows: Iterable[Mapping[str, Any]]) -> Iterator[dict[str, str]]:
    """Pair the highest-rated and lowest-rated completion for each UltraFeedback prompt.

    A prompt whose best and worst completions have the same rating gives no preference,
    so it is skipped.
    """
    for row in rows:
        rated = [
            (rating, completion["response"])
            for completion in row["completions"]
            if (rating := _rating(completion)) is not None
        ]
        if len(rated) < 2:
            continue
        rated.sort(key=lambda item: item[0], reverse=True)
        (best_rating, best), (worst_rating, worst) = rated[0], rated[-1]
        if best_rating == worst_rating:
            continue
        yield {"prompt": row["instruction"], "reply_a": best, "reply_b": worst}


# ----------------------------------------------------------------- readers


def _hub_rows(manifest: Manifest, config: str | None = None, streaming: bool = True) -> Any:
    """Open a Hugging Face dataset. Imported here so local-only runs do not need it."""
    from datasets import load_dataset

    args = (manifest.source.location, config) if config else (manifest.source.location,)
    return load_dataset(*args, split=manifest.source.split, streaming=streaming)


@source_reader("humaneval_mutants")
def _read_humaneval(manifest: Manifest) -> Iterable[Mapping[str, Any]]:
    return humaneval_pairs(_hub_rows(manifest))


@source_reader("mbpp_mutants")
def _read_mbpp(manifest: Manifest) -> Iterable[Mapping[str, Any]]:
    # MBPP has two configurations. "full" holds all problems.
    return mbpp_pairs(_hub_rows(manifest, config="full"))


@source_reader("oasst_tree")
def _read_oasst(manifest: Manifest) -> Iterable[Mapping[str, Any]]:
    # Not streamed: replies must be linked to their prompts across rows.
    return oasst_pairs(_hub_rows(manifest, streaming=False))


@source_reader("ultrafeedback_best_worst")
def _read_ultrafeedback(manifest: Manifest) -> Iterable[Mapping[str, Any]]:
    return ultrafeedback_pairs(_hub_rows(manifest))
