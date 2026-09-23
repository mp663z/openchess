"""Production store restore for data/contracts/restore.yaml.

Verified restore of a content-addressed backup receipt to canonical
graph state. The receipt verifies FIRST through the linked production
backup (store.backup); the bundle parser is UNTRUSTED input behind the
boundary (exactly one call per restore on the frozen bundle, exact
built-in dict output). Every parsed record validates through the shipped
graph.node runtime, the state id is recomputed through graph.diff, and
the staged state must reserialize byte-for-byte to the frozen bundle
through the linked canonical serializer. The input receipt is restored
bit-identical on every exit and never mutated.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from graph.diff import state_id
from graph.node import make_record, record_identity
from graph.position_digest import DigestError
from store import backup as _backup
from tools.restore_contract_lint import FAILURE_MAPPING
from tools.variant_runtime import VariantError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "restore.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = tuple(_CC["record"]["fields"])
_RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
# exactly the rejections the linked graph.node runtime uses for bad
# input (the same set store.wal treats as a record rejection); anything
# else propagates
_LINKED_REJECTIONS = (VariantError, DigestError, ValueError)
_BACKUP = _backup.BackupEngine(_backup.serialize_bundle)  # linked, trusted

__all__ = ["FAILURE_MAPPING", "RestoreEngine", "RestoreError",
           "derive_restore_id", "parse_bundle"]


class RestoreError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise RestoreError(failure_class, FAILURE_MAPPING[failure_class])


def parse_bundle(bundle):
    """The honest bundle parser: exact inverse of the canonical
    serialization (store.backup.serialize_bundle) - key line, then
    field line, per record."""
    state = {}
    lines = bundle.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for i in range(0, len(lines), 2):
        record = {}
        for pair in lines[i + 1].split("|"):
            field, _sep, value = pair.partition("=")
            record[field] = value
        state[lines[i]] = record
    return state


def derive_restore_id(backup_id, sid):
    """Public restore-id derivation: sha256 over backup id and state id."""
    return "rst1:" + hashlib.sha256(
        f"{backup_id}\n{sid}".encode()).hexdigest()


def _record_identity(record):
    """Exact node record through the shipped graph.node runtime; returns
    the canonical identity, or None when the record is invalid."""
    if type(record) is not dict or \
            not all(type(key) is str for key in dict.keys(record)) or \
            set(dict.keys(record)) != set(_RECORD_FIELDS):
        return None
    if any(type(record[field]) is not str for field in _RECORD_FIELDS):
        return None
    try:
        derived = make_record(record["variant"], record["snapshot_fen"])
        identity = record_identity(derived)
    except _LINKED_REJECTIONS:
        return None
    # exact equality: a well-formed but wrong digest is rejected too
    if dict(record) != dict(derived):
        return None
    return identity


class RestoreEngine:
    """Linked backup verification first, frozen receipt fields, exactly
    one untrusted parser call, total record-by-record validation,
    recomputed state id and canonical round-trip; staged state only."""

    def __init__(self, bundle_parser):
        self.parser = bundle_parser  # UNTRUSTED

    def _parse(self, bundle):
        try:
            out = self.parser(bundle)
        except BaseException:
            _fail("divergent_parse")
        if type(out) is not dict:
            _fail("divergent_parse")
        return out

    def restore(self, receipt):
        try:
            _BACKUP.verify(receipt)
        except _backup.BackupError as err:
            if err.failure_class == "malformed_backup_record":
                _fail("malformed_restore_record")
            _fail("unverified_backup")
        # frozen before the parser call; later phases read only these
        frozen_backup_id = receipt["backup_id"]
        frozen_state_id = receipt["state_id"]
        frozen_bundle = receipt["bundle"]
        saved = dict(receipt)
        try:
            parsed = self._parse(frozen_bundle)
        finally:
            receipt.clear()
            receipt.update(saved)
        state = {}
        for key, record in parsed.items():
            if type(key) is not str or type(record) is not dict:
                _fail("divergent_state")
            if _record_identity(record) != key:
                _fail("divergent_state")
            state[key] = dict(record)
        if state_id(state) != frozen_state_id:
            _fail("divergent_state")
        if _backup.serialize_bundle(state) != frozen_bundle:
            _fail("divergent_parse")
        return {"restore_id": derive_restore_id(frozen_backup_id,
                                                frozen_state_id),
                "backup_id": frozen_backup_id,
                "state_id": frozen_state_id,
                "state": state}
