"""Production store backup for data/contracts/backup.yaml.

Content-addressed backup receipts over a WAL-logged canonical graph
state. The source log validates and replays through the linked
production WAL (store.wal); the bundle serializer is UNTRUSTED input
behind the boundary (exactly one call per backup, frozen log and state
snapshots, exact built-in str UTF-8 output); verification is LOCAL (no
oracle calls). A rejected backup or verify leaves every input
bit-identical.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from graph.diff import state_id
from store import wal as _wal
from tools.backup_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "backup.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = tuple(_CC["record"]["fields"])
_BACKUP_RE = re.compile(_CC["identifiers"]["backup_id"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["head"]["grammar"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
# inclusive int64 domain ceiling, checked before any int->str conversion
_COUNT_MAX = _CC["record"]["field_definitions"]["entry_count"]["max_value"]
GENESIS = _wal.GENESIS
EMPTY_STATE_ID = state_id({})

__all__ = ["EMPTY_STATE_ID", "FAILURE_MAPPING", "GENESIS", "BackupEngine", "BackupError",
           "derive_backup_id", "serialize_bundle"]


class BackupError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise BackupError(failure_class, FAILURE_MAPPING[failure_class])


def _exact_str_keys(mapping):
    """Key-type guard before any set/hash comparison on a caller dict."""
    return all(type(key) is str for key in dict.keys(mapping))


def serialize_bundle(state):
    """The honest bundle serializer: one canonical string over the
    sorted identity -> exact record map."""
    parts = []
    for key in sorted(state):
        record = state[key]
        body = "|".join(f"{field}={record[field]}" for field in sorted(record))
        parts.append(f"{key}\n{body}\n")
    return "".join(parts)


def _derive(head, sid, count, bundle, on_unencodable):
    """The canonical backup-id encoding shared by backup and verify:
    every string field is UTF-8 checked before the join."""
    for value in (head, sid, bundle):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            _fail(on_unencodable)
    return "bck1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{bundle}".encode()).hexdigest()


def derive_backup_id(head, sid, entry_count, bundle):
    """Public canonical backup-id derivation (sha256 over head, state id,
    entry count and bundle, newline-joined)."""
    return _derive(head, sid, entry_count, bundle, "malformed_backup_record")


class BackupEngine:
    """Linked-WAL validation and replay of the source, frozen detached
    state snapshot, untrusted serializer (exactly one call), derived
    content-addressed receipt, local total verify."""

    def __init__(self, bundle_serializer):
        self.serializer = bundle_serializer  # UNTRUSTED
        self._wal = _wal.WalEngine(_wal.canonical_payload)  # linked, trusted

    def _serialize(self, state):
        try:
            out = self.serializer(state)
        except BaseException:
            _fail("divergent_snapshot")
        if type(out) is not str:
            _fail("divergent_snapshot")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_snapshot")
        return out

    def backup(self, log):
        """Atomic: validate and replay the source through store.wal (any
        WAL rejection is corrupt_source), freeze the state before the
        single serializer call, restore the log on every exit."""
        if type(log) is not list:
            _fail("malformed_backup_record")
        try:
            replayed = self._wal.replay(log)
        except _wal.WalError:
            _fail("corrupt_source")
        container, saved = _wal.snapshot(log)
        frozen = {key: dict(rec) for key, rec in replayed["state"].items()}
        try:
            bundle = self._serialize(
                {key: dict(rec) for key, rec in frozen.items()})
        finally:
            _wal.restore(log, container, saved)
        return {
            "backup_id": _derive(replayed["head"], replayed["state_id"],
                                 replayed["applied"], bundle,
                                 "divergent_snapshot"),
            "head": replayed["head"],
            "state_id": replayed["state_id"],
            "entry_count": replayed["applied"],
            "bundle": bundle,
        }

    def verify(self, receipt):
        """Local and total: exact shape and types, pinned grammars,
        bounded count, head/count consistency, then the recomputed
        backup id must equal the stored one. No oracle calls."""
        if type(receipt) is not dict or not _exact_str_keys(receipt) or \
                set(dict.keys(receipt)) != set(_FIELDS):
            _fail("malformed_backup_record")
        for field, grammar in (("backup_id", _BACKUP_RE), ("head", _HEAD_RE),
                               ("state_id", _STATE_RE)):
            if type(receipt[field]) is not str or \
                    grammar.fullmatch(receipt[field]) is None:
                _fail("malformed_backup_record")
        count = receipt["entry_count"]
        if type(count) is not int or not 0 <= count <= _COUNT_MAX:
            _fail("malformed_backup_record")
        if type(receipt["bundle"]) is not str:
            _fail("malformed_backup_record")
        if count == 0 and (receipt["head"] != GENESIS or
                           receipt["state_id"] != EMPTY_STATE_ID):
            _fail("divergent_backup")
        if count > 0 and receipt["head"] == GENESIS:
            _fail("divergent_backup")
        if _derive(receipt["head"], receipt["state_id"], count,
                   receipt["bundle"], "malformed_backup_record") != \
                receipt["backup_id"]:
            _fail("divergent_backup")
        return {field: receipt[field] for field in _FIELDS}
