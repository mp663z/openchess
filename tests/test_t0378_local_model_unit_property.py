"""T0378 seeded unit/property proof for production local_model.

An independent small transition model checks seeded mixed valid, malformed,
stale and unapproved chains, rollback, no-op, overflow and record detachment.
No reference implementation computes expected results. Every seeded trace is
reproducible and every refusal must be fresh, typed, pure and correctly mapped.
"""

from __future__ import annotations

import copy
import hashlib
import random

import pytest

from desktop.privacy import local_model as production

MAX = 2**63 - 1
IDS = ["ins1:" + str(n) * 64 for n in range(1, 4)]
FAILURES = {
    "malformed_model_request": "malformed_request",
    "unknown_install": "unknown_install",
    "stale_revision": "conflict",
    "opt_in_missing": "approval_required",
    "revision_exhausted": "revision_exhausted",
}


def token(cid, rev):
    return "lmo1:" + hashlib.sha256(f"{cid}|{rev}|large-on".encode()).hexdigest()


def factory():
    return production.LocalModels()


def receipt(cid, value, revision, history):
    return {
        "install_id": cid,
        "large_model": value,
        "revision": revision,
        "reference_claims": "valid" if value == "off" else "suspended",
    }


def request(cid, target, revision, approved=True):
    return {
        "install_id": cid,
        "large_model": target,
        "expected_revision": revision,
        "opt_in": token(cid, revision) if approved else None,
    }


def expected(state, rq, rollback=False):
    cid = rq["install_id"]
    revision = rq["expected_revision"]
    target = rq.get("large_model")
    approval = rq["opt_in"]
    if type(revision) is not int or not 0 <= revision <= MAX:
        return "malformed_model_request", None
    if not rollback and not (type(target) is str and target in ("off", "on")):
        return "malformed_model_request", None
    if approval is not None and (
        type(approval) is not str or not approval.startswith("lmo1:") or len(approval) != 69
    ):
        return "malformed_model_request", None
    if cid not in state:
        return "unknown_install", None
    value, current, history = state[cid]
    if revision != current:
        return "stale_revision", None
    if rollback:
        if not history:
            return "empty_history", None
        target = history[-1]
    if not rollback and target == value:
        return None, (value, current, list(history))
    if target == "on" and approval != token(cid, revision):
        return "opt_in_missing", None
    if current == MAX:
        return "revision_exhausted", None
    next_history = history[:-1] if rollback else []
    return None, (target, current + 1, next_history)


def assert_call(store, state, rq, rollback=False):
    snapshot = copy.deepcopy((store._state, rq))
    failure, result = expected(state, rq, rollback)
    fn = store.rollback if rollback else store.transition
    if failure:
        with pytest.raises(production.ModelError) as caught:
            fn(rq)
        exc = caught.value
        assert type(exc) is production.ModelError
        assert exc.failure_class == failure and exc.code == FAILURES[failure]
        assert exc.__cause__ is None and exc.__context__ is None
        assert (store._state, rq) == snapshot
        return
    output = fn(rq)
    cid = rq["install_id"]
    state[cid] = result
    assert output == receipt(cid, *result)
    assert type(output) is dict and type(output["revision"]) is int
    assert rq == snapshot[1]
    output["revision"] = -1
    assert store.record(cid) == receipt(cid, *result)
    for known, entry in state.items():
        assert store.record(known) == receipt(known, *entry)


@pytest.mark.parametrize("seed", range(20))
def test_seeded_mixed_transition_traces(seed):
    rng = random.Random(seed)
    store = factory()
    state = {}
    for cid in IDS[:2]:
        assert store.register(cid) == receipt(cid, "off", 0, [])
        state[cid] = ("off", 0, [])
    for _ in range(150):
        cid = rng.choice(IDS)
        current = state.get(cid, ("off", 0, []))[1]
        target = rng.choice(["off", "on"])
        rev = rng.choice([current, current, max(0, current - 1), current + 1, True, -1, MAX + 1])
        rq = request(cid, target, rev, rng.choice([True, False]))
        rollback = False
        if rollback:
            del rq["large_model"]
        assert_call(store, state, rq, rollback)


@pytest.mark.parametrize("revision", [0, 1, MAX - 1, MAX])
@pytest.mark.parametrize("target", ["off", "on"])
@pytest.mark.parametrize("approved", [True, False])
def test_revision_edges_and_noop(revision, target, approved):
    cid = IDS[0]
    store = factory()
    store.register(cid)
    store._state[cid] = ("off", revision)
    state = {cid: ("off", revision, [])}
    assert_call(store, state, request(cid, target, revision, approved))


def test_reregister_never_resets_state():
    cid = IDS[0]
    store = factory()
    store.register(cid)
    state = {cid: ("off", 0, [])}
    assert_call(store, state, request(cid, "on", 0))
    prior = copy.deepcopy(store._state)
    assert store.register(cid) == receipt(cid, *state[cid])
    assert store._state == prior


@pytest.mark.parametrize("known", [False, True])
def test_verdict_gates_at_call_time(known):
    store = factory()
    cid = IDS[0]
    if known:
        store.register(cid)
    request = {"install_id": cid, "tier": "small", "host": "desktop", "destination": "local"}
    assert store.verdict(request) == {"verdict": "allow", "reference_claims": "valid"}
    for host in ["web", "server"]:
        request["host"] = host
        with pytest.raises(production.ModelError) as caught:
            store.verdict(request)
        assert caught.value.failure_class == "host_not_desktop"
    request.update(host="web", destination="cloud", tier="large")
    with pytest.raises(production.ModelError) as caught:
        store.verdict(request)
    assert caught.value.failure_class == "egress_requested"


@pytest.mark.parametrize("bad", [None, 0, 1, "invalid", [], {}, b"off"])
def test_malformed_targets_are_typed_and_atomic(bad):
    store = factory()
    cid = IDS[0]
    store.register(cid)
    state = {cid: ("off", 0, [])}
    rq = request(cid, bad, 0)
    assert_call(store, state, rq)
