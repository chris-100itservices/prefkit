"""Map raw dataset rows onto the canonical schema.

This is the only module that knows how a dataset is laid out. It reads rows through
a source reader, maps each one using the manifest, and either yields a canonical
record or quarantines the row with a reason. Nothing downstream depends on the source.

Extension points (both use decorator registries):
  @extractor("name")     a new row layout, e.g. one prompt column or a shared prefix
  @source_reader("kind") a new place to read rows from, e.g. a Hub id or a local file
"""

from __future__ import annotations

import functools
import gzip
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from prefkit.canonical import Comparison, Prompt, Response
from prefkit.datasets.manifest import Label, Manifest, MappingSpec

log = logging.getLogger(__name__)

# Repository root, used to resolve relative paths in manifests. Resolving against this,
# not the current directory, means a debugger started from any folder finds the data.
PROJECT_ROOT: Final = Path(__file__).resolve().parents[2]

# Rows checked before deciding a mapping is catastrophically wrong.
SAMPLE_SIZE: Final = 200

# If at least this share of the first SAMPLE_SIZE rows fail, stop early. Set well above
# the normal limit, so a single bad row cannot abort a run.
EARLY_ABORT_RATE: Final = 0.5

# Keep examples of failures, not every failure, so memory stays bounded.
MAX_STORED_EXAMPLES: Final = 100

# Reasons are truncated, because exception messages can contain whole rows.
MAX_REASON_CHARS: Final = 200

_LABELS: Final = frozenset({"a", "b", "tie"})

# Swapping A and B exchanges the winner too. "tie" and "unlabeled" stay as they are.
_FLIP: Final[dict[Label | None, Label | None]] = {"a": "b", "b": "a", "tie": "tie", None: None}


class RowError(Exception):
    """One row cannot be mapped. The row is quarantined and the run continues."""


class AbortIngest(Exception):
    """The run cannot continue. This usually means the mapping is wrong."""


def row_errors(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Turn any unexpected exception raised while mapping one row into RowError.

    Why: real datasets contain shapes nobody anticipated. Each row should be isolated,
    so a surprise becomes a quarantine entry rather than a crashed run. RowErrors pass
    through unchanged so their messages stay precise.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except RowError:
            raise
        except Exception as exc:  # deliberate: this is the per-row isolation boundary
            raise RowError(f"{type(exc).__name__}: {exc}") from exc

    return wrapper


# An extractor takes one row and returns (prompt, response_a, response_b) as text.
ExtractFn = Callable[[Mapping[str, Any], MappingSpec], tuple[str, str, str]]
_EXTRACTORS: dict[str, ExtractFn] = {}


def extractor(strategy: str) -> Callable[[ExtractFn], ExtractFn]:
    """Register a prompt-extraction strategy under the name manifests use."""

    def register(fn: ExtractFn) -> ExtractFn:
        if strategy in _EXTRACTORS:
            raise RuntimeError(f"extractor '{strategy}' is already registered")
        _EXTRACTORS[strategy] = fn
        return fn

    return register


# A source reader takes a manifest and yields raw rows, without interpreting them.
SourceFn = Callable[[Manifest], Iterable[Mapping[str, Any]]]
_SOURCES: dict[str, SourceFn] = {}


def source_reader(kind: str) -> Callable[[SourceFn], SourceFn]:
    """Register a way of reading a dataset, keyed by the manifest's source.type."""

    def register(fn: SourceFn) -> SourceFn:
        if kind in _SOURCES:
            raise RuntimeError(f"source reader '{kind}' is already registered")
        _SOURCES[kind] = fn
        return fn

    return register


@source_reader("hf")
def _read_hub(manifest: Manifest) -> Iterable[Mapping[str, Any]]:
    # Imported here, so local-only runs and the test suite do not need the package.
    from datasets import load_dataset

    rows = load_dataset(
        manifest.source.location,
        split=manifest.source.split,
        streaming=True,  # rows are read on demand; the full dataset is never held in memory
    )
    # load_dataset returns a union of types depending on its arguments. With a single
    # split it yields one iterable of rows, so cast it to the type we declare.
    return cast(Iterable[Mapping[str, Any]], rows)


def _open_text(path: Path) -> Any:
    """Open a text file for reading. A name ending in .gz is decompressed while it is read."""
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open(encoding="utf-8")


@source_reader("local")
def _read_jsonl(manifest: Manifest) -> Iterator[Mapping[str, Any]]:
    path = Path(manifest.source.location)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    # A missing or malformed file is a file-level problem, not a row-level one, so it aborts.
    try:
        handle_ctx = _open_text(path)
    except FileNotFoundError as exc:
        raise AbortIngest(f"data file not found: {path}") from exc
    with handle_ctx as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise AbortIngest(f"{path}:{line_number} is not valid JSON") from exc


def read_rows(manifest: Manifest) -> Iterable[Mapping[str, Any]]:
    """Return the raw rows for a manifest's source, read lazily."""
    try:
        reader = _SOURCES[manifest.source.type]
    except KeyError as exc:
        raise AbortIngest(f"no source reader registered for '{manifest.source.type}'") from exc
    return reader(manifest)


def _text(row: Mapping[str, Any], key: str) -> str:
    """Return a non-empty string field, or raise RowError naming the field."""
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RowError(f"field '{key}' is missing, empty or not text")
    return value


@extractor("field")
def _extract_field(row: Mapping[str, Any], spec: MappingSpec) -> tuple[str, str, str]:
    # The manifest validator guarantees prompt_field is set for this strategy.
    assert spec.prompt_field is not None
    return (
        _text(row, spec.prompt_field),
        _text(row, spec.response_a_field),
        _text(row, spec.response_b_field),
    )


# HH-RLHF transcripts look like "\n\nHuman: ...\n\nAssistant: ...". The lookahead keeps
# each turn's marker at the start of its piece, so the pieces join back exactly.
_TURN_START = re.compile(r"(?=\n\n(?:Human|Assistant): )")
_ASSISTANT_MARK = "\n\nAssistant: "


def _split_turns(transcript: str) -> list[str]:
    return [piece for piece in _TURN_START.split(transcript) if piece]


@extractor("common_prefix")
def _extract_common_prefix(row: Mapping[str, Any], spec: MappingSpec) -> tuple[str, str, str]:
    """Both responses are full transcripts that share leading turns.

    The shared turns become the prompt. The first differing turn starts each response,
    and it must be an assistant turn.
    """
    a_raw = _text(row, spec.response_a_field)
    b_raw = _text(row, spec.response_b_field)
    if a_raw == b_raw:
        raise RowError("chosen and rejected are identical, so there is no preference")

    chosen = _split_turns(a_raw)
    rejected = _split_turns(b_raw)

    shared = 0
    for a_turn, b_turn in zip(chosen, rejected, strict=False):
        if a_turn != b_turn:
            break
        shared += 1

    if shared == 0:
        raise RowError("transcripts share no leading turn, so there is no prompt")
    if shared == len(chosen) or shared == len(rejected):
        raise RowError("one transcript is a prefix of the other, so nothing differs")

    a_tail = "".join(chosen[shared:])
    b_tail = "".join(rejected[shared:])
    if not (a_tail.startswith(_ASSISTANT_MARK) and b_tail.startswith(_ASSISTANT_MARK)):
        raise RowError("the first differing turn is not an assistant turn")

    prompt = "".join(chosen[:shared]).strip()
    return (
        prompt,
        a_tail.removeprefix(_ASSISTANT_MARK).strip(),
        b_tail.removeprefix(_ASSISTANT_MARK).strip(),
    )


@row_errors
def _resolve_winner(row: Mapping[str, Any], spec: MappingSpec) -> Label | None:
    """Return "a", "b", "tie", or None when the row has no label yet."""
    if spec.winner_fixed is not None:
        return spec.winner_fixed

    assert spec.winner_field is not None
    raw = row.get(spec.winner_field)
    if raw is None:
        return None  # unlabeled: kept for annotation, excluded from training

    key = str(raw)  # 1 and "1" both become "1", so integer labels work
    if spec.winner_map is not None:
        if key not in spec.winner_map:
            raise RowError(f"winner value {raw!r} is not in winner_map")
        return spec.winner_map[key]

    if key not in _LABELS:
        raise RowError(f"winner value {raw!r} is not a/b/tie and no winner_map is set")
    return cast(Label, key)


def _digest(*parts: str) -> str:
    """Stable short id from several strings. The separator stops ("ab","c") and ("a","bc") colliding."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:16]


def _show_b_first(row_bytes: bytes, enabled: bool) -> bool:
    """Decide whether to show B first, from the row's own content.

    Why content and not random(): re-running ingest must produce identical data, or
    results from two runs cannot be compared.
    """
    if not enabled:
        return False
    return hashlib.sha256(row_bytes).digest()[0] % 2 == 1


def _is_gold(row: Mapping[str, Any], manifest: Manifest) -> bool:
    policy = manifest.gold
    if policy.kind == "field":
        return bool(row.get(policy.field))
    # "execution" is decided later by running the tests in a sandbox.
    return False


@dataclass(frozen=True, slots=True)
class Mapped:
    """One successfully mapped row, in canonical form."""

    prompt: Prompt
    responses: tuple[Response, Response]
    comparison: Comparison


@dataclass(frozen=True, slots=True)
class Quarantined:
    """One row that could not be mapped, with its position and reason."""

    row_index: int
    reason: str


@row_errors
def map_row(row: Mapping[str, Any], manifest: Manifest) -> Mapped:
    """Map one raw row to canonical records. Raises RowError if it cannot be mapped."""
    spec = manifest.mapping
    extract = _EXTRACTORS.get(spec.prompt_strategy)
    if extract is None:
        raise RowError(f"no extractor registered for '{spec.prompt_strategy}'")

    prompt_text, a_text, b_text = extract(row, spec)
    winner = _resolve_winner(row, spec)

    # Ids come from text, not position, so they stay the same whichever slot a response
    # occupies after a position swap.
    prompt_id = _digest(prompt_text)
    a = Response(id=_digest(prompt_id, a_text), prompt_id=prompt_id, text=a_text)
    b = Response(id=_digest(prompt_id, b_text), prompt_id=prompt_id, text=b_text)

    row_bytes = json.dumps(row, sort_keys=True, default=str).encode("utf-8")
    if _show_b_first(row_bytes, spec.shuffle_positions):
        a, b = b, a
        winner = _FLIP[winner]

    comparison = Comparison(
        id=_digest(prompt_id, a.id, b.id),
        prompt_id=prompt_id,
        a_id=a.id,
        b_id=b.id,
        winner=winner,
        gold=_is_gold(row, manifest),
        source=manifest.judged_by,
    )
    return Mapped(
        prompt=Prompt(id=prompt_id, text=prompt_text),
        responses=(a, b),
        comparison=comparison,
    )


class IngestRun:
    """One ingest of one dataset.

    Counts are kept as rows arrive, so the quarantine limit can stop a broken mapping
    early instead of after the whole dataset has been read.
    """

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self.accepted = 0
        self.reason_counts: Counter[str] = Counter()
        self.examples: list[Quarantined] = []

    @property
    def quarantined(self) -> int:
        return sum(self.reason_counts.values())

    @property
    def quarantine_rate(self) -> float:
        total = self.accepted + self.quarantined
        return self.quarantined / total if total else 0.0

    def snapshot(self) -> dict[str, int]:
        """Counts for live progress reporting."""
        return {"accepted": self.accepted, "quarantined": self.quarantined}

    def run(self, rows: Iterable[Mapping[str, Any]]) -> Iterator[Mapped]:
        for index, row in enumerate(rows):
            try:
                mapped = map_row(row, self.manifest)
            except RowError as exc:
                self._quarantine(index, str(exc))
                self._check(final=False)
                continue
            self.accepted += 1
            yield mapped

        self._check(final=True)
        log.info(
            "ingest %s: accepted=%d quarantined=%d (%.2f%%)",
            self.manifest.name,
            self.accepted,
            self.quarantined,
            self.quarantine_rate * 100,
        )

    def _quarantine(self, index: int, reason: str) -> None:
        reason = reason[:MAX_REASON_CHARS]
        self.reason_counts[reason] += 1
        if len(self.examples) < MAX_STORED_EXAMPLES:
            self.examples.append(Quarantined(row_index=index, reason=reason))

    def _check(self, final: bool) -> None:
        total = self.accepted + self.quarantined
        limit = self.manifest.max_quarantine_rate

        if not final:
            # Mid-run: only the catastrophic case stops early.
            if total < SAMPLE_SIZE or self.quarantine_rate < EARLY_ABORT_RATE:
                return
            raise AbortIngest(self._abort_message(total, EARLY_ABORT_RATE))

        if self.quarantine_rate > limit:
            raise AbortIngest(self._abort_message(total, limit))

    def _abort_message(self, total: int, limit: float) -> str:
        if not self.reason_counts:
            return f"{self.manifest.name}: aborted after {total} rows"
        top_reason, count = self.reason_counts.most_common(1)[0]
        return (
            f"{self.manifest.name}: {self.quarantined} of {total} rows quarantined "
            f"({self.quarantine_rate:.1%}), above the limit of {limit:.1%}. "
            f"Most common reason ({count} rows): {top_reason}"
        )


# Imported last: the module registers its readers with the decorators above.
from prefkit.datasets import pair_sources  # noqa: E402,F401
