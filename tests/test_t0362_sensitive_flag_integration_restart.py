"""T0362 repository-hosted integration with fresh-process journal replay.

Scope: synthetic validated operation journal, not production persistence.
A fresh interpreter replays public policy operations, then continues at the
retained revision. Refused operations cannot change the replayed state. No
claims of crash-safe disk storage, arbitrary corrupt snapshots or app restart.
"""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import sensitive_flag as production
from tests import privacy_restart_support as harness
from tests.privacy_probe_support import ForeignCrash, outcome

CID = "col1:" + "a" * 64
COL = "col1:" + "b" * 64


def entry(op, request):
    return {"op": op, "request": request}


def transition(target, revision, approved=True):
    return entry(
        "transition",
        {
            "collection_id": CID,
            "sensitive": target,
            "expected_revision": revision,
            "approval": production.approval_for(CID, revision) if approved else None,
        },
    )


def canonical(value):
    return json.loads(json.dumps(value))


def scenario():
    return [
        entry("register", {"collection_id": CID}),
        transition(False, 0),
        transition(True, 1),
        entry(
            "rollback",
            {
                "collection_id": CID,
                "expected_revision": 2,
                "approval": production.approval_for(CID, 2),
            },
        ),
        entry("verdict", {"collection_id": CID, "destination": "cloud"}),
    ]


@pytest.mark.parametrize("cut", range(6))
def test_fresh_process_replay_and_continuation(cut):
    journal = scenario()
    before = copy.deepcopy(journal)
    prefix = journal[:cut]
    first = harness.fresh("sensitive_flag", prefix)
    assert first == canonical(harness.replay("sensitive_flag", prefix))
    continued = harness.fresh("sensitive_flag", journal)
    assert continued == canonical(harness.replay("sensitive_flag", journal))
    resumed = harness.fresh("sensitive_flag", journal[cut:], first["state"])
    assert resumed["state"] == continued["state"]
    assert resumed["outputs"] == continued["outputs"][cut:]
    assert journal == before
    assert continued["outputs"][-1] == ["ok", "allow"]
    assert continued["state"]["policy"][CID][1] == 3


def refusal_probe(replay):
    journal = scenario()
    good = replay("sensitive_flag", journal)
    rq = transition(False, 0)["request"]
    got = replay("sensitive_flag", journal + [entry("transition", rq)])
    assert canonical(got["state"]) == canonical(good["state"])
    assert list(got["outputs"][-1]) == ["failure", "stale_revision", "conflict"]


def test_stale_pre_restart_request_does_not_commit():
    refusal_probe(harness.replay)


def test_independent_lost_committed_transition_is_red():
    journal = scenario()
    # Remove the first policy transition, not a read-only verdict or refusal.
    index = next(i for i, item in enumerate(journal) if item["op"] == "transition")
    incomplete = journal[:index] + journal[index + 1 :]
    expected = canonical(harness.replay("sensitive_flag", journal))
    lost = canonical(harness.replay("sensitive_flag", incomplete))
    # Pin the fault's effect independently of receipt count/content assertions.
    assert lost["state"] != expected["state"]
    with pytest.raises(AssertionError):
        assert lost["state"] == expected["state"]
    # A real fresh interpreter exhibits the same committed-state mismatch.
    restarted = harness.fresh("sensitive_flag", incomplete)
    assert restarted["state"] == lost["state"]
    assert restarted["state"] != expected["state"]


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_candidate_only_crash_not_semantic(exception):
    def broken(rq):
        raise exception("candidate crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.FlagError, {})
