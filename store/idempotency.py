"""Production store idempotency for data/contracts/idempotency.yaml.

Keyed exactly-once apply over a WAL-logged store. The request payload,
the source log and the staged append all go through the linked
production WAL (store.wal, public surface only); the ledger is validated
in full and bound to the live log; the request fingerprinter is
UNTRUSTED input behind the boundary (exactly one call per apply on a
detached copy of the frozen request, exact built-in str output in the
pinned grammar, bound byte-exact to the local request derivation).
Deduplication is key-then-fingerprint; the WAL entry and receipt commit
together last. A rejected apply leaves log, ledger and request
bit-identical.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

import yaml

from store import wal as _wal
from tools.idempotency_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "idempotency.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = frozenset(_CC["record"]["fields"])
_IDS = _CC["identifiers"]
_RECEIPT_RE = re.compile(_IDS["receipt_id"]["grammar"])
_KEY_RE = re.compile(_IDS["idempotency_key"]["grammar"])
_FP_RE = re.compile(_IDS["request_fingerprint"]["grammar"])
_ENTRY_RE = re.compile(_IDS["entry_id"]["grammar"])
_REQUEST_KEYS = frozenset({"idempotency_key", "op", "payload"})

__all__ = ["FAILURE_MAPPING", "IdempotencyEngine", "IdempotencyError",
           "derive_receipt_id", "request_fingerprint"]


class IdempotencyError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise IdempotencyError(failure_class, FAILURE_MAPPING[failure_class])


def request_fingerprint(op, payload):
    """The canonical request fingerprint (and the honest fingerprinter):
    sha256 over the domain-separated, length-framed serialization of
    op, identity and every node-record field, in order."""
    record = payload["record"]
    parts = ["idf1"]
    for field in (op, payload["identity"], record["variant"],
                  record["digest"], record["snapshot_fen"]):
        text = str(field)
        parts.append(f"{len(text)}:{text}")
    return "idf1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def derive_receipt_id(key, fingerprint, entry_id, sequence):
    return "idr1:" + hashlib.sha256(
        f"{key}\n{fingerprint}\n{entry_id}\n{sequence}".encode()).hexdigest()


def _str_keyed(mapping):
    """Key-type guard before any set/hash comparison on a caller dict."""
    return all(type(key) is str for key in dict.keys(mapping))


def _payload_str_keyed(payload):
    if type(payload) is not dict:
        return True
    if not _str_keyed(payload):
        return False
    record = payload.get("record")
    return type(record) is not dict or _str_keyed(record)


def _log_str_keyed(log):
    if type(log) is not list:
        return True
    for entry in log:
        if type(entry) is not dict:
            continue
        if not _str_keyed(entry) or \
                not _payload_str_keyed(entry.get("payload")):
            return False
    return True


def _valid_key(key):
    return type(key) is str and _KEY_RE.fullmatch(key) is not None


def _freeze(log):
    """Detached plain copy of a validated log."""
    return [{"entry_id": entry["entry_id"], "sequence": entry["sequence"],
             "op": entry["op"],
             "payload": {"identity": entry["payload"]["identity"],
                         "record": dict(entry["payload"]["record"])},
             "prior_entry_id": entry["prior_entry_id"]}
            for entry in log]


class IdempotencyEngine:
    """Exact request validation (payload through the linked WAL), full
    source and ledger validation, frozen inputs, one untrusted
    fingerprinter call bound to the local derivation, key-then-
    fingerprint deduplication, entry and receipt committed last."""

    def __init__(self, request_fingerprinter):
        self.fingerprinter = request_fingerprinter  # UNTRUSTED
        self._wal = _wal.WalEngine(_wal.canonical_payload)  # linked, trusted

    def _fingerprint(self, frozen_req):
        payload = frozen_req["payload"]
        try:
            out = self.fingerprinter(
                frozen_req["op"],
                {"identity": payload["identity"],
                 "record": dict(payload["record"])})
        except BaseException:
            _fail("divergent_fingerprint")
        if type(out) is not str or _FP_RE.fullmatch(out) is None:
            _fail("divergent_fingerprint")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_fingerprint")
        if out != request_fingerprint(frozen_req["op"], payload):
            _fail("divergent_fingerprint")
        return out

    def _validate_request(self, request):
        if type(request) is not dict or not _str_keyed(request) or \
                set(dict.keys(request)) != _REQUEST_KEYS:
            _fail("malformed_idempotency_request")
        if not _valid_key(request["idempotency_key"]):
            _fail("malformed_idempotency_request")
        if type(request["op"]) is not str or \
                not _payload_str_keyed(request["payload"]):
            _fail("malformed_idempotency_request")
        # op registration and payload validation through the linked WAL's
        # public append on an empty scratch log (the trusted canonicalizer
        # cannot fail, so any rejection is the request's; the WAL restores
        # the payload on every exit)
        try:
            self._wal.append([], {"op": request["op"],
                                  "payload": request["payload"]})
        except _wal.WalError:
            _fail("malformed_idempotency_request")

    @staticmethod
    def _validate_ledger(ledger, log):
        if type(ledger) is not list:
            _fail("corrupt_ledger")
        seen_keys = set()
        last_seq = 0
        for receipt in ledger:
            if type(receipt) is not dict or not _str_keyed(receipt) or \
                    set(dict.keys(receipt)) != _FIELDS:
                _fail("corrupt_ledger")
            key = receipt["idempotency_key"]
            fp = receipt["request_fingerprint"]
            entry_id = receipt["entry_id"]
            seq = receipt["sequence"]
            rid = receipt["receipt_id"]
            if not _valid_key(key):
                _fail("corrupt_ledger")
            for value, grammar in ((fp, _FP_RE), (entry_id, _ENTRY_RE),
                                   (rid, _RECEIPT_RE)):
                if type(value) is not str or grammar.fullmatch(value) is None:
                    _fail("corrupt_ledger")
            if type(seq) is not int or seq <= last_seq or seq > len(log):
                _fail("corrupt_ledger")
            if key in seen_keys:
                _fail("corrupt_ledger")
            if rid != derive_receipt_id(key, fp, entry_id, seq):
                _fail("corrupt_ledger")
            entry = log[seq - 1]
            if entry["entry_id"] != entry_id or \
                    request_fingerprint(entry["op"], entry["payload"]) != fp:
                _fail("corrupt_ledger")
            seen_keys.add(key)
            last_seq = seq

    def apply(self, log, ledger, request):
        self._validate_request(request)
        if not _log_str_keyed(log):
            _fail("corrupt_source")
        try:
            self._wal.replay(log)
        except _wal.WalError:
            _fail("corrupt_source")
        self._validate_ledger(ledger, log)
        container, saved = _wal.snapshot(log)
        saved_ledger = (list(ledger), [(r, dict(r)) for r in ledger])
        payload = request["payload"]
        saved_req = (request, dict(request), payload, dict(payload),
                     payload["record"], dict(payload["record"]))
        frozen_req = {"idempotency_key": request["idempotency_key"],
                      "op": request["op"],
                      "payload": {"identity": payload["identity"],
                                  "record": dict(payload["record"])}}
        frozen_log = _freeze(log)
        frozen_ledger = [dict(r) for r in ledger]
        try:
            token = self._fingerprint(frozen_req)
            stored = None
            for receipt in frozen_ledger:
                if receipt["idempotency_key"] == frozen_req["idempotency_key"]:
                    stored = receipt
            if stored is not None:
                if stored["request_fingerprint"] != token:
                    _fail("key_conflict")
                outcome, receipt, staged = "replayed", dict(stored), None
            else:
                staged = self._wal.append(
                    _freeze(frozen_log),
                    {"op": frozen_req["op"],
                     "payload": copy.deepcopy(frozen_req["payload"])})
                receipt = {
                    "receipt_id": derive_receipt_id(
                        frozen_req["idempotency_key"], token,
                        staged["entry_id"], staged["sequence"]),
                    "idempotency_key": frozen_req["idempotency_key"],
                    "request_fingerprint": token,
                    "entry_id": staged["entry_id"],
                    "sequence": staged["sequence"]}
                outcome = "applied"
        finally:
            _wal.restore(log, container, saved)
            ledger_container, receipts = saved_ledger
            for obj, snap in receipts:
                obj.clear()
                obj.update(snap)
            ledger[:] = ledger_container
            req, req_copy, pay, pay_copy, rec, rec_copy = saved_req
            rec.clear()
            rec.update(rec_copy)
            pay.clear()
            pay.update(pay_copy)
            req.clear()
            req.update(req_copy)
        if staged is not None:
            log.append(staged)
            ledger.append(dict(receipt))
        return {"outcome": outcome, "receipt": dict(receipt)}
