"""T0388 seeded one-locus BYOM payload/terms fuzz and composed refusal faults."""

from __future__ import annotations

import copy
import random

import pytest

from desktop.privacy import byom as production
from desktop.privacy.cloud_off import CloudError
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome

INS = "ins1:" + "a" * 64
COL = "col1:" + "b" * 64
PID = "prv1:fixture"
TERMS = {"retention_days": 30, "region": "eu", "trains_on_inputs": False}


class Gate:
    def __init__(self, refusal=None):
        self.refusal = refusal
        self.calls = []

    def verdict(self, rq):
        self.calls.append(copy.deepcopy(rq))
        if self.refusal:
            raise CloudError(self.refusal)
        return "allow"


def world(module):
    gate = Gate()
    store = module.ByomStore(gate)
    store.register(INS, PID)
    rq = {
        "install_id": INS,
        "provider_id": PID,
        "terms": copy.deepcopy(TERMS),
        "expected_revision": 0,
        "approval": module.byom_approval_for(INS, PID, module.terms_digest(TERMS), 0),
    }
    assert outcome(store.transition, module.ByomError, rq)[0] == "ok"
    return store, gate


def send(payload=None):
    return {
        "install_id": INS,
        "provider_id": PID,
        "collection_id": COL,
        "terms": copy.deepcopy(TERMS),
        "payload": {"fen": "~", "task": "weekly-plan", "moves": ["a", "~"], "max-tokens": 1}
        if payload is None
        else payload,
    }


def defects():
    rows = []
    for field, values in [
        ("fen", ["", "x" * 129, "é", "x\n", 1]),
        ("moves", [[""], ["x" * 17], ["x y"], ["x"] * 513, {}]),
        ("task", ["invalid", "weekly-plan\n", None]),
        ("max-tokens", [0, 4097, True, "1"]),
    ]:
        for value in values:
            rq = send()
            rq["payload"][field] = value
            rows.append(("payload_rejected", rq))
    for field in ["account-keys", "full-corpus", "sync-keys"]:
        rq = send()
        rq["payload"][field] = "synthetic"
        rows.append(("payload_rejected", rq))
    for field, values in [
        ("retention_days", [-1, 3651, True]),
        ("region", ["", "EU", "us-east-1"]),
        ("trains_on_inputs", [0, None]),
    ]:
        for value in values:
            rq = send()
            rq["terms"][field] = value
            rows.append(("malformed_byom_request", rq))
    return rows


def reject(module, store, gate, rq, failure):
    before = copy.deepcopy((store._state, rq, gate.calls))
    assert outcome(store.send, module.ByomError, rq) == (
        "failure",
        failure,
        module.FAILURE_MAPPING[failure],
    )
    assert (store._state, rq, gate.calls) == before


@pytest.mark.parametrize("seed", range(30))
def test_seeded_schema_fuzz_has_no_cloud_side_effect(seed):
    rows = defects()
    random.Random(seed).shuffle(rows)
    for failure, rq in rows:
        store, gate = world(production)
        reject(production, store, gate, rq, failure)
        assert outcome(store.send, production.ByomError, send())[0] == "ok"


@pytest.mark.parametrize("failure", ["cloud_mode_off", "collection_sensitive"])
def test_composed_gate_refusal_is_pure_and_followup_succeeds(failure):
    store, gate = world(production)
    gate.refusal = failure
    rq = send()
    before = copy.deepcopy((store._state, rq))
    assert outcome(store.send, production.ByomError, rq) == ("failure", failure, "cloud_off")
    assert (store._state, rq) == before
    assert len(gate.calls) == 1
    gate.refusal = None
    assert outcome(store.send, production.ByomError, rq)[0] == "ok"


@pytest.mark.parametrize(
    "old,new",
    [
        ("if not keys <= _PAYLOAD_KEYS or not keys >= _MANDATORY:", "if not keys >= _MANDATORY:"),
        (
            "type(n) is not int or not 1 <= n <= _TOKENS_MAX",
            "type(n) is not int or not 1 <= n <= _TOKENS_MAX+1",
        ),
        (
            "type(days) is not int or not 0 <= days <= _MAX_DAYS",
            "type(days) is not int or not 0 <= days <= _MAX_DAYS+1",
        ),
    ],
)
def test_independent_red_schema_reproductions(old, new):
    module = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        for failure, rq in defects():
            store, gate = world(module)
            reject(module, store, gate, rq, failure)


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_gate_crash_is_not_semantic(exception):
    store, gate = world(production)

    def broken(rq):
        raise exception("synthetic foreign gate crash")

    gate.verdict = broken
    with pytest.raises(ForeignCrash):
        outcome(store.send, production.ByomError, send())
