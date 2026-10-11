"""T0381 instrument identity, content exclusion, trace faults and red controls."""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import local_model as policy
from desktop.privacy import local_model_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome


class Engine:
    def __init__(self, result=None, exception=None):
        self.result = result
        self.exception = exception
        self.calls = []

    def transition(self, rq):
        self.calls.append(rq)
        if self.exception is not None:
            raise self.exception
        return self.result


def guarded(fn, rq):
    try:
        return fn(rq)
    except policy.ModelError:
        raise
    except Exception as exc:
        raise ForeignCrash(type(exc).__name__) from exc


def probe(module):
    receipt = {"synthetic-private-result": [1]}
    rq = {"synthetic-private-input": "fixture"}
    engine = Engine(receipt)
    tracer = module.ModelTracer(engine)
    got = guarded(tracer.transition, rq)
    assert got is receipt and engine.calls[0] is rq
    assert tracer.records == ({"seq": 0, "operation": "transition", "outcome": "accept"},)
    visible = json.dumps(tracer.records)
    assert "synthetic-private" not in visible and "fixture" not in visible
    view = tracer.records
    view[0]["outcome"] = "tamper"
    assert tracer.records[0]["outcome"] == "accept"
    error = policy.ModelError("malformed_model_request")
    engine.exception = error
    try:
        guarded(tracer.transition, rq)
    except policy.ModelError as caught:
        assert caught is error
    else:
        raise AssertionError("original refusal swallowed")
    assert tracer.records[-1] == {"seq": 1, "operation": "transition", "outcome": "reject"}


def test_exact_identity_purity_and_content_free_records():
    probe(production)


@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_foreign_crash_is_reraised_same_object(exception):
    error = exception("synthetic-private-crash")
    engine = Engine(exception=error)
    tracer = production.ModelTracer(engine)
    with pytest.raises(exception) as caught:
        tracer.transition({})
    assert caught.value is error
    assert tracer.records == ({"seq": 0, "operation": "transition", "outcome": "crash"},)


def test_faulting_trace_container_does_not_execute_hooks_or_change_result():
    calls = []

    class Bomb(list):
        def append(self, value):
            calls.append(value)
            raise AssertionError("hook")

    receipt = {"ok": []}
    tracer = production.ModelTracer(Engine(receipt))
    tracer._trace = Bomb()
    assert tracer.transition({}) is receipt
    assert calls == [] and tracer.records[0]["outcome"] == "accept"


@pytest.mark.parametrize(
    "old,new",
    [
        ("return result", "return dict(result)"),
        ('self._append(operation, "reject")', 'self._append(operation, "accept")'),
        (
            "return tuple(dict(row) for row in trace) if type(trace) is list else ()",
            "return tuple(trace) if type(trace) is list else ()",
        ),
    ],
)
def test_independent_red_reproductions(old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        probe(mutant)


def test_candidate_assertion_only_crash_is_not_semantic_kill():
    error = AssertionError("candidate-only crash")
    tracer = production.ModelTracer(Engine(exception=error))
    with pytest.raises(ForeignCrash):
        outcome(tracer.transition, policy.ModelError, {})


def test_real_policy_wrapper_matches_bare_state_and_refusal():
    wrapped, bare = policy.LocalModels(), policy.LocalModels()
    tracer = production.ModelTracer(wrapped)
    cid = "ins1:" + "a" * 64
    assert tracer.register(cid) == bare.register(cid)
    rq = {
        "install_id": cid,
        "large_model": "on",
        "expected_revision": 0,
        "opt_in": policy.opt_in_for(cid, 0),
    }
    snapshot = copy.deepcopy(rq)
    assert tracer.transition(rq) == bare.transition(rq)
    assert wrapped._state == bare._state and rq == snapshot
    before = copy.deepcopy(wrapped._state)
    result = outcome(tracer.transition, policy.ModelError, rq)
    assert result == outcome(bare.transition, policy.ModelError, rq)
    assert wrapped._state == bare._state == before
    assert [r["outcome"] for r in tracer.records] == ["accept", "accept", "reject"]
    assert set(tracer.records[-1]) == {"seq", "operation", "outcome"}
