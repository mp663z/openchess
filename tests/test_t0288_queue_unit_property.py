"""T0288: unit and seeded property checks of the production jobs queue.

An independent model (a plain list of dicts, no shared code with
server.jobs_queue beyond the public id helpers) is driven with the same
random operation streams as QueueEngine. After every step the engine and
the model must agree on receipt or failure class, and on state. Every
rejected call must leave state and request byte-identical. Seeded, so a
failure replays. This is a single-threaded decision battery, not a
concurrency test.
"""

from __future__ import annotations

import copy
import json
import random

import pytest

from server.jobs_queue import MAX_ATTEMPTS, MAX_JOBS, QueueEngine, QueueError, job_id_for

LEASE = 100
WORKERS = ("w1", "w2")


def _empty():
    return {"jobs": [], "next_seq": 0}


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _enqueue(key, priority=5, payload=None):
    return {
        "op": "enqueue",
        "dedupe_key": key,
        "priority": priority,
        "payload": {} if payload is None else payload,
    }


def _claim(worker="w1", now=0, lease_ms=LEASE):
    return {"op": "claim", "worker": worker, "now": now, "lease_ms": lease_ms}


def _settle(op, worker, now, jid):
    return {"op": op, "worker": worker, "now": now, "job_id": jid}


class Model:
    """Independent reference: ordered list of jobs, rules restated from the contract."""

    def __init__(self):
        self.jobs = []
        self.next_seq = 0

    def state(self):
        return {"jobs": copy.deepcopy(self.jobs), "next_seq": self.next_seq}

    def _find(self, jid):
        for job in self.jobs:
            if job["job_id"] == jid:
                return job
        return None

    def step(self, req):
        """Returns (failure_class, receipt-ish) and mutates only on success."""
        snapshot = (copy.deepcopy(self.jobs), self.next_seq)
        try:
            return None, self._do(req)
        except _Fail as fail:
            if req["op"] != "claim" or not self._dead_marked:
                self.jobs, self.next_seq = snapshot
            return fail.args[0], None

    _dead_marked = False

    def _do(self, req):
        op = req["op"]
        if op == "enqueue":
            jid = job_id_for(req["dedupe_key"])
            job = self._find(jid)
            if job is not None:
                if job["priority"] != req["priority"] or _canon(job["payload"]) != _canon(
                    req["payload"]
                ):
                    raise _Fail("dedupe_conflict")
                return job
            if len(self.jobs) >= MAX_JOBS:
                raise _Fail("capacity_exceeded")
            job = {
                "job_id": jid,
                "seq": self.next_seq,
                "priority": req["priority"],
                "payload": copy.deepcopy(req["payload"]),
                "status": "ready",
                "attempts": 0,
                "lease_owner": None,
                "lease_expires_at": None,
            }
            self.jobs.append(job)
            self.next_seq += 1
            return job
        if op == "claim":
            self._dead_marked = False
            now = req["now"]
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
                    lease_owner=req["worker"],
                    lease_expires_at=now + req["lease_ms"],
                )
                return job
            return None
        job = self._find(req["job_id"])
        if job is None:
            raise _Fail("unknown_job")
        if not (
            job["status"] == "leased"
            and job["lease_owner"] == req["worker"]
            and req["now"] < job["lease_expires_at"]
        ):
            raise _Fail("lease_conflict")
        job.update(
            status="done" if op == "ack" else "ready",
            lease_owner=None,
            lease_expires_at=None,
        )
        return job


class _Fail(Exception):
    pass


def _engine_step(engine, state, req):
    before_state, before_req = _canon(state), _canon(req)
    try:
        receipt = engine.apply(state, req)
    except QueueError as error:
        assert type(error) is QueueError
        if req["op"] != "claim":
            assert _canon(state) == before_state
        assert _canon(req) == before_req
        return error.failure_class, None
    assert _canon(req) == before_req
    return None, receipt


# ---------- unit ----------


def test_enqueue_creates_ready_job_with_derived_id():
    state, engine = _empty(), QueueEngine()
    receipt = engine.apply(state, _enqueue("k1", 3, {"a": 1}))
    assert receipt["job_id"] == job_id_for("k1")
    assert (receipt["status"], receipt["attempts"], receipt["lease_expires_at"]) == (
        "ready",
        0,
        None,
    )
    assert state["next_seq"] == 1 and state["jobs"][0]["payload"] == {"a": 1}


def test_identical_reenqueue_is_idempotent_and_leaves_state_unchanged():
    state, engine = _empty(), QueueEngine()
    first = engine.apply(state, _enqueue("k1", 3, {"a": [1, 2]}))
    snap = _canon(state)
    again = engine.apply(state, _enqueue("k1", 3, {"a": [1, 2]}))
    assert again == first and _canon(state) == snap


@pytest.mark.parametrize("change", [{"priority": 4}, {"payload": {"b": 1}}])
def test_reenqueue_with_different_content_is_dedupe_conflict_and_atomic(change):
    state, engine = _empty(), QueueEngine()
    engine.apply(state, _enqueue("k1", 3, {"a": 1}))
    snap = _canon(state)
    req = {**_enqueue("k1", 3, {"a": 1}), **change}
    with pytest.raises(QueueError) as info:
        engine.apply(state, req)
    assert info.value.failure_class == "dedupe_conflict"
    assert _canon(state) == snap


def test_claim_orders_by_priority_then_seq():
    state, engine = _empty(), QueueEngine()
    for key, pr in (("a", 5), ("b", 2), ("c", 2), ("d", 9)):
        engine.apply(state, _enqueue(key, pr))
    got = [engine.apply(state, _claim(now=0))["job_id"] for _ in range(4)]
    assert got == [job_id_for(k) for k in ("b", "c", "a", "d")]
    assert engine.apply(state, _claim(now=0))["job_id"] is None


def test_claim_on_empty_queue_returns_null_receipt_and_keeps_state():
    state, engine = _empty(), QueueEngine()
    receipt = engine.apply(state, _claim())
    assert receipt["job_id"] is None and receipt["status"] is None
    assert state == _empty()


def test_lease_boundary_is_strict_for_ack_and_claimable_at_expiry():
    state, engine = _empty(), QueueEngine()
    engine.apply(state, _enqueue("a"))
    jid = engine.apply(state, _claim("w1", now=10))["job_id"]
    # at now == expiry the holder has lost the lease
    with pytest.raises(QueueError) as info:
        engine.apply(state, _settle("ack", "w1", 10 + LEASE, jid))
    assert info.value.failure_class == "lease_conflict"
    # one tick before it still acks
    engine.apply(state, _settle("ack", "w1", 10 + LEASE - 1, jid))
    assert state["jobs"][0]["status"] == "done"


def test_expired_lease_is_reclaimed_by_another_worker_and_old_holder_loses():
    state, engine = _empty(), QueueEngine()
    engine.apply(state, _enqueue("a"))
    jid = engine.apply(state, _claim("w1", now=0))["job_id"]
    receipt = engine.apply(state, _claim("w2", now=LEASE))
    assert receipt["job_id"] == jid and receipt["attempts"] == 2
    with pytest.raises(QueueError) as info:
        engine.apply(state, _settle("ack", "w1", LEASE + 1, jid))
    assert info.value.failure_class == "lease_conflict"


def test_wrong_worker_and_unknown_job_are_typed_failures():
    state, engine = _empty(), QueueEngine()
    engine.apply(state, _enqueue("a"))
    jid = engine.apply(state, _claim("w1", now=0))["job_id"]
    with pytest.raises(QueueError) as info:
        engine.apply(state, _settle("nack", "w2", 1, jid))
    assert info.value.failure_class == "lease_conflict"
    with pytest.raises(QueueError) as info:
        engine.apply(state, _settle("ack", "w1", 1, job_id_for("nope")))
    assert info.value.failure_class == "unknown_job"


def test_nack_returns_job_to_ready_and_keeps_attempts():
    state, engine = _empty(), QueueEngine()
    engine.apply(state, _enqueue("a"))
    jid = engine.apply(state, _claim("w1", now=0))["job_id"]
    receipt = engine.apply(state, _settle("nack", "w1", 1, jid))
    assert (receipt["status"], receipt["attempts"]) == ("ready", 1)
    assert engine.apply(state, _claim("w1", now=2))["attempts"] == 2


def test_job_goes_dead_after_max_attempts_and_is_never_claimed_again():
    state, engine = _empty(), QueueEngine()
    engine.apply(state, _enqueue("a"))
    now = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        receipt = engine.apply(state, _claim("w1", now=now))
        assert receipt["attempts"] == attempt
        now += LEASE
    assert engine.apply(state, _claim("w1", now=now))["job_id"] is None
    job = state["jobs"][0]
    assert job["status"] == "dead" and job["attempts"] == MAX_ATTEMPTS
    assert job["lease_owner"] is None and job["lease_expires_at"] is None
    assert engine.apply(state, _claim("w1", now=now + 10**6))["job_id"] is None


def test_capacity_exceeded_at_max_jobs_but_existing_key_still_idempotent():
    state, engine = _empty(), QueueEngine()
    state["jobs"] = [
        {
            "job_id": job_id_for(f"k{i}"),
            "seq": i,
            "priority": 5,
            "payload": {},
            "status": "ready",
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
        }
        for i in range(MAX_JOBS)
    ]
    state["next_seq"] = MAX_JOBS
    snap = _canon(state)
    with pytest.raises(QueueError) as info:
        engine.apply(state, _enqueue("overflow"))
    assert info.value.failure_class == "capacity_exceeded" and _canon(state) == snap
    engine.apply(state, _enqueue("k0"))
    assert _canon(state) == snap


def test_malformed_request_is_checked_before_corrupt_state():
    engine = QueueEngine()
    bad_state = {"jobs": "x", "next_seq": 0}
    with pytest.raises(QueueError) as info:
        engine.apply(bad_state, {"op": "bogus"})
    assert info.value.failure_class == "malformed_queue_request"
    with pytest.raises(QueueError) as info:
        engine.apply(bad_state, _enqueue("a"))
    assert info.value.failure_class == "corrupt_queue"


def test_payload_and_receipt_are_detached_from_caller_objects():
    state, engine = _empty(), QueueEngine()
    payload = {"a": [1, {"b": 2}]}
    engine.apply(state, _enqueue("a", payload=payload))
    payload["a"][1]["b"] = 99
    assert state["jobs"][0]["payload"] == {"a": [1, {"b": 2}]}


# ---------- seeded properties ----------


def _random_request(rng, now, known):
    pick = rng.random()
    if pick < 0.35:
        key = f"k{rng.randrange(8)}"
        return _enqueue(key, rng.choice((1, 1, 2, 5)), {"n": rng.choice((0, 0, 1))})
    if pick < 0.65:
        return _claim(rng.choice(WORKERS), now, rng.choice((1, 5, LEASE)))
    jid = rng.choice(known) if known and rng.random() < 0.9 else job_id_for("ghost")
    return _settle(rng.choice(("ack", "nack")), rng.choice(WORKERS), now, jid)


@pytest.mark.parametrize("seed", range(40))
def test_engine_matches_independent_model_on_random_streams(seed):
    rng, engine, state, model = random.Random(seed), QueueEngine(), _empty(), Model()
    now, known = 0, [job_id_for(f"k{i}") for i in range(8)]
    for _ in range(150):
        now += rng.choice((0, 0, 1, 3, 50))
        req = _random_request(rng, now, known)
        want_fail, want = model.step(copy.deepcopy(req))
        got_fail, receipt = _engine_step(engine, state, req)
        assert got_fail == want_fail, (seed, req)
        if want_fail is None:
            assert receipt["job_id"] == (None if want is None else want["job_id"])
            if want is not None:
                assert receipt["status"] == want["status"]
                assert receipt["attempts"] == want["attempts"]
        assert state == model.state(), (seed, req)


@pytest.mark.parametrize("seed", range(20))
def test_state_invariants_hold_after_every_random_step(seed):
    rng, engine, state = random.Random(1000 + seed), QueueEngine(), _empty()
    now, known = 0, [job_id_for(f"k{i}") for i in range(8)]
    for _ in range(120):
        now += rng.choice((0, 1, 7, 60))
        _engine_step(engine, state, _random_request(rng, now, known))
        seqs = [j["seq"] for j in state["jobs"]]
        assert seqs == sorted(set(seqs)) and all(s < state["next_seq"] for s in seqs)
        assert len({j["job_id"] for j in state["jobs"]}) == len(state["jobs"])
        for job in state["jobs"]:
            assert job["attempts"] <= MAX_ATTEMPTS
            leased = job["status"] == "leased"
            assert (job["lease_owner"] is not None) == leased
            assert (job["lease_expires_at"] is not None) == leased
            if job["status"] == "dead":
                assert job["attempts"] == MAX_ATTEMPTS


@pytest.mark.parametrize("seed", range(10))
def test_replaying_the_same_stream_is_deterministic(seed):
    def run():
        rng, engine, state = random.Random(seed), QueueEngine(), _empty()
        now, known, out = 0, [job_id_for(f"k{i}") for i in range(8)], []
        for _ in range(100):
            now += rng.choice((0, 1, 20))
            out.append(_engine_step(engine, state, _random_request(rng, now, known)))
        return out, _canon(state)

    assert run() == run()


@pytest.mark.parametrize("seed", range(10))
def test_enqueue_is_idempotent_under_random_repeats(seed):
    rng, engine, state = random.Random(seed), QueueEngine(), _empty()
    reqs = [_enqueue(f"k{i}", rng.randrange(10), {"i": i}) for i in range(10)]
    for req in reqs:
        engine.apply(state, req)
    snap = _canon(state)
    for _ in range(50):
        engine.apply(state, copy.deepcopy(rng.choice(reqs)))
    assert _canon(state) == snap
