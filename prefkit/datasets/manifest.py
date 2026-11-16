"""Load and validate dataset manifests.

A manifest is a YAML file that says where a dataset lives and how its fields map
onto prefkit's canonical schema (prompt, two responses, a winner). It is checked
once, when loaded, so a broken manifest fails before any data is read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# The three outcomes prefkit understands. Every other label is translated into these.
Label = Literal["a", "b", "tie"]


class ManifestError(Exception):
    """A manifest file is missing, unparseable, or describes an invalid mapping."""


class StrictModel(BaseModel):
    # extra="forbid" turns a typo such as "winner_feild" into an error. The default
    # silently ignores unknown keys, which would hide the mistake.
    model_config = ConfigDict(extra="forbid")


class SourceSpec(StrictModel):
    # hf and local read rows as they are stored. The other kinds turn a public dataset
    # into {"prompt", "reply_a", "reply_b"} rows (see pair_sources.py).
    type: Literal[
        "hf",
        "local",
        "humaneval_mutants",
        "mbpp_mutants",
        "oasst_tree",
        "ultrafeedback_best_worst",
    ]
    location: str = Field(min_length=1)  # Hub id for "hf", file path for "local"
    split: str | None = None  # required for "hf"

    @model_validator(mode="after")
    def check_split(self):
        if self.type == "hf" and not self.split:
            raise ValueError("hf sources need a split, e.g. 'train'")
        return self


class MappingSpec(StrictModel):
    """How one dataset row becomes prompt, response A, response B and a winner."""

    # "field": the prompt is stored in one column.
    # "common_prefix": the prompt is the text both responses share. HH-RLHF needs this
    #   because its rows hold whole conversations.
    prompt_strategy: Literal["field", "common_prefix"]
    prompt_field: str | None = None

    response_a_field: str = Field(min_length=1)
    response_b_field: str = Field(min_length=1)

    # Exactly one of these says who won: a column holds the answer, or every row has
    # the same fixed answer.
    winner_field: str | None = None
    winner_fixed: Label | None = None

    # Translates raw values in winner_field into "a", "b" or "tie".
    winner_map: dict[str, Label] | None = None

    # Present A and B in a content-determined order, so models cannot learn that one
    # position usually wins. The adapter flips the winner to match any swap.
    shuffle_positions: bool = True

    @model_validator(mode="after")
    def check_consistency(self):
        if self.prompt_strategy == "field" and not self.prompt_field:
            raise ValueError("prompt_strategy 'field' needs prompt_field")
        if self.prompt_strategy == "common_prefix" and self.prompt_field:
            raise ValueError("prompt_strategy 'common_prefix' must not set prompt_field")

        # Exactly one winner source. Both set, or both missing, is an error.
        if (self.winner_field is None) == (self.winner_fixed is None):
            raise ValueError("set exactly one of winner_field or winner_fixed")

        if self.winner_map and self.winner_field is None:
            raise ValueError("winner_map only applies together with winner_field")

        if self.response_a_field == self.response_b_field:
            raise ValueError("response_a_field and response_b_field must be different columns")
        return self


class GoldPolicy(StrictModel):
    """Where known-correct answers come from. Gold items drive game scoring."""

    # none:      no gold items; the game falls back to agreement scoring.
    # field:     a column already holds a boolean for "this item is gold".
    # execution: a column holds unit tests; a response counts as correct if it passes.
    kind: Literal["none", "field", "execution"] = "none"
    field: str | None = None

    @model_validator(mode="after")
    def check_field(self):
        if self.kind == "none" and self.field:
            raise ValueError("gold.field is set but gold.kind is 'none'")
        if self.kind != "none" and not self.field:
            raise ValueError(f"gold.kind '{self.kind}' needs gold.field")
        return self


class Manifest(StrictModel):
    # The name is the key the rest of prefkit uses to find the dataset, so it must be
    # a lowercase identifier and must match the file name.
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    source: SourceSpec
    card: str = Field(pattern=r"^https://")  # dataset card: provenance and intended use
    license: str = Field(min_length=1)
    mapping: MappingSpec
    gold: GoldPolicy = Field(default_factory=GoldPolicy)

    # If more than this share of rows fail during ingest, the run aborts. This catches a
    # wrong mapping, which would otherwise quarantine everything and report success.
    max_quarantine_rate: float = Field(default=0.01, ge=0.0, le=1.0)

    # Who made the judgement, recorded on every comparison. "execution" means tests decided,
    # "judge" means a model decided, and "human" means a person decided.
    judged_by: Literal["human", "judge", "execution"] = "human"


def load_manifest(path: Path) -> Manifest:
    """Read, validate and return one manifest. Raises ManifestError with a readable message."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"{path}: cannot read or parse YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: top level must be a mapping of keys to values")

    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        # One line per problem, with the key path, instead of pydantic's nested dump.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
        )
        raise ManifestError(f"{path}: {problems}") from exc

    if manifest.name != path.stem:
        raise ManifestError(f"{path}: name '{manifest.name}' must match the file name '{path.stem}'")

    return manifest
