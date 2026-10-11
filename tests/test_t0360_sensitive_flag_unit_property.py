"""T0360 seeded unit/property proof for production sensitive_flag.

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

from desktop.privacy import sensitive_flag as production

MAX = 2**63 - 1
IDS = ["col1:" + str(n) * 64 for n in range(1, 4)]
FAILURES = {
    "malformed_flag_request": "malformed_request",
    "unknown_collection": "unknown_collection",
    "stale_revision": "conflict",
    "approval_missing": "approval_required",
    "revision_exhausted": "revision_exhausted",
    "empty_history": "nothing_to_roll_back",
}


def token(cid, rev):
    return "apv1:" + hashlib.sha256(f"{cid}|{rev}|false".encode()).hexdigest()


def factory():
    return production.FlagStore()


def receipt(cid, value, revision, history):
    return {
        "collection_id": cid,
        "sensitive": value,
        "revision": revision,
        "prior": history[-1] if history else None,
    }


def request(cid, target, revision, approved=True):
    return {
        "collection_id": cid,
        "sensitive": target,
        "expected_revision": revision,
        "approval": token(cid, revision) if approved else None,
    }


def expected(state, rq, rollback=False):
    cid = rq["collection_id"]
    revision = rq["expected_revision"]
    target = rq.get("sensitive")
    approval = rq["approval"]
    if type(revision) is not int or not 0 <= revision <= MAX:
        return "malformed_flag_request", None
    if not rollback and type(target) is not bool:
        return "malformed_flag_request", None
    if approval is not None and (
        type(approval) is not str or not approval.startswith("apv1:") or len(approval) != 69
    ):
        return "malformed_flag_request", None
    if cid not in state:
        return "unknown_collection", None
    value, current, history = state[cid]
    if revision != current:
        return "stale_revision", None
    if rollback:
        if not history:
            return "empty_history", None
        target = history[-1]
    if not rollback and target == value:
        return None, (value, current, list(history))
    if target is False and approval != token(cid, revision):
        return "approval_missing", None
    if current == MAX:
        return "revision_exhausted", None
    next_history = history[:-1] if rollback else history + [value]
    return None, (target, current + 1, next_history)


def assert_call(store, state, rq, rollback=False):
    snapshot = copy.deepcopy((store._state, rq))
    failure, result = expected(state, rq, rollback)
    fn = store.rollback if rollback else store.transition
    if failure:
        with pytest.raises(production.FlagError) as caught:
            fn(rq)
        exc = caught.value
        assert type(exc) is production.FlagError
        assert exc.failure_class == failure and exc.code == FAILURES[failure]
        assert exc.__cause__ is None and exc.__context__ is None
        assert (store._state, rq) == snapshot
        return
    output = fn(rq)
    cid = rq["collection_id"]
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
        assert store.register(cid) == receipt(cid, True, 0, [])
        state[cid] = (True, 0, [])
    for _ in range(150):
        cid = rng.choice(IDS)
        current = state.get(cid, (True, 0, []))[1]
        target = rng.choice([True, False])
        rev = rng.choice([current, current, max(0, current - 1), current + 1, True, -1, MAX + 1])
        rq = request(cid, target, rev, rng.choice([True, False]))
        rollback = rng.randrange(4) == 0
        if rollback:
            del rq["sensitive"]
        assert_call(store, state, rq, rollback)


@pytest.mark.parametrize("revision", [0, 1, MAX - 1, MAX])
@pytest.mark.parametrize("target", [True, False])
@pytest.mark.parametrize("approved", [True, False])
def test_revision_edges_and_noop(revision, target, approved):
    cid = IDS[0]
    store = factory()
    store.register(cid)
    store._state[cid] = (True, revision, [])
    state = {cid: (True, revision, [])}
    assert_call(store, state, request(cid, target, revision, approved))


def test_reregister_never_resets_state():
    cid = IDS[0]
    store = factory()
    store.register(cid)
    state = {cid: (True, 0, [])}
    assert_call(store, state, request(cid, False, 0))
    prior = copy.deepcopy(store._state)
    assert store.register(cid) == receipt(cid, *state[cid])
    assert store._state == prior


def test_default_unknown_and_local_verdicts():
    store = factory()
    cid = IDS[0]
    assert store.verdict({"collection_id": cid, "destination": "local"}) == "allow"
    with pytest.raises(production.FlagError) as caught:
        store.verdict({"collection_id": cid, "destination": "cloud"})
    assert caught.value.failure_class == "egress_blocked"


@pytest.mark.parametrize("bad", [None, 0, 1, "invalid", [], {}, b"off"])
def test_malformed_targets_are_typed_and_atomic(bad):
    store = factory()
    cid = IDS[0]
    store.register(cid)
    state = {cid: (True, 0, [])}
    rq = request(cid, bad, 0)
    assert_call(store, state, rq)


def grammar_probes(module):
    def allowed(fn, rq):
        try:
            return fn(rq)
        except module.FlagError as exc:
            raise AssertionError("well-formed allowed verdict was refused") from exc

    def refused(store, fn, *args):
        before = copy.deepcopy((store._state, args))
        try:
            fn(*args)
        except module.FlagError as exc:
            assert type(exc) is module.FlagError
            assert exc.failure_class == "malformed_flag_request" and exc.code == "malformed_request"
            assert exc.__cause__ is None and exc.__context__ is None
        else:
            raise AssertionError("grammar-invalid value accepted")
        assert (store._state, args) == before

    store = module.FlagStore()
    cid = IDS[0]
    store.register(cid)
    for bad in [
        "",
        "col1:" + "g" * 64,
        "col1:" + "a" * 63,
        "col1:" + "a" * 65,
        "col1:" + "a" * 64 + "\n",
        "wrong:" + "a" * 64,
    ]:
        refused(store, store.register, bad)
        rq = request(cid, False, 0)
        rq["collection_id"] = bad
        refused(store, store.transition, rq)
        rq = {"collection_id": cid, "destination": "local"}
        rq["collection_id"] = bad
        refused(store, store.verdict, rq)
    for bad in [
        "",
        "apv1:" + "g" * 64,
        "apv1:" + "a" * 63,
        "apv1:" + "a" * 65,
        "apv1:" + "a" * 64 + "\n",
    ]:
        rq = request(cid, False, 0)
        rq["approval"] = bad
        refused(store, store.transition, rq)
    for bad in ["", "LOCAL", "local\n", "remote"]:
        rq = {"collection_id": cid, "destination": "local"}
        rq["destination"] = bad
        refused(store, store.verdict, rq)
    store.transition(request(cid, False, 0))
    assert allowed(store.verdict, {"collection_id": cid, "destination": "cloud"}) == "allow"


def test_single_violation_grammar_rows_and_allowed_verdict():
    grammar_probes(production)


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None",
            "not _exact_str(collection_id) and _COL_RE.fullmatch(collection_id) is None",
        ),
        (
            "not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) is None",
            "not _exact_str(collection_id) or _COL_RE.fullmatch(collection_id) == 0",
        ),
        (
            "not _exact_str(cid) or _COL_RE.fullmatch(cid) is None",
            "not _exact_str(cid) and _COL_RE.fullmatch(cid) is None",
        ),
        (
            "not _exact_str(cid) or _COL_RE.fullmatch(cid) is None",
            "not _exact_str(cid) or _COL_RE.fullmatch(cid) == 0",
        ),
        ("_APV_RE.fullmatch(apv) is None", "_APV_RE.fullmatch(apv) == 0"),
        ("state is None or state[0] is not False", "state is None or state[0] is False"),
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
