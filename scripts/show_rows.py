"""Print selected rows from a JSON Lines file, for checking quarantined data by eye.

Usage:
    python scripts/show_rows.py data/hh-rlhf/helpful-base/train.jsonl.gz 517 608
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

MAX_CHARS = 300  # enough to see the difference without flooding the terminal


def open_text(path: Path):
    return gzip.open(path, "rt", encoding="utf-8") if path.suffix == ".gz" else path.open(encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    path = Path(argv[0])
    wanted = {int(arg) for arg in argv[1:]}

    # Row numbers count non-empty lines, matching the ingest reader.
    position = 0
    with open_text(path) as handle:
        for line in handle:
            if not line.strip():
                continue
            if position in wanted:
                row = json.loads(line)
                print(f"--- row {position} ---")
                print("chosen:  ", row["chosen"][-MAX_CHARS:].replace("\n", " | "))
                print("rejected:", row["rejected"][-MAX_CHARS:].replace("\n", " | "))
                print()
            position += 1
            if position > max(wanted):
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
