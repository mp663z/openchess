"""T0345 content-free limit instrumentation, identity/parity and red accounting."""

from __future__ import annotations

import copy
import json

import pytest

from server import jobs_limit as policy
from server import jobs_limit_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant

MAX_NOW = 2**53 - 1


class Engine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def admit(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def boundary(fn, *args):
    try:
        return ("ok", fn(*args))
    except policy.LimitError as exc:
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
    tracer = ok(boundary(module.LimitTracer, engine))
    assert ok(boundary(tracer.admit, request)) is result
    assert engine.calls[0] is request
    assert view(tracer) == ({"seq": 0, "operation": "admit", "outcome": "accept"},)
    records = view(tracer)
    records[0]["outcome"] = "tamper"
    assert view(tracer)[0]["outcome"] == "accept"
    sentinel = policy.LimitError("malformed_limit_request")
    engine.error = sentinel
    got = boundary(tracer.admit, request)
    if got[0] == "crash":
        raise ForeignCrash(type(got[1]).__name__) from got[1]
    assert got[0] == "reject" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 1, "operation": "admit", "outcome": "reject"}
    sentinel = AssertionError("synthetic-private-crash")
    engine.error = sentinel
    got = boundary(tracer.admit, request)
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 2, "operation": "admit", "outcome": "crash"}
    assert all(set(row) == {"seq", "operation", "outcome"} for row in view(tracer))
    visible = json.dumps(view(tracer))
    assert "synthetic-private" not in visible and "fixture" not in visible

    class HostileTracer(module.LimitTracer):
        getter_calls = []

        @property
        def _trace(self):
            type(self).getter_calls.append("get")
            raise KeyboardInterrupt("diagnostics hook")

        @_trace.setter
        def _trace(self, value):
            object.__setattr__(self, "_real_trace", value)

    hostile_engine = Engine(result)
    hostile = HostileTracer(hostile_engine)
    got = boundary(hostile.admit, request)
    assert got[0] == "ok" and got[1] is result
    reject = policy.LimitError("malformed_limit_request")
    hostile_engine.error = reject
    got = boundary(hostile.admit, request)
    assert got[0] == "reject" and got[1] is reject
    crash = AssertionError("synthetic-private-crash")
    hostile_engine.error = crash
    got = boundary(hostile.admit, request)
    assert got[0] == "crash" and got[1] is crash
    assert HostileTracer.getter_calls == ["get", "get", "get"]


def test_identity_exact_outcomes_and_content_free_detached_trace():
    probe(production)


@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_foreign_exception_identity_is_preserved(exception):
    sentinel = exception("private fixture")
    tracer = production.LimitTracer(Engine(error=sentinel))
    got = boundary(tracer.admit, {})
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer) == ({"seq": 0, "operation": "admit", "outcome": "crash"},)


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
        else policy.LimitError("malformed_limit_request")
        if kind == "reject"
        else AssertionError("crash")
    )
    tracer = production.LimitTracer(Engine(result, error))
    tracer._trace = Bomb()
    assert view(tracer) == ()
    got = boundary(tracer.admit, {})
    assert got[0] == ("ok" if kind == "accept" else kind)
    assert got[1] is (result if kind == "accept" else error)
    assert view(tracer) == ({"seq": 0, "operation": "admit", "outcome": kind},)
    assert calls == []


def admit_req(key, cost, now, capacity, refill_ms, previous):
    return {
        "op": "admit",
        "key": key,
        "cost": cost,
        "now": now,
        "policy": {"capacity": capacity, "refill_ms": refill_ms},
        "previous": previous,
    }


def test_real_admit_stream_agrees_with_bare_module_and_keeps_requests_pure():
    tracer = production.LimitTracer(policy)
    first = admit_req("k1", 10, 0, 10, 100, None)
    r1 = policy.admit(copy.deepcopy(first))
    deny_req = admit_req("k1", 10, 0, 10, 100, r1)  # empty bucket: deny
    r2 = policy.admit(copy.deepcopy(deny_req))
    edge = admit_req("k3", 10, MAX_NOW - 100, 10, 86_400_000, None)
    r_edge = policy.admit(copy.deepcopy(edge))
    stream = [
        first,
        deny_req,
        admit_req("k1", 10, 1000, 10, 100, r2),  # refilled exactly at retry_after
        admit_req("k2", 1, 0, 1, 86_400_000, None),  # cost == capacity boundary
        admit_req("k3", 10, MAX_NOW - 50, 10, 86_400_000, r_edge),  # clock_overflow
        admit_req("k1", 11, 0, 10, 100, None),  # cost above capacity: malformed
        admit_req("k1", 1, 0, 0, 100, None),  # invalid_limit_policy
        admit_req("k1", 1, 0, 10, 100, {}),  # corrupt_previous_bucket
        admit_req("kX", 1, 1, 10, 100, r1),  # limit_conflict: key differs
        {},
    ]
    outcomes = []
    for request in stream:
        original = copy.deepcopy(request)
        a = boundary(tracer.admit, request)
        b = boundary(policy.admit, request)
        assert a[0] == b[0] and a[0] != "crash"
        if a[0] == "ok":
            assert a[1] == b[1]
        else:
            assert a[1].failure_class == b[1].failure_class and a[1].code == b[1].code
        assert request == original
        outcomes.append("accept" if a[0] == "ok" else a[0])
    assert outcomes == ["accept"] * 4 + ["reject"] * 6
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
    (
        "swallow-scope",
        "except BaseException:\n"
        "            pass  # diagnostics must never replace a policy result or exception",
        "except Exception:\n"
        "            pass  # diagnostics must never replace a policy result or exception",
    ),
]


@pytest.mark.parametrize("name,old,new", EDITS, ids=[e[0] for e in EDITS])
def test_named_edits_killed_by_oracle_not_crash(name, old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        probe(mutant)


def test_candidate_assertion_only_crash_is_not_oracle_kill():
    class Broken(production.LimitTracer):
        def admit(self, request):
            raise AssertionError("candidate-only crash")

    class Module:
        LimitTracer = Broken

    with pytest.raises(ForeignCrash):
        probe(Module)
