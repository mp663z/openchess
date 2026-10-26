"""T0318 content-free retry instrumentation, identity/parity and red accounting."""

from __future__ import annotations

import copy
import json

import pytest

from server import jobs_retry as policy
from server import jobs_retry_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant

JOB = "job1:" + "0" * 64
MAX_NOW = 2**53 - 1


class Engine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def decide(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def boundary(fn, *args):
    try:
        return ("ok", fn(*args))
    except policy.RetryError as exc:
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
    tracer = ok(boundary(module.RetryTracer, engine))
    assert ok(boundary(tracer.decide, request)) is result
    assert engine.calls[0] is request
    assert view(tracer) == ({"seq": 0, "operation": "decide", "outcome": "accept"},)
    records = view(tracer)
    records[0]["outcome"] = "tamper"
    assert view(tracer)[0]["outcome"] == "accept"
    sentinel = policy.RetryError("malformed_retry_request")
    engine.error = sentinel
    got = boundary(tracer.decide, request)
    if got[0] == "crash":
        raise ForeignCrash(type(got[1]).__name__) from got[1]
    assert got[0] == "reject" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 1, "operation": "decide", "outcome": "reject"}
    sentinel = AssertionError("synthetic-private-crash")
    engine.error = sentinel
    got = boundary(tracer.decide, request)
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 2, "operation": "decide", "outcome": "crash"}
    assert all(set(row) == {"seq", "operation", "outcome"} for row in view(tracer))
    visible = json.dumps(view(tracer))
    assert "synthetic-private" not in visible and "fixture" not in visible


def test_identity_exact_outcomes_and_content_free_detached_trace():
    probe(production)


@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_foreign_exception_identity_is_preserved(exception):
    sentinel = exception("private fixture")
    tracer = production.RetryTracer(Engine(error=sentinel))
    got = boundary(tracer.decide, {})
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer) == ({"seq": 0, "operation": "decide", "outcome": "crash"},)


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
        else policy.RetryError("malformed_retry_request")
        if kind == "reject"
        else AssertionError("crash")
    )
    tracer = production.RetryTracer(Engine(result, error))
    tracer._trace = Bomb()
    assert view(tracer) == ()
    got = boundary(tracer.decide, {})
    assert got[0] == ("ok" if kind == "accept" else kind)
    assert got[1] is (result if kind == "accept" else error)
    assert view(tracer) == ({"seq": 0, "operation": "decide", "outcome": kind},)
    assert calls == []


def test_real_decide_stream_agrees_with_bare_module_and_keeps_requests_pure():
    tracer = production.RetryTracer(policy)
    pol = {"base_delay_ms": 1000, "multiplier": 10, "max_delay_ms": 10_000}

    def req(attempts, failure, now, policy_map):
        return {
            "op": "decide",
            "job_id": JOB,
            "attempts": attempts,
            "failure": failure,
            "now": now,
            "policy": policy_map,
        }

    stream = [
        req(1, "transient", 0, dict(pol)),
        req(3, "timeout", 5, dict(pol)),  # delay capped at max_delay_ms
        req(1, "permanent", 0, dict(pol)),  # permanent failure: dead
        req(5, "transient", 0, dict(pol)),  # attempts == MAX_ATTEMPTS: dead
        req(1, "transient", MAX_NOW - 9999, dict(pol)),  # retry_at still within the clock
        req(1, "transient", MAX_NOW, dict(pol)),  # clock_overflow
        req(1, "transient", 0, {"base_delay_ms": 0, "multiplier": 10, "max_delay_ms": 10_000}),
        {},
    ]
    outcomes = []
    for request in stream:
        original = copy.deepcopy(request)
        a = boundary(tracer.decide, request)
        b = boundary(policy.decide, request)
        assert a[0] == b[0] and a[0] != "crash"
        if a[0] == "ok":
            assert a[1] == b[1]
        else:
            assert a[1].failure_class == b[1].failure_class and a[1].code == b[1].code
        assert request == original
        outcomes.append("accept" if a[0] == "ok" else a[0])
    assert outcomes == ["accept"] * 5 + ["reject"] * 3
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
    class Broken(production.RetryTracer):
        def decide(self, request):
            raise AssertionError("candidate-only crash")

    class Module:
        RetryTracer = Broken

    with pytest.raises(ForeignCrash):
        probe(Module)
