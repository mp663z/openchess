"""Production store migration for data/contracts/migration.yaml.

One registered schema step per migration over a content-addressed graph
state. Records validate through the shipped graph.node runtime; the
target digest oracle is UNTRUSTED input behind the boundary (exactly one
call per retained key, frozen request and source, exact built-in str
output in the target schema's digest grammar). The transform runs on a
staged copy that is validated against the target schema before the
receipt is derived; the request and source are restored bit-identical
on every exit.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from graph.node import make_record, record_identity
from graph.position_digest import DigestError
from tools.migration_contract_lint import FAILURE_MAPPING
from tools.variant_runtime import VariantError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "migration.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_IDS = _CC["identifiers"]
_STATE_RE = re.compile(_IDS["state_id"]["grammar"])
_SCHEMA_RE = re.compile(_IDS["schema_id"]["grammar"])
_SCHEMAS = {entry["id"]: entry for entry in _CC["registry"]["schemas"]}
_STEPS = {(step["from_schema"], step["to_schema"]): step
          for step in _CC["registry"]["steps"]}
_REQUEST_KEYS = frozenset({"from_schema", "to_schema", "source_id"})
_RECORD_FIELDS = frozenset({"variant", "digest", "snapshot_fen"})
# exactly the rejections the linked graph.node runtime uses for bad input
_LINKED_REJECTIONS = (VariantError, DigestError, ValueError)

__all__ = ["FAILURE_MAPPING", "MigrationEngine", "MigrationError",
           "derive_migration_id", "pdv2_digest", "state_id"]


class MigrationError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise MigrationError(failure_class, FAILURE_MAPPING[failure_class])


def state_id(state):
    """Canonical state content digest (graph-diff serialization) over any
    schema's records; the caller validates the state first."""
    parts = []
    for key in sorted(state):
        record = state[key]
        body = "|".join(f"{field}={record[field]}" for field in sorted(record))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256("".join(parts).encode()).hexdigest()


def derive_migration_id(from_schema, to_schema, source_id, target_id):
    return "mg1:" + hashlib.sha256(
        f"{from_schema}\n{to_schema}\n{source_id}\n{target_id}"
        .encode()).hexdigest()


def pdv2_digest(variant, snapshot_fen):
    """The honest store-v2 target digest oracle."""
    return "pdv2:" + hashlib.sha256(
        f"{variant}\n{snapshot_fen}".encode()).hexdigest()


def _record_ok(key, record, digest_re):
    """Exact record under a schema: exact str key and field set, exact
    str fields, canonical variant/snapshot through graph.node, digest in
    the schema's grammar, derived identity == key."""
    if type(key) is not str or type(record) is not dict:
        return False
    fields = list(dict.keys(record))
    if not all(type(field) is str for field in fields) or \
            set(fields) != _RECORD_FIELDS:
        return False
    if any(type(record[field]) is not str for field in _RECORD_FIELDS):
        return False
    try:
        derived = make_record(record["variant"], record["snapshot_fen"])
        identity = record_identity(derived)
    except _LINKED_REJECTIONS:
        return False
    if record["variant"] != derived["variant"] or \
            record["snapshot_fen"] != derived["snapshot_fen"]:
        return False
    if digest_re.fullmatch(record["digest"]) is None:
        return False
    return identity == key


class MigrationEngine:
    """Total request and source validation, structural source check,
    registered step, untrusted target oracle behind the boundary, staged
    transform validated against the target schema, derived receipt."""

    def __init__(self, target_oracle):
        self.target_oracle = target_oracle  # UNTRUSTED

    def _call_target_oracle(self, variant, snapshot_fen, digest_re):
        try:
            out = self.target_oracle(variant, snapshot_fen)
        except BaseException:
            _fail("divergent_target")
        if type(out) is not str or digest_re.fullmatch(out) is None:
            _fail("divergent_target")
        return out

    def migrate(self, request, source_state):
        if type(request) is not dict:
            _fail("malformed_migration_record")
        request_keys = list(dict.keys(request))
        if not all(type(key) is str for key in request_keys) or \
                set(request_keys) != _REQUEST_KEYS:
            _fail("malformed_migration_record")
        saved_request = list(dict.items(request))
        frozen_req = {}
        for field in ("from_schema", "to_schema"):
            value = request[field]
            if type(value) is not str or \
                    _SCHEMA_RE.fullmatch(value) is None or \
                    value not in _SCHEMAS:
                _fail("malformed_migration_record")
            frozen_req[field] = value
        value = request["source_id"]
        if type(value) is not str or _STATE_RE.fullmatch(value) is None:
            _fail("malformed_migration_record")
        frozen_req["source_id"] = value
        if (frozen_req["from_schema"], frozen_req["to_schema"]) not in _STEPS:
            _fail("unknown_migration")
        source_re = re.compile(
            _SCHEMAS[frozen_req["from_schema"]]["digest_grammar"])
        target_re = re.compile(
            _SCHEMAS[frozen_req["to_schema"]]["digest_grammar"])
        if type(source_state) is not dict:
            _fail("malformed_migration_record")
        for key, record in dict.items(source_state):
            if not _record_ok(key, record, source_re):
                _fail("malformed_migration_record")
        saved_container = dict(source_state)
        saved_records = {id(rec): (rec, dict(rec))
                         for rec in source_state.values()}
        frozen = {key: dict(rec) for key, rec in source_state.items()}
        try:
            if state_id(frozen) != frozen_req["source_id"]:
                _fail("conflicting_source")
            staged = {}
            for key in sorted(frozen):
                record = frozen[key]
                staged[key] = {
                    "variant": record["variant"],
                    "digest": self._call_target_oracle(
                        record["variant"], record["snapshot_fen"], target_re),
                    "snapshot_fen": record["snapshot_fen"]}
            if set(staged) != set(frozen) or len(staged) != len(frozen):
                _fail("divergent_target")
            for key, record in dict.items(staged):
                if not _record_ok(key, record, target_re):
                    _fail("divergent_target")
            target_id = state_id(staged)
        finally:
            for rec, content in saved_records.values():
                rec.clear()
                rec.update(content)
            source_state.clear()
            source_state.update(saved_container)
            dict.clear(request)
            dict.update(request, saved_request)
        return {"migration_id": derive_migration_id(
                    frozen_req["from_schema"], frozen_req["to_schema"],
                    frozen_req["source_id"], target_id),
                "from_schema": frozen_req["from_schema"],
                "to_schema": frozen_req["to_schema"],
                "source_id": frozen_req["source_id"],
                "target_id": target_id,
                "state": staged}
