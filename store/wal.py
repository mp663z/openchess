"""Production write-ahead log for data/contracts/wal.yaml.

A single log of content-addressed graph-state mutations (put/delete of
exact node records). Entry payloads validate through the shipped
graph.node runtime; the payload canonicalizer is UNTRUSTED input behind
the boundary (exactly one call per entry per operation, frozen log and
request snapshots, exact built-in str output). append is atomic with a
staged commit; a rejected append or replay leaves every input
bit-identical, and so does a successful one apart from the appended
entry.
"""

from __future__ import annotations

import functools
import hashlib
import re
from pathlib import Path

import yaml

from graph.diff import state_id
from graph.node import make_record, record_identity
from graph.position_digest import DigestError
from tools.variant_runtime import VariantError
from tools.wal_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "wal.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = tuple(_CC["record"]["fields"])
_ENTRY_RE = re.compile(_CC["identifiers"]["entry_id"]["grammar"])
_PRIOR_RE = re.compile(_CC["identifiers"]["prior_entry_id"]["grammar"])
_OPS = {spec["op"]: tuple(spec["payload_fields"])
        for spec in _CC["registry"]["operations"]}
_RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
GENESIS = "wal0:" + "0" * 64

__all__ = ["FAILURE_MAPPING", "GENESIS", "WalEngine", "WalError",
           "canonical_payload", "restore", "snapshot"]


class WalError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise WalError(failure_class, FAILURE_MAPPING[failure_class])


def _exact_str_keys(mapping):
    """Key-type guard, run BEFORE any set/hash comparison on a caller
    dict: a key whose __hash__ collides with a field name and whose
    __eq__ raises (or a str subclass) fails closed typed. dict.keys is
    called unbound so a subclass cannot intercept it."""
    return all(type(key) is str for key in dict.keys(mapping))


def canonical_payload(identity, record):
    """The honest payload canonicalizer: the entry-id derivation input."""
    return (f"{identity}\n{record['variant']}\n"
            f"{record['digest']}\n{record['snapshot_fen']}")


def _derive_entry_id(sequence, op, canonical, prior):
    return "wal1:" + hashlib.sha256(
        f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


# memo bounds: longer inputs take the uncached path so the cache never
# retains unbounded caller-controlled bytes
_MEMO_MAX_FEN = 256
_MEMO_MAX_VARIANT = 64
# exactly the rejections the linked runtime uses for bad caller input;
# anything else (MemoryError, a TypeError bug, ...) propagates
_LINKED_REJECTIONS = (VariantError, DigestError, ValueError)


def _derive_linked(variant, snapshot_fen):
    """(exact record items, identity) from the shipped graph.node runtime.
    Raises whatever the linked runtime raises."""
    record = make_record(variant, snapshot_fen)
    return tuple(record.items()), record_identity(record)


# caches SUCCESSES only: lru_cache never stores a raised exception
_linked = functools.lru_cache(maxsize=4096)(_derive_linked)


def _validate_record(record):
    """Exact node record through the shipped graph.node runtime; returns
    the canonical identity string."""
    if type(record) is not dict or not _exact_str_keys(record) or \
            set(dict.keys(record)) != set(_RECORD_FIELDS):
        _fail("malformed_wal_entry")
    if any(type(record[field]) is not str for field in _RECORD_FIELDS):
        _fail("malformed_wal_entry")
    variant, fen = record["variant"], record["snapshot_fen"]
    derive = _derive_linked if (len(fen) > _MEMO_MAX_FEN or
                                len(variant) > _MEMO_MAX_VARIANT) else _linked
    try:
        derived, identity = derive(variant, fen)
    except _LINKED_REJECTIONS:
        _fail("malformed_wal_entry")
    # exact equality: a well-formed but wrong digest is rejected too
    if dict(record) != dict(derived):
        _fail("malformed_wal_entry")
    return identity


def _validate_payload(op, payload):
    if type(payload) is not dict or not _exact_str_keys(payload) or \
            set(dict.keys(payload)) != set(_OPS[op]):
        _fail("malformed_wal_entry")
    identity = payload["identity"]
    if type(identity) is not str:
        _fail("malformed_wal_entry")
    if _validate_record(payload["record"]) != identity:
        _fail("malformed_wal_entry")


def _validate_entry(entry, position, prior_tip):
    """Everything decidable without the oracle: shape, registered op,
    exact 1-based sequence, id grammars, prior link, payload."""
    if type(entry) is not dict or not _exact_str_keys(entry) or \
            set(dict.keys(entry)) != set(_FIELDS):
        _fail("malformed_wal_entry")
    op = entry["op"]
    if type(op) is not str:
        _fail("malformed_wal_entry")
    if op not in _OPS:
        _fail("unknown_operation")
    sequence = entry["sequence"]
    if type(sequence) is not int:
        _fail("malformed_wal_entry")
    if sequence != position:
        _fail("sequence_conflict")
    for field, grammar in (("entry_id", _ENTRY_RE),
                           ("prior_entry_id", _PRIOR_RE)):
        if type(entry[field]) is not str or \
                grammar.fullmatch(entry[field]) is None:
            _fail("malformed_wal_entry")
    if entry["prior_entry_id"] != prior_tip:
        _fail("corrupt_chain")
    _validate_payload(op, entry["payload"])


def _validate_log(log):
    tip = GENESIS
    for position, entry in enumerate(log, start=1):
        _validate_entry(entry, position, tip)
        tip = entry["entry_id"]


def snapshot(log):
    """Reference-preserving snapshot of a structurally validated log.
    Untrusted code may hold external references into the caller's log,
    so pair this with restore() on every exit. Returns (container,
    saved) for restore()."""
    saved = []
    for entry in log:
        payload = entry["payload"]
        record = payload["record"]
        saved.append((entry, dict(entry), payload, dict(payload),
                      record, dict(record)))
    return list(log), saved


def restore(log, container, saved):
    """Put a log back exactly as snapshot() found it: same list contents,
    same entry / payload / record objects, same values."""
    log[:] = container
    for entry, e_copy, payload, p_copy, record, r_copy in saved:
        record.clear()
        record.update(r_copy)
        payload.clear()
        payload.update(p_copy)
        entry.clear()
        entry.update(e_copy)


def _freeze_entry(entry):
    return {"entry_id": entry["entry_id"],
            "sequence": entry["sequence"],
            "op": entry["op"],
            "payload": {"identity": entry["payload"]["identity"],
                        "record": dict(entry["payload"]["record"])},
            "prior_entry_id": entry["prior_entry_id"]}


class WalEngine:
    """Total validation, untrusted canonicalizer behind the boundary,
    content-addressed chained entries, atomic staged commit and a
    deterministic replay fold."""

    def __init__(self, payload_canonicalizer):
        self.canonicalizer = payload_canonicalizer  # UNTRUSTED

    def _canonicalize(self, identity, record):
        """The oracle boundary: any BaseException, non-exact-str output
        or a non-UTF-8-encodable string fails closed."""
        try:
            out = self.canonicalizer(identity, dict(record))
        except BaseException:
            _fail("divergent_canonicalization")
        if type(out) is not str:
            _fail("divergent_canonicalization")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_canonicalization")
        return out

    def _rederive(self, frozen):
        """One oracle call per entry; a divergent id is corrupt_chain."""
        tip = GENESIS
        for entry in frozen:
            canonical = self._canonicalize(entry["payload"]["identity"],
                                           entry["payload"]["record"])
            if _derive_entry_id(entry["sequence"], entry["op"], canonical,
                                entry["prior_entry_id"]) != \
                    entry["entry_id"]:
                _fail("corrupt_chain")
            tip = entry["entry_id"]
        return tip

    def append(self, log, request):
        """Validate and freeze the request, validate the whole log, then
        re-derive the chain and stage the new entry behind the oracle
        boundary; commit last. Rejection leaves log and request
        bit-identical."""
        if type(log) is not list:
            _fail("malformed_wal_entry")
        if type(request) is not dict or not _exact_str_keys(request) or \
                set(dict.keys(request)) != {"op", "payload"}:
            _fail("malformed_wal_entry")
        op = request["op"]
        if type(op) is not str:
            _fail("malformed_wal_entry")
        if op not in _OPS:
            _fail("unknown_operation")
        _validate_payload(op, request["payload"])
        frozen_req = {"op": op,
                      "payload": {
                          "identity": request["payload"]["identity"],
                          "record": dict(request["payload"]["record"])}}
        _validate_log(log)
        container, saved = snapshot(log)
        payload = request["payload"]
        record = payload["record"]
        saved_req = (dict(request), dict(payload), dict(record))
        frozen = [_freeze_entry(entry) for entry in log]
        try:
            tip = self._rederive(frozen)
            canonical = self._canonicalize(
                frozen_req["payload"]["identity"],
                frozen_req["payload"]["record"])
            sequence = len(frozen) + 1
            staged = {
                "entry_id": _derive_entry_id(sequence, op, canonical, tip),
                "sequence": sequence,
                "op": op,
                "payload": {
                    "identity": frozen_req["payload"]["identity"],
                    "record": dict(frozen_req["payload"]["record"])},
                "prior_entry_id": tip,
            }
        finally:
            restore(log, container, saved)
            req_copy, p_copy, r_copy = saved_req
            record.clear()
            record.update(r_copy)
            payload.clear()
            payload.update(p_copy)
            request.clear()
            request.update(req_copy)
        log.append(staged)
        return _freeze_entry(staged)

    def replay(self, log):
        """Validate the whole log, re-derive the chain behind the oracle
        boundary and fold the registered ops over the empty state. The
        log is never mutated."""
        if type(log) is not list:
            _fail("malformed_wal_entry")
        _validate_log(log)
        container, saved = snapshot(log)
        frozen = [_freeze_entry(entry) for entry in log]
        try:
            tip = self._rederive(frozen)
            state = {}
            for entry in frozen:
                identity = entry["payload"]["identity"]
                if entry["op"] == "put":
                    state[identity] = dict(entry["payload"]["record"])
                else:
                    state.pop(identity, None)
            result = {"state": state, "state_id": state_id(state),
                      "head": tip, "applied": len(frozen)}
        finally:
            restore(log, container, saved)
        return result
