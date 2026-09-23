"""Production store export for data/contracts/export.yaml.

Canonical read-only export of a WAL-logged graph state. The source log
validates and replays through the linked production WAL (store.wal); the
document exporter is UNTRUSTED input behind the boundary (exactly one
call per export on a detached by-value state copy, frozen log, state and
request, exact built-in str UTF-8 output bound byte-for-byte to the
local canonical rendering). Every export leaves log and request
bit-identical.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from store import wal as _wal
from tools.export_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "export.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQUEST_FIELDS = frozenset(_CC["request"]["fields"])
FORMATS = tuple(_CC["request"]["formats"])

__all__ = ["FAILURE_MAPPING", "FORMATS", "ExportEngine", "ExportError",
           "derive_export_id", "render_document"]


class ExportError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise ExportError(failure_class, FAILURE_MAPPING[failure_class])


def render_document(state, fmt):
    """The canonical rendering (and the honest exporter): one line per
    live record sorted by identity, each canonical JSON (sorted keys,
    compact separators) of {"identity": ..., "record": {...}}."""
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format: {fmt!r}")
    return "".join(
        json.dumps({"identity": key, "record": dict(state[key])},
                   sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False) + "\n"
        for key in sorted(state))


def derive_export_id(head, sid, record_count, fmt, document):
    """Public export-id derivation: sha256 over head, state id, record
    count, format and document, newline-joined."""
    return "exp1:" + hashlib.sha256(
        f"{head}\n{sid}\n{record_count}\n{fmt}\n{document}"
        .encode()).hexdigest()


class ExportEngine:
    """Exact request, closed format list, linked-WAL validation and
    replay, frozen snapshots, exactly one untrusted exporter call bound
    to the local canonical rendering; read-only."""

    def __init__(self, exporter):
        self.exporter = exporter  # UNTRUSTED
        self._wal = _wal.WalEngine(_wal.canonical_payload)  # linked, trusted

    def _export(self, frozen_state, fmt):
        try:
            out = self.exporter(
                {key: dict(rec) for key, rec in frozen_state.items()}, fmt)
        except BaseException:
            _fail("divergent_export")
        if type(out) is not str:
            _fail("divergent_export")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_export")
        return out

    def export(self, log, request):
        if type(log) is not list or type(request) is not dict:
            _fail("malformed_export_request")
        # key-type guard before any set/hash comparison
        if not all(type(key) is str for key in dict.keys(request)) or \
                set(dict.keys(request)) != _REQUEST_FIELDS:
            _fail("malformed_export_request")
        fmt = request["format"]
        if type(fmt) is not str:
            _fail("malformed_export_request")
        if fmt not in FORMATS:
            _fail("unsupported_format")
        try:
            replayed = self._wal.replay(log)
        except _wal.WalError:
            _fail("corrupt_source")
        container, saved = _wal.snapshot(log)
        saved_request = dict(request)
        frozen_state = {key: dict(rec)
                        for key, rec in replayed["state"].items()}
        head, sid = replayed["head"], replayed["state_id"]
        try:
            document = self._export(frozen_state, fmt)
            if document != render_document(frozen_state, fmt):
                _fail("divergent_export")
        finally:
            _wal.restore(log, container, saved)
            request.clear()
            request.update(saved_request)
        count = len(frozen_state)
        return {"export_id": derive_export_id(head, sid, count, fmt,
                                              document),
                "head": head,
                "state_id": sid,
                "record_count": count,
                "format": fmt,
                "document": document}
