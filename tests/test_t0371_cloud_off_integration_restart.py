"""T0371 repository-hosted integration with fresh-process journal replay.

Scope: synthetic validated operation journal, not production persistence.
A fresh interpreter replays public policy operations, then continues at the
retained revision. Refused operations cannot change the replayed state. No
claims of crash-safe disk storage, arbitrary corrupt snapshots or app restart.
"""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import cloud_off as production
from tests import privacy_restart_support as harness
from tests.privacy_probe_support import ForeignCrash, outcome

CID = "ins1:" + "a" * 64
COL = "col1:" + "b" * 64


def entry(op, request):
    return {"op": op, "request": request}


def transition(target, revision, approved=True):
    return entry(
        "transition",
        {
            "install_id": CID,
            "cloud_mode": target,
            "expected_revision": revision,
            "opt_in": production.opt_in_for(CID, revision) if approved else None,
        },
    )


def canonical(value):
    return json.loads(json.dumps(value))


def scenario():
    return [
        entry("flag_register", {"collection_id": COL}),
        entry(
            "flag_transition",
            {
                "collection_id": COL,
                "sensitive": False,
                "expected_revision": 0,
                "approval": __import__(
                    "desktop.privacy.sensitive_flag", fromlist=["approval_for"]
                ).approval_for(COL, 0),
            },
        ),
    ] + [
        entry("register", {"install_id": CID}),
        transition("on", 0),
        transition("off", 1),
        transition("on", 2),
        entry("verdict", {"install_id": CID, "collection_id": COL, "destination": "cloud"}),
    ]


@pytest.mark.parametrize("cut", range(8))
def test_fresh_process_replay_and_continuation(cut):
    journal = scenario()
    before = copy.deepcopy(journal)
    prefix = journal[:cut]
    first = harness.fresh("cloud_off", prefix)
    assert first == canonical(harness.replay("cloud_off", prefix))
    continued = harness.fresh("cloud_off", journal)
    assert continued == canonical(harness.replay("cloud_off", journal))
    resumed = harness.fresh("cloud_off", journal[cut:], first["state"])
    assert resumed["state"] == continued["state"]
    assert resumed["outputs"] == continued["outputs"][cut:]
    assert journal == before
    assert continued["outputs"][-1] == ["ok", "allow"]
    assert continued["state"]["policy"][CID][1] == 3


def refusal_probe(replay):
    journal = scenario()
    good = replay("cloud_off", journal)
    rq = transition("on", 0)["request"]
    got = replay("cloud_off", journal + [entry("transition", rq)])
    assert canonical(got["state"]) == canonical(good["state"])
    assert list(got["outputs"][-1]) == ["failure", "stale_revision", "conflict"]


def test_stale_pre_restart_request_does_not_commit():
    refusal_probe(harness.replay)


def test_independent_lost_state_reproduction_is_red():
    def drops_last(policy, journal):
        return harness.replay(policy, journal[:-1])

    with pytest.raises(AssertionError):
        refusal_probe(drops_last)


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_candidate_only_crash_not_semantic(exception):
    def broken(rq):
        raise exception("candidate crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.CloudError, {})
