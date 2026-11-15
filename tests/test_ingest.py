"""Row mapping and quarantine rules: pure Python, no database."""

import pytest

from prefkit.canonical import Comparison
from prefkit.datasets.ingest import AbortIngest, IngestRun, map_row
from prefkit.datasets.manifest import Manifest


def make_manifest(**mapping_overrides) -> Manifest:
    mapping = {
        "prompt_strategy": "common_prefix",
        "response_a_field": "chosen",
        "response_b_field": "rejected",
        "winner_fixed": "a",
        "shuffle_positions": False,
    } | mapping_overrides
    return Manifest.model_validate(
        {
            "name": "test_set",
            "version": 1,
            "source": {"type": "local", "location": "unused.jsonl"},
            "card": "https://example.org/card",
            "license": "mit",
            "mapping": mapping,
        }
    )


def hh_row(question: str, good: str, bad: str) -> dict[str, str]:
    prefix = f"\n\nHuman: {question}"
    return {
        "chosen": f"{prefix}\n\nAssistant: {good}",
        "rejected": f"{prefix}\n\nAssistant: {bad}",
    }


def test_common_prefix_splits_prompt_and_responses():
    mapped = map_row(hh_row("How do I boil water?", "Use a kettle.", "No idea."), make_manifest())

    assert mapped.prompt.text == "Human: How do I boil water?"
    assert mapped.responses[0].text == "Use a kettle."
    assert mapped.responses[1].text == "No idea."
    assert mapped.comparison.winner == "a"


def test_swap_flips_winner_with_the_response():
    rows = [hh_row(f"question {i}", f"good {i}", f"bad {i}") for i in range(40)]
    manifest = make_manifest(shuffle_positions=True)

    swapped_seen = unswapped_seen = False
    for row in rows:
        mapped = map_row(row, manifest)
        preferred = mapped.responses[0] if mapped.comparison.winner == "a" else mapped.responses[1]
        # The preferred response must always be the one the winner points to.
        assert preferred.text == row["chosen"].split("Assistant: ")[1]
        if mapped.responses[0].text.startswith("bad"):
            swapped_seen = True
        else:
            unswapped_seen = True

    # Both orders should occur, otherwise the shuffle is doing nothing.
    assert swapped_seen and unswapped_seen


def test_same_row_always_maps_identically():
    row = hh_row("Why is the sky blue?", "Scattering.", "Paint.")
    manifest = make_manifest(shuffle_positions=True)
    assert map_row(row, manifest) == map_row(row, manifest)


def test_single_bad_row_is_quarantined_not_fatal():
    good = [hh_row(f"q{i}", f"g{i}", f"b{i}") for i in range(300)]
    broken = {"chosen": "no turn markers here", "rejected": "nor here"}

    run = IngestRun(make_manifest())
    accepted = list(run.run(good + [broken]))

    assert len(accepted) == 300
    assert run.quarantined == 1
    assert run.examples[0].row_index == 300


def test_mostly_broken_dataset_aborts_early():
    broken = [{"chosen": "x", "rejected": "y"}] * 500
    run = IngestRun(make_manifest())

    with pytest.raises(AbortIngest, match="quarantined"):
        list(run.run(broken))

    # The abort should come at the sample check, not after all 500 rows.
    assert run.accepted + run.quarantined < 500


def test_comparison_ids_are_stable_across_runs():
    rows = [hh_row("same", "one", "two")]
    manifest = make_manifest()

    first = [m.comparison for m in IngestRun(manifest).run(rows)]
    second = [m.comparison for m in IngestRun(manifest).run(rows)]

    assert first == second
    assert isinstance(first[0], Comparison)


def test_field_strategy_with_integer_labels():
    manifest = Manifest.model_validate(
        {
            "name": "int_labels",
            "version": 1,
            "source": {"type": "local", "location": "unused.jsonl"},
            "card": "https://example.org/card",
            "license": "mit",
            "mapping": {
                "prompt_strategy": "field",
                "prompt_field": "p",
                "response_a_field": "a",
                "response_b_field": "b",
                "winner_field": "w",
                "winner_map": {"1": "a", "2": "b", "tie": "tie"},
                "shuffle_positions": False,
            },
        }
    )
    mapped = map_row({"p": "hi", "a": "x", "b": "y", "w": 2}, manifest)
    assert mapped.comparison.winner == "b"
