"""Pair builders for datasets that are not stored as preference pairs.

The mutant builders run each program in the Docker sandbox. These tests replace the
sandbox with a stand-in that runs the small programs below in a separate Python process,
so they need neither Docker nor internet access. That is safe only because these programs
are written here; dataset code always goes through the real sandbox.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from prefkit import sandbox
from prefkit.datasets import pair_sources
from prefkit.datasets.ingest import map_row
from prefkit.datasets.manifest import load_manifest
from prefkit.datasets.pair_sources import (
    humaneval_pairs,
    mbpp_pairs,
    oasst_pairs,
    ultrafeedback_pairs,
)


def _run_here(program: str, timeout_s: int = 10) -> bool:
    """Stand-in for sandbox.passes, for the test programs in this file only."""
    try:
        done = subprocess.run([sys.executable, "-I", "-c", program], capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False
    return done.returncode == 0


@pytest.fixture(autouse=True)
def stand_in_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "passes", _run_here)


ADD_HEADER = 'def add(a, b):\n    """Return the sum of a and b."""\n'


def _msg(message_id, parent_id, role, text, rank=None, deleted=False):
    return {
        "message_id": message_id,
        "parent_id": parent_id,
        "role": role,
        "text": text,
        "rank": rank,
        "deleted": deleted,
    }


def test_humaneval_pairs_correct_solution_against_broken_variant():
    row = {
        "task_id": "test/0",
        "prompt": ADD_HEADER,
        "canonical_solution": "    return a + b\n",
        "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        "entry_point": "add",
    }
    (pair,) = list(humaneval_pairs([row]))
    assert pair["prompt"] == ADD_HEADER
    assert pair["reply_a"] == "    return a + b\n"
    assert pair["reply_b"] == "    return a - b\n"


def test_humaneval_skips_problem_when_no_small_edit_breaks_the_tests():
    # No token in this body can be edited into a different, failing result.
    row = {
        "task_id": "test/1",
        "prompt": ADD_HEADER,
        "canonical_solution": "    return a\n",
        "test": "def check(candidate):\n    assert candidate(1, 2) == 1\n",
        "entry_point": "add",
    }
    assert list(humaneval_pairs([row])) == []


def test_humaneval_skips_problem_whose_own_solution_fails():
    row = {
        "task_id": "test/2",
        "prompt": ADD_HEADER,
        "canonical_solution": "    return a - b\n",
        "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        "entry_point": "add",
    }
    assert list(humaneval_pairs([row])) == []


def test_mbpp_pairs_correct_solution_against_broken_variant():
    row = {
        "task_id": 1,
        "text": "Write a function to add two numbers.",
        "code": "def add(a, b):\n    return a + b\n",
        "test_list": ["assert add(1, 2) == 3"],
        "test_setup_code": "",
    }
    (pair,) = list(mbpp_pairs([row]))
    assert pair["prompt"] == "Write a function to add two numbers."
    assert pair["reply_a"] == "def add(a, b):\n    return a + b\n"
    assert pair["reply_b"] == "def add(a, b):\n    return a - b\n"


def test_oasst_pairs_best_two_replies_to_each_prompt():
    rows = [
        _msg("p1", None, "prompter", "How do I boil water?", rank=None, deleted=False),
        _msg("a1", "p1", "assistant", "No idea.", rank=1, deleted=False),
        _msg("a2", "p1", "assistant", "Use a kettle.", rank=0, deleted=False),
        _msg("a3", "p1", "assistant", "Ask someone.", rank=2, deleted=False),
        _msg("p2", None, "prompter", "Only one reply here.", rank=None, deleted=False),
        _msg("a4", "p2", "assistant", "Only reply.", rank=0, deleted=False),
    ]
    (pair,) = list(oasst_pairs(rows))
    assert pair == {"prompt": "How do I boil water?", "reply_a": "Use a kettle.", "reply_b": "No idea."}


def test_oasst_ignores_deleted_replies():
    rows = [
        _msg("p1", None, "prompter", "Question", rank=None, deleted=False),
        _msg("a1", "p1", "assistant", "Deleted best", rank=0, deleted=True),
        _msg("a2", "p1", "assistant", "Live one", rank=1, deleted=False),
        _msg("a3", "p1", "assistant", "Live two", rank=2, deleted=False),
    ]
    (pair,) = list(oasst_pairs(rows))
    assert pair["reply_a"] == "Live one"
    assert pair["reply_b"] == "Live two"


def _completion(text, rating):
    return {"response": text, "annotations": {"helpfulness": {"Rating": rating}}}


def test_ultrafeedback_pairs_highest_and_lowest_rated():
    row = {
        "instruction": "Explain rain.",
        "completions": [
            _completion("Rain is water falling.", "3"),
            _completion("Rain is water vapour condensing and falling.", "5"),
            _completion("Dunno.", "1"),
            _completion("Clouds.", "2"),
        ],
    }
    (pair,) = list(ultrafeedback_pairs([row]))
    assert pair == {
        "prompt": "Explain rain.",
        "reply_a": "Rain is water vapour condensing and falling.",
        "reply_b": "Dunno.",
    }


def test_ultrafeedback_skips_when_best_and_worst_tie():
    row = {
        "instruction": "Say hi.",
        "completions": [_completion("Hi.", "4"), _completion("Hello.", "4")],
    }
    assert list(ultrafeedback_pairs([row])) == []


def test_ultrafeedback_skips_missing_or_non_numeric_ratings():
    row = {
        "instruction": "Say hi.",
        "completions": [_completion("Hi.", "N/A"), _completion("Hello.", "2")],
    }
    assert list(ultrafeedback_pairs([row])) == []


def test_tested_pairs_are_recorded_as_execution_judged():
    manifest = load_manifest(Path("prefkit/datasets/manifests/humaneval.yaml"))
    pair = {"prompt": ADD_HEADER, "reply_a": "    return a + b\n", "reply_b": "    return a - b\n"}
    assert map_row(pair, manifest).comparison.source == "execution"


def test_identical_transcripts_are_reported_as_no_preference():
    from prefkit.datasets.ingest import RowError, map_row
    from prefkit.datasets.manifest import Manifest

    manifest = Manifest.model_validate(
        {
            "name": "identical_test",
            "version": 1,
            "source": {"type": "local", "location": "unused.jsonl"},
            "card": "https://example.org/card",
            "license": "cc0-1.0",
            "mapping": {
                "prompt_strategy": "common_prefix",
                "response_a_field": "chosen",
                "response_b_field": "rejected",
                "winner_fixed": "a",
            },
        }
    )
    same = "\n\nHuman: Hi\n\nAssistant: Hello."
    try:
        map_row({"chosen": same, "rejected": same}, manifest)
    except RowError as exc:
        assert str(exc) == "chosen and rejected are identical, so there is no preference"
    else:
        raise AssertionError("identical transcripts were not rejected")


def test_import_stops_when_docker_is_not_available(monkeypatch):
    """Without Docker, the import must stop, never run the code directly."""

    def no_docker(program, timeout_s=10):
        raise sandbox.SandboxUnavailable("Docker is not installed.")

    monkeypatch.setattr(sandbox, "passes", no_docker)
    row = {
        "task_id": "t/0",
        "prompt": ADD_HEADER,
        "canonical_solution": "    return a + b\n",
        "test": "def check(candidate):\n    assert candidate(1, 2) == 3\n",
        "entry_point": "add",
    }
    with pytest.raises(sandbox.SandboxUnavailable):
        list(humaneval_pairs([row]))


def test_the_import_uses_the_sandbox():
    assert pair_sources._passes.__doc__ and "sandbox" in pair_sources._passes.__doc__
