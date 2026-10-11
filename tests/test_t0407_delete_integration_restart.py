"""T0407 fresh-process delete embedding integration/restart.

Known-valid synthetic journal/snapshot, no production persistence API or durable
crash/restart promise. Redaction is stateless; no snapshot is retained. Arbitrary
callable retention remains outside scope. Named controls pin state/output faults.
"""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import delete as production
from tests import privacy_later_restart_support as harness
from tests.privacy_probe_support import ForeignCrash, outcome

CID = "col1:" + "a" * 64
INS = "ins1:" + "b" * 64
PID = "prv1:fixture"
TERMS = {"retention_days": 30, "region": "eu", "trains_on_inputs": False}


def step(op, rq):
    return {"op": op, "request": rq}


def canonical(value):
    return json.loads(json.dumps(value))


def scenario():
    return [
        step("register", {"collection_id": CID}),
        step(
            "delete",
            {
                "collection_id": CID,
                "expected_revision": 0,
                "decision": production.decision_for(CID, 0),
            },
        ),
        step("access", {"collection_id": CID}),
        step("delete", {"collection_id": CID, "expected_revision": 1, "decision": None}),
        step("access", {"collection_id": CID}),
    ]


@pytest.mark.parametrize("cut", range(6))
def test_fresh_interpreter_snapshot_suffix_agrees_with_uninterrupted(cut):
    journal = scenario()
    before = copy.deepcopy(journal)
    first = harness.fresh("delete", journal[:cut])
    assert first == canonical(harness.replay("delete", journal[:cut]))
    entire = harness.fresh("delete", journal)
    resumed = harness.fresh("delete", journal[cut:], first["state"])
    assert resumed["state"] == entire["state"]
    assert resumed["outputs"] == entire["outputs"][cut:]
    assert journal == before
    assert entire["outputs"][-1] == ["failure", "collection_deleted", "collection_deleted"]
    assert entire["state"]["state"][CID] == ["deleted", 1, []]
    assert all(not b["data"] for b in entire["state"]["backends"].values())


def test_independent_lost_commit_or_sink_reproduction_is_red():
    journal = scenario()
    expected = canonical(harness.replay("delete", journal))
    # Lose committed retry; pending index store must survive the fault.
    lost = harness.fresh("delete", journal[:3] + journal[4:])
    assert lost["state"]["state"][CID][2] == ["index"]
    assert lost["state"] != expected["state"]
    with pytest.raises(AssertionError):
        assert lost["state"] == expected["state"]


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_candidate_crashes_are_not_semantic_kills(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.DeleteError, {})
