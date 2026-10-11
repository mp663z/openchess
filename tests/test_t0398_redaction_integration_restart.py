"""T0398 fresh-process redaction embedding integration/restart.

Known-valid synthetic journal/snapshot, no production persistence API or durable
crash/restart promise. Redaction is stateless; no snapshot is retained. Arbitrary
callable retention remains outside scope. Named controls pin state/output faults.
"""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import redaction as production
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
        step("redact", {"text": "safe", "secrets": []}),
        step("redact", {"text": "KEY", "secrets": ["key"]}),
        step("emit_text", {"text": "private", "secrets": None}),
        step("redact", {"text": "KEY", "secrets": []}),
        step("emit_text", {"text": "safe", "secrets": []}),
    ]


@pytest.mark.parametrize("cut", range(6))
def test_fresh_interpreter_snapshot_suffix_agrees_with_uninterrupted(cut):
    journal = scenario()
    before = copy.deepcopy(journal)
    first = harness.fresh("redaction", journal[:cut])
    assert first == canonical(harness.replay("redaction", journal[:cut]))
    entire = harness.fresh("redaction", journal)
    resumed = harness.fresh("redaction", journal[cut:], first["state"])
    assert resumed["state"] == entire["state"]
    assert resumed["outputs"] == entire["outputs"][cut:]
    assert journal == before
    assert entire["state"] == {}
    assert entire["outputs"] == [
        ["ok", "safe"],
        ["ok", "redacted"],
        ["ok", "redacted"],
        ["ok", "KEY"],
        ["ok", "safe"],
    ]


def test_independent_lost_commit_or_sink_reproduction_is_red():
    expected = harness.fresh("redaction", scenario())
    faulty = copy.deepcopy(expected)
    faulty["outputs"][2] = ["ok", "private"]
    # Stateless policy: this is explicitly an unsafe sink-output fault, not lost state.
    assert faulty["state"] == expected["state"] == {}
    with pytest.raises(AssertionError):
        assert faulty["outputs"] == expected["outputs"]


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_candidate_crashes_are_not_semantic_kills(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.RedactionError, {})
