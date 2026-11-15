"""Manifest validation: pure Python, no Django, no network."""

from pathlib import Path

import pytest

from prefkit.datasets.manifest import ManifestError, load_manifest

MANIFEST_DIR = Path(__file__).resolve().parents[1] / "prefkit" / "datasets" / "manifests"

BASE = """\
name: {name}
version: 1
source:
  type: hf
  location: example/dataset
  split: train
card: https://example.org/card
license: mit
mapping:
  prompt_strategy: field
  prompt_field: prompt
  response_a_field: a_text
  response_b_field: b_text
  winner_field: winner
"""


def write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["hh_rlhf", "code_pairs", "demo_local"])
def test_shipped_manifests_load(name):
    assert load_manifest(MANIFEST_DIR / f"{name}.yaml").name == name


def test_valid_base_manifest_loads(tmp_path):
    path = write(tmp_path, "good", BASE.format(name="good"))
    assert load_manifest(path).mapping.winner_field == "winner"


def test_name_must_match_file_name(tmp_path):
    path = write(tmp_path, "wrong", BASE.format(name="right"))
    with pytest.raises(ManifestError, match="must match the file name"):
        load_manifest(path)


def test_unknown_key_is_rejected(tmp_path):
    body = BASE.format(name="typo").replace("winner_field", "winner_feild")
    with pytest.raises(ManifestError):
        load_manifest(write(tmp_path, "typo", body))


def test_common_prefix_rejects_prompt_field(tmp_path):
    body = BASE.format(name="prefix").replace("prompt_strategy: field", "prompt_strategy: common_prefix")
    with pytest.raises(ManifestError, match="must not set prompt_field"):
        load_manifest(write(tmp_path, "prefix", body))


def test_winner_needs_exactly_one_source(tmp_path):
    body = BASE.format(name="both") + "  winner_fixed: a\n"
    with pytest.raises(ManifestError, match="exactly one of winner_field or winner_fixed"):
        load_manifest(write(tmp_path, "both", body))


def test_gold_kind_requires_field(tmp_path):
    body = BASE.format(name="gold") + "gold:\n  kind: field\n"
    with pytest.raises(ManifestError, match="needs gold.field"):
        load_manifest(write(tmp_path, "gold", body))


def test_hf_source_requires_split(tmp_path):
    body = BASE.format(name="nosplit").replace("  split: train\n", "")
    with pytest.raises(ManifestError, match="need a split"):
        load_manifest(write(tmp_path, "nosplit", body))
