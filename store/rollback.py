"""Production store rollback for data/contracts/rollback.yaml.

Verified truncation rollback of a WAL-logged store to a prior position.
The source log validates through the linked production WAL (store.wal);
the tail archiver is UNTRUSTED input behind the boundary (exactly one
call per rollback on a deep-detached tail copy, frozen log and request,
exact built-in str output in the pinned grammar, bound byte-exact to the
local canonical tail serialization). Tail removal commits last: a
rejected rollback leaves every input bit-identical.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

import yaml

from store import wal as _wal
from tools.rollback_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "rollback.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_TOKEN_RE = re.compile(_CC["identifiers"]["archive_token"]["grammar"])
_REQUEST_FIELDS = {"target_sequence"}
GENESIS = _wal.GENESIS

__all__ = ["FAILURE_MAPPING", "GENESIS", "RollbackEngine", "RollbackError",
           "archive_tail", "derive_rollback_id"]


class RollbackError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise RollbackError(failure_class, FAILURE_MAPPING[failure_class])


def archive_tail(tail):
    """The canonical tail archive token (and the honest archiver): sha256
    over the domain-separated, length-framed serialization of every
    exact WAL field of every tail entry, in order."""
    parts = ["arc1"]
    for entry in tail:
        record = entry["payload"]["record"]
        for field in (entry["sequence"], entry["op"], entry["entry_id"],
                      entry["prior_entry_id"], entry["payload"]["identity"],
                      record["variant"], record["digest"],
                      record["snapshot_fen"]):
            text = str(field)
            parts.append(f"{len(text)}:{text}")
    return "arc1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def derive_rollback_id(from_head, to_head, truncated_count, archive_token):
    return "rbk1:" + hashlib.sha256(
        f"{from_head}\n{to_head}\n{truncated_count}\n{archive_token}"
        .encode()).hexdigest()


def _freeze(log):
    """Detached plain copy of a validated log; derivation reads only it."""
    return [{"sequence": entry["sequence"], "op": entry["op"],
             "entry_id": entry["entry_id"],
             "prior_entry_id": entry["prior_entry_id"],
             "payload": {"identity": entry["payload"]["identity"],
                         "record": dict(entry["payload"]["record"])}}
            for entry in log]


class RollbackEngine:
    """Linked-WAL source validation, exact target resolution, frozen log
    and request, one untrusted archiver call bound to the local tail
    token, tail removal committed last."""

    def __init__(self, tail_archiver):
        self.archiver = tail_archiver  # UNTRUSTED
        self._wal = _wal.WalEngine(_wal.canonical_payload)  # linked, trusted

    def _archive(self, frozen_tail):
        """The archiver boundary: any BaseException, non-exact-str,
        wrong-grammar or non-UTF-8 output fails closed."""
        try:
            out = self.archiver(copy.deepcopy(frozen_tail))
        except BaseException:
            _fail("divergent_archive")
        if type(out) is not str or _TOKEN_RE.fullmatch(out) is None:
            _fail("divergent_archive")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_archive")
        return out

    def rollback(self, log, request):
        """Atomic: exact containers, exact-str request keys before the
        set compare, linked-WAL validation, target in [0, len(log)],
        snapshot + freeze before the single archiver call, token bound
        byte-exact to archive_tail(frozen tail), log and request
        restored on every exit, then delete exactly the tail."""
        if type(log) is not list:
            _fail("malformed_rollback_record")
        if type(request) is not dict or \
                not all(type(key) is str for key in dict.keys(request)) or \
                set(dict.keys(request)) != _REQUEST_FIELDS:
            _fail("malformed_rollback_record")
        target = request["target_sequence"]
        if type(target) is not int:
            _fail("malformed_rollback_record")
        try:
            self._wal.replay(log)
        except _wal.WalError:
            _fail("corrupt_source")
        if target < 0 or target > len(log):
            _fail("unknown_target")
        container, saved = _wal.snapshot(log)
        saved_req = dict(request)
        frozen = _freeze(log)
        try:
            frozen_tail = frozen[target:]
            token = self._archive(frozen_tail)
            if token != archive_tail(frozen_tail):
                _fail("divergent_archive")
        finally:
            _wal.restore(log, container, saved)
            request.clear()
            request.update(saved_req)
        from_head = frozen[-1]["entry_id"] if frozen else GENESIS
        to_head = frozen[target - 1]["entry_id"] if target else GENESIS
        count = len(frozen) - target
        del log[target:]
        return {"rollback_id": derive_rollback_id(from_head, to_head,
                                                  count, token),
                "from_head": from_head,
                "to_head": to_head,
                "truncated_count": count,
                "archive_token": token}
