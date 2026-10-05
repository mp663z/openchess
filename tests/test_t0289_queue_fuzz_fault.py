"""T0289: seeded fuzz and fault battery for the production jobs queue.

The T0284 reference engine is the differential oracle. Valid streams,
hostile requests, hostile states and faulting payload objects are driven
through server.jobs_queue.QueueEngine and the reference. Both must return
the same receipt or failure class and leave the same state. A rejected
call must leave state and request byte-identical, and production must never
run caller code (hash, eq, repr, iteration hooks) on hostile objects.
Seeded and single-threaded.
"""

from __future__ import annotations

import copy
import random

import pytest

from server.jobs_queue import MAX_ATTEMPTS, MAX_DEPTH, QueueEngine, QueueError, job_id_for
from tests import test_t0284_queue_contract as _reference

PROD, REF = QueueEngine, _reference.QueueEngine
JOB_FIELDS = (
    "job_id",
    "seq",
    "priority",
    "payload",
    "status",
    "attempts",
    "lease_owner",
    "lease_expires_at",
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


def _run(engine_cls, state, request):
    state = copy.deepcopy(state)
    try:
        receipt = engine_cls().apply(state, request)
        return ("ok", receipt, _canon(state))
    except Exception as error:  # noqa: BLE001 - the oracle boundary is the class name
        return (type(error).__name__, getattr(error, "failure_class", None), _canon(state))


def _same(state, request):
    prod, ref = _run(PROD, state, request), _run(REF, state, request)
    assert prod[0] == ref[0] or {prod[0], ref[0]} <= {"QueueError"}, (prod, ref)
    assert prod[1] == ref[1], (prod, ref)
    assert prod[2] == ref[2], (prod, ref)
    return prod


def _job(i, **kw):
    job = {
        "job_id": job_id_for(f"k{i}"),
        "seq": i,
        "priority": 3,
        "payload": {"i": i},
        "status": "ready",
        "attempts": 0,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    job.update(kw)
    return job


def _state(n=4):
    return {"jobs": [_job(i) for i in range(n)], "next_seq": n}


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

VALID = {
    "enqueue": {"op": "enqueue", "dedupe_key": "k9", "priority": 2, "payload": {"a": 1}},
    "claim": {"op": "claim", "worker": "w1", "now": 5, "lease_ms": 10},
    "ack": {"op": "ack", "worker": "w1", "now": 5, "job_id": job_id_for("k0")},
    "nack": {"op": "nack", "worker": "w1", "now": 5, "job_id": job_id_for("k0")},
}


def _leased_state():
    state = _state()
    state["jobs"][0].update(status="leased", attempts=1, lease_owner="w1", lease_expires_at=50)
    return state


# ---------- hostile request fuzz ----------


@pytest.mark.parametrize("op", sorted(VALID))
def test_every_field_of_every_op_rejects_or_matches_oracle_on_hostile_values(op):
    state = _leased_state()
    for field in VALID[op]:
        for bad in HOSTILE:
            request = copy.deepcopy(VALID[op])
            request[field] = bad
            snap = _canon(state)
            result = _same(state, request)
            if result[0] != "ok":
                assert result[2] == snap


@pytest.mark.parametrize("op", sorted(VALID))
def test_missing_and_extra_request_fields_are_malformed(op):
    state = _leased_state()
    for field in VALID[op]:
        request = copy.deepcopy(VALID[op])
        del request[field]
        assert _same(state, request)[:2] == ("QueueError", "malformed_queue_request")
    request = {**VALID[op], "extra": 1}
    assert _same(state, request)[:2] == ("QueueError", "malformed_queue_request")


@pytest.mark.parametrize("request_value", HOSTILE, ids=range(len(HOSTILE)))
def test_non_dict_requests_are_malformed_and_total(request_value):
    assert _same(_state(), request_value)[:2] == ("QueueError", "malformed_queue_request")


# ---------- hostile state fuzz ----------


def _mutations(rng):
    state = _leased_state()
    choice = rng.randrange(6)
    jobs = state["jobs"]
    if choice == 0:
        state[rng.choice(("jobs", "next_seq"))] = rng.choice(HOSTILE)
    elif choice == 1:
        rng.choice(jobs)[rng.choice(JOB_FIELDS)] = rng.choice(HOSTILE)
    elif choice == 2:
        del rng.choice(jobs)[rng.choice(JOB_FIELDS)]
    elif choice == 3:
        jobs[rng.randrange(len(jobs))] = rng.choice(HOSTILE)
    elif choice == 4:
        jobs.append(copy.deepcopy(rng.choice(jobs)))  # duplicate id and seq
    else:
        rng.shuffle(jobs)
    return state


@pytest.mark.parametrize("seed", range(40))
def test_hostile_states_match_oracle_and_never_mutate_on_reject(seed):
    rng = random.Random(seed)
    for _ in range(25):
        state = _mutations(rng)
        request = copy.deepcopy(VALID[rng.choice(sorted(VALID))])
        snap = _canon(state)
        result = _same(state, request)
        if result[:2] == ("QueueError", "corrupt_queue"):
            assert result[2] == snap


def test_state_corrupt_cases_are_corrupt_queue():
    cases = []
    s = _leased_state()
    s["jobs"][0]["lease_owner"] = None
    cases.append(s)
    s = _state()
    s["jobs"][1]["seq"] = 0  # not strictly ascending
    cases.append(s)
    s = _state()
    s["next_seq"] = 2  # below an existing seq
    cases.append(s)
    s = _state()
    s["jobs"][2]["status"] = "dead"  # dead needs max attempts
    cases.append(s)
    s = _state()
    s["jobs"][0]["status"] = "done"  # done needs an attempt
    cases.append(s)
    s = _state()
    s["jobs"][0]["lease_expires_at"] = 5  # lease fields on a ready job
    cases.append(s)
    s = _state()
    s["jobs"][3]["job_id"] = s["jobs"][0]["job_id"]
    cases.append(s)
    for state in cases:
        assert _same(state, VALID["claim"])[:2] == ("QueueError", "corrupt_queue")


# ---------- fault injection: payload objects that fault if touched ----------


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
def test_faulting_or_hostile_payloads_are_rejected_without_running_caller_code(make):
    payload = make()
    state = _state()
    snap = _canon(state)
    request = {**VALID["enqueue"], "payload": payload}
    with pytest.raises(QueueError) as info:
        QueueEngine().apply(state, request)
    assert info.value.failure_class == "malformed_queue_request"
    assert _canon(state) == snap


def test_payload_at_depth_limit_is_accepted_and_one_deeper_is_not():
    state = _state()
    ok = {**VALID["enqueue"], "payload": _nested(MAX_DEPTH)}
    assert _same(state, ok)[0] == "ok"
    deeper = {**VALID["enqueue"], "payload": _nested(MAX_DEPTH + 1)}
    assert _same(state, deeper)[:2] == ("QueueError", "malformed_queue_request")


def test_cyclic_and_aliased_payloads_are_rejected():
    cycle = []
    cycle.append(cycle)
    shared = [1]
    for payload in (cycle, {"a": shared, "b": shared}):
        request = {**VALID["enqueue"], "payload": payload}
        assert _same(_state(), request)[:2] == ("QueueError", "malformed_queue_request")


def test_aliased_container_across_stored_jobs_is_corrupt_queue():
    state = _state()
    shared = {"x": 1}
    state["jobs"][0]["payload"] = shared
    state["jobs"][1]["payload"] = shared
    assert _same(state, VALID["claim"])[:2] == ("QueueError", "corrupt_queue")


def test_faulting_state_payload_never_runs_caller_code_and_is_corrupt():
    for payload in (Boom(), BoomDict(a=1), BoomList([1])):
        state = _state()
        state["jobs"][0]["payload"] = payload
        with pytest.raises(QueueError) as info:
            QueueEngine().apply(state, VALID["claim"])
        assert info.value.failure_class == "corrupt_queue"


# ---------- fault injection: faults after validation cannot tear state ----------


def test_commit_is_last_a_late_internal_fault_leaves_state_untouched(monkeypatch):
    class Faulty(QueueEngine):
        def _claim(self, work, req):
            super()._claim(work, req)
            raise RuntimeError("fault after mutation of the working copy")

    state = _state()
    snap = _canon(state)
    with pytest.raises(RuntimeError):
        Faulty().apply(state, VALID["claim"])
    assert _canon(state) == snap


def test_receipt_state_id_matches_committed_state_and_is_replayable():
    state = _state()
    receipt = QueueEngine().apply(state, VALID["claim"])
    from server.jobs_queue import state_id_for

    assert receipt["state_id"] == state_id_for(state)
    replay = _state()
    assert QueueEngine().apply(replay, VALID["claim"]) == receipt and replay == state


# ---------- valid-stream differential fuzz with lease and attempt faults ----------


@pytest.mark.parametrize("seed", range(30))
def test_valid_streams_with_crashing_workers_match_oracle(seed):
    rng = random.Random(500 + seed)
    state, now = {"jobs": [], "next_seq": 0}, 0
    for _ in range(120):
        now += rng.choice((0, 1, 9, 200))  # leases lapse as if the worker crashed
        pick = rng.random()
        if pick < 0.3:
            request = {
                "op": "enqueue",
                "dedupe_key": f"k{rng.randrange(6)}",
                "priority": rng.randrange(3),
                "payload": {"n": rng.randrange(2)},
            }
        elif pick < 0.7:
            request = {
                "op": "claim",
                "worker": rng.choice(("w1", "w2")),
                "now": now,
                "lease_ms": rng.choice((1, 10, 100)),
            }
        else:
            request = {
                "op": rng.choice(("ack", "nack")),
                "worker": rng.choice(("w1", "w2")),
                "now": now,
                "job_id": job_id_for(f"k{rng.randrange(7)}"),
            }
        prod = _same(state, request)
        engine_state = copy.deepcopy(state)
        try:
            QueueEngine().apply(engine_state, request)
            state = engine_state
        except QueueError:
            if request["op"] == "claim":  # dead-marking commits even on a null claim
                state = engine_state
        for job in state["jobs"]:
            assert job["attempts"] <= MAX_ATTEMPTS
        assert prod[0] in ("ok", "QueueError")
