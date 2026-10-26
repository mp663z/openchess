"""T0298: seeded fuzz and fault battery for the production jobs idempotency engine.

The T0293 reference engine is the differential oracle. Valid idempotent
streams, hostile requests, hostile ledgers, hostile states and faulting
payload objects are driven through server.jobs_idempotency and the
reference. Both must agree on the outcome word, the receipt, or the
failure layer and class, and leave canonically-identical state and
ledger. A rejected call leaves state, ledger and request
canonical-identical, and production must never run caller code (hash,
eq, repr, iteration, encode hooks) on hostile objects. The fingerprinter
is caller-supplied: hostile oracles must fail closed as
divergent_fingerprint on both engines with the inputs untouched.
Seeded and single-threaded. Happy, boundary, malformed and rollback
behavior per the board acceptance.
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import jobs_idempotency as _prod_module  # noqa: E402
from server.jobs_idempotency import (  # noqa: E402
    IdempotencyError,
    JobsIdempotencyEngine,
    QueueError,
)
from server.jobs_queue import MAX_DEPTH, QueueEngine, job_id_for, state_id_for  # noqa: E402
from tests import test_t0293_jobs_idempotency_contract as _reference  # noqa: E402

PROD, REF = JobsIdempotencyEngine, _reference.JobsIdempotencyEngine
HONEST = _reference.request_fingerprint
RECEIPT_FIELDS = (
    "receipt_id",
    "idempotency_key",
    "request_fingerprint",
    "prior_state_id",
    "state_id",
    "sequence",
)


def _canon(obj, _depth=0):
    """Structural fingerprint that never calls str() on huge ints or caller hooks."""
    kind = type(obj)
    if _depth > 200:
        return "deep"
    if kind is dict:
        items = sorted(
            ((_canon(k, _depth + 1), _canon(v, _depth + 1)) for k, v in dict.items(obj)),
            key=repr,
        )
        return ("dict", items)
    if kind in (list, tuple):
        return (
            kind.__name__,
            [
                _canon(v, _depth + 1)
                for v in (list.__iter__(obj) if kind is list else tuple.__iter__(obj))
            ],
        )
    if kind is int:
        return ("int", hex(obj))
    if kind in (str, float, bool, bytes, type(None)):
        return (kind.__name__, repr(obj))
    return ("other", kind.__name__)


def _run(engine_cls, state, ledger, request):
    """One apply on deep copies; returns the comparable outcome tuple plus
    the post-call canonical request (the engine must not touch it)."""
    state, ledger, request = (
        copy.deepcopy(state),
        copy.deepcopy(ledger),
        copy.deepcopy(request),
    )
    try:
        result = engine_cls(HONEST).apply(state, ledger, request)
        return (
            ("ok", result["outcome"], _canon(result["receipt"]), _canon(state), _canon(ledger)),
            _canon(request),
        )
    except Exception as error:  # noqa: BLE001 - the oracle boundary is the class name
        return (
            (
                type(error).__name__,
                getattr(error, "failure_class", None),
                getattr(error, "code", None),
                _canon(state),
                _canon(ledger),
            ),
            _canon(request),
        )


def _same(state, ledger, request):
    """Production and the reference must agree exactly; inputs stay canonical."""
    before = (_canon(state), _canon(ledger), _canon(request))
    prod, prod_req = _run(PROD, state, ledger, request)
    ref, ref_req = _run(REF, state, ledger, request)
    assert prod == ref, (prod, ref)
    assert prod_req == before[2] and ref_req == before[2]
    if prod[0] != "ok":
        assert prod[3] == before[0] and prod[4] == before[1]
        assert ref[3] == before[0] and ref[4] == before[1]
    return prod


# ---------- compositions ----------


def _req(key, op, **payload):
    return {"idempotency_key": key, "op": op, "payload": payload}


def _composition():
    """A valid non-trivial composition: four enqueued jobs, one leased to
    w1 until 150, and the matching five-receipt ledger, built honestly
    through the production engine."""
    state, ledger = {"jobs": [], "next_seq": 0}, []
    engine = JobsIdempotencyEngine(HONEST)
    for i in range(4):
        engine.apply(
            state,
            ledger,
            _req(f"enq-{i}", "enqueue", dedupe_key=f"k{i}", priority=3, payload={"i": i}),
        )
    engine.apply(state, ledger, _req("claim-1", "claim", worker="w1", now=10, lease_ms=140))
    return state, ledger


VALID = {
    "enqueue": _req("fresh-1", "enqueue", dedupe_key="k9", priority=2, payload={"a": 1}),
    "claim": _req("fresh-2", "claim", worker="w2", now=20, lease_ms=10),
    "ack": _req("fresh-3", "ack", worker="w1", now=100, job_id=job_id_for("k0")),
    "nack": _req("fresh-4", "nack", worker="w1", now=100, job_id=job_id_for("k0")),
}

HOSTILE = (
    None,
    True,
    False,
    -1,
    2**70,
    10**5000,
    1.5,
    float("nan"),
    float("inf"),
    "",
    "x" * 300,
    "\ud800",
    b"bytes",
    [],
    {},
    (1, 2),
    {1, 2},
    object(),
)


# ---------- hostile request fuzz (malformed) ----------


@pytest.mark.parametrize("field", ["idempotency_key", "op", "payload"])
def test_every_request_field_rejects_or_matches_oracle_on_hostile_values(field):
    state, ledger = _composition()
    for template in VALID.values():
        for bad in HOSTILE:
            request = copy.deepcopy(template)
            request[field] = bad
            result = _same(state, ledger, request)
            if field != "payload":
                assert result[:2] == ("IdempotencyError", "malformed_idempotency_request")


@pytest.mark.parametrize("request_value", HOSTILE, ids=range(len(HOSTILE)))
def test_non_dict_requests_are_malformed_and_total(request_value):
    state, ledger = _composition()
    result = _same(state, ledger, request_value)
    assert result[:2] == ("IdempotencyError", "malformed_idempotency_request")


def test_missing_and_extra_request_fields_are_malformed():
    state, ledger = _composition()
    for template in VALID.values():
        for field in ("idempotency_key", "op", "payload"):
            request = copy.deepcopy(template)
            del request[field]
            result = _same(state, ledger, request)
            assert result[:2] == ("IdempotencyError", "malformed_idempotency_request")
        request = {**template, "extra": 1}
        result = _same(state, ledger, request)
        assert result[:2] == ("IdempotencyError", "malformed_idempotency_request")


def test_unknown_op_and_op_smuggled_in_payload_are_malformed():
    state, ledger = _composition()
    result = _same(state, ledger, _req("k", "bogus", worker="w1", now=1))
    assert result[:2] == ("IdempotencyError", "malformed_idempotency_request")
    smuggled = _req("k", "ack", worker="w1", now=1, job_id=job_id_for("k0"))
    smuggled["payload"] = {"worker": "w1", "now": 1, "job_id": job_id_for("k0"), "op": "ack"}
    result = _same(state, ledger, smuggled)
    assert result[:2] == ("IdempotencyError", "malformed_idempotency_request")


# ---------- hostile ledger fuzz (malformed / rollback) ----------


def _ledger_mutations(rng, ledger):
    ledger = copy.deepcopy(ledger)
    choice = rng.randrange(9)
    if choice == 0:
        return rng.choice(HOSTILE)  # not a list at all
    if choice == 1:
        rng.choice(ledger)[rng.choice(RECEIPT_FIELDS)] = rng.choice(HOSTILE)
    elif choice == 2:
        del rng.choice(ledger)[rng.choice(RECEIPT_FIELDS)]
    elif choice == 3:
        rng.choice(ledger)["extra"] = 1
    elif choice == 4:
        ledger[rng.randrange(len(ledger))]["sequence"] += rng.choice((1, -1, 100))
    elif choice == 5:
        ledger[1]["idempotency_key"] = ledger[0]["idempotency_key"]  # duplicate key
    elif choice == 6:
        r = rng.choice(ledger)
        r["receipt_id"] = "jir1:" + "0" * 64  # tampered id
    elif choice == 7:
        ledger.reverse()  # chain and sequence break
    else:
        ledger.pop()  # stale composition: ledger no longer names the live state
    return ledger


@pytest.mark.parametrize("seed", range(40))
def test_hostile_ledgers_match_oracle_and_never_mutate_on_reject(seed):
    rng = random.Random(7000 + seed)
    state, ledger = _composition()
    for _ in range(25):
        broken = _ledger_mutations(rng, ledger)
        request = copy.deepcopy(VALID[rng.choice(sorted(VALID))])
        result = _same(state, broken, request)
        if result[0] != "ok":
            assert result[0] in ("IdempotencyError", "QueueError")


def test_stale_compositions_reject_corrupt_ledger_then_repaired_accepts():
    """Rollback: the live state moved past the ledger's last receipt."""
    state, ledger = _composition()
    for cut in range(1, len(ledger)):
        stale = ledger[: len(ledger) - cut]
        result = _same(state, stale, VALID["enqueue"])
        assert result[:2] == ("IdempotencyError", "corrupt_ledger"), cut
    # the intact composition still accepts, on both engines
    result = _same(state, ledger, VALID["enqueue"])
    assert result[0] == "ok"


def test_empty_ledger_is_a_genesis_boundary_on_both_engines():
    """Detection boundary, pinned as implemented: a ledger truncated to
    empty has no terminal receipt to chain against, so both engines treat
    it as genesis and accept against a moved state. Truncation that keeps
    at least one receipt whose state_id no longer matches the live digest
    rejects corrupt_ledger; truncation of a state-neutral suffix or the
    whole ledger is undetectable. No stronger rollback guarantee is
    claimed."""
    state, ledger = _composition()
    assert _same(state, [], VALID["claim"])[0] == "ok"


# ---------- hostile state fuzz (corrupt_queue through the linked contract) ----------


def _state_mutations(rng, state):
    state = copy.deepcopy(state)
    jobs = state["jobs"]
    choice = rng.randrange(6)
    if choice == 0:
        state[rng.choice(("jobs", "next_seq"))] = rng.choice(HOSTILE)
    elif choice == 1:
        rng.choice(jobs)[
            rng.choice(
                (
                    "job_id",
                    "seq",
                    "priority",
                    "payload",
                    "status",
                    "attempts",
                    "lease_owner",
                    "lease_expires_at",
                )
            )
        ] = rng.choice(HOSTILE)
    elif choice == 2:
        del rng.choice(jobs)[
            rng.choice(
                (
                    "job_id",
                    "seq",
                    "priority",
                    "payload",
                    "status",
                    "attempts",
                    "lease_owner",
                    "lease_expires_at",
                )
            )
        ]
    elif choice == 3:
        jobs[rng.randrange(len(jobs))] = rng.choice(HOSTILE)
    elif choice == 4:
        jobs.append(copy.deepcopy(rng.choice(jobs)))
    else:
        rng.shuffle(jobs)
    return state


@pytest.mark.parametrize("seed", range(30))
def test_hostile_states_match_oracle_and_never_mutate_on_reject(seed):
    rng = random.Random(3000 + seed)
    state, ledger = _composition()
    for _ in range(25):
        broken = _state_mutations(rng, state)
        request = copy.deepcopy(VALID[rng.choice(sorted(VALID))])
        # the ledger may or may not still chain to the mutated state, but
        # state validation runs first: corrupt_queue or agreement either way
        result = _same(state, broken, request)
        if result[0] != "ok":
            assert result[0] in ("IdempotencyError", "QueueError")


# ---------- fault injection: caller code never runs on hostile objects ----------


class Boom:
    def __getattribute__(self, name):
        raise AssertionError("production touched caller object: " + name)


class BoomDict(dict):
    def __iter__(self):
        raise AssertionError("iter hook ran")

    def items(self):
        raise AssertionError("items hook ran")

    def __eq__(self, other):
        raise AssertionError("eq hook ran")

    __hash__ = None


class BoomList(list):
    def __iter__(self):
        raise AssertionError("iter hook ran")

    def __len__(self):
        raise AssertionError("len hook ran")


class BoomStr(str):
    def encode(self, *a, **k):
        raise AssertionError("encode hook ran")

    def __eq__(self, other):
        raise AssertionError("eq hook ran")

    __hash__ = str.__hash__


def _nested(depth):
    node = 1
    for _ in range(depth):
        node = [node]
    return node


PAYLOAD_FACTORIES = (
    lambda: Boom(),
    lambda: BoomDict(a=1),
    lambda: BoomList([1]),
    lambda: {"a": Boom()},
    lambda: {"a": [BoomList()]},
    lambda: {BoomStr("k"): 1},
    lambda: {"a": float("nan")},
    lambda: {"a": b"x"},
    lambda: {1: "int key"},
    lambda: _nested(MAX_DEPTH + 1),
)


@pytest.mark.parametrize("make", PAYLOAD_FACTORIES, ids=range(len(PAYLOAD_FACTORIES)))
def test_faulting_or_hostile_payloads_rejected_without_running_caller_code(make):
    state, ledger = _composition()
    snap = (_canon(state), _canon(ledger))
    request = _req("fresh-p", "enqueue", dedupe_key="kx", priority=1, payload=make())
    with pytest.raises(IdempotencyError) as info:
        JobsIdempotencyEngine(HONEST).apply(state, ledger, request)
    assert info.value.failure_class == "malformed_idempotency_request"
    assert (_canon(state), _canon(ledger)) == snap


@pytest.mark.parametrize("make", PAYLOAD_FACTORIES, ids=range(len(PAYLOAD_FACTORIES)))
def test_faulting_state_payloads_rejected_without_running_caller_code(make):
    state, ledger = _composition()
    state["jobs"][0]["payload"] = make()
    with pytest.raises(IdempotencyError) as info:
        JobsIdempotencyEngine(HONEST).apply(state, ledger, VALID["claim"])
    assert info.value.failure_class in ("corrupt_queue", "corrupt_ledger")


def test_faulting_ledger_receipt_rejected_without_running_caller_code():
    state, ledger = _composition()
    ledger[0] = BoomDict(ledger[0])
    with pytest.raises(IdempotencyError) as info:
        JobsIdempotencyEngine(HONEST).apply(state, ledger, VALID["claim"])
    assert info.value.failure_class == "corrupt_ledger"


# ---------- fault injection: hostile fingerprint oracles fail closed ----------


def _oracle_raising(exc_factory):
    def oracle(op, payload):
        raise exc_factory()

    return oracle


@pytest.mark.parametrize(
    "oracle",
    [
        _oracle_raising(lambda: KeyboardInterrupt("k")),
        _oracle_raising(lambda: SystemExit("s")),
        _oracle_raising(lambda: GeneratorExit("g")),
        _oracle_raising(lambda: RuntimeError("r")),
        lambda op, payload: 42,
        lambda op, payload: "not-a-fingerprint",
        lambda op, payload: "jif1:" + "0" * 64,  # well-formed but unbound
        lambda op, payload: "jif1:" + "\ud800" * 64,
    ],
    ids=range(8),
)
def test_hostile_oracles_fail_closed_on_both_engines(oracle):
    for engine_cls in (PROD, REF):
        state, ledger = _composition()
        request = copy.deepcopy(VALID["enqueue"])
        snap = (_canon(state), _canon(ledger), _canon(request))
        with pytest.raises(Exception) as info:  # noqa: BLE001 - class pinned next line
            engine_cls(oracle).apply(state, ledger, request)
        assert type(info.value).__name__ == "IdempotencyError"
        assert info.value.failure_class == "divergent_fingerprint"
        assert info.value.__cause__ is None
        assert (_canon(state), _canon(ledger), _canon(request)) == snap


def test_argument_mutating_oracle_is_inert_on_both_engines():
    outcomes = []
    for engine_cls in (PROD, REF):
        state, ledger = _composition()
        request = copy.deepcopy(VALID["enqueue"])

        def mutator(op, payload):
            payload.clear()
            return HONEST(op, payload)

        outcomes.append(_run(engine_cls, state, ledger, request)[0])
    assert outcomes[0] == outcomes[1]


def test_closure_mutating_oracle_cannot_corrupt_inputs_on_either_engine():
    finals = []
    for engine_cls in (PROD, REF):
        state, ledger = _composition()
        request = copy.deepcopy(VALID["enqueue"])
        before_req = _canon(request)

        def closure_hostile(op, payload, _s=state, _l=ledger, _r=request):
            _s.clear()
            _l.clear()
            _r["idempotency_key"] = "rewritten"
            return HONEST(op, payload)

        result = engine_cls(closure_hostile).apply(state, ledger, request)
        assert result["outcome"] == "applied"
        assert _canon(request) == before_req
        finals.append((_canon(state), _canon(ledger)))
    assert finals[0] == finals[1]


def test_late_internal_fault_leaves_state_ledger_and_request_untouched(monkeypatch):
    class FaultyQueue(QueueEngine):
        def apply(self, state, request):
            super().apply(state, request)
            raise RuntimeError("fault after the staged transition")

    state, ledger = _composition()
    monkeypatch.setattr(_prod_module, "_QUEUE", FaultyQueue())
    request = copy.deepcopy(VALID["enqueue"])
    snap = (_canon(state), _canon(ledger), _canon(request))
    with pytest.raises(RuntimeError):
        JobsIdempotencyEngine(HONEST).apply(state, ledger, request)
    assert (_canon(state), _canon(ledger), _canon(request)) == snap


# ---------- boundary differentials ----------


def test_payload_depth_and_int_boundaries_match_oracle():
    state, ledger = _composition()
    ok = _req("edge-1", "enqueue", dedupe_key="kd", priority=1, payload=_nested(MAX_DEPTH))
    assert _same(state, ledger, ok)[0] == "ok"
    deep = _req("edge-2", "enqueue", dedupe_key="kd", priority=1, payload=_nested(MAX_DEPTH + 1))
    assert _same(state, ledger, deep)[:2] == ("IdempotencyError", "malformed_idempotency_request")
    big = _req("edge-3", "enqueue", dedupe_key="kd", priority=1, payload={"n": 10**4000 - 1})
    assert _same(state, ledger, big)[0] == "ok"
    huge = _req("edge-4", "enqueue", dedupe_key="kd", priority=1, payload={"n": 10**4000})
    assert _same(state, ledger, huge)[:2] == ("IdempotencyError", "malformed_idempotency_request")


def test_key_grammar_boundaries_match_oracle():
    state, ledger = _composition()
    ok = _req("a" + "b" * 127, "enqueue", dedupe_key="kg", priority=1, payload={})
    assert _same(state, ledger, ok)[0] == "ok"
    for bad_key in ("a" * 129, ".x", "", "has space", "é"):
        bad = _req(bad_key, "enqueue", dedupe_key="kg", priority=1, payload={})
        result = _same(state, ledger, bad)
        assert result[:2] == ("IdempotencyError", "malformed_idempotency_request")


# ---------- valid-stream differential fuzz (happy) ----------


@pytest.mark.parametrize("seed", range(30))
def test_valid_idempotent_streams_match_oracle(seed):
    rng = random.Random(1000 + seed)
    prod_state, prod_ledger = {"jobs": [], "next_seq": 0}, []
    now, known = 0, [job_id_for(f"k{i}") for i in range(6)]
    prod_engine = JobsIdempotencyEngine(HONEST)
    for _ in range(120):
        now += rng.choice((0, 0, 1, 3, 50))  # leases lapse as if workers crash
        key = f"k{rng.randrange(8)}" if rng.random() < 0.8 else f"fresh{rng.randrange(10**6)}"
        pick = rng.random()
        if pick < 0.4:
            request = _req(
                key,
                "enqueue",
                dedupe_key=f"k{rng.randrange(6)}",
                priority=rng.choice((1, 1, 2, 5)),
                payload={"n": rng.choice((0, 0, 1))},
            )
        elif pick < 0.65:
            request = _req(
                key,
                "claim",
                worker=rng.choice(("w1", "w2")),
                now=now,
                lease_ms=rng.choice((1, 10, 100)),
            )
        else:
            jid = rng.choice(known) if rng.random() < 0.9 else job_id_for("ghost")
            request = _req(
                key,
                rng.choice(("ack", "nack")),
                worker=rng.choice(("w1", "w2")),
                now=now,
                job_id=jid,
            )
        result = _same(prod_state, prod_ledger, request)
        # keep the driver composition: replay the agreed outcome on the prod side
        if result[0] == "ok":
            assert result[1] in ("applied", "replayed")
            try:
                prod_engine.apply(prod_state, prod_ledger, request)
            except (IdempotencyError, QueueError):
                raise AssertionError("driver diverged from the agreed outcome") from None
        # on a rejection the composition is already unchanged


@pytest.mark.parametrize("seed", range(20))
def test_streams_with_random_rollback_windows_match_oracle(seed):
    """Interleave honest applies with stale-ledger windows: after any raw
    truncation the next apply must reject corrupt_ledger identically, then
    the re-synced composition continues."""
    rng = random.Random(9000 + seed)
    state, ledger = {"jobs": [], "next_seq": 0}, []
    engine = JobsIdempotencyEngine(HONEST)
    now = 0
    for step in range(100):
        now += rng.choice((0, 1, 3, 20))
        if ledger and rng.random() < 0.15:
            cut = rng.randrange(1, len(ledger) + 1)
            stale = ledger[: len(ledger) - cut]
            request = _req(f"rb-{seed}-{step}", "claim", worker="w1", now=now, lease_ms=10)
            result = _same(state, stale, request)
            if not stale:
                # genesis boundary: full truncation is undetectable
                assert result[0] == "ok"
            elif stale[-1]["state_id"] != state_id_for(state):
                # the truncation crosses a state-changing receipt: detectable
                assert result[:2] == ("IdempotencyError", "corrupt_ledger")
            else:
                # the truncated suffix was state-neutral (replay receipts):
                # content-addressed detection cannot see it; pinned boundary
                assert result[0] == "ok"
        key = f"k{rng.randrange(8)}"
        request = _req(
            key,
            "enqueue",
            dedupe_key=f"k{rng.randrange(6)}",
            priority=rng.choice((1, 2, 5)),
            payload={"n": rng.choice((0, 1))},
        )
        result = _same(state, ledger, request)
        if result[0] == "ok":
            engine.apply(state, ledger, request)
