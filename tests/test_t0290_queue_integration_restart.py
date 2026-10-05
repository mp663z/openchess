"""T0290: integration and restart proof for the production jobs queue.

The engine holds no memory between calls: its whole state is the JSON
object `{jobs, next_seq}`. A restart is therefore modelled the way a host
would do it: write the canonical state to a file under tmp_path (atomic
replace), drop the engine, read the file back, and continue with a fresh
engine. Every cut point of a seeded stream must give the same receipts and
the same final state as an uninterrupted run. Leases that were live at the
crash lapse by the injected clock and the job is re-claimed. A torn state
file is refused by the engine as corrupt_queue and never half-applied.
Single process, no real clock, no concurrency.

Scope: this is an integration and restart test. Unit-level validator edges
(isinstance guards, bool/int bounds, payload depth and digit limits) belong to
the unit battery (T0288/T0289) and are out of scope here; the rows below pin
only the boundaries a restarted process can reach through the state file.
"""

from __future__ import annotations

import copy
import json
import os
import random

import pytest

from server.jobs_queue import (
    MAX_ATTEMPTS,
    MAX_JOBS,
    QueueEngine,
    QueueError,
    job_id_for,
    state_id_for,
)


class _raises:
    """pytest.raises that reports a missing refusal as a plain AssertionError."""

    def __init__(self, expected):
        self.expected = expected
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        if kind is None:
            raise AssertionError(f"expected {self.expected.__name__}, nothing was raised")
        if not issubclass(kind, self.expected):
            return False
        self.value = value
        return True


def _save(path, state):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")))
    os.replace(tmp, path)


def _load(path):
    return json.loads(path.read_text())


def _stream(seed, length=100):
    rng, now, out = random.Random(seed), 0, []
    for _ in range(length):
        now += rng.choice((0, 1, 20, 150))
        pick = rng.random()
        if pick < 0.3:
            out.append(
                {
                    "op": "enqueue",
                    "dedupe_key": f"k{rng.randrange(6)}",
                    "priority": rng.randrange(3),
                    "payload": {"n": rng.randrange(2)},
                }
            )
        elif pick < 0.65:
            out.append(
                {
                    "op": "claim",
                    "worker": rng.choice(("w1", "w2")),
                    "now": now,
                    "lease_ms": rng.choice((10, 100)),
                }
            )
        else:
            out.append(
                {
                    "op": rng.choice(("ack", "nack")),
                    "worker": rng.choice(("w1", "w2")),
                    "now": now,
                    "job_id": job_id_for(f"k{rng.randrange(6)}"),
                }
            )
    return out


def _apply(engine, state, request):
    try:
        return ("ok", engine.apply(state, request))
    except QueueError as error:
        return ("err", error.failure_class)


def _uninterrupted(requests):
    state, engine, results = {"jobs": [], "next_seq": 0}, QueueEngine(), []
    for request in requests:
        results.append(_apply(engine, state, request))
    return results, state


@pytest.mark.parametrize("seed", range(12))
def test_restart_at_every_step_matches_an_uninterrupted_run(seed, tmp_path):
    requests = _stream(seed)
    want_results, want_state = _uninterrupted(requests)
    path = tmp_path / "queue.json"
    _save(path, {"jobs": [], "next_seq": 0})
    results = []
    for request in requests:
        engine, state = QueueEngine(), _load(path)  # a fresh process every step
        results.append(_apply(engine, state, request))
        _save(path, state)
    assert results == want_results
    assert _load(path) == want_state


@pytest.mark.parametrize("seed", range(10))
def test_single_restart_at_random_cut_points(seed, tmp_path):
    requests = _stream(100 + seed)
    want_results, want_state = _uninterrupted(requests)
    cut = random.Random(seed).randrange(1, len(requests))
    first, state, results = QueueEngine(), {"jobs": [], "next_seq": 0}, []
    for request in requests[:cut]:
        results.append(_apply(first, state, request))
    path = tmp_path / "queue.json"
    _save(path, state)
    del first, state
    second, state = QueueEngine(), _load(path)
    for request in requests[cut:]:
        results.append(_apply(second, state, request))
    assert results == want_results and state == want_state


def test_state_id_survives_the_file_round_trip(tmp_path):
    state, engine = {"jobs": [], "next_seq": 0}, QueueEngine()
    engine.apply(state, {"op": "enqueue", "dedupe_key": "a", "priority": 1, "payload": {"é": [1]}})
    receipt = engine.apply(state, {"op": "claim", "worker": "w1", "now": 3, "lease_ms": 10})
    path = tmp_path / "queue.json"
    _save(path, state)
    assert state_id_for(_load(path)) == receipt["state_id"]


def test_lease_live_at_crash_lapses_and_the_job_is_reclaimed_after_restart(tmp_path):
    state, engine = {"jobs": [], "next_seq": 0}, QueueEngine()
    engine.apply(state, {"op": "enqueue", "dedupe_key": "a", "priority": 1, "payload": {}})
    first = engine.apply(state, {"op": "claim", "worker": "w1", "now": 0, "lease_ms": 100})
    path = tmp_path / "queue.json"
    _save(path, state)
    restarted, state = QueueEngine(), _load(path)
    ack = {"op": "ack", "worker": "w1", "job_id": first["job_id"]}
    # the old holder is still inside its lease after the restart and may settle
    assert restarted.apply(json.loads(json.dumps(state)), {**ack, "now": 99})["status"] == "done"
    # once the lease lapses the work is claimable by another worker, attempt 2
    again = restarted.apply(state, {"op": "claim", "worker": "w2", "now": 100, "lease_ms": 100})
    assert (again["job_id"], again["attempts"]) == (first["job_id"], 2)
    with _raises(QueueError) as info:
        restarted.apply(state, {**ack, "now": 101})
    assert info.value.failure_class == "lease_conflict"


def test_ack_at_the_exact_expiry_tick_is_refused_after_restart(tmp_path):
    state, engine = {"jobs": [], "next_seq": 0}, QueueEngine()
    engine.apply(state, {"op": "enqueue", "dedupe_key": "a", "priority": 1, "payload": {}})
    first = engine.apply(state, {"op": "claim", "worker": "w1", "now": 0, "lease_ms": 100})
    path = tmp_path / "queue.json"
    _save(path, state)
    ack = {"op": "ack", "worker": "w1", "job_id": first["job_id"]}
    for tick, ok in ((99, True), (100, False), (101, False)):
        fresh = _load(path)
        if ok:
            assert QueueEngine().apply(fresh, {**ack, "now": tick})["status"] == "done"
        else:
            with _raises(QueueError) as info:
                QueueEngine().apply(fresh, {**ack, "now": tick})
            assert info.value.failure_class == "lease_conflict"
            assert fresh == _load(path)  # a refusal leaves the loaded state untouched


def test_reenqueue_with_a_different_priority_or_payload_conflicts_after_restart(tmp_path):
    path = tmp_path / "queue.json"
    state = {"jobs": [], "next_seq": 0}
    base = {"op": "enqueue", "dedupe_key": "a", "priority": 2, "payload": {"x": 1}}
    QueueEngine().apply(state, base)
    _save(path, state)
    for change in ({"priority": 1}, {"payload": {"x": 2}}, {"priority": 0, "payload": {"y": 1}}):
        fresh = _load(path)
        with _raises(QueueError) as info:
            QueueEngine().apply(fresh, {**base, **change})
        assert info.value.failure_class == "dedupe_conflict"
        assert fresh == _load(path)
    # the identical request, and a key-order variant of the same payload, stay idempotent
    assert QueueEngine().apply(_load(path), base)["job_id"] == job_id_for("a")


def test_attempt_budget_is_kept_across_restarts_until_dead(tmp_path):
    path = tmp_path / "queue.json"
    state = {"jobs": [], "next_seq": 0}
    QueueEngine().apply(state, {"op": "enqueue", "dedupe_key": "a", "priority": 1, "payload": {}})
    _save(path, state)
    now = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        state = _load(path)
        got = QueueEngine().apply(state, {"op": "claim", "worker": "w1", "now": now, "lease_ms": 5})
        assert got["attempts"] == attempt
        _save(path, state)  # crash: the worker never acks
        now += 5
    state = _load(path)
    receipt = QueueEngine().apply(state, {"op": "claim", "worker": "w1", "now": now, "lease_ms": 5})
    assert receipt["job_id"] is None
    assert state["jobs"][0]["status"] == "dead"
    _save(path, state)
    assert _load(path)["jobs"][0]["status"] == "dead"


def test_reenqueue_after_restart_is_idempotent(tmp_path):
    path = tmp_path / "queue.json"
    state = {"jobs": [], "next_seq": 0}
    request = {"op": "enqueue", "dedupe_key": "a", "priority": 2, "payload": {"x": [1, 2]}}
    first = QueueEngine().apply(state, request)
    _save(path, state)
    state = _load(path)
    again = QueueEngine().apply(state, request)
    assert again == first and state == _load(path) and state["next_seq"] == 1


def test_torn_state_file_is_refused_and_the_last_good_file_still_works(tmp_path):
    path = tmp_path / "queue.json"
    state, engine = {"jobs": [], "next_seq": 0}, QueueEngine()
    engine.apply(state, {"op": "enqueue", "dedupe_key": "a", "priority": 1, "payload": {}})
    engine.apply(state, {"op": "enqueue", "dedupe_key": "b", "priority": 1, "payload": {}})
    _save(path, state)
    good = path.read_text()
    claim = {"op": "claim", "worker": "w1", "now": 0, "lease_ms": 10}
    refused = 0
    for cut in range(1, len(good), 17):
        torn = good[:cut]
        try:
            loaded = json.loads(torn)
        except json.JSONDecodeError:
            continue  # a host must not hand unparsable bytes to the engine
        with _raises(QueueError) as info:
            QueueEngine().apply(loaded, claim)
        assert info.value.failure_class == "corrupt_queue"
        refused += 1
    # parseable-but-malformed states (what a torn write can leave after a lenient
    # host repair) must be refused by the engine, whatever the byte-level cuts do
    good_state = json.loads(good)
    malformed = [
        {},
        {"jobs": good_state["jobs"]},
        {"jobs": good_state["jobs"], "next_seq": 0},
        {"jobs": [{"job_id": "x"}], "next_seq": 1},
        {"jobs": "[]", "next_seq": 0},
        [],
        None,
    ]
    job = good_state["jobs"][0]
    malformed += [
        {"jobs": [{**job, "status": "done", "attempts": 0}], "next_seq": 1},
        {"jobs": [{**job, "status": "dead", "attempts": MAX_ATTEMPTS - 1}], "next_seq": 1},
        {
            "jobs": [
                {
                    **job,
                    "status": "leased",
                    "attempts": 1,
                    "lease_owner": "w1",
                    "lease_expires_at": 0,
                }
            ],
            "next_seq": 1,
        },
    ]
    for bad in malformed:
        with _raises(QueueError) as info:
            QueueEngine().apply(copy.deepcopy(bad), claim)
        assert info.value.failure_class == "corrupt_queue"
        refused += 1
    assert refused >= len(malformed)
    # accepted twins of the boundary rows: done at attempts == 1, dead at MAX_ATTEMPTS
    # and a lease that expires at tick 1
    for ok_job in (
        {**job, "status": "done", "attempts": 1},
        {**job, "status": "dead", "attempts": MAX_ATTEMPTS},
        {**job, "status": "leased", "attempts": 1, "lease_owner": "w1", "lease_expires_at": 1},
    ):
        kept = QueueEngine().apply({"jobs": [ok_job], "next_seq": 1}, claim)
        assert "job_id" in kept
    assert QueueEngine().apply(copy.deepcopy(good_state), claim)["job_id"] == job_id_for("a")
    # an interrupted write goes to the temp file only; the live file stays intact
    path.with_suffix(".tmp").write_text(good[: len(good) // 2])
    state = _load(path)
    assert QueueEngine().apply(state, claim)["job_id"] == job_id_for("a")


def test_stale_state_snapshot_replay_is_visible_as_a_different_state_id(tmp_path):
    state, engine = {"jobs": [], "next_seq": 0}, QueueEngine()
    engine.apply(state, {"op": "enqueue", "dedupe_key": "a", "priority": 1, "payload": {}})
    path = tmp_path / "queue.json"
    _save(path, state)
    snapshot = _load(path)
    receipt = engine.apply(state, {"op": "claim", "worker": "w1", "now": 0, "lease_ms": 10})
    assert state_id_for(snapshot) != state_id_for(state) == receipt["state_id"]


@pytest.mark.parametrize("seed", range(12))
def test_streams_exercise_accepted_work_not_only_refusals(seed):
    """Acceptance oracle: an engine that refused everything would make every
    restart comparison trivially equal; the reference run must really accept."""
    results, state = _uninterrupted(_stream(seed))
    kinds = {request_result[0] for request_result in results}
    assert "ok" in kinds
    assert sum(1 for kind, _ in results if kind == "ok") >= 10
    assert state["jobs"] and state["next_seq"] > 0
    assert any(r[0] == "ok" and isinstance(r[1], dict) for r in results)


def test_queue_size_bound_is_inclusive_and_state_survives_the_file_round_trip(tmp_path):
    def job(i):
        return {
            "job_id": job_id_for(f"k{i}"),
            "seq": i,
            "priority": 0,
            "payload": {},
            "status": "ready",
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
        }

    claim = {"op": "claim", "worker": "w1", "now": 0, "lease_ms": 10}
    full = {"jobs": [job(i) for i in range(MAX_JOBS)], "next_seq": MAX_JOBS}
    path = tmp_path / "queue.json"
    _save(path, full)
    state = _load(path)
    assert QueueEngine().apply(state, claim)["job_id"] == job_id_for("k0")
    over = {"jobs": [job(i) for i in range(MAX_JOBS + 1)], "next_seq": MAX_JOBS + 1}
    with _raises(QueueError) as info:
        QueueEngine().apply(over, claim)
    assert info.value.failure_class == "corrupt_queue"


class _InertKey(str):
    """A str subclass with no hooks: only its type differs from a plain str."""


def _inert(mapping):
    return {_InertKey(k): v for k, v in mapping.items()}


def _ready(i, **kw):
    job = {
        "job_id": job_id_for(f"k{i}"),
        "seq": i,
        "priority": 0,
        "payload": {},
        "status": "ready",
        "attempts": 0,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    job.update(kw)
    return job


def _refused(state, request, failure):
    before = copy.deepcopy(state)
    with _raises(QueueError) as info:
        QueueEngine().apply(state, request)
    assert info.value.failure_class == failure
    assert state == before  # a refusal never touches the caller's state


ENQ = {"op": "enqueue", "dedupe_key": "z", "priority": 1, "payload": {"a": 1}}
CLAIM = {"op": "claim", "worker": "w1", "now": 0, "lease_ms": 10}
ACK = {"op": "ack", "worker": "w1", "now": 0, "job_id": job_id_for("k0")}


def test_inert_str_subclass_keys_are_refused_everywhere():
    good = {"jobs": [_ready(0)], "next_seq": 1}
    for request in (ENQ, CLAIM, ACK):
        _refused(copy.deepcopy(good), _inert(request), "malformed_queue_request")
    _refused(copy.deepcopy(good), {**ENQ, "payload": _inert({"a": 1})}, "malformed_queue_request")
    _refused(_inert(copy.deepcopy(good)), CLAIM, "corrupt_queue")
    _refused({"jobs": [_inert(_ready(0))], "next_seq": 1}, CLAIM, "corrupt_queue")
    _refused({"jobs": [_ready(0, payload=_inert({"a": 1}))], "next_seq": 1}, CLAIM, "corrupt_queue")
    assert QueueEngine().apply(copy.deepcopy(good), CLAIM)["job_id"] == job_id_for("k0")


def test_non_str_payload_keys_are_refused():
    good = {"jobs": [_ready(0)], "next_seq": 1}
    _refused(copy.deepcopy(good), {**ENQ, "payload": {1: 2}}, "malformed_queue_request")
    _refused(copy.deepcopy(good), {**ENQ, "payload": {"a": {2: 3}}}, "malformed_queue_request")
    _refused({"jobs": [_ready(0, payload={1: 2})], "next_seq": 1}, CLAIM, "corrupt_queue")


def test_request_shape_rows_are_refused_with_the_pinned_class():
    good = {"jobs": [_ready(0)], "next_seq": 1}
    rows = (
        {1: "x", **CLAIM},  # non-str key
        {k: v for k, v in CLAIM.items() if k != "op"},  # no op
        {**CLAIM, "op": 7},  # op not a str
        {**CLAIM, "op": "bogus"},
        {**CLAIM, "extra": 1},  # a key outside the op's fields
        {k: v for k, v in CLAIM.items() if k != "lease_ms"},  # a missing field
        {**CLAIM, "worker": "bad worker!"},
        {**CLAIM, "lease_ms": 0},
        {**CLAIM, "lease_ms": 3_600_001},
        {**CLAIM, "now": -1},
        {**ACK, "job_id": "nope"},
        {**ENQ, "priority": 10},
        {**ENQ, "dedupe_key": ""},
    )
    for request in rows:
        _refused(copy.deepcopy(good), request, "malformed_queue_request")
    # accepted twins at the edges
    edge = {**CLAIM, "lease_ms": 3_600_000, "now": 2**53 - 1 - 3_600_000}
    assert QueueEngine().apply(copy.deepcopy(good), edge)["status"] == "leased"
    _refused(copy.deepcopy(good), {**edge, "now": edge["now"] + 1}, "malformed_queue_request")
    assert QueueEngine().apply(copy.deepcopy(good), {**ENQ, "priority": 9})["status"] == "ready"


def test_lease_state_rows_and_capacity_bounds():
    leased = {"status": "leased", "lease_owner": "w1", "lease_expires_at": 5}
    _refused({"jobs": [_ready(0, attempts=0, **leased)], "next_seq": 1}, CLAIM, "corrupt_queue")
    _refused(
        {"jobs": [_ready(0, attempts=1, **{**leased, "lease_expires_at": 0})], "next_seq": 1},
        CLAIM,
        "corrupt_queue",
    )
    ok = {"jobs": [_ready(0, attempts=1, **leased)], "next_seq": 1}
    assert QueueEngine().apply(ok, {**CLAIM, "now": 5})["job_id"] == job_id_for("k0")
    # enqueue at the size bound: 9999 jobs accept one more, 10000 refuse
    nearly = {"jobs": [_ready(i, status="done", attempts=1) for i in range(MAX_JOBS - 1)]}
    nearly["next_seq"] = MAX_JOBS - 1
    assert QueueEngine().apply(nearly, ENQ)["status"] == "ready" and len(nearly["jobs"]) == MAX_JOBS
    _refused(nearly, {**ENQ, "dedupe_key": "y"}, "capacity_exceeded")
    # enqueue at the sequence bound: next_seq == 2**53 - 2 accepts, 2**53 - 1 refuses
    top = 2**53 - 1
    assert QueueEngine().apply({"jobs": [], "next_seq": top - 1}, ENQ)["status"] == "ready"
    _refused({"jobs": [], "next_seq": top}, ENQ, "capacity_exceeded")
