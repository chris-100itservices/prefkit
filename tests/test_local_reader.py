"""The local reader handles plain and gzip-compressed JSON Lines."""

import gzip
import json

from prefkit.datasets.ingest import read_rows
from prefkit.datasets.manifest import Manifest

ROWS = [{"chosen": "a", "rejected": "b"}, {"chosen": "c", "rejected": "d"}]


def manifest_for(path) -> Manifest:
    return Manifest.model_validate(
        {
            "name": "reader_test",
            "version": 1,
            "source": {"type": "local", "location": str(path)},
            "card": "https://example.org/card",
            "license": "mit",
            "mapping": {
                "prompt_strategy": "common_prefix",
                "response_a_field": "chosen",
                "response_b_field": "rejected",
                "winner_fixed": "a",
            },
        }
    )


def test_plain_jsonl_is_read(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in ROWS), encoding="utf-8")

    assert list(read_rows(manifest_for(path))) == ROWS


def test_gzip_jsonl_is_read(tmp_path):
    path = tmp_path / "rows.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write("\n".join(json.dumps(r) for r in ROWS))

    assert list(read_rows(manifest_for(path))) == ROWS
    