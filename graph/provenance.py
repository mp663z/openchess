"""Contract-derived graph provenance runtime.

Provenance records are validated against the linted T0149 contract and its
linked source and target contracts. The table is keyed only by each owning
sibling's target identity and merges source observations by set union.
"""

from __future__ import annotations

import ast
import copy
import re
from datetime import datetime
from pathlib import Path

import yaml

from graph.node import make_record as make_node_record
from graph.node import record_identity as node_identity
from graph.position_digest import DigestError
from tools.provenance_contract_lint import CONTRACT, IMPORT, lint
from tools.variant_runtime import VariantError

ROOT = Path(__file__).resolve().parents[1]
VARIANT = ROOT / "data/contracts/variant.yaml"
LEGAL_MOVES = ROOT / "data/contracts/legal_moves.yaml"

lint(CONTRACT)
_PROVENANCE = yaml.safe_load(CONTRACT.read_text())["contract"]
_IMPORT = yaml.safe_load(IMPORT.read_text())["contract"]
_VARIANT = yaml.safe_load(VARIANT.read_text())["contract"]
_LEGAL_MOVES = yaml.safe_load(LEGAL_MOVES.read_text())["contract"]

FAILURE_MAPPING = dict(_PROVENANCE["failures"]["mapping"])
_FAILURE_CLASSES = set(_PROVENANCE["failures"]["classes"])
_ERROR_ENUM = set(_PROVENANCE["errors"]["closed_enum"])
_TARGET_KINDS = tuple(_PROVENANCE["targets"]["kinds"])
_RECORD_FIELDS = set(_PROVENANCE["record"]["fields"])
_SOURCE_FIELDS = set(_PROVENANCE["source_entry"]["fields"])
_SOURCE_IDS = {entry["id"] for entry in _IMPORT["sources"]["entries"]}
_VARIANT_IDS = {entry["id"] for entry in _VARIANT["variants"]["entries"]}

_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z"
)


class ProvenanceError(Exception):
    """A closed, non-retryable provenance contract rejection."""

    def __init__(self, failure_class: str, message: str | None = None) -> None:
        if type(failure_class) is not str or failure_class not in _FAILURE_CLASSES:
            raise ValueError(f"undeclared provenance failure {failure_class!r}")
        code = FAILURE_MAPPING[failure_class]
        if code not in _ERROR_ENUM:
            raise ValueError(f"undeclared provenance code {code!r}")
        detail = message or failure_class
        if type(detail) is not str or not detail.strip():
            raise ValueError("error message must be a nonempty string")
        super().__init__(detail)
        self.failure_class = failure_class
        self.code = code
        self.message = detail
        self.retryable = False


def _fail(failure_class: str, message: str | None = None) -> None:
    raise ProvenanceError(failure_class, message)


def _exact_dict(obj: object) -> bool:
    """Exact dict with exact-str keys (T0149 reference _exact_dict): a key
    str subclass with a raising or lying __eq__/__hash__ never reaches a
    set build, membership test or lookup."""
    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))


def _source_key(entry: dict) -> tuple[str, str, str]:
    return entry["source_id"], entry["game_id"], entry["first_observed_at"]


def _valid_timestamp(value: object) -> bool:
    if type(value) is not str or not value.isascii() or _TIMESTAMP.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _valid_game_id(value: object) -> bool:
    return type(value) is str and bool(value) and all(0x21 <= ord(char) <= 0x7E for char in value)


def _canonical_sources(sources: object) -> list[dict]:
    if type(sources) is not list or not sources:
        _fail("malformed_provenance_record", "sources must be a nonempty list")
    unique = {}
    for entry in sources:
        if not _exact_dict(entry) or set(entry) != _SOURCE_FIELDS:
            _fail("malformed_provenance_record", "source entry has the wrong shape")
        # exact-str type BEFORE membership (T0149 reference): an unhashable or
        # str-subclass id never reaches a hash/== compare.
        if type(entry["source_id"]) is not str:
            _fail("malformed_provenance_record", "source_id must be a str")
        if entry["source_id"] not in _SOURCE_IDS:
            _fail("unknown_source", f"unknown source {entry['source_id']!r}")
        if not _valid_game_id(entry["game_id"]):
            _fail("malformed_provenance_record", "game_id has the wrong shape")
        if not _valid_timestamp(entry["first_observed_at"]):
            _fail("malformed_provenance_record", "timestamp has the wrong shape")
        key = _source_key(entry)
        unique[key] = {field: entry[field] for field in _PROVENANCE["source_entry"]["fields"]}
    return [unique[key] for key in sorted(unique)]


def _move_text_valid(move: object) -> bool:
    shape = _LEGAL_MOVES["move_model"]["shape"]
    grammar = _LEGAL_MOVES["move_model"]["square_grammar"]
    if type(move) is not str or not move.isascii() or len(move) not in (4, 5):
        return False
    start, end = move[:2], move[2:4]
    if any(square[0] not in grammar["files"] or square[1] not in grammar["ranks"]
           for square in (start, end)):
        return False
    if shape["from_to_distinct"] and start == end:
        return False
    return len(move) == 4 or move[4] in shape["types"]["promotion"]["enum"]


def _node_target_identity(target: object) -> tuple:
    if not _exact_dict(target) or set(target) != {"variant", "snapshot_fen"}:
        _fail("malformed_target_identity")
    if type(target["variant"]) is not str or type(target["snapshot_fen"]) is not str:
        _fail("malformed_target_identity")
    failed = False
    try:
        record = make_node_record(target["variant"], target["snapshot_fen"])
        if record["snapshot_fen"] != target["snapshot_fen"]:
            _fail("malformed_target_identity", "node snapshot is not canonical")
        identity = ast.literal_eval(node_identity(record))
        return "transposition_node", target["variant"], identity
    except (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):
        failed = True
    if failed:  # fresh, unchained: raised outside the except block
        _fail("malformed_target_identity", "linked node derivation rejected the snapshot")


def _edge_target_identity(target: object) -> tuple:
    fields = {"variant", "move", "from_snapshot_fen", "to_snapshot_fen"}
    if not _exact_dict(target) or set(target) != fields:
        _fail("malformed_target_identity")
    if (not all(type(target[field]) is str for field in fields)
            or not _move_text_valid(target["move"])):
        _fail("malformed_target_identity")
    failed = False
    try:
        before = make_node_record(target["variant"], target["from_snapshot_fen"])
        after = make_node_record(target["variant"], target["to_snapshot_fen"])
        if (before["snapshot_fen"] != target["from_snapshot_fen"]
                or after["snapshot_fen"] != target["to_snapshot_fen"]):
            _fail("malformed_target_identity", "edge snapshot is not canonical")
        return (
            "route_edge",
            target["variant"],
            target["move"],
            ast.literal_eval(node_identity(before)),
            ast.literal_eval(node_identity(after)),
        )
    except (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):
        failed = True
    if failed:  # fresh, unchained: raised outside the except block
        _fail("malformed_target_identity", "linked node derivation rejected a snapshot")


def _context_target_identity(target: object) -> tuple:
    if not _exact_dict(target) or set(target) != {"variant", "path_moves"}:
        _fail("malformed_target_identity")
    variant, path = target["variant"], target["path_moves"]
    if type(variant) is not str or variant not in _VARIANT_IDS:
        _fail("malformed_target_identity")
    if type(path) is not list or not all(_move_text_valid(move) for move in path):
        _fail("malformed_target_identity")
    return "opening_context", variant, tuple(path)


def _target_identity(kind: str, target: object) -> tuple:
    if kind == "transposition_node":
        return _node_target_identity(target)
    if kind == "route_edge":
        return _edge_target_identity(target)
    if kind == "opening_context":
        return _context_target_identity(target)
    _fail("unknown_target_kind")


def validate_record(record: object) -> dict:
    """Validate and return a detached canonical provenance record."""
    if not _exact_dict(record) or set(record) != _RECORD_FIELDS:
        _fail("malformed_provenance_record", "record has the wrong shape")
    kind = record["target_kind"]
    if type(kind) is not str or kind not in _TARGET_KINDS:
        _fail("unknown_target_kind")
    sources = _canonical_sources(record["sources"])
    _target_identity(kind, record["target"])
    return {
        "target_kind": kind,
        "target": copy.deepcopy(record["target"]),
        "sources": sources,
    }


class ProvenanceTable:
    """Atomic insert-or-union provenance table keyed by target identity."""

    def __init__(self) -> None:
        self._records: dict[tuple, dict] = {}

    def insert(self, record: object) -> dict:
        canonical = validate_record(record)
        identity = _target_identity(canonical["target_kind"], canonical["target"])
        key = canonical["target_kind"], identity
        existing = self._records.get(key)
        if existing is None:
            self._records[key] = canonical
            return copy.deepcopy(canonical)
        sources = _canonical_sources(existing["sources"] + canonical["sources"])
        if sources != existing["sources"]:
            self._records[key] = {**existing, "sources": sources}
        return copy.deepcopy(self._records[key])

    def merge(self, other: object) -> ProvenanceTable:
        # Untrusted merge source: any failure while reading records() is a
        # fresh, unchained typed rejection (flag pattern, raised outside the
        # except block), and the batch must be an exact list (T0149 reference).
        failed = False
        try:
            reader = getattr(other, "records", None)
            incoming = reader() if callable(reader) else None
        except BaseException:
            failed = True
        if failed:
            _fail("malformed_provenance_record", "merge source records() failed")
        if type(incoming) is not list:
            _fail("malformed_provenance_record", "merge source must expose records() -> list")
        staged = copy.deepcopy(self._records)
        candidate = ProvenanceTable()
        candidate._records = staged
        for record in list(incoming):
            candidate.insert(record)
        self._records = candidate._records
        return self

    def records(self) -> list[dict]:
        return copy.deepcopy(list(self._records.values()))

    def serialize(self) -> list[tuple]:
        out = []
        for key, record in self._records.items():
            out.append(
                (
                    record["target_kind"],
                    repr(key),
                    tuple(_source_key(entry) for entry in record["sources"]),
                )
            )
        return sorted(out)
