"""T0336 content-free dead letter instrumentation, identity/parity and red accounting."""

from __future__ import annotations

import copy
import json

import pytest

from server import jobs_dead_letter as policy
from server import jobs_dead_letter_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant

JOB = "job1:" + "0" * 64
MAX_NOW = 2**53 - 1


class Engine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def bury(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def boundary(fn, *args):
    try:
        return ("ok", fn(*args))
    except policy.DeadLetterError as exc:
        return ("reject", exc)
    except BaseException as exc:
        return ("crash", exc)


def ok(result):
    if result[0] == "crash":
        raise ForeignCrash(type(result[1]).__name__) from result[1]
    assert result[0] == "ok"
    return result[1]


def view(tracer):
    return ok(boundary(lambda: tracer.records))


def probe(module):
    result = {"synthetic-private-result": [1]}
    request = {"synthetic-private-request": "fixture"}
    engine = Engine(result)
    tracer = ok(boundary(module.DeadLetterTracer, engine))
    assert ok(boundary(tracer.bury, request)) is result
    assert engine.calls[0] is request
    assert view(tracer) == ({"seq": 0, "operation": "bury", "outcome": "accept"},)
    records = view(tracer)
    records[0]["outcome"] = "tamper"
    assert view(tracer)[0]["outcome"] == "accept"
    sentinel = policy.DeadLetterError("malformed_bury_request")
    engine.error = sentinel
    got = boundary(tracer.bury, request)
    if got[0] == "crash":
        raise ForeignCrash(type(got[1]).__name__) from got[1]
    assert got[0] == "reject" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 1, "operation": "bury", "outcome": "reject"}
    sentinel = AssertionError("synthetic-private-crash")
    engine.error = sentinel
    got = boundary(tracer.bury, request)
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 2, "operation": "bury", "outcome": "crash"}
    assert all(set(row) == {"seq", "operation", "outcome"} for row in view(tracer))
    visible = json.dumps(view(tracer))
    assert "synthetic-private" not in visible and "fixture" not in visible


def test_identity_exact_outcomes_and_content_free_detached_trace():
    probe(production)


@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_foreign_exception_identity_is_preserved(exception):
    sentinel = exception("private fixture")
    tracer = production.DeadLetterTracer(Engine(error=sentinel))
    got = boundary(tracer.bury, {})
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer) == ({"seq": 0, "operation": "bury", "outcome": "crash"},)


@pytest.mark.parametrize("kind", ["accept", "reject", "crash"])
def test_hostile_trace_list_never_executes_hook(kind):
    calls = []

    class Bomb(list):
        def append(self, value):
            calls.append("append")
            raise AssertionError("caller hook")

        def __iter__(self):
            calls.append("iter")
            raise KeyError("caller hook")

    result = {"ok": []}
    error = (
        None
        if kind == "accept"
        else policy.DeadLetterError("malformed_bury_request")
        if kind == "reject"
        else AssertionError("crash")
    )
    tracer = production.DeadLetterTracer(Engine(result, error))
    tracer._trace = Bomb()
    assert view(tracer) == ()
    got = boundary(tracer.bury, {})
    assert got[0] == ("ok" if kind == "accept" else kind)
    assert got[1] is (result if kind == "accept" else error)
    assert view(tracer) == ({"seq": 0, "operation": "bury", "outcome": kind},)
    assert calls == []


def dead_job(seq, **overrides):
    job = {
        "job_id": "job1:" + format(seq, "064x"),
        "seq": seq,
        "priority": 1,
        "payload": {"private": "fixture"},
        "status": "dead",
        "attempts": policy.MAX_ATTEMPTS,
        "lease_owner": None,
        "lease_expires_at": None,
    }
    job.update(overrides)
    return job


def test_real_bury_stream_agrees_with_bare_module_and_keeps_requests_pure():
    tracer = production.DeadLetterTracer(policy)
    stream = [
        {"op": "bury", "job": dead_job(0), "reason": "exhausted", "now": 0},
        {"op": "bury", "job": dead_job(1, payload={}), "reason": "permanent", "now": MAX_NOW},
        {
            "op": "bury",
            "job": dead_job(2, status="ready", attempts=0),
            "reason": "exhausted",
            "now": 0,
        },
        {"op": "bury", "job": dead_job(3, attempts=1), "reason": "exhausted", "now": 0},
        {"op": "bury", "job": {}, "reason": "exhausted", "now": 0},
        {"op": "bury", "job": dead_job(4), "reason": "unknown", "now": 0},
        {},
    ]
    outcomes = []
    for request in stream:
        original = copy.deepcopy(request)
        a = boundary(tracer.bury, request)
        b = boundary(policy.bury, request)
        assert a[0] == b[0] and a[0] != "crash"
        if a[0] == "ok":
            assert a[1] == b[1]
        else:
            assert a[1].failure_class == b[1].failure_class and a[1].code == b[1].code
        assert request == original
        outcomes.append("accept" if a[0] == "ok" else a[0])
    assert outcomes == ["accept"] * 2 + ["reject"] * 5
    assert [r["outcome"] for r in view(tracer)] == outcomes
    assert "fixture" not in json.dumps(view(tracer))


EDITS = [
    ("copy-result", "return result", "return dict(result)"),
    ("wrong-refusal", 'self._append(operation, "reject")', 'self._append(operation, "accept")'),
    ("wrong-crash", 'self._append(operation, "crash")', 'self._append(operation, "accept")'),
    (
        "alias-view",
        "return tuple(dict(row) for row in trace) if type(trace) is list else ()",
        "return tuple(trace) if type(trace) is list else ()",
    ),
    ("sequence", '"seq": list.__len__(trace)', '"seq": 0'),
    ("disclosure", '"outcome": outcome', '"outcome": outcome, "private": "fixture"'),
]


@pytest.mark.parametrize("name,old,new", EDITS, ids=[e[0] for e in EDITS])
def test_named_edits_killed_by_oracle_not_crash(name, old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        probe(mutant)


def test_candidate_assertion_only_crash_is_not_oracle_kill():
    class Broken(production.DeadLetterTracer):
        def bury(self, request):
            raise AssertionError("candidate-only crash")

    class Module:
        DeadLetterTracer = Broken

    with pytest.raises(ForeignCrash):
        probe(Module)
