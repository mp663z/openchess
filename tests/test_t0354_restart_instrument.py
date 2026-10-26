"""T0354 content-free restart instrumentation, identity/parity and red accounting."""

from __future__ import annotations

import copy
import json

import pytest

from server import jobs_restart as policy
from server import jobs_restart_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant

MAX_NOW = 2**53 - 1


class Engine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def restart(self, state, request):
        self.calls.append((state, request))
        if self.error is not None:
            raise self.error
        return self.result


def boundary(fn, *args):
    try:
        return ("ok", fn(*args))
    except policy.RestartError as exc:
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
    state = {"synthetic-private-state": []}
    request = {"synthetic-private-request": "fixture"}
    engine = Engine(result)
    tracer = ok(boundary(module.RestartTracer, engine))
    assert ok(boundary(tracer.restart, state, request)) is result
    assert engine.calls[0][0] is state and engine.calls[0][1] is request
    assert view(tracer) == ({"seq": 0, "operation": "restart", "outcome": "accept"},)
    records = view(tracer)
    records[0]["outcome"] = "tamper"
    assert view(tracer)[0]["outcome"] == "accept"
    sentinel = policy.RestartError("malformed_restart_request")
    engine.error = sentinel
    got = boundary(tracer.restart, state, request)
    if got[0] == "crash":
        raise ForeignCrash(type(got[1]).__name__) from got[1]
    assert got[0] == "reject" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 1, "operation": "restart", "outcome": "reject"}
    sentinel = AssertionError("synthetic-private-crash")
    engine.error = sentinel
    got = boundary(tracer.restart, state, request)
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 2, "operation": "restart", "outcome": "crash"}
    assert all(set(row) == {"seq", "operation", "outcome"} for row in view(tracer))
    visible = json.dumps(view(tracer))
    assert "synthetic-private" not in visible and "fixture" not in visible

    class HostileTracer(module.RestartTracer):
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
    got = boundary(hostile.restart, state, request)
    assert got[0] == "ok" and got[1] is result
    reject = policy.RestartError("malformed_restart_request")
    hostile_engine.error = reject
    got = boundary(hostile.restart, state, request)
    assert got[0] == "reject" and got[1] is reject
    crash = AssertionError("synthetic-private-crash")
    hostile_engine.error = crash
    got = boundary(hostile.restart, state, request)
    assert got[0] == "crash" and got[1] is crash
    assert HostileTracer.getter_calls == ["get", "get", "get"]


def test_identity_exact_outcomes_and_content_free_detached_trace():
    probe(production)


@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_foreign_exception_identity_is_preserved(exception):
    sentinel = exception("private fixture")
    tracer = production.RestartTracer(Engine(error=sentinel))
    got = boundary(tracer.restart, {}, {})
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer) == ({"seq": 0, "operation": "restart", "outcome": "crash"},)


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
        else policy.RestartError("malformed_restart_request")
        if kind == "reject"
        else AssertionError("crash")
    )
    tracer = production.RestartTracer(Engine(result, error))
    tracer._trace = Bomb()
    assert view(tracer) == ()
    got = boundary(tracer.restart, {}, {})
    assert got[0] == ("ok" if kind == "accept" else kind)
    assert got[1] is (result if kind == "accept" else error)
    assert view(tracer) == ({"seq": 0, "operation": "restart", "outcome": kind},)
    assert calls == []


def job(seq, status, attempts, owner=None, expires=None):
    return {
        "job_id": "job1:" + format(seq, "064x"),
        "seq": seq,
        "priority": 1,
        "payload": {"private": "fixture"},
        "status": status,
        "attempts": attempts,
        "lease_owner": owner,
        "lease_expires_at": expires,
    }


def test_real_restart_stream_matches_bare_module_on_mutation_purity_and_atomicity():
    left = {
        "jobs": [
            job(0, "leased", 1, "w1", 100),
            job(1, "leased", 1, "w2", 100),
            job(2, "ready", 0),
            job(3, "leased", 1, "w1", 50),
        ],
        "next_seq": 4,
    }
    right = copy.deepcopy(left)
    tracer = production.RestartTracer(policy)
    stream = [
        {"op": "restart", "worker": "w1", "now": 99},  # releases seq 0; seq 3 lease lapsed
        {"op": "restart", "worker": "w1", "now": 100},  # boundary: exp == now is not live
        {},  # malformed: state untouched
        {"op": "restart", "worker": "w2", "now": 0},  # releases seq 1
    ]
    outcomes = []
    for request in stream:
        original = copy.deepcopy(request)
        state_before = copy.deepcopy(left)
        a = boundary(tracer.restart, left, request)
        b = boundary(policy.restart, right, request)
        assert a[0] == b[0] and a[0] != "crash"
        if a[0] == "ok":
            assert a[1] == b[1]
        else:
            assert a[1].failure_class == b[1].failure_class and a[1].code == b[1].code
            assert left == state_before
        assert left == right and request == original
        outcomes.append("accept" if a[0] == "ok" else a[0])
    assert outcomes == ["accept", "accept", "reject", "accept"]
    assert [r["outcome"] for r in view(tracer)] == outcomes
    assert [j["status"] for j in left["jobs"]] == ["ready", "ready", "ready", "leased"]
    assert left["jobs"][3]["lease_owner"] == "w1"  # lapsed lease is left alone
    assert "fixture" not in json.dumps(view(tracer))
    bad_states = (
        {"jobs": [], "next_seq": -1},
        {"jobs": [job(0, "leased", 0, "w1", 100)], "next_seq": 1},  # leased needs attempts >= 1
        {},
    )
    for bad in bad_states:
        a_state, b_state = copy.deepcopy(bad), copy.deepcopy(bad)
        request = {"op": "restart", "worker": "w1", "now": 0}
        a = boundary(tracer.restart, a_state, request)
        b = boundary(policy.restart, b_state, request)
        assert a[0] == b[0] == "reject"
        assert a[1].failure_class == b[1].failure_class == "corrupt_queue"
        assert a_state == b_state == bad


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
    class Broken(production.RestartTracer):
        def restart(self, state, request):
            raise AssertionError("candidate-only crash")

    class Module:
        RestartTracer = Broken

    with pytest.raises(ForeignCrash):
        probe(Module)
