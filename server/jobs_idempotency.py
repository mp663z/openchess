"""Production jobs idempotency engine for
data/contracts/jobs_idempotency.yaml (contract id `jobs-idempotency`).

Exactly-once application of jobs-queue transitions under caller-supplied
idempotency keys. `JobsIdempotencyEngine(fingerprinter).apply(state,
ledger, request)` validates the request first
(malformed_idempotency_request, the payload checked through the linked
queue contract), then the queue state (corrupt_queue), then the receipt
ledger bound to the live state (corrupt_ledger), calls the
caller-supplied fingerprinter exactly once on a DETACHED copy
(divergent_fingerprint on any BaseException, wrong shape or
request-divergent output), deduplicates key-then-fingerprint
(key_conflict), and commits the queue transition and its receipt
together LAST. A rejected call leaves the state, the ledger and the
request bit-identical: reference-preserving snapshots are taken before
the oracle call and restored in a finally, so even a fingerprinter that
rewrites the LIVE inputs through a captured closure - the detached
argument does not isolate an ambient closure - cannot corrupt them.
Queue rejections from the staged transition propagate UNWRAPPED with the
queue contract's own codes (unknown_job, lease_conflict, dedupe_conflict,
capacity_exceeded); the queue's malformed/corrupt failures never surface
here because this layer validates first.

Every check is total on hostile input: exact built-in types only, key
types are checked before any set or hash comparison, grammars by ASCII
fullmatch, receipt ids and state ids re-derived. Production never
imports tests.*.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from server.jobs_queue import MAX_NOW as _MAX_NOW
from server.jobs_queue import (
    QueueEngine,
    QueueError,
    _canonical,
    _check_request,
    _check_state,
    state_id_for,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "jobs_idempotency.yaml"
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = tuple(_CC["record"]["fields"])
_RECEIPT_RE = re.compile(_CC["identifiers"]["receipt_id"]["grammar"], re.ASCII)
_KEY_RE = re.compile(_CC["identifiers"]["idempotency_key"]["grammar"], re.ASCII)
_FP_RE = re.compile(_CC["identifiers"]["request_fingerprint"]["grammar"], re.ASCII)
_STATE_ID_RE = re.compile(_CC["identifiers"]["prior_state_id"]["grammar"], re.ASCII)
_REQUEST_KEYS = frozenset(_CC["request"]["fields"])
_FAILURE_MAPPING = dict(_CC["failures"]["mapping"])
_OPS = frozenset(("enqueue", "claim", "ack", "nack"))

__all__ = [
    "FAILURE_MAPPING",
    "IdempotencyError",
    "JobsIdempotencyEngine",
    "QueueError",
    "derive_receipt_id",
    "request_fingerprint",
]

FAILURE_MAPPING = dict(_FAILURE_MAPPING)

_QUEUE = QueueEngine()  # linked, trusted


class IdempotencyError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise IdempotencyError(failure_class, _FAILURE_MAPPING[failure_class])


def request_fingerprint(op, payload):
    """THE pinned canonical request fingerprint: sha256 over the
    domain-separated, length-framed serialization of the op and the
    canonical JSON of the whole payload, in order."""
    canon = _canonical(payload)
    text = f"jif1|{len(op)}:{op}|{len(canon)}:{canon}"
    return "jif1:" + hashlib.sha256(text.encode()).hexdigest()


def derive_receipt_id(key, fingerprint, prior_state_id, state_id, sequence):
    return (
        "jir1:"
        + hashlib.sha256(
            f"{key}\n{fingerprint}\n{prior_state_id}\n{state_id}\n{sequence}".encode()
        ).hexdigest()
    )


def _grammar(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _str_keyed(d):
    """Hostile-key guard for an exact built-in dict: every key must be
    an exact built-in str, checked BEFORE any set construction or key
    comparison; dict.keys is the unbound built-in."""
    return all(type(k) is str for k in dict.keys(d))


def _container_ids(root):
    """Identity set of every exact-dict/list container reachable from
    root. Runs only on already-validated exact, finite, acyclic JSON
    graphs, so it is total; the inputs stay alive for the whole walk,
    so no id() can be freed and reused while it runs."""
    seen = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if type(node) is dict:
            if id(node) in seen:
                continue
            seen.add(id(node))
            stack.extend(dict.values(node))
        elif type(node) is list:
            if id(node) in seen:
                continue
            seen.add(id(node))
            stack.extend(list.__iter__(node))
    return seen


def _snapshot(obj):
    """Reference-preserving deep snapshot of exact JSON values: keeps
    the identity of every container so a restore returns each input
    bit-identical AND identity-identical."""
    if type(obj) is dict:
        return (obj, {k: _snapshot(v) for k, v in dict.items(obj)})
    if type(obj) is list:
        return (obj, [_snapshot(v) for v in list.__iter__(obj)])
    return (obj, obj)


def _restore(snap):
    obj, content = snap
    if type(obj) is dict:
        obj.clear()
        for key, value in content.items():
            obj[key] = _restore(value)
        return obj
    if type(obj) is list:
        obj[:] = [_restore(v) for v in content]
        return obj
    return obj


class JobsIdempotencyEngine:
    """The contract's idempotent apply: exact request validation
    (payload through the linked queue contract), full queue-state
    validation, full ledger validation bound to the live state, frozen
    request/state/ledger, exactly one untrusted fingerprinter call on
    a detached copy, byte-exact fingerprint binding, key-then-
    fingerprint deduplication, queue transition and receipt committed
    together LAST."""

    def __init__(self, request_fingerprinter):
        self.fingerprinter = request_fingerprinter  # UNTRUSTED

    def _fingerprint(self, frozen_req):
        """THE fingerprinter boundary: raising ANY BaseException
        (including KeyboardInterrupt/SystemExit/GeneratorExit),
        non-exact-str, UTF-8-inencodable, wrong-grammar or
        request-divergent output fails closed as
        divergent_fingerprint."""
        try:
            # DETACHED argument copy: the fingerprinter never sees the
            # frozen snapshot objects - argument mutation is inert
            out = self.fingerprinter(
                frozen_req["op"], json.loads(_canonical(frozen_req["payload"]))
            )
        except BaseException:
            # fail closed against the FULL BaseException surface
            # (totality): no raw escape from the untrusted oracle
            _fail("divergent_fingerprint")
        if type(out) is not str or _FP_RE.fullmatch(out) is None:
            _fail("divergent_fingerprint")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_fingerprint")
        # REQUEST-BOUND: byte-exact equality with the LOCAL canonical
        # derivation over the frozen request
        if out != request_fingerprint(frozen_req["op"], frozen_req["payload"]):
            _fail("divergent_fingerprint")
        return out

    @staticmethod
    def _validate_request(request):
        """malformed_idempotency_request on ANY shape, type, grammar
        or bound violation; the payload is validated through the
        LINKED queue contract. Returns the FROZEN detached request."""
        if (
            type(request) is not dict
            or not _str_keyed(request)
            or set(dict.keys(request)) != _REQUEST_KEYS
        ):
            _fail("malformed_idempotency_request")
        if not _grammar(request["idempotency_key"], _KEY_RE):
            _fail("malformed_idempotency_request")
        op = request["op"]
        if type(op) is not str or op not in _OPS:
            _fail("malformed_idempotency_request")
        payload = request["payload"]
        if type(payload) is not dict or not _str_keyed(payload) or "op" in payload:
            _fail("malformed_idempotency_request")
        try:
            frozen_q = _check_request(dict(payload, op=op), _MAX_NOW)
        except QueueError:
            _fail("malformed_idempotency_request")
        return {
            "idempotency_key": request["idempotency_key"],
            "op": op,
            "payload": {k: v for k, v in frozen_q.items() if k != "op"},
        }

    @staticmethod
    def _validate_ledger(ledger, live_state_id):
        """Everything decidable WITHOUT the oracle: exact receipt
        shape, grammars, receipt-id re-derivation, unique keys,
        contiguous sequences from zero, prior/state chaining, and the
        last receipt naming the LIVE state."""
        if type(ledger) is not list:
            _fail("corrupt_ledger")
        seen_keys = set()
        prev_state = None
        for index, receipt in enumerate(ledger):
            if (
                type(receipt) is not dict
                or not _str_keyed(receipt)
                or set(dict.keys(receipt)) != set(_FIELDS)
            ):
                _fail("corrupt_ledger")
            key = receipt["idempotency_key"]
            fp = receipt["request_fingerprint"]
            prior = receipt["prior_state_id"]
            sid = receipt["state_id"]
            seq = receipt["sequence"]
            rid = receipt["receipt_id"]
            if (
                not _grammar(key, _KEY_RE)
                or not _grammar(fp, _FP_RE)
                or not _grammar(prior, _STATE_ID_RE)
                or not _grammar(sid, _STATE_ID_RE)
                or not _grammar(rid, _RECEIPT_RE)
            ):
                _fail("corrupt_ledger")
            if type(seq) is not int or seq != index:
                _fail("corrupt_ledger")
            if key in seen_keys:
                _fail("corrupt_ledger")
            if prev_state is not None and prior != prev_state:
                _fail("corrupt_ledger")
            if rid != derive_receipt_id(key, fp, prior, sid, seq):
                _fail("corrupt_ledger")
            seen_keys.add(key)
            prev_state = sid
        if prev_state is not None and prev_state != live_state_id:
            _fail("corrupt_ledger")

    def apply(self, state, ledger, request):
        """ATOMIC: validate the request, the queue state and the
        ledger; snapshot all three; one fingerprinter call; dedupe by
        key then byte-exact fingerprint; stage the transition on a
        detached copy; commit state and receipt together LAST -
        rejection leaves every input bit-identical."""
        frozen_req = self._validate_request(request)
        try:
            _check_state(state)
        except QueueError:
            _fail("corrupt_queue")
        live_state_id = state_id_for(state)
        self._validate_ledger(ledger, live_state_id)
        # GRAPH DISJOINTNESS (after validation, before the oracle):
        # the request must be a self-contained graph - a container
        # shared with the state or the ledger would let the commit
        # rewrite the request through the alias, after the restore.
        req_ids = _container_ids(request)
        if req_ids & _container_ids(state) or req_ids & _container_ids(ledger):
            _fail("malformed_idempotency_request")
        # INPUT PRESERVATION snapshots (reference-preserving) +
        # FREEZE, all BEFORE the fingerprinter call.
        snaps = (_snapshot(state), _snapshot(ledger), _snapshot(request))
        frozen_state = json.loads(_canonical(state))
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
                # the transition stages on the FROZEN state, never on
                # the live state the oracle may have rewritten mid-call
                work = json.loads(_canonical(frozen_state))
                queue_request = json.loads(
                    _canonical(dict(frozen_req["payload"], op=frozen_req["op"]))
                )
                # the queue's own typed rejection propagates with NO
                # receipt and NO transition (finally restores)
                _QUEUE.apply(work, queue_request)
                new_state_id = state_id_for(work)
                sequence = len(frozen_ledger)
                receipt = {
                    "receipt_id": derive_receipt_id(
                        frozen_req["idempotency_key"], token, live_state_id, new_state_id, sequence
                    ),
                    "idempotency_key": frozen_req["idempotency_key"],
                    "request_fingerprint": token,
                    "prior_state_id": live_state_id,
                    "state_id": new_state_id,
                    "sequence": sequence,
                }
                outcome, staged = "applied", work
        finally:
            for snap in snaps:
                _restore(snap)
        if staged is not None:
            # COMMIT LAST: transition and receipt together
            state.clear()
            state.update(staged)
            ledger.append(dict(receipt))
        return {"outcome": outcome, "receipt": dict(receipt)}
