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
"""

from __future__ import annotations

import json
import os
import random

import pytest

from server.jobs_queue import MAX_ATTEMPTS, QueueEngine, QueueError, job_id_for, state_id_for


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
    with pytest.raises(QueueError) as info:
        restarted.apply(state, {**ack, "now": 101})
    assert info.value.failure_class == "lease_conflict"


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
    for cut in range(1, len(good), 17):
        torn = good[:cut]
        try:
            loaded = json.loads(torn)
        except json.JSONDecodeError:
            continue  # a host must not hand unparsable bytes to the engine
        with pytest.raises(QueueError) as info:
            QueueEngine().apply(loaded, claim)
        assert info.value.failure_class == "corrupt_queue"
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
