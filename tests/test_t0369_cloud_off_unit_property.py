"""T0369 seeded unit/property proof for production cloud_off.

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

from desktop.privacy import cloud_off as production

MAX = 2**63 - 1
IDS = ["ins1:" + str(n) * 64 for n in range(1, 4)]
FAILURES = {
    "malformed_cloud_request": "malformed_request",
    "unknown_install": "unknown_install",
    "stale_revision": "conflict",
    "opt_in_missing": "approval_required",
    "revision_exhausted": "revision_exhausted",
}


def token(cid, rev):
    return "opt1:" + hashlib.sha256(f"{cid}|{rev}|on".encode()).hexdigest()


def factory():
    from desktop.privacy.sensitive_flag import FlagStore

    return production.CloudSwitch(FlagStore())


def receipt(cid, value, revision, history):
    return {"install_id": cid, "cloud_mode": value, "revision": revision}


def request(cid, target, revision, approved=True):
    return {
        "install_id": cid,
        "cloud_mode": target,
        "expected_revision": revision,
        "opt_in": token(cid, revision) if approved else None,
    }


def expected(state, rq, rollback=False):
    cid = rq["install_id"]
    revision = rq["expected_revision"]
    target = rq.get("cloud_mode")
    approval = rq["opt_in"]
    if type(revision) is not int or not 0 <= revision <= MAX:
        return "malformed_cloud_request", None
    if not rollback and not (type(target) is str and target in ("off", "on")):
        return "malformed_cloud_request", None
    if approval is not None and (
        type(approval) is not str or not approval.startswith("opt1:") or len(approval) != 69
    ):
        return "malformed_cloud_request", None
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
        with pytest.raises(production.CloudError) as caught:
            fn(rq)
        exc = caught.value
        assert type(exc) is production.CloudError
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
            del rq["cloud_mode"]
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
    collection = "col1:" + "a" * 64
    store._flags.register(collection)
    request = {"install_id": cid, "collection_id": collection, "destination": "local"}
    assert store.verdict(request) == "allow"
    request["destination"] = "cloud"
    with pytest.raises(production.CloudError) as caught:
        store.verdict(request)
    assert caught.value.failure_class == "cloud_mode_off"
    if known:
        store.transition(
            {"install_id": cid, "cloud_mode": "on", "expected_revision": 0, "opt_in": token(cid, 0)}
        )
        with pytest.raises(production.CloudError) as caught:
            store.verdict(request)
        assert caught.value.failure_class == "collection_sensitive"


@pytest.mark.parametrize("bad", [None, 0, 1, "invalid", [], {}, b"off"])
def test_malformed_targets_are_typed_and_atomic(bad):
    store = factory()
    cid = IDS[0]
    store.register(cid)
    state = {cid: ("off", 0, [])}
    rq = request(cid, bad, 0)
    assert_call(store, state, rq)


def grammar_probes(module):
    def allowed(fn, rq):
        try:
            return fn(rq)
        except module.CloudError as exc:
            raise AssertionError("well-formed allowed verdict was refused") from exc

    def refused(store, fn, *args):
        before = copy.deepcopy((store._state, args))
        try:
            fn(*args)
        except module.CloudError as exc:
            assert type(exc) is module.CloudError
            assert (
                exc.failure_class == "malformed_cloud_request" and exc.code == "malformed_request"
            )
            assert exc.__cause__ is None and exc.__context__ is None
        else:
            raise AssertionError("grammar-invalid value accepted")
        assert (store._state, args) == before

    from desktop.privacy.sensitive_flag import FlagStore, approval_for

    flags = FlagStore()
    collection = "col1:" + "a" * 64
    flags.register(collection)
    flags.transition(
        {
            "collection_id": collection,
            "sensitive": False,
            "expected_revision": 0,
            "approval": approval_for(collection, 0),
        }
    )
    store = module.CloudSwitch(flags)
    cid = IDS[0]
    store.register(cid)
    for bad in [
        "",
        "ins1:" + "g" * 64,
        "ins1:" + "a" * 63,
        "ins1:" + "a" * 65,
        "ins1:" + "a" * 64 + "\n",
        "wrong:" + "a" * 64,
    ]:
        refused(store, store.register, bad)
        rq = request(cid, "on", 0)
        rq["install_id"] = bad
        refused(store, store.transition, rq)
        rq = {"install_id": cid, "collection_id": collection, "destination": "local"}
        rq["install_id"] = bad
        refused(store, store.verdict, rq)
    for bad in [
        "",
        "opt1:" + "g" * 64,
        "opt1:" + "a" * 63,
        "opt1:" + "a" * 65,
        "opt1:" + "a" * 64 + "\n",
    ]:
        rq = request(cid, "on", 0)
        rq["opt_in"] = bad
        refused(store, store.transition, rq)
    for bad in ["", "LOCAL", "local\n", "remote"]:
        rq = {"install_id": cid, "collection_id": collection, "destination": "local"}
        rq["destination"] = bad
        refused(store, store.verdict, rq)
    store.transition(request(cid, "on", 0))
    assert (
        store.verdict({"install_id": cid, "collection_id": collection, "destination": "cloud"})
        == "allow"
    )
    rq = {"install_id": cid, "collection_id": "col1:" + "g" * 64, "destination": "cloud"}
    refused(store, store.verdict, rq)


def test_single_violation_grammar_rows_and_allowed_verdict():
    grammar_probes(production)


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None",
            "not _exact_str(install_id) and _INS_RE.fullmatch(install_id) is None",
        ),
        (
            "not _exact_str(install_id) or _INS_RE.fullmatch(install_id) is None",
            "not _exact_str(install_id) or _INS_RE.fullmatch(install_id) == 0",
        ),
        (
            "not _exact_str(iid) or _INS_RE.fullmatch(iid) is None",
            "not _exact_str(iid) and _INS_RE.fullmatch(iid) is None",
        ),
        (
            "not _exact_str(iid) or _INS_RE.fullmatch(iid) is None",
            "not _exact_str(iid) or _INS_RE.fullmatch(iid) == 0",
        ),
        ("_OPT_RE.fullmatch(token) is None", "_OPT_RE.fullmatch(token) == 0"),
        ("_COL_RE.fullmatch(cid) is None", "_COL_RE.fullmatch(cid) == 0"),
    ],
)
def test_named_grammar_mutants_are_semantically_killed(old, new):
    import types
    from pathlib import Path

    source = (Path(production.__file__)).read_text()
    assert old in source
    mutant = types.ModuleType("synthetic_grammar_mutant")
    mutant.__file__ = production.__file__
    exec(compile(source.replace(old, new), production.__file__, "exec"), vars(mutant))
    with pytest.raises(AssertionError):
        grammar_probes(mutant)
