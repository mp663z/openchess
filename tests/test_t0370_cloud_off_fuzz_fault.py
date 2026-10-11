"""T0370 deterministic malformed-request fuzz and refusal/fault probes.

Every rejection compares deep state and the actual request before/after,
checks its exact refusal class/code, then commits a valid continuation. Seeded
one-locus defects have independently pinned expected malformed outcomes.
No corrupt internal-state behavior or external failure recovery is promised.
"""

from __future__ import annotations

import copy
import random

import pytest

from desktop.privacy import cloud_off as production
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome

CID = "ins1:" + "a" * 64
MALFORMED = "malformed_cloud_request"


class BombDict(dict):
    calls = []

    def keys(self):
        self.calls.append("keys")
        raise AssertionError("caller hook")

    def __iter__(self):
        self.calls.append("iter")
        raise KeyError("caller hook")


class BombStr(str):
    def __eq__(self, other):
        BombDict.calls.append("eq")
        raise AssertionError("caller hook")

    def __hash__(self):
        return str.__hash__(self)


def world(module):
    from desktop.privacy.sensitive_flag import FlagStore

    store = module.CloudSwitch(FlagStore())
    store.register(CID)
    return store


def valid(module):
    return {
        "install_id": CID,
        "cloud_mode": "on",
        "expected_revision": 0,
        "opt_in": module.opt_in_for(CID, 0),
    }


def snapshot(store):
    return copy.deepcopy((store._state, store._flags._state))


def raw_snapshot(value):
    if isinstance(value, dict):
        return (type(value), [(id(k), raw_snapshot(v)) for k, v in dict.items(value)])
    if isinstance(value, list):
        return (type(value), [raw_snapshot(v) for v in list.__iter__(value)])
    if isinstance(value, str):
        return (type(value), str.__str__(value))
    return (type(value), value)


def reject(module, store, rq, expected=MALFORMED):
    before = snapshot(store)
    req_before = raw_snapshot(rq)
    BombDict.calls.clear()
    got = outcome(store.transition, module.CloudError, rq)
    assert got == ("failure", expected, module.FAILURE_MAPPING[expected])
    assert snapshot(store) == before
    assert raw_snapshot(rq) == req_before
    assert BombDict.calls == []


def defects(module):
    base = valid(module)
    rows = []
    for field in base:
        rq = copy.deepcopy(base)
        del rq[field]
        rows.append(rq)
    rq = copy.deepcopy(base)
    rq["extra"] = 1
    rows.append(rq)
    for bad in ["", "ins1:" + "g" * 64, "ins1:" + "a" * 63, CID + "\n", None, True, [], b"id"]:
        rq = copy.deepcopy(base)
        rq["install_id"] = bad
        rows.append(rq)
    for bad in [
        "",
        "opt1:" + "g" * 64,
        "opt1:" + "a" * 63,
        "opt1:" + "a" * 65,
        "opt1:" + "a" * 64 + "\n",
        7,
        [],
        True,
    ]:
        rq = copy.deepcopy(base)
        rq["opt_in"] = bad
        rows.append(rq)
    for bad in [-1, 2**63, True, None, 1.5, "0", []]:
        rq = copy.deepcopy(base)
        rq["expected_revision"] = bad
        rows.append(rq)
    for bad in [None, 0, True, "ON", "on\n", [], {}]:
        rq = copy.deepcopy(base)
        rq["cloud_mode"] = bad
        rows.append(rq)
    return rows


@pytest.mark.parametrize("seed", range(30))
def test_seeded_malformed_fuzz_and_valid_continuation(seed):
    rng = random.Random(seed)
    rows = defects(production)
    rng.shuffle(rows)
    for rq in rows:
        store = world(production)
        reject(production, store, rq)
        assert outcome(store.transition, production.CloudError, valid(production))[0] == "ok"


@pytest.mark.parametrize("kind", ["dict", "value", "key", "str", "none"])
def test_hostile_types_fail_before_caller_code(kind):
    store = world(production)
    rq = valid(production)
    if kind == "dict":
        rq = BombDict(rq)
    elif kind == "value":
        rq["install_id"] = BombStr(CID)
    elif kind == "key":
        value = rq.pop("install_id")
        rq[BombStr("install_id")] = value
    elif kind == "str":
        rq = BombStr("request")
    else:
        rq = None
    reject(production, store, rq)


def fault_probe(module):
    store = world(module)
    request = valid(module)
    request["opt_in"] = None
    reject(module, store, request, "opt_in_missing")
    assert outcome(store.transition, module.CloudError, valid(module))[0] == "ok"
    stale = valid(module)
    reject(module, store, stale, "stale_revision")
    # Overflow refuses without changing the staged state. No wrap/saturation commit.
    store._state[CID] = ("off", 2**63 - 1)
    rq = valid(module)
    rq["expected_revision"] = 2**63 - 1
    rq["opt_in"] = module.opt_in_for(CID, 2**63 - 1)
    reject(module, store, rq, "revision_exhausted")


def test_refusal_and_overflow_faults_are_atomic():
    fault_probe(production)


@pytest.mark.parametrize(
    "old,new",
    [
        ("if rev >= _MAX_REV:", "if rev > _MAX_REV:"),
        ('if target == "on" and not approved:', "if False and not approved:"),
        ('if req["expected_revision"] != rev:', "if False:"),
    ],
)
def test_independent_red_reproductions(old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        fault_probe(mutant)


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_candidate_crash_never_qualifies_as_a_semantic_kill(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.CloudError, {})
