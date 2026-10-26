"""T0297: unit and seeded property checks of the production jobs idempotency engine.

An independent model (a dict of key to receipt plus the queue rules,
restated from the contracts - no shared code with server.jobs_idempotency
or server.jobs_queue beyond the public id helpers job_id_for,
state_id_for, request_fingerprint and derive_receipt_id) is driven with
the same random idempotent-apply streams as JobsIdempotencyEngine. After
every step the engine and the model agree on outcome, receipt or failure
class AND error layer (idempotency vs propagated queue), and on state
and ledger byte-for-byte. Every rejected call leaves state, ledger and
request byte-identical. Seeded, so a failure replays. This is a
single-threaded decision battery, not a concurrency test.
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.jobs_idempotency import (  # noqa: E402
    IdempotencyError,
    JobsIdempotencyEngine,
    QueueError,
    derive_receipt_id,
    request_fingerprint,
)
from server.jobs_queue import MAX_ATTEMPTS, MAX_JOBS, job_id_for, state_id_for  # noqa: E402

LEASE = 100
WORKERS = ("w1", "w2")


def _empty():
    return {"jobs": [], "next_seq": 0}


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _req(key, op, **payload):
    return {"idempotency_key": key, "op": op, "payload": payload}


def _enqueue(key, dedupe_key, priority=5, payload=None):
    return _req(
        key,
        "enqueue",
        dedupe_key=dedupe_key,
        priority=priority,
        payload={} if payload is None else payload,
    )


def _claim(key, worker="w1", now=0, lease_ms=LEASE):
    return _req(key, "claim", worker=worker, now=now, lease_ms=lease_ms)


def _settle(key, op, worker, now, jid):
    return _req(key, op, worker=worker, now=now, job_id=jid)


class _Fail(Exception):
    """Restated contract failure: args[0] is the layer, args[1] the class."""


class Model:
    """Independent reference: jobs + receipt ledger, rules restated from
    the jobs-queue and jobs-idempotency contracts."""

    def __init__(self):
        self.jobs = []
        self.next_seq = 0
        self.ledger = []

    def state(self):
        return {"jobs": copy.deepcopy(self.jobs), "next_seq": self.next_seq}

    def _find(self, jid):
        for job in self.jobs:
            if job["job_id"] == jid:
                return job
        return None

    def step(self, req):
        """Returns (layer, failure_class, receipt-ish); mutates only on commit."""
        snapshot = (copy.deepcopy(self.jobs), self.next_seq, copy.deepcopy(self.ledger))
        try:
            return None, None, self._do(req)
        except _Fail as fail:
            self.jobs, self.next_seq, self.ledger = snapshot
            return fail.args[0], fail.args[1], None

    def _do(self, req):
        key = req["idempotency_key"]
        fp = request_fingerprint(req["op"], req["payload"])
        for stored in self.ledger:
            if stored["idempotency_key"] == key:
                if stored["request_fingerprint"] != fp:
                    raise _Fail("idem", "key_conflict")
                return dict(stored)
        self._queue_op(req)
        new_state = {"jobs": copy.deepcopy(self.jobs), "next_seq": self.next_seq}
        receipt = {
            "receipt_id": derive_receipt_id(
                key,
                fp,
                state_id_for(self._prior_state),
                state_id_for(new_state),
                len(self.ledger),
            ),
            "idempotency_key": key,
            "request_fingerprint": fp,
            "prior_state_id": state_id_for(self._prior_state),
            "state_id": state_id_for(new_state),
            "sequence": len(self.ledger),
        }
        self.ledger.append(receipt)
        return dict(receipt)

    @property
    def _prior_state(self):
        return self.__dict__["_prior_snapshot"]

    def _queue_op(self, req):
        self.__dict__["_prior_snapshot"] = self.state()
        op, p = req["op"], req["payload"]
        if op == "enqueue":
            jid = job_id_for(p["dedupe_key"])
            job = self._find(jid)
            if job is not None:
                if job["priority"] != p["priority"] or _canon(job["payload"]) != _canon(
                    p["payload"]
                ):
                    raise _Fail("queue", "dedupe_conflict")
                return job
            if len(self.jobs) >= MAX_JOBS:
                raise _Fail("queue", "capacity_exceeded")
            job = {
                "job_id": jid,
                "seq": self.next_seq,
                "priority": p["priority"],
                "payload": copy.deepcopy(p["payload"]),
                "status": "ready",
                "attempts": 0,
                "lease_owner": None,
                "lease_expires_at": None,
            }
            self.jobs.append(job)
            self.next_seq += 1
            return job
        if op == "claim":
            now = p["now"]
            cands = sorted(
                (
                    j
                    for j in self.jobs
                    if j["status"] == "ready"
                    or (j["status"] == "leased" and j["lease_expires_at"] <= now)
                ),
                key=lambda j: (j["priority"], j["seq"]),
            )
            for job in cands:
                if job["attempts"] >= MAX_ATTEMPTS:
                    job.update(status="dead", lease_owner=None, lease_expires_at=None)
                    continue
                job.update(
                    status="leased",
                    attempts=job["attempts"] + 1,
                    lease_owner=p["worker"],
                    lease_expires_at=now + p["lease_ms"],
                )
                return job
            return None
        job = self._find(p["job_id"])
        if job is None:
            raise _Fail("queue", "unknown_job")
        if not (
            job["status"] == "leased"
            and job["lease_owner"] == p["worker"]
            and p["now"] < job["lease_expires_at"]
        ):
            raise _Fail("queue", "lease_conflict")
        job.update(
            status="done" if op == "ack" else "ready",
            lease_owner=None,
            lease_expires_at=None,
        )
        return job


def _engine_step(engine, state, ledger, req):
    before = (_canon(state), _canon(ledger), _canon(req))
    try:
        result = engine.apply(state, ledger, req)
    except IdempotencyError as error:
        assert type(error) is IdempotencyError
        assert error.__cause__ is None
        assert (_canon(state), _canon(ledger), _canon(req)) == before
        return "idem", error.failure_class, None
    except QueueError as error:
        assert type(error) is QueueError
        assert error.__cause__ is None and error.__context__ is None
        assert (_canon(state), _canon(ledger), _canon(req)) == before
        return "queue", error.failure_class, None
    assert _canon(req) == before[2]
    assert set(dict.keys(result)) == {"outcome", "receipt"}
    return None, None, result


# ---------- unit ----------


def test_first_apply_commits_transition_and_receipt_together():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    req = _enqueue("k1", "task-a", 3, {"a": 1})
    result = engine.apply(state, ledger, req)
    assert result["outcome"] == "applied"
    receipt = result["receipt"]
    assert tuple(dict.keys(receipt)) == (
        "receipt_id",
        "idempotency_key",
        "request_fingerprint",
        "prior_state_id",
        "state_id",
        "sequence",
    )
    assert receipt["idempotency_key"] == "k1"
    assert receipt["request_fingerprint"] == request_fingerprint("enqueue", req["payload"])
    assert receipt["prior_state_id"] == state_id_for(_empty())
    assert receipt["state_id"] == state_id_for(state)
    assert receipt["sequence"] == 0
    assert receipt["receipt_id"] == derive_receipt_id(
        "k1",
        receipt["request_fingerprint"],
        receipt["prior_state_id"],
        receipt["state_id"],
        0,
    )
    assert ledger == [receipt] and len(state["jobs"]) == 1


def test_replay_returns_equal_fresh_receipt_and_commits_nothing():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    req = _enqueue("k1", "task-a", 3, {"a": [1, 2]})
    first = engine.apply(state, ledger, req)["receipt"]
    snap = (_canon(state), _canon(ledger))
    again = engine.apply(state, ledger, req)
    assert again["outcome"] == "replayed"
    assert again["receipt"] == first
    assert again["receipt"] is not ledger[0]
    assert (_canon(state), _canon(ledger)) == snap


def test_same_key_different_payload_is_key_conflict_and_atomic():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    engine.apply(state, ledger, _enqueue("k1", "task-a", 3, {"a": 1}))
    snap = (_canon(state), _canon(ledger))
    with pytest.raises(IdempotencyError) as info:
        engine.apply(state, ledger, _enqueue("k1", "task-b", 3, {"a": 1}))
    assert info.value.failure_class == "key_conflict"
    assert (_canon(state), _canon(ledger)) == snap


def test_second_key_chains_prior_state_and_sequence():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    first = engine.apply(state, ledger, _enqueue("k1", "task-a"))["receipt"]
    second = engine.apply(state, ledger, _enqueue("k2", "task-b"))["receipt"]
    assert second["sequence"] == 1
    assert second["prior_state_id"] == first["state_id"]
    assert second["state_id"] == state_id_for(state)


def test_stale_ledger_composition_is_corrupt_ledger():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    engine.apply(state, ledger, _enqueue("k1", "task-a"))
    stale = [dict(ledger[0])]
    engine.apply(state, ledger, _enqueue("k2", "task-b"))
    snap = (_canon(state), _canon(stale))
    with pytest.raises(IdempotencyError) as info:
        engine.apply(state, stale, _enqueue("k3", "task-c"))
    assert info.value.failure_class == "corrupt_ledger"
    assert (_canon(state), _canon(stale)) == snap


def test_queue_rejection_propagates_unwrapped_and_retry_commits():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    ghost = _settle("a1", "ack", "w1", 1, job_id_for("ghost"))
    with pytest.raises(QueueError) as info:
        engine.apply(state, ledger, ghost)
    assert type(info.value) is QueueError
    assert info.value.failure_class == "unknown_job"
    assert info.value.__cause__ is None and info.value.__context__ is None
    assert ledger == [] and state == _empty()
    # no phantom receipt: the same key retries as a fresh apply
    engine.apply(state, ledger, _enqueue("a1", "task-a"))
    assert ledger[0]["idempotency_key"] == "a1"


def test_oracle_never_called_on_invalid_inputs():
    calls = []

    def counting(op, payload):
        calls.append((op, payload))
        return request_fingerprint(op, payload)

    engine = JobsIdempotencyEngine(counting)
    for state, ledger, req in (
        (_empty(), [], {"idempotency_key": 1, "op": "enqueue", "payload": {}}),
        ({"jobs": "x", "next_seq": 0}, [], _enqueue("k1", "task-a")),
        (_empty(), [{"bad": "receipt"}], _enqueue("k1", "task-a")),
    ):
        with pytest.raises(IdempotencyError):
            engine.apply(state, ledger, req)
    assert calls == []


def test_oracle_raising_baseexception_fails_closed():
    state, ledger = _empty(), []
    req = _enqueue("k1", "task-a", 3, {"a": [1, 2]})
    before = (_canon(state), _canon(ledger), _canon(req))

    def hostile(op, payload):
        raise KeyboardInterrupt("hostile")

    engine = JobsIdempotencyEngine(hostile)
    with pytest.raises(IdempotencyError) as info:
        engine.apply(state, ledger, req)
    assert info.value.failure_class == "divergent_fingerprint"
    assert (_canon(state), _canon(ledger), _canon(req)) == before


def test_oracle_mutating_live_inputs_through_closure_is_restored():
    state, ledger = _empty(), []
    req = _enqueue("k1", "task-a", 3, {"a": [1, 2]})
    before_req = _canon(req)

    def closure_hostile(op, payload):
        state.clear()
        state["jobs"] = "corrupted"
        req["idempotency_key"] = "rewritten"
        req["payload"].clear()
        return request_fingerprint(op, payload)

    engine = JobsIdempotencyEngine(closure_hostile)
    result = engine.apply(state, ledger, req)
    assert result["outcome"] == "applied"
    assert _canon(req) == before_req
    assert len(state["jobs"]) == 1 and state["next_seq"] == 1
    assert ledger[0]["state_id"] == state_id_for(state)


def test_request_sharing_a_container_with_state_is_malformed():
    # the ledger-alias form is pinned by the battery fixtures (ledger
    # receipts are flat scalar dicts; the state carries the shareable graph)
    engine = JobsIdempotencyEngine(request_fingerprint)
    state, ledger = _empty(), []
    engine.apply(state, ledger, _enqueue("k1", "task-a"))
    snap = (_canon(state), _canon(ledger))
    shared = state["jobs"][0]["payload"]
    with pytest.raises(IdempotencyError) as info:
        engine.apply(state, ledger, _enqueue("k2", "task-b", 5, shared))
    assert info.value.failure_class == "malformed_idempotency_request"
    assert (_canon(state), _canon(ledger)) == snap


def test_result_and_stored_receipt_do_not_share_mutable_state():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    result = engine.apply(state, ledger, _enqueue("k1", "task-a"))
    result["receipt"]["idempotency_key"] = "mutated"
    assert ledger[0]["idempotency_key"] == "k1"
    replay = engine.apply(state, ledger, _enqueue("k1", "task-a"))["receipt"]
    replay["sequence"] = 99
    assert ledger[0]["sequence"] == 0


def test_caller_request_containers_keep_identity_and_value():
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    req = _enqueue("k1", "task-a", 3, {"a": [1, {"b": 2}]})
    ids = (
        id(req),
        id(req["payload"]),
        id(req["payload"]["payload"]),
        id(req["payload"]["payload"]["a"]),
    )
    before = _canon(req)
    engine.apply(state, ledger, req)
    assert (
        id(req),
        id(req["payload"]),
        id(req["payload"]["payload"]),
        id(req["payload"]["payload"]["a"]),
    ) == ids
    assert _canon(req) == before
    state["jobs"][0]["payload"]["a"][1]["b"] = 99  # committed state is detached anyway
    assert req["payload"]["payload"]["a"][1]["b"] == 2


def test_validation_order_request_then_state_then_ledger():
    engine = JobsIdempotencyEngine(request_fingerprint)
    with pytest.raises(IdempotencyError) as info:
        engine.apply(
            {"jobs": "x", "next_seq": 0},
            [{"bad": 1}],
            {"idempotency_key": 1, "op": "enqueue", "payload": {}},
        )
    assert info.value.failure_class == "malformed_idempotency_request"
    with pytest.raises(IdempotencyError) as info:
        engine.apply({"jobs": "x", "next_seq": 0}, [{"bad": 1}], _enqueue("k1", "task-a"))
    assert info.value.failure_class == "corrupt_queue"
    with pytest.raises(IdempotencyError) as info:
        engine.apply(_empty(), [{"bad": 1}], _enqueue("k1", "task-a"))
    assert info.value.failure_class == "corrupt_ledger"


def test_determinism_by_value_across_fresh_engines():
    outcomes = []
    for _ in range(2):
        state, ledger = _empty(), []
        engine = JobsIdempotencyEngine(request_fingerprint)
        engine.apply(state, ledger, _enqueue("k1", "task-a", 3, {"a": 1}))
        engine.apply(state, ledger, _claim("k2", "w1", 10))
        outcomes.append((state, ledger))
    assert _canon(outcomes[0]) == _canon(outcomes[1])


def test_key_grammar_edges():
    engine = JobsIdempotencyEngine(request_fingerprint)
    ok = "a" + "b" * 127  # 128 chars, the grammar maximum
    state, ledger = _empty(), []
    assert engine.apply(state, ledger, _enqueue(ok, "task-a"))["outcome"] == "applied"
    for bad in ("a" * 129, ".lead", "", "with space", "é"):
        with pytest.raises(IdempotencyError) as info:
            engine.apply(_empty(), [], _enqueue(bad, "task-a"))
        assert info.value.failure_class == "malformed_idempotency_request"


def test_payload_int_digit_limit_runs_through_the_linked_contract():
    engine = JobsIdempotencyEngine(request_fingerprint)
    state, ledger = _empty(), []
    assert (
        engine.apply(state, ledger, _enqueue("k1", "task-a", 5, {"n": 10**4000 - 1}))["outcome"]
        == "applied"
    )
    with pytest.raises(IdempotencyError) as info:
        engine.apply(_empty(), [], _enqueue("k2", "task-b", 5, {"n": 10**4000}))
    assert info.value.failure_class == "malformed_idempotency_request"


# ---------- seeded properties ----------


def _random_request(rng, now, known):
    pick = rng.random()
    key = f"k{rng.randrange(8)}" if rng.random() < 0.8 else f"fresh{rng.randrange(10**6)}"
    if pick < 0.40:
        return _enqueue(
            key,
            f"task-{rng.randrange(6)}",
            rng.choice((1, 1, 2, 5)),
            {"n": rng.choice((0, 0, 1))},
        )
    if pick < 0.65:
        return _claim(key, rng.choice(WORKERS), now, rng.choice((1, 5, LEASE)))
    jid = rng.choice(known) if known and rng.random() < 0.9 else job_id_for("ghost")
    return _settle(key, rng.choice(("ack", "nack")), rng.choice(WORKERS), now, jid)


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_streams(seed):
    rng = random.Random(seed)
    state, ledger, model = _empty(), [], Model()
    engine = JobsIdempotencyEngine(request_fingerprint)
    now, known = 0, [job_id_for(f"task-{i}") for i in range(6)]
    for _ in range(150):
        now += rng.choice((0, 0, 1, 3, 50))
        req = _random_request(rng, now, known)
        got_layer, got_fail, got_result = _engine_step(engine, state, ledger, req)
        want_layer, want_fail, want_receipt = model.step(req)
        assert (got_layer, got_fail) == (want_layer, want_fail), (seed, req)
        if want_receipt is None and want_fail is None:
            # claim on an empty/idle queue: the queue's null-job receipt is
            # still an applied transition with a receipt
            assert got_result["outcome"] == "applied"
        if got_result is not None:
            assert got_result["receipt"] == want_receipt, (seed, req)
        assert state == model.state(), (seed, req)
        assert ledger == model.ledger, (seed, req)


@pytest.mark.parametrize("seed", range(10))
def test_replaying_the_same_stream_is_deterministic(seed):
    finals = []
    for _ in range(2):
        rng = random.Random(seed)
        state, ledger = _empty(), []
        engine = JobsIdempotencyEngine(request_fingerprint)
        now, known = 0, [job_id_for(f"task-{i}") for i in range(6)]
        for _ in range(100):
            now += rng.choice((0, 0, 1, 3, 50))
            _engine_step(engine, state, ledger, _random_request(rng, now, known))
        finals.append((state, ledger))
    assert _canon(finals[0]) == _canon(finals[1])


@pytest.mark.parametrize("seed", range(20))
def test_ledger_chain_invariants_hold_after_every_step(seed):
    rng = random.Random(5000 + seed)
    state, ledger = _empty(), []
    engine = JobsIdempotencyEngine(request_fingerprint)
    now, known = 0, [job_id_for(f"task-{i}") for i in range(6)]
    for _ in range(100):
        now += rng.choice((0, 0, 1, 3, 50))
        _engine_step(engine, state, ledger, _random_request(rng, now, known))
        for index, receipt in enumerate(ledger):
            assert receipt["sequence"] == index
            assert receipt["receipt_id"] == derive_receipt_id(
                receipt["idempotency_key"],
                receipt["request_fingerprint"],
                receipt["prior_state_id"],
                receipt["state_id"],
                index,
            )
            if index:
                assert receipt["prior_state_id"] == ledger[index - 1]["state_id"]
        if ledger:
            assert ledger[-1]["state_id"] == state_id_for(state)
        assert len({r["idempotency_key"] for r in ledger}) == len(ledger)


@pytest.mark.parametrize("seed", range(10))
def test_hostile_oracle_streams_fail_closed_and_never_commit(seed):
    rng = random.Random(9000 + seed)
    state, ledger = _empty(), []
    honest = JobsIdempotencyEngine(request_fingerprint)
    now, known = 0, [job_id_for(f"task-{i}") for i in range(6)]
    # build a non-trivial composition honestly first
    for _ in range(20):
        now += rng.choice((0, 1, 3))
        _engine_step(honest, state, ledger, _random_request(rng, now, known))
    for _ in range(30):
        now += rng.choice((0, 1, 3))
        req = _random_request(rng, now, known)
        raised = []

        def hostile(op, payload, _r=rng, _raised=raised):
            if _r.random() < 0.5:
                _raised.append(1)
                raise SystemExit("hostile")
            return request_fingerprint(op, payload)

        engine = JobsIdempotencyEngine(hostile)
        layer, failure, result = _engine_step(engine, state, ledger, req)
        if raised:
            # the oracle only runs after request, state and ledger all
            # validate, so a raised call always fails closed as
            # divergent_fingerprint and commits nothing (the atomicity
            # pins live in _engine_step)
            assert (layer, failure) == ("idem", "divergent_fingerprint"), (seed, req)
            assert result is None
        if ledger:
            assert ledger[-1]["state_id"] == state_id_for(state)
