"""Path-attributed opening classification and atomic context storage."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_REGISTRY_PATH = ROOT / "data" / "openings" / "registry.yaml"
_VARIANT_PATH = ROOT / "data" / "contracts" / "variant.yaml"
_FIELDS = {"variant", "path_moves", "opening_code", "opening_name"}
_MOVE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$|^[a-h][1-8][a-h][1-8]$")
_CODE = re.compile(r"^[A-E][0-9][0-9]$")
FAILURE_MAPPING = {
    "malformed_context_record": "malformed_request",
    "malformed_path": "malformed_request",
    "unknown_variant": "unknown_variant",
    "unknown_opening_code": "unknown_opening_code",
    "conflicting_context": "conflicting_context",
}


class ContextError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]


def _fail(failure_class):
    raise ContextError(failure_class)


def _load():
    registry = yaml.safe_load(_REGISTRY_PATH.read_text())["registry"]
    variants = yaml.safe_load(_VARIANT_PATH.read_text())["contract"]["variants"]["entries"]
    return copy.deepcopy(registry), {entry["id"] for entry in variants}


def _path(value):
    if type(value) is not list:
        _fail("malformed_path")
    out = []
    try:
        for move in value:
            if type(move) is not str or not move.isascii() or _MOVE.fullmatch(move) is None:
                _fail("malformed_path")
            if move[:2] == move[2:4]:
                _fail("malformed_path")
            out.append(move)
    except ContextError:
        raise
    except BaseException as exc:
        raise ContextError("malformed_path") from exc
    return out


def resolve(registry, path):
    moves = _path(path)
    best = None
    for entry in registry["entries"]:
        prefix = entry["moves"]
        if moves[: len(prefix)] == prefix and (best is None or len(prefix) > len(best["moves"])):
            best = entry
    sentinel = registry["none_sentinel"]
    return (sentinel, sentinel) if best is None else (best["code"], best["name"])


class ContextTable:
    def __init__(self, _docs=None, *, registry=None, variants=None):
        loaded_registry, loaded_variants = _load()
        self.registry = copy.deepcopy(loaded_registry if registry is None else registry)
        self.reg = self.registry
        self.variants = set(loaded_variants if variants is None else variants)
        self._records = {}

    @property
    def records(self):
        return copy.deepcopy(list(self._records.values()))

    @property
    def map(self):
        """Detached compatibility view for pre-runtime red probes."""
        return copy.deepcopy(self._records)

    def _make(self, variant, path):
        if type(variant) is not str or variant not in self.variants:
            _fail("unknown_variant")
        moves = _path(path)
        code, name = resolve(self.registry, moves)
        return {"variant": variant, "path_moves": moves, "opening_code": code, "opening_name": name}

    def _validate(self, record):
        if type(record) is not dict or set(record) != _FIELDS:
            _fail("malformed_context_record")
        if type(record["variant"]) is not str or record["variant"] not in self.variants:
            _fail("unknown_variant")
        moves = _path(record["path_moves"])
        code, name = record["opening_code"], record["opening_name"]
        sentinel = self.registry["none_sentinel"]
        if type(code) is not str or type(name) is not str:
            _fail("malformed_context_record")
        if (code == sentinel) != (name == sentinel):
            _fail("malformed_context_record")
        entries = {entry["code"]: entry for entry in self.registry["entries"]}
        if code != sentinel:
            if _CODE.fullmatch(code) is None or code not in entries:
                _fail(
                    "unknown_opening_code" if _CODE.fullmatch(code) else "malformed_context_record"
                )
            if entries[code]["name"] != name:
                _fail("malformed_context_record")
        if (code, name) != resolve(self.registry, moves):
            _fail("malformed_context_record")
        return {
            "variant": record["variant"],
            "path_moves": moves,
            "opening_code": code,
            "opening_name": name,
        }

    def insert(self, variant, path):
        record = self._make(variant, path)
        key = (variant, tuple(record["path_moves"]))
        existing = self._records.get(key)
        if existing is not None:
            if existing != record:
                _fail("conflicting_context")
            return copy.deepcopy(existing)
        self._records[key] = copy.deepcopy(record)
        return copy.deepcopy(record)

    def serialize(self):
        return sorted(
            (r["variant"], " ".join(r["path_moves"]), r["opening_code"], r["opening_name"])
            for r in self._records.values()
        )

    def _validated_pairs(self, records):
        out = []
        if type(records) is not dict:
            _fail("malformed_context_record")
        try:
            items = list(records.items())
        except BaseException as exc:
            raise ContextError("malformed_context_record") from exc
        for raw_key, raw_record in items:
            record = self._validate(raw_record)
            expected_key = (record["variant"], tuple(record["path_moves"]))
            if type(raw_key) is not tuple or raw_key != expected_key:
                _fail("malformed_context_record")
            out.append((expected_key, record))
        return out

    def merge(self, other):
        if type(other) is not ContextTable:
            _fail("malformed_context_record")
        # Validate both sides before staging: existing corruption or key drift
        # is a typed rejection, never carried through a successful merge.
        current = self._validated_pairs(self._records)
        incoming = self._validated_pairs(other._records)
        staged = ContextTable(registry=self.registry, variants=self.variants)
        staged._records = {key: copy.deepcopy(record) for key, record in current}
        for key, record in incoming:
            existing = staged._records.get(key)
            if existing is not None and existing != record:
                _fail("conflicting_context")
            staged._records[key] = copy.deepcopy(record)
        self._records = staged._records
        return self
