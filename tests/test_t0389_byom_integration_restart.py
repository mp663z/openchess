"""T0389 fresh-process byom embedding integration/restart.

Known-valid synthetic journal/snapshot, no production persistence API or durable
crash/restart promise. Redaction is stateless; no snapshot is retained. Arbitrary
callable retention remains outside scope. Named controls pin state/output faults.
"""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import byom as production
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
    from desktop.privacy.cloud_off import opt_in_for
    from desktop.privacy.sensitive_flag import approval_for

    accepted = {
        "install_id": INS,
        "provider_id": PID,
        "terms": TERMS,
        "expected_revision": 0,
        "approval": production.byom_approval_for(INS, PID, production.terms_digest(TERMS), 0),
    }
    send = {
        "install_id": INS,
        "provider_id": PID,
        "collection_id": CID,
        "terms": TERMS,
        "payload": {"fen": "F", "task": "weekly-plan"},
    }
    return [
        step("flag_register", {"collection_id": CID}),
        step(
            "flag_transition",
            {
                "collection_id": CID,
                "sensitive": False,
                "expected_revision": 0,
                "approval": approval_for(CID, 0),
            },
        ),
        step("cloud_register", {"install_id": INS}),
        step(
            "cloud_transition",
            {
                "install_id": INS,
                "cloud_mode": "on",
                "expected_revision": 0,
                "opt_in": opt_in_for(INS, 0),
            },
        ),
        step("register", {"install_id": INS, "provider_id": PID}),
        step("transition", accepted),
        step("send", send),
        step("transition", dict(accepted, terms=None, expected_revision=1, approval=None)),
        step("send", send),
    ]


@pytest.mark.parametrize("cut", range(10))
def test_fresh_interpreter_snapshot_suffix_agrees_with_uninterrupted(cut):
    journal = scenario()
    before = copy.deepcopy(journal)
    first = harness.fresh("byom", journal[:cut])
    assert first == canonical(harness.replay("byom", journal[:cut]))
    entire = harness.fresh("byom", journal)
    resumed = harness.fresh("byom", journal[cut:], first["state"])
    assert resumed["state"] == entire["state"]
    assert resumed["outputs"] == entire["outputs"][cut:]
    assert journal == before
    assert entire["outputs"][-1] == ["failure", "terms_not_accepted", "terms_required"]
    assert entire["state"]["terms"] == [[INS, PID, None, 2]]


def test_independent_lost_commit_or_sink_reproduction_is_red():
    journal = scenario()
    expected = canonical(harness.replay("byom", journal))
    # Lose committed revoke, not a read-only send response.
    index = 7
    lost = harness.fresh("byom", journal[:index] + journal[index + 1 :])
    assert lost["state"] != expected["state"]
    assert lost["outputs"][-1][0] == "ok"
    with pytest.raises(AssertionError):
        assert lost["state"] == expected["state"]


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_candidate_crashes_are_not_semantic_kills(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.ByomError, {})
