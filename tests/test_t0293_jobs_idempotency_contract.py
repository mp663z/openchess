#!/usr/bin/env python3
"""Reference battery for data/contracts/jobs_idempotency_contract.yaml
(T0293): exactly-once application of queue-contract transitions
under caller-supplied idempotency keys.

The IdempotencyEngine below is THE pinned reference
implementation of the contract. It links the queue contract's
machinery (imported, never restated): the queue owns request,
state and transition validity; this layer owns the receipt
ledger, the fingerprint binding and the deduplication order.
The tests probe the full boundary: happy path, key conflict,
grammar boundaries, hostile requests, corrupt queue state,
corrupt ledgers, hostile fingerprinters, input preservation,
rollback interplay, behavioral mutants and lint mutants."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0284_queue_contract import (  # noqa: E402
    _OPS,
    MAX_NOW,
    QueueEngine,
    QueueError,
    _canon,
    _validate_state,
    empty_state,
    job_id_for,
    state_id_for,
)
from tests.test_t0284_queue_contract import (  # noqa: E402
    _validate_request as _validate_queue_request,
)
from tools.jobs_idempotency_contract_lint import (  # noqa: E402
    CONTRACT,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_OUTCOMES = _CC["outcomes"]["values"]
_RECEIPT_RE = re.compile(
    _CC["identifiers"]["receipt_id"]["grammar"], re.ASCII)
_KEY_RE = re.compile(
    _CC["identifiers"]["idempotency_key"]["grammar"], re.ASCII)
_FP_RE = re.compile(
    _CC["identifiers"]["request_fingerprint"]["grammar"], re.ASCII)
_STATE_ID_RE = re.compile(
    _CC["identifiers"]["prior_state_id"]["grammar"], re.ASCII)
_REQUEST_KEYS = {"idempotency_key", "op", "payload"}
_FAILURE_MAPPING = dict(_CC["failures"]["mapping"])
_ERROR_ENUM = set(_CC["errors"]["closed_enum"])

_QUEUE = QueueEngine()  # linked, trusted


class IdempotencyError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise IdempotencyError(cls, _FAILURE_MAPPING[cls])


def request_fingerprint(op, payload):
    """THE pinned canonical request fingerprint: sha256 over the
    DOMAIN-SEPARATED, LENGTH-FRAMED serialization of EVERY exact
    frozen request field except the key - the op and the canonical
    JSON of the whole payload, in order. The engine derives this
    LOCALLY and binds the untrusted fingerprinter's output to it
    byte-for-byte."""
    canon = _canon(payload)
    text = f"jif1|{len(op)}:{op}|{len(canon)}:{canon}"
    return "jif1:" + hashlib.sha256(text.encode()).hexdigest()


def derive_receipt_id(key, fingerprint, prior_state_id, state_id,
                      sequence):
    return "jir1:" + hashlib.sha256(
        f"{key}\n{fingerprint}\n{prior_state_id}\n{state_id}\n"
        f"{sequence}".encode()).hexdigest()


def _grammar(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _str_keyed(d):
    """HOSTILE-KEY GUARD for an exact built-in dict: every key must
    be an exact built-in str. Runs BEFORE any set construction or
    key comparison, so a key object whose __hash__ collides with a
    real field name and whose __eq__ raises is rejected typed and
    never compared. dict.keys is the unbound built-in: no
    caller-overridable method runs."""
    return all(type(k) is str for k in dict.keys(d))


def _snapshot(obj):
    """Reference-preserving deep snapshot of exact JSON values:
    keeps the identity of every container so a restore returns
    each input bit-identical AND identity-identical."""
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
    """The contract's pinned idempotent apply: exact request
    validation (payload through the linked queue contract), full
    queue-state validation, full ledger validation bound to the
    live state, frozen request/state/ledger, exactly one
    untrusted fingerprinter call on a DETACHED copy, byte-exact
    fingerprint binding, key-then-fingerprint deduplication,
    queue transition and receipt committed together LAST."""

    def __init__(self, request_fingerprinter):
        self.fingerprinter = request_fingerprinter  # UNTRUSTED

    def _fingerprint(self, frozen_req):
        """THE fingerprinter boundary: raising ANY BaseException
        (including KeyboardInterrupt/SystemExit/GeneratorExit),
        non-exact-str, UTF-8-inencodable, wrong-grammar or
        request-divergent output fails closed as
        divergent_fingerprint."""
        try:
            # DETACHED argument copy: the fingerprinter never sees
            # the frozen snapshot objects - mutation is inert
            out = self.fingerprinter(
                frozen_req["op"],
                json.loads(_canon(frozen_req["payload"])))
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
        # REQUEST-BOUND: byte-exact equality with the LOCAL
        # canonical derivation over the frozen request
        if out != request_fingerprint(
                frozen_req["op"], frozen_req["payload"]):
            _fail("divergent_fingerprint")
        return out

    @staticmethod
    def _validate_request(request):
        """malformed_idempotency_request on ANY shape, type,
        grammar or bound violation; the payload is validated
        through the LINKED queue contract. Returns the FROZEN
        detached request."""
        if type(request) is not dict or not _str_keyed(request) or \
                set(dict.keys(request)) != _REQUEST_KEYS:
            _fail("malformed_idempotency_request")
        if not _grammar(request["idempotency_key"], _KEY_RE):
            _fail("malformed_idempotency_request")
        op = request["op"]
        if type(op) is not str or op not in _OPS:
            _fail("malformed_idempotency_request")
        payload = request["payload"]
        if type(payload) is not dict or not _str_keyed(payload) or \
                "op" in payload:
            _fail("malformed_idempotency_request")
        try:
            frozen_q = _validate_queue_request(dict(payload, op=op))
        except QueueError:
            _fail("malformed_idempotency_request")
        return {"idempotency_key": request["idempotency_key"],
                "op": op,
                "payload": {k: v for k, v in frozen_q.items()
                            if k != "op"}}

    @staticmethod
    def _validate_ledger(ledger, live_state_id):
        """Everything decidable WITHOUT the oracle: exact receipt
        shape, grammars, receipt-id re-derivation, unique keys,
        contiguous sequences from zero, prior/state chaining, and
        the last receipt naming the LIVE state."""
        if type(ledger) is not list:
            _fail("corrupt_ledger")
        seen_keys = set()
        prev_state = None
        for index, receipt in enumerate(ledger):
            if type(receipt) is not dict or \
                    not _str_keyed(receipt) or \
                    set(dict.keys(receipt)) != set(_FIELDS):
                _fail("corrupt_ledger")
            key = receipt["idempotency_key"]
            fp = receipt["request_fingerprint"]
            prior = receipt["prior_state_id"]
            sid = receipt["state_id"]
            seq = receipt["sequence"]
            rid = receipt["receipt_id"]
            if not _grammar(key, _KEY_RE) or \
                    not _grammar(fp, _FP_RE) or \
                    not _grammar(prior, _STATE_ID_RE) or \
                    not _grammar(sid, _STATE_ID_RE) or \
                    not _grammar(rid, _RECEIPT_RE):
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
        ledger; snapshot all three; one fingerprinter call; dedupe
        by key then byte-exact fingerprint; stage the transition
        on a detached copy; commit state and receipt together
        LAST - rejection leaves every input bit-identical."""
        frozen_req = self._validate_request(request)
        try:
            _validate_state(state)
        except QueueError:
            _fail("corrupt_queue")
        live_state_id = state_id_for(state)
        self._validate_ledger(ledger, live_state_id)
        # INPUT PRESERVATION snapshots (reference-preserving) +
        # FREEZE, all BEFORE the fingerprinter call.
        snaps = (_snapshot(state), _snapshot(ledger),
                 _snapshot(request))
        frozen_state = json.loads(_canon(state))
        frozen_ledger = [dict(r) for r in ledger]
        try:
            token = self._fingerprint(frozen_req)
            stored = None
            for receipt in frozen_ledger:
                if receipt["idempotency_key"] == \
                        frozen_req["idempotency_key"]:
                    stored = receipt
            if stored is not None:
                if stored["request_fingerprint"] != token:
                    _fail("key_conflict")
                outcome, receipt, staged = "replayed", \
                    dict(stored), None
            else:
                # the transition stages on the FROZEN state,
                # never on the live state the oracle may have
                # rewritten mid-call
                work = json.loads(_canon(frozen_state))
                queue_request = json.loads(_canon(
                    dict(frozen_req["payload"],
                         op=frozen_req["op"])))
                # the queue's own typed rejection propagates with
                # NO receipt and NO transition (finally restores)
                _QUEUE.apply(work, queue_request)
                new_state_id = state_id_for(work)
                sequence = len(frozen_ledger)
                receipt = {
                    "receipt_id": derive_receipt_id(
                        frozen_req["idempotency_key"], token,
                        live_state_id, new_state_id, sequence),
                    "idempotency_key": frozen_req["idempotency_key"],
                    "request_fingerprint": token,
                    "prior_state_id": live_state_id,
                    "state_id": new_state_id,
                    "sequence": sequence}
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


def _engine():
    return JobsIdempotencyEngine(request_fingerprint)


def _key(name):
    return name  # the grammar is a general token, keys are opaque


def _enqueue(key, dedupe="task-a", priority=5, payload=None):
    return {"idempotency_key": _key(key),
            "op": "enqueue",
            "payload": {
                "dedupe_key": dedupe,
                "priority": priority,
                "payload": {"task": payload
                            if payload is not None else dedupe}}}


def _claim(key, worker="worker-1", now=100, lease_ms=50):
    return {"idempotency_key": _key(key),
            "op": "claim",
            "payload": {"worker": worker, "now": now,
                        "lease_ms": lease_ms}}


def _ack(key, job_id, worker="worker-1", now=110):
    return {"idempotency_key": _key(key),
            "op": "ack",
            "payload": {"job_id": job_id, "worker": worker,
                        "now": now}}


def _nack(key, job_id, worker="worker-1", now=110):
    return {"idempotency_key": _key(key),
            "op": "nack",
            "payload": {"job_id": job_id, "worker": worker,
                        "now": now}}


def _store(*requests):
    """Build a valid (state, ledger) by applying requests."""
    state, ledger = empty_state(), []
    engine = _engine()
    for req in requests:
        engine.apply(state, ledger, req)
    return state, ledger


class _Pin:
    """Identity token for id()-only snapshot fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot
    be freed and reused) for as long as the snapshot does. It
    compares by identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _snap(*objs):
    """Structural snapshot that NEVER calls a caller-supplied
    __eq__/__hash__: exact types, str keys by value, any other
    key or leaf object by identity."""
    def walk(o):
        t = type(o)
        if t is dict:
            return ("dict", tuple(
                (k if type(k) is str else ("key", _Pin(k)), walk(v))
                for k, v in dict.items(o)))
        if t in (list, tuple):
            return (t.__name__, tuple(walk(v) for v in o))
        if t in (str, int, bool, float, bytes, type(None)):
            return (t.__name__, o)
        return ("obj", _Pin(o))
    return walk(objs)


def _expect(cls, fn, *inputs):
    before = _snap(*inputs)
    try:
        fn()
    except IdempotencyError as caught:
        err = caught
    except BaseException as raw:  # noqa: BLE001 - totality probe
        # a RAW escape (including KeyboardInterrupt/SystemExit/
        # GeneratorExit) is a totality failure, reported as a test
        # failure rather than aborting the session
        pytest.fail(f"raw escape: {type(raw).__name__}")
    else:
        pytest.fail(f"expected {cls}, apply succeeded")
    assert err.failure_class == cls
    assert err.code == _FAILURE_MAPPING[cls]
    assert err.code in _ERROR_ENUM
    assert _snap(*inputs) == before  # atomic
    return err


def _expect_queue_rejection(fn, *inputs):
    """The queue's own typed rejection propagates UNWRAPPED, with
    no receipt, no transition and every input bit-identical."""
    before = _snap(*inputs)
    try:
        fn()
    except QueueError:
        pass
    except BaseException as raw:  # noqa: BLE001 - totality probe
        pytest.fail(f"raw escape: {type(raw).__name__}")
    else:
        pytest.fail("expected a queue rejection, apply succeeded")
    assert _snap(*inputs) == before


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_applied():
    state, ledger = empty_state(), []
    req = _enqueue("order-1")
    req_before = copy.deepcopy(req)
    result = _engine().apply(state, ledger, req)
    assert set(result) == {"outcome", "receipt"}
    assert result["outcome"] == "applied"
    assert result["outcome"] in _OUTCOMES
    receipt = result["receipt"]
    assert list(receipt) == _FIELDS
    assert _RECEIPT_RE.fullmatch(receipt["receipt_id"])
    assert _FP_RE.fullmatch(receipt["request_fingerprint"])
    assert receipt["idempotency_key"] == req["idempotency_key"]
    assert receipt["sequence"] == 0
    assert receipt["state_id"] == state_id_for(state)
    assert len(state["jobs"]) == 1 and ledger == [receipt]
    assert req == req_before


def test_every_op_applies_and_replays():
    state, ledger = empty_state(), []
    engine = _engine()
    first = engine.apply(state, ledger, _enqueue("e"))
    job_id = job_id_for("task-a")
    second = engine.apply(state, ledger, _claim("c"))
    third = engine.apply(state, ledger, _ack("a", job_id))
    fourth = engine.apply(state, ledger, _enqueue("e2", dedupe="b"))
    fifth = engine.apply(state, ledger, _claim("c2"))
    sixth = engine.apply(state, ledger, _nack("n", job_id_for("b")))
    results = [first, second, third, fourth, fifth, sixth]
    assert [r["outcome"] for r in results] == ["applied"] * 6
    assert [r["receipt"]["sequence"] for r in results] == \
        [0, 1, 2, 3, 4, 5]
    for req, result in [
            (_enqueue("e"), first), (_claim("c"), second),
            (_ack("a", job_id), third),
            (_enqueue("e2", dedupe="b"), fourth),
            (_claim("c2"), fifth),
            (_nack("n", job_id_for("b")), sixth)]:
        before = _snap(state, ledger)
        again = engine.apply(state, ledger, req)
        assert again["outcome"] == "replayed"
        assert again["receipt"] == result["receipt"]
        assert _snap(state, ledger) == before


def test_replay_returns_identical_receipt_and_commits_nothing():
    state, ledger = _store(_enqueue("k1"), _enqueue("k2", dedupe="b"))
    engine = _engine()
    first = engine.apply(state, ledger, _enqueue("k1"))
    state_before, ledger_before = copy.deepcopy((state, ledger))
    for _ in range(3):
        again = engine.apply(state, ledger, _enqueue("k1"))
        assert again["outcome"] == "replayed"
        assert again["receipt"] == first["receipt"]
    assert (state, ledger) == (state_before, ledger_before)


def test_same_request_distinct_keys_apply_twice():
    """The key, not the content, is the identity: two keys with
    byte-identical requests are two applies (a no-op claim is
    still a committed transition)."""
    state, ledger = _store(_enqueue("e"))
    state, ledger2 = _store(_enqueue("e"))
    engine = _engine()
    one = engine.apply(state, ledger, _claim("k1"))
    two = engine.apply(state, ledger, _claim("k2"))
    assert one["outcome"] == two["outcome"] == "applied"
    assert one["receipt"]["request_fingerprint"] == \
        two["receipt"]["request_fingerprint"]
    assert one["receipt"]["receipt_id"] != two["receipt"]["receipt_id"]
    assert len(ledger) == 3 and ledger2 is not ledger


def test_apply_extends_existing_ledger():
    state, ledger = _store(_enqueue("a"), _enqueue("b", dedupe="b"))
    result = _engine().apply(state, ledger, _enqueue("c", dedupe="c"))
    assert result["receipt"]["sequence"] == 2
    assert [r["sequence"] for r in ledger] == [0, 1, 2]


def test_determinism():
    def build():
        state, ledger = _store(_enqueue("a"), _claim("c"))
        replay = _engine().apply(state, ledger, _enqueue("a"))
        return state, ledger, replay
    assert build() == build()


def test_one_fingerprinter_call_per_apply():
    calls = {"n": 0}

    def counting(op, payload):
        calls["n"] += 1
        return request_fingerprint(op, payload)

    engine = JobsIdempotencyEngine(counting)
    state, ledger = empty_state(), []
    engine.apply(state, ledger, _enqueue("k"))          # applied
    engine.apply(state, ledger, _enqueue("k"))          # replayed
    with pytest.raises(IdempotencyError):
        engine.apply(state, ledger, _enqueue("k", dedupe="b"))
    assert calls["n"] == 3
    # rejections decidable without the oracle never reach it
    with pytest.raises(IdempotencyError):
        engine.apply(state, ledger, _enqueue("k", priority=-1))
    with pytest.raises(IdempotencyError):
        engine.apply(state, "not-a-ledger", _enqueue("k"))
    assert calls["n"] == 3


# -- key conflict ------------------------------------------------------------------


@pytest.mark.parametrize("other", [
    _enqueue("k", dedupe="other"),
    _enqueue("k", priority=9),
    _enqueue("k", payload={"changed": True}),
    _claim("k")],
    ids=["different-dedupe", "different-priority",
         "different-payload", "different-op"])
def test_key_conflict(other):
    state, ledger = _store(_enqueue("k"))
    _expect("key_conflict",
            lambda: _engine().apply(state, ledger, other),
            state, ledger, other)
    assert len(state["jobs"]) == 1 and len(ledger) == 1


# -- malformed requests ------------------------------------------------------------


@pytest.mark.parametrize("req", [
    None, [], "", 0, 3.5, True, set(), (), b"bytes",
    {},
    {"idempotency_key": _key("a")},
    {"idempotency_key": _key("a"), "op": "enqueue"},
    {"op": "enqueue", "payload": {}},
    {"idempotency_key": _key("a"), "payload": {}},
    {"idempotency_key": _key("a"), "op": "enqueue",
     "payload": {}, "extra": 1},
    {"idempotency_key": _key("a"), "op": "Enqueue",
     "payload": {"dedupe_key": "a", "priority": 1, "payload": 1}},
    {"idempotency_key": _key("a"), "op": "", "payload": {}},
    {"idempotency_key": _key("a"), "op": 0, "payload": {}},
    {"idempotency_key": _key("a"), "op": ["enqueue"],
     "payload": {}},
    {"idempotency_key": _key("a"), "op": "enqueue", "payload": []},
    {"idempotency_key": _key("a"), "op": "enqueue",
     "payload": {"op": "ack", "dedupe_key": "a", "priority": 1,
                 "payload": 1}},
    {"idempotency_key": _key("a"), "op": "enqueue",
     "payload": {"dedupe_key": "a", "priority": 1}},
    {"idempotency_key": _key("a"), "op": "enqueue",
     "payload": {"dedupe_key": "a", "priority": 1, "payload": 1,
                 "now": 3}},
    {"idempotency_key": _key("a"), "op": "claim",
     "payload": {"worker": "w", "now": 1, "lease_ms": 0}},
    {"idempotency_key": _key("a"), "op": "claim",
     "payload": {"worker": "w", "now": MAX_NOW,
                 "lease_ms": 1}},
    {"idempotency_key": _key("a"), "op": "ack",
     "payload": {"job_id": "job1:nope", "worker": "w", "now": 1}},
], ids=lambda r: type(r).__name__ + ":" + repr(r)[:32])
def test_total_over_hostile_requests(req):
    state, ledger = _store(_enqueue("a"))
    _expect("malformed_idempotency_request",
            lambda: _engine().apply(state, ledger, req),
            state, ledger, req)


@pytest.mark.parametrize("key", [
    "", ".starts-with-dot", "-starts-with-dash",
    ":starts-with-colon", "_starts-with-underscore",
    "has space", "has\ttab", "has\nnewline",
    "café", "a" * 129, "a!" , "a/", "a@", "a#",
    0, None, ["a"], True, 3.5,
], ids=repr)
def test_key_grammar_rejects(key):
    req = _enqueue("a")
    req["idempotency_key"] = key
    state, ledger = empty_state(), []
    _expect("malformed_idempotency_request",
            lambda: _engine().apply(state, ledger, req),
            state, ledger, req)


@pytest.mark.parametrize("key", [
    "a", "0", "Z", "a" * 128,
    "order-2026-10-26:0001",
    "k._:-9" * 21,
], ids=repr)
def test_key_grammar_boundary_accepts(key):
    req = _enqueue("a")
    req["idempotency_key"] = key
    state, ledger = empty_state(), []
    result = _engine().apply(state, ledger, req)
    assert result["outcome"] == "applied"
    assert ledger[0]["idempotency_key"] == key


# -- corrupt queue state -----------------------------------------------------------


def _corrupt_state_cases():
    def not_a_dict(s, _l):
        return "not-a-state"

    def missing_jobs(s, _l):
        return {"next_seq": s["next_seq"]}

    def extra_field(s, _l):
        out = copy.deepcopy(s)
        out["extra"] = True
        return out

    def bad_status(s, _l):
        out = copy.deepcopy(s)
        out["jobs"][0]["status"] = "gone"
        return out

    def duplicate_job_ids(s, _l):
        out = copy.deepcopy(s)
        out["jobs"].append(copy.deepcopy(out["jobs"][0]))
        return out

    def next_seq_wrong_type(s, _l):
        out = copy.deepcopy(s)
        out["next_seq"] = "1"
        return out

    def lease_without_owner(s, _l):
        out = copy.deepcopy(s)
        out["jobs"][0]["status"] = "leased"
        out["jobs"][0]["lease_expires_at"] = 100
        return out

    def attempts_negative(s, _l):
        out = copy.deepcopy(s)
        out["jobs"][0]["attempts"] = -1
        return out

    return [(fn.__name__, fn) for fn in (
        not_a_dict, missing_jobs, extra_field, bad_status,
        duplicate_job_ids, next_seq_wrong_type, lease_without_owner,
        attempts_negative)]


@pytest.mark.parametrize("name,mutate", _corrupt_state_cases(),
                         ids=[n for n, _ in _corrupt_state_cases()])
def test_corrupt_queue_state(name, mutate):
    state, ledger = _store(_enqueue("a"))
    broken = mutate(state, ledger)
    req = _enqueue("b", dedupe="b")
    calls = {"n": 0}

    def counting(op, payload):
        calls["n"] += 1
        return request_fingerprint(op, payload)

    _expect("corrupt_queue",
            lambda: JobsIdempotencyEngine(counting).apply(
                broken, ledger, req),
            broken, ledger, req)
    assert calls["n"] == 0  # the oracle is never reached


# -- corrupt ledger ---------------------------------------------------------------


def _corrupt_ledger_cases():
    def not_a_list(_s, ledger):
        return {r["idempotency_key"]: r for r in ledger}

    def receipt_not_a_dict(_s, ledger):
        return ledger + ["not-a-receipt"]

    def missing_field(_s, ledger):
        out = copy.deepcopy(ledger)
        del out[0]["sequence"]
        return out

    def extra_field(_s, ledger):
        out = copy.deepcopy(ledger)
        out[0]["entry_id"] = "wle1:" + "0" * 64
        return out

    def bad_key_grammar(_s, ledger):
        out = copy.deepcopy(ledger)
        out[0]["idempotency_key"] = "bogus"
        return out

    def bad_fingerprint_grammar(_s, ledger):
        out = copy.deepcopy(ledger)
        out[0]["request_fingerprint"] = "jif1:" + "A" * 64
        return out

    def bad_receipt_id_grammar(_s, ledger):
        out = copy.deepcopy(ledger)
        out[0]["receipt_id"] = "jir9:" + "a" * 64
        return out

    def bad_state_id_grammar(_s, ledger):
        out = copy.deepcopy(ledger)
        out[0]["state_id"] = "xs1:" + "a" * 64
        return out

    def receipt_id_not_rederived(_s, ledger):
        out = copy.deepcopy(ledger)
        out[0]["receipt_id"] = "jir1:" + "0" * 64
        return out

    def sequence_gap(_s, ledger):
        out = copy.deepcopy(ledger)
        out[1] = dict(out[1], sequence=7)
        out[1]["receipt_id"] = derive_receipt_id(
            out[1]["idempotency_key"],
            out[1]["request_fingerprint"],
            out[1]["prior_state_id"], out[1]["state_id"], 7)
        return out

    def sequence_wrong_type(_s, ledger):
        out = copy.deepcopy(ledger)
        out[0] = dict(out[0], sequence=True)
        return out

    def duplicate_keys(_s, ledger):
        out = copy.deepcopy(ledger)
        twin = dict(out[1], idempotency_key=out[0]["idempotency_key"],
                    sequence=1)
        twin["receipt_id"] = derive_receipt_id(
            twin["idempotency_key"], twin["request_fingerprint"],
            twin["prior_state_id"], twin["state_id"], 1)
        out[1] = twin
        return out

    def chain_break(_s, ledger):
        out = copy.deepcopy(ledger)
        moved = dict(out[1], prior_state_id=out[0]["prior_state_id"])
        moved["receipt_id"] = derive_receipt_id(
            moved["idempotency_key"], moved["request_fingerprint"],
            moved["prior_state_id"], moved["state_id"], 1)
        out[1] = moved
        return out

    def terminal_mismatch(state, ledger):
        out = copy.deepcopy(ledger)
        fake = dict(out[-1],
                    state_id=state_id_for(
                        {"jobs": [], "next_seq": 99}))
        fake["receipt_id"] = derive_receipt_id(
            fake["idempotency_key"], fake["request_fingerprint"],
            fake["prior_state_id"], fake["state_id"],
            fake["sequence"])
        out[-1] = fake
        return out

    return [(fn.__name__, fn) for fn in (
        not_a_list, receipt_not_a_dict, missing_field, extra_field,
        bad_key_grammar, bad_fingerprint_grammar,
        bad_receipt_id_grammar, bad_state_id_grammar,
        receipt_id_not_rederived, sequence_gap, sequence_wrong_type,
        duplicate_keys, chain_break, terminal_mismatch)]


@pytest.mark.parametrize("name,mutate", _corrupt_ledger_cases(),
                         ids=[n for n, _ in _corrupt_ledger_cases()])
def test_corrupt_ledger(name, mutate):
    state, ledger = _store(_enqueue("a"), _enqueue("b", dedupe="b"))
    broken = mutate(state, ledger)
    req = _enqueue("c", dedupe="c")
    _expect("corrupt_ledger",
            lambda: _engine().apply(state, broken, req),
            state, broken, req)


def test_replay_against_corrupt_ledger_still_fails_closed():
    """A replay is NEVER served from a corrupt ledger, not even
    for the key whose receipt looks intact."""
    state, ledger = _store(_enqueue("a"), _enqueue("b", dedupe="b"))
    broken = copy.deepcopy(ledger)
    broken[1]["sequence"] = 0
    _expect("corrupt_ledger",
            lambda: _engine().apply(state, broken, _enqueue("a")),
            state, broken)


# -- hostile fingerprinter -----------------------------------------------------------


def _valid_token():
    return request_fingerprint("enqueue", _enqueue("x")["payload"])


@pytest.mark.parametrize("name,fp", [
    ("raises-value-error",
     lambda o, p: (_ for _ in ()).throw(ValueError("boom"))),
    ("raises-keyboard-interrupt",
     lambda o, p: (_ for _ in ()).throw(KeyboardInterrupt())),
    ("raises-system-exit",
     lambda o, p: (_ for _ in ()).throw(SystemExit(3))),
    ("raises-generator-exit",
     lambda o, p: (_ for _ in ()).throw(GeneratorExit())),
    ("returns-none", lambda o, p: None),
    ("returns-int", lambda o, p: 0),
    ("returns-bytes", lambda o, p: b"jif1:" + b"a" * 64),
    ("returns-dict", lambda o, p: {"token": "x"}),
    ("returns-str-subclass",
     lambda o, p: type("S", (str,), {})(_valid_token())),
    ("returns-garbage", lambda o, p: "garbage"),
    ("returns-empty", lambda o, p: ""),
    ("returns-wrong-grammar",
     lambda o, p: "jif1:" + "a" * 63),
    ("returns-valid-grammar-wrong-token",
     lambda o, p: "jif1:" + "0" * 64),
], ids=[n for n, _ in [
    ("raises-value-error", 0), ("raises-keyboard-interrupt", 0),
    ("raises-system-exit", 0), ("raises-generator-exit", 0),
    ("returns-none", 0), ("returns-int", 0), ("returns-bytes", 0),
    ("returns-dict", 0), ("returns-str-subclass", 0),
    ("returns-garbage", 0), ("returns-empty", 0),
    ("returns-wrong-grammar", 0),
    ("returns-valid-grammar-wrong-token", 0)]])
def test_hostile_fingerprinter(name, fp):
    state, ledger = _store(_enqueue("a"))
    req = _enqueue("b", dedupe="b")
    _expect("divergent_fingerprint",
            lambda: JobsIdempotencyEngine(fp).apply(
                state, ledger, req),
            state, ledger, req)
    assert len(ledger) == 1  # never committed


def test_stateful_alternating_fingerprinter_never_commits():
    calls = {"n": 0}

    def flaky(op, payload):
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            return "jif1:" + "0" * 64
        return request_fingerprint(op, payload)

    engine = JobsIdempotencyEngine(flaky)
    state, ledger = empty_state(), []
    ok = engine.apply(state, ledger, _enqueue("a"))
    assert ok["outcome"] == "applied"
    req = _enqueue("b", dedupe="b")
    _expect("divergent_fingerprint",
            lambda: engine.apply(state, ledger, req),
            state, ledger, req)
    assert len(ledger) == 1
    again = engine.apply(state, ledger, _enqueue("b", dedupe="b"))
    assert again["outcome"] == "applied"
    assert len(ledger) == 2


def test_fingerprint_binds_every_request_field():
    """Mutating ANY request field - op, dedupe_key, priority,
    payload, worker, now, lease_ms, job_id - changes the
    fingerprint, so the same key under a mutated request is a
    key_conflict, never a silent replay."""
    base_enq = _enqueue("k")
    base_claim = _claim("k")
    mutations = [
        ("op", _claim("k")),
        ("dedupe_key", _enqueue("k", dedupe="other")),
        ("priority", _enqueue("k", priority=0)),
        ("enqueue-payload", _enqueue("k", payload={"x": 1})),
        ("worker", _claim("k", worker="worker-2")),
        ("now", _claim("k", now=101)),
        ("lease_ms", _claim("k", lease_ms=51)),
        ("job_id", _ack("k", job_id_for("other"))),
    ]
    for name, other in mutations:
        base = base_claim if name in (
            "worker", "now", "lease_ms") else base_enq
        if name == "job_id":
            base = _ack("k", job_id_for("task-a"))
        fp_base = request_fingerprint(base["op"], base["payload"])
        fp_other = request_fingerprint(other["op"],
                                       other["payload"])
        assert fp_base != fp_other, name


def test_fingerprinter_mutating_its_argument_is_inert():
    def hostile(op, payload):
        token = request_fingerprint(op, copy.deepcopy(payload))
        payload.clear()
        payload["injected"] = True
        return token

    state, ledger = empty_state(), []
    result = JobsIdempotencyEngine(hostile).apply(
        state, ledger, _enqueue("k"))
    assert result["outcome"] == "applied"
    assert state["jobs"][0]["payload"] == {"task": "task-a"}


def test_fingerprinter_mutating_live_inputs_mid_call():
    """The fingerprinter's window to rewrite the LIVE state,
    ledger or request mid-call closes to nothing: snapshots are
    taken BEFORE the call, the call sees only detached copies,
    and the finally-restore reverts any live mutation."""
    state, ledger = _store(_enqueue("a"))
    req = _enqueue("b", dedupe="b")

    def hostile(op, payload):
        token = request_fingerprint(op, payload)
        state["jobs"].clear()
        ledger.clear()
        req["payload"]["dedupe_key"] = "rewritten"
        req["op"] = "claim"
        return token

    result = JobsIdempotencyEngine(hostile).apply(state, ledger, req)
    assert result["outcome"] == "applied"
    assert [j["job_id"] for j in state["jobs"]] == [
        job_id_for("task-a"), job_id_for("b")]
    assert req == _enqueue("b", dedupe="b")
    assert len(ledger) == 2
    assert result["receipt"]["request_fingerprint"] == \
        request_fingerprint("enqueue",
                            _enqueue("b", dedupe="b")["payload"])


# -- atomicity + queue rejection ---------------------------------------------------


def test_rejected_apply_leaves_inputs_bit_identical():
    state, ledger = _store(_enqueue("a"))
    engine = _engine()
    for cls, req, lg in [
            ("malformed_idempotency_request",
             _enqueue("a", priority=-1), ledger),
            ("key_conflict", _enqueue("a", dedupe="b"), ledger),
            ("corrupt_ledger", _enqueue("b", dedupe="b"),
             ledger + [{}])]:
        _expect(cls, lambda r=req, g=lg: engine.apply(state, g, r),
                state, lg, req)
    req = _enqueue("b", dedupe="b")
    _expect("divergent_fingerprint",
            lambda: JobsIdempotencyEngine(lambda o, p: 0).apply(
                state, ledger, req), state, ledger, req)


@pytest.mark.parametrize("name", [
    "dedupe-conflict", "ack-unknown-job", "nack-not-leased",
    "ack-wrong-worker"])
def test_queue_rejection_propagates_typed_and_commits_nothing(name):
    if name == "dedupe-conflict":
        # same dedupe_key, different priority: the queue's own
        # never-overwrite rule rejects it
        state, ledger = _store(_enqueue("a"))
        req = _enqueue("x", priority=9)
    elif name == "ack-unknown-job":
        state, ledger = _store(_enqueue("a"))
        req = _ack("x", job_id_for("ghost"))
    elif name == "nack-not-leased":
        state, ledger = _store(_enqueue("a"))
        req = _nack("x", job_id_for("task-a"))
    else:  # ack-wrong-worker
        state, ledger = _store(_enqueue("a"), _claim("c"))
        req = _ack("x", job_id_for("task-a"), worker="worker-2")
        state, ledger = _store(_enqueue("a"), _claim("c"))
        req = _ack("x", job_id_for("task-a"), worker="worker-2")
    calls = {"n": 0}

    def counting(op, payload):
        calls["n"] += 1
        return request_fingerprint(op, payload)

    engine = JobsIdempotencyEngine(counting)
    _expect_queue_rejection(
        lambda: engine.apply(state, ledger, req),
        state, ledger, req)
    expected = 2 if name == "ack-wrong-worker" else 1
    assert len(ledger) == expected  # no receipt committed
    # no receipt was committed, so a retry RE-ATTEMPTS (and the
    # queue rejects again) instead of replaying a phantom receipt
    _expect_queue_rejection(
        lambda: engine.apply(state, ledger, req),
        state, ledger, req)
    assert calls["n"] == 2


# -- rollback interplay ----------------------------------------------------------


def test_state_rollback_makes_stale_ledger_fail_closed():
    """ROLLBACK: restoring the queue state to a snapshot taken
    before a receipt's transition leaves the ledger's terminal
    binding false - every apply fails closed as corrupt_ledger,
    the stale receipt is NEVER replayed. Trimming the ledger to
    the surviving prefix restores service; the rolled-back key
    then re-applies deterministically."""
    state, ledger = empty_state(), []
    engine = _engine()
    engine.apply(state, ledger, _enqueue("a"))
    snapshot = copy.deepcopy(state)
    engine.apply(state, ledger, _enqueue("b", dedupe="b"))
    stale = dict(ledger[1])
    state.clear()
    state.update(snapshot)  # rolled back past the second receipt
    for req in (_enqueue("b", dedupe="b"), _enqueue("a"),
                _enqueue("c", dedupe="c")):
        _expect("corrupt_ledger",
                lambda r=req: engine.apply(state, ledger, r),
                state, ledger, req)
    ledger = ledger[:1]
    again = engine.apply(state, ledger, _enqueue("b", dedupe="b"))
    assert again["outcome"] == "applied"
    assert again["receipt"]["sequence"] == 1
    assert again["receipt"] == stale  # deterministic re-apply


def test_rollback_then_divergent_transition_rejects_stale_receipt():
    """After a rollback, a DIFFERENT transition lands at the stale
    receipt's sequence: mixing the stale receipt back in breaks
    the chain/sequence binding, so the old key can never replay
    the wrong transition."""
    state, ledger = empty_state(), []
    engine0 = _engine()
    engine0.apply(state, ledger, _enqueue("a"))
    snapshot = copy.deepcopy(state)
    engine0.apply(state, ledger, _enqueue("b", dedupe="b"))
    stale = copy.deepcopy(ledger[1])
    state.clear()
    state.update(snapshot)
    ledger = ledger[:1]
    engine = _engine()
    engine.apply(state, ledger, _enqueue("c", dedupe="c"))
    mixed = ledger + [stale]
    _expect("corrupt_ledger",
            lambda: engine.apply(state, mixed, _enqueue("a")),
            state, mixed)


# -- behavioral mutants ------------------------------------------------------------------


def test_mutant_key_only_deduplication():
    """Mutant: deduplicating on the key alone silently returns
    the stored receipt for a DIFFERENT request - the second
    transition is lost. Counter-test: the real engine fails
    closed."""
    def mutant(state, ledger, request):
        for r in ledger:
            if r["idempotency_key"] == request["idempotency_key"]:
                return {"outcome": "replayed", "receipt": dict(r)}
        return _engine().apply(state, ledger, request)

    state, ledger = _store(_enqueue("k"))
    lost = mutant(state, ledger, _enqueue("k", dedupe="b"))
    assert lost["outcome"] == "replayed"  # the "b" write is lost
    req = _enqueue("k", dedupe="b")
    _expect("key_conflict",
            lambda: _engine().apply(state, ledger, req),
            state, ledger, req)


def test_mutant_no_deduplication():
    """Mutant: applying every request afresh double-applies a
    retry - the second apply hits the queue's own dedupe_key
    rejection, and the receipt ledger no longer binds to the
    state. Counter-test: the real engine replays."""
    def mutant(state, ledger, request):
        engine = _engine()
        engine._validate_request(request)
        work = json.loads(_canon(state))
        _QUEUE.apply(work, dict(request["payload"],
                                op=request["op"]))
        state.clear()
        state.update(work)
        return {"outcome": "applied"}

    jid = job_id_for("task-a")
    state, ledger = _store(_enqueue("k"), _claim("c"))
    mutant(state, ledger, _ack("a", jid))
    with pytest.raises(QueueError):  # retry double-applies
        mutant(state, ledger, _ack("a", jid))
    state, ledger = _store(_enqueue("k"), _claim("c"))
    engine = _engine()
    engine.apply(state, ledger, _ack("a", jid))
    again = engine.apply(state, ledger, _ack("a", jid))
    assert again["outcome"] == "replayed"
    assert len(ledger) == 3


def test_mutant_grammar_only_fingerprint():
    """Mutant: validating only the GRAMMAR of the oracle's token
    (never binding it to the request) lets a well-formed foreign
    token in - a collision with another request's token then
    replays the WRONG receipt. Counter-test: byte-exact local
    binding."""
    def mutant_fp(op, payload):
        return "jif1:" + "0" * 64  # grammar-valid, request-foreign

    state, ledger = empty_state(), []
    req = _enqueue("k")
    _expect("divergent_fingerprint",
            lambda: JobsIdempotencyEngine(mutant_fp).apply(
                state, ledger, req), state, ledger, req)


def test_mutant_ledger_unbound_to_state():
    """Mutant: dropping the chain/terminal binding lets a stale
    ledger (state rolled back) pass validation and replay a
    receipt whose transition is GONE. Counter-test: the real
    engine fails closed."""
    def mutant_validate(ledger, live_state_id):
        if type(ledger) is not list:
            raise IdempotencyError("corrupt_ledger",
                                   _FAILURE_MAPPING["corrupt_ledger"])
        # no chain check, no terminal binding

    state, ledger = _store(_enqueue("a"), _enqueue("b", dedupe="b"))
    state.clear()
    state.update({"jobs": [], "next_seq": 0})
    mutant_validate(ledger, state_id_for(state))  # passes
    _expect("corrupt_ledger",
            lambda: _engine().apply(state, ledger, _enqueue("b",
                                                            dedupe="b")),
            state, ledger)


def test_mutant_commit_before_fingerprint():
    """Mutant: committing the transition BEFORE the fingerprinter
    call leaves a changed state with no receipt when the oracle
    fails - a retry then double-applies (or wrongly rejects).
    Counter-test: commit last."""
    def mutant(state, ledger, request):
        work = json.loads(_canon(state))
        _QUEUE.apply(work, dict(request["payload"],
                                op=request["op"]))
        state.clear()
        state.update(work)
        JobsIdempotencyEngine(
            lambda o, p: None)._fingerprint(
                {"op": request["op"], "payload": request["payload"]})

    state, ledger = empty_state(), []
    with pytest.raises(IdempotencyError):
        mutant(state, ledger, _enqueue("k"))
    assert len(state["jobs"]) == 1 and ledger == []  # orphan write
    state, ledger = empty_state(), []
    req = _enqueue("k")
    _expect("divergent_fingerprint",
            lambda: JobsIdempotencyEngine(lambda o, p: None).apply(
                state, ledger, req), state, ledger, req)
    assert state == empty_state() and ledger == []


def test_mutant_live_request_reread_after_fingerprinter():
    """Mutant: re-reading the LIVE request after the oracle lets a
    mid-call rewrite commit a payload the fingerprint never
    described. Counter-test: the frozen request is committed."""
    req = _enqueue("k")

    def rewriting(op, payload):
        token = request_fingerprint(op, payload)
        req["payload"]["dedupe_key"] = "rewritten"
        return token

    def mutant(state, request):
        token = rewriting(request["op"], request["payload"])
        work = json.loads(_canon(state))
        _QUEUE.apply(work, dict(request["payload"], op=request["op"]))
        return token, state_id_for(work)

    token, sid = mutant(empty_state(), req)
    assert sid != state_id_for(empty_state())
    assert request_fingerprint(
        "enqueue", {"dedupe_key": "rewritten", "priority": 5,
                    "payload": {"task": "task-a"}}) != token
    req = _enqueue("k")
    state, ledger = empty_state(), []
    result = JobsIdempotencyEngine(rewriting).apply(state, ledger, req)
    assert state["jobs"][0]["job_id"] == job_id_for("task-a")
    assert result["receipt"]["request_fingerprint"] == \
        request_fingerprint("enqueue", _enqueue("k")["payload"])
    assert req == _enqueue("k")


# -- hostile dict keys --------------------------------------------------------------


class _CollidingKey:
    """Non-str key whose hash equals a real field name's hash and
    whose __eq__ raises: any set construction or key comparison
    against the real field name escapes raw unless guarded."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile key __eq__")


class _CollidingStrKey(str):
    """str-subclass variant of the same attack."""

    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        raise RuntimeError("hostile str key __eq__")


def _swap_key(d, name, cls):
    d[cls(name)] = d.pop(name)


def _hostile_key_cases():
    def request(cls):
        state, ledger = _store(_enqueue("a"))
        req = _enqueue("b", dedupe="b")
        _swap_key(req, "op", cls)
        return "malformed_idempotency_request", state, ledger, req

    def payload(cls):
        state, ledger = _store(_enqueue("a"))
        req = _enqueue("b", dedupe="b")
        _swap_key(req["payload"], "dedupe_key", cls)
        return "malformed_idempotency_request", state, ledger, req

    def ledger_receipt(cls):
        state, ledger = _store(_enqueue("a"))
        _swap_key(ledger[0], "sequence", cls)
        return "corrupt_ledger", state, ledger, _enqueue("a")

    def state_top(cls):
        state, ledger = _store(_enqueue("a"))
        _swap_key(state, "jobs", cls)
        return "corrupt_queue", state, ledger, _enqueue("b", dedupe="b")

    def state_job(cls):
        state, ledger = _store(_enqueue("a"))
        _swap_key(state["jobs"][0], "job_id", cls)
        return "corrupt_queue", state, ledger, _enqueue("b", dedupe="b")

    return [(fn.__name__, fn) for fn in (
        request, payload, ledger_receipt, state_top, state_job)]


@pytest.mark.parametrize("cls", [_CollidingKey, _CollidingStrKey],
                         ids=["non-str", "str-subclass"])
@pytest.mark.parametrize("name,build", _hostile_key_cases(),
                         ids=[n for n, _ in _hostile_key_cases()])
def test_hostile_colliding_dict_key(name, build, cls):
    """At EVERY dict boundary (request, payload, ledger receipt,
    queue state, job record) a key whose hash collides with a real
    field name and whose __eq__ raises fails closed typed, never
    raw, inputs bit-identical, and the fingerprinter is never
    reached."""
    calls = {"n": 0}

    def counting(op, payload):
        calls["n"] += 1
        return request_fingerprint(op, payload)

    failure, state, ledger, req = build(cls)
    _expect(failure,
            lambda: JobsIdempotencyEngine(counting).apply(
                state, ledger, req),
            state, ledger, req)
    assert calls["n"] == 0


@pytest.mark.parametrize("name,build", [
    c for c in _hostile_key_cases() if c[0] != "payload"],
    ids=[n for n, _ in _hostile_key_cases() if n != "payload"])
def test_mutant_without_hostile_key_guard_escapes_raw(
        name, build, monkeypatch):
    """Engine mutant: with the key-type guard removed, the same
    input escapes RAW (the hostile __eq__ fires inside set
    comparison or the linked queue machinery) - the guard is what
    makes the boundary total."""
    monkeypatch.setattr(sys.modules[__name__], "_str_keyed",
                        lambda d: True)
    # the linked queue contract carries its own key-type guard;
    # strip it too so the mutant is truly guardless at every layer
    monkeypatch.setattr(
        sys.modules["tests.test_t0284_queue_contract"],
        "_exact_keys", lambda m, fields: True)
    _failure, state, ledger, req = build(_CollidingKey)
    with pytest.raises(RuntimeError, match="hostile key"):
        _engine().apply(state, ledger, req)


class _PinProbeLeaf:
    pass


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original
    leaf and the second can land on its freed address, which a
    bare id() would miss. The snapshot pins the original, so the
    swap goes red."""
    value = {"k": _PinProbeLeaf()}
    before = _snap(value)
    value["k"] = _PinProbeLeaf()
    value["k"] = _PinProbeLeaf()
    assert _snap(value) != before


# -- lint mutants -------------------------------------------------------------------------


def _mutants():
    doc = yaml.safe_load(CONTRACT.read_text())
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    c = ["contract"]
    add("role kind drift", c + ["role", "kind"], "best-effort-dedupe")
    add("not_scope drift", c + ["role", "not_scope"],
        "dedupe-key-expiry-owned-here")
    add("record fields drift", c + ["record", "fields"],
        ["receipt_id"])
    add("record exact drift", c + ["record", "exact"], False)
    add("record exact int confusion", c + ["record", "exact"], 1)
    add("receipt id grammar drift",
        c + ["identifiers", "receipt_id", "grammar"], "^.*$")
    add("receipt id supplied",
        c + ["identifiers", "receipt_id", "source"],
        "caller-supplied")
    add("key grammar drift",
        c + ["identifiers", "idempotency_key", "grammar"], "^.*$")
    add("key derived",
        c + ["identifiers", "idempotency_key", "source"],
        "derived-from-payload")
    add("fingerprint derivation drift",
        c + ["identifiers", "request_fingerprint", "derivation"],
        "sha256-over-request-including-key")
    add("fingerprint binding dropped",
        c + ["identifiers", "request_fingerprint", "source"],
        "fingerprinter-output-shape-validated-only")
    add("fingerprint grammar drift",
        c + ["identifiers", "request_fingerprint", "grammar"],
        "^.*$")
    add("prior state id grammar drift",
        c + ["identifiers", "prior_state_id", "grammar"], "^.*$")
    add("prior state id supplied",
        c + ["identifiers", "prior_state_id", "source"],
        "caller-supplied")
    add("state id grammar drift",
        c + ["identifiers", "state_id", "grammar"], "^.*$")
    add("receipt id kind drift",
        c + ["identifiers", "receipt_id", "kind"],
        "random-uuid")
    add("key kind drift",
        c + ["identifiers", "idempotency_key", "kind"],
        "server-issued-key")
    add("outcome added", c + ["outcomes", "values"],
        ["applied", "replayed", "overwritten"])
    add("outcomes open", c + ["outcomes", "closed"], False)
    add("applied semantics drift", c + ["outcomes", "applied"],
        "key-recorded-before-transition")
    add("replayed semantics drift", c + ["outcomes", "replayed"],
        "seen-key-re-applies-the-transition")
    add("request fields drift",
        c + ["request", "fields"], ["idempotency_key", "op"])
    add("request exact drift", c + ["request", "exact"], False)
    add("request exact int confusion",
        c + ["request", "exact"], 0)
    add("request validation dropped",
        c + ["semantics", "request_validation"], "trusted")
    add("queue state validation dropped",
        c + ["semantics", "queue_state_validation"],
        "state-trusted")
    add("ledger binding dropped",
        c + ["semantics", "ledger_binding"], "ledger-trusted")
    add("dedupe before ledger validation",
        c + ["semantics", "validation_order"],
        "dedupe-then-validate")
    add("key-only dedupe",
        c + ["semantics", "deduplication"], "key-only")
    add("conflict overwrites",
        c + ["semantics", "conflict"], "last-write-wins")
    add("queue rejection committed",
        c + ["semantics", "queue_rejection"],
        "rejection-also-recorded")
    add("commit drift", c + ["semantics", "commit"],
        "receipt-before-transition")
    add("oracle trusted", c + ["oracle_boundary", "role"],
        "fingerprinter-always-honest")
    add("single evaluation dropped",
        c + ["oracle_boundary", "single_evaluation"],
        "retries-allowed")
    add("frozen dropped",
        c + ["oracle_boundary", "frozen_snapshots"],
        "live-re-read")
    add("output validation dropped",
        c + ["oracle_boundary", "output_validation"], "any-output")
    add("failure class dropped", c + ["failures", "classes"],
        ["corrupt_queue"])
    add("failure trigger drift",
        c + ["failures", "triggers", "key_conflict"], "never")
    add("failure mapping drift",
        c + ["failures", "mapping", "key_conflict"],
        "E-QUEUE-INTERNAL")
    add("failures open", c + ["failures", "closed"], False)
    add("enum drift", c + ["errors", "closed_enum"],
        ["E-QUEUE-INTERNAL"])
    add("retryable drift",
        c + ["errors", "shape", "retryable_true_only_for"],
        ["E-QUEUE-INTERNAL", "E-QUEUE-KEY-CONFLICT"])
    add("property drift", c + ["properties", "idempotent"],
        "at-least-once")
    add("atomicity property drift",
        c + ["properties", "atomicity"],
        "state-committed-on-rejection")
    add("determinism property drift",
        c + ["properties", "determinism"],
        "wall-clock-dependent")
    add("rollback property drift",
        c + ["properties", "rollback"], "stale-receipts-replayed")
    add("base path drift", c + ["versioning", "base_path"],
        "/jobs/idempotency/v0")
    add("prefix drift", c + ["versioning", "prefix"], "T9999")
    add("link drift", c + ["links", "queue_contract"],
        "data/contracts/san.yaml")
    add("undeclared section", c + ["expiry"], {"ttl": 60})
    add("contract id drift", c + ["id"], "jobs-dedupe")
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 30
    for name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)
        assert name


def test_mutants_never_silent_subset():
    base = yaml.safe_load(CONTRACT.read_text())["contract"]
    covered = set()
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != base.get(section) or \
                    type(content) is not type(base.get(section)) or \
                    repr(content) != repr(base.get(section)):
                covered.add(section)
    assert covered >= set(base)
