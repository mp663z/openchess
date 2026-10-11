"""T0380 repository-hosted integration with fresh-process journal replay.

Scope: synthetic validated operation journal, not production persistence.
A fresh interpreter replays public policy operations, then continues at the
retained revision. Refused operations cannot change the replayed state. No
claims of crash-safe disk storage, arbitrary corrupt snapshots or app restart.
"""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import local_model as production
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
            "large_model": target,
            "expected_revision": revision,
            "opt_in": production.opt_in_for(CID, revision) if approved else None,
        },
    )


def canonical(value):
    return json.loads(json.dumps(value))


def scenario():
    return [
        entry("register", {"install_id": CID}),
        transition("on", 0),
        transition("off", 1),
        transition("on", 2),
        entry(
            "verdict",
            {"install_id": CID, "tier": "large", "host": "desktop", "destination": "local"},
        ),
    ]


@pytest.mark.parametrize("cut", range(6))
def test_fresh_process_replay_and_continuation(cut):
    journal = scenario()
    before = copy.deepcopy(journal)
    prefix = journal[:cut]
    first = harness.fresh("local_model", prefix)
    assert first == canonical(harness.replay("local_model", prefix))
    continued = harness.fresh("local_model", journal)
    assert continued == canonical(harness.replay("local_model", journal))
    resumed = harness.fresh("local_model", journal[cut:], first["state"])
    assert resumed["state"] == continued["state"]
    assert resumed["outputs"] == continued["outputs"][cut:]
    assert journal == before
    assert continued["outputs"][-1] == ["ok", {"verdict": "allow", "reference_claims": "suspended"}]
    assert continued["state"]["policy"][CID][1] == 3


def refusal_probe(replay):
    journal = scenario()
    good = replay("local_model", journal)
    rq = transition("on", 0)["request"]
    got = replay("local_model", journal + [entry("transition", rq)])
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
        outcome(broken, production.ModelError, {})
