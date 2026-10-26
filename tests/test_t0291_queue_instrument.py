"""T0291 content-free queue instrumentation, identity/parity and red accounting."""

from __future__ import annotations

import copy
import json

import pytest

from server import jobs_queue as policy
from server import jobs_queue_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant


class Engine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def apply(self, state, request):
        self.calls.append((state, request))
        if self.error is not None:
            raise self.error
        return self.result


def boundary(fn, *args):
    try:
        return ("ok", fn(*args))
    except policy.QueueError as exc:
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
    rq = {"synthetic-private-request": "fixture"}
    engine = Engine(result)
    tracer = ok(boundary(module.QueueTracer, engine))
    assert ok(boundary(tracer.apply, state, rq)) is result
    assert engine.calls[0][0] is state and engine.calls[0][1] is rq
    assert view(tracer) == ({"seq": 0, "operation": "apply", "outcome": "accept"},)
    records = view(tracer)
    records[0]["outcome"] = "tamper"
    assert view(tracer)[0]["outcome"] == "accept"
    sentinel = policy.QueueError("malformed_queue_request", "malformed_request")
    engine.error = sentinel
    got = boundary(tracer.apply, state, rq)
    if got[0] == "crash":
        raise ForeignCrash(type(got[1]).__name__) from got[1]
    assert got[0] == "reject" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 1, "operation": "apply", "outcome": "reject"}
    sentinel = AssertionError("synthetic-private-crash")
    engine.error = sentinel
    got = boundary(tracer.apply, state, rq)
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 2, "operation": "apply", "outcome": "crash"}
    assert all(set(row) == {"seq", "operation", "outcome"} for row in view(tracer))
    visible = json.dumps(view(tracer))
    assert "synthetic-private" not in visible and "fixture" not in visible

    class HostileTracer(module.QueueTracer):
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
    got = boundary(hostile.apply, state, rq)
    assert got[0] == "ok" and got[1] is result
    reject = policy.QueueError("malformed_queue_request", "malformed_request")
    hostile_engine.error = reject
    got = boundary(hostile.apply, state, rq)
    assert got[0] == "reject" and got[1] is reject
    crash = AssertionError("synthetic-private-crash")
    hostile_engine.error = crash
    got = boundary(hostile.apply, state, rq)
    assert got[0] == "crash" and got[1] is crash
    assert HostileTracer.getter_calls == ["get", "get", "get"]


def test_identity_exact_outcomes_and_content_free_detached_trace():
    probe(production)


@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_foreign_exception_identity_is_preserved(exception):
    sentinel = exception("private fixture")
    tracer = production.QueueTracer(Engine(error=sentinel))
    got = boundary(tracer.apply, {}, {})
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer) == ({"seq": 0, "operation": "apply", "outcome": "crash"},)


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
        else policy.QueueError("malformed_queue_request", "malformed_request")
        if kind == "reject"
        else AssertionError("crash")
    )
    tracer = production.QueueTracer(Engine(result, error))
    tracer._trace = Bomb()
    assert view(tracer) == ()
    got = boundary(tracer.apply, {}, {})
    assert got[0] == ("ok" if kind == "accept" else kind)
    assert got[1] is (result if kind == "accept" else error)
    assert view(tracer) == ({"seq": 0, "operation": "apply", "outcome": kind},)
    assert calls == []


def test_real_enqueue_claim_nack_ack_and_refusal_preserve_mutation_semantics():
    left = {"jobs": [], "next_seq": 0}
    right = copy.deepcopy(left)
    tracer = production.QueueTracer(policy.QueueEngine())
    bare = policy.QueueEngine()
    jid = policy.job_id_for("fixture")
    stream = [
        {
            "op": "enqueue",
            "dedupe_key": "fixture",
            "priority": 1,
            "payload": {"private": "fixture"},
        },
        {"op": "claim", "worker": "worker", "now": 0, "lease_ms": 100},
        {"op": "ack", "job_id": jid, "worker": "wrong", "now": 1},
        {"op": "nack", "job_id": jid, "worker": "worker", "now": 1},
        {"op": "claim", "worker": "worker", "now": 2, "lease_ms": 100},
        {"op": "ack", "job_id": jid, "worker": "worker", "now": 3},
        {},
    ]
    for request in stream:
        original = copy.deepcopy(request)
        state_before = copy.deepcopy(left)
        a = boundary(tracer.apply, left, request)
        b = boundary(bare.apply, right, request)
        assert a[0] == b[0] and a[0] != "crash"
        if a[0] == "ok":
            assert a[1] == b[1]
        else:
            assert a[1].failure_class == b[1].failure_class and a[1].code == b[1].code
            assert left == state_before
        assert left == right and request == original
    assert left["jobs"][0]["status"] == "done"
    assert [r["outcome"] for r in view(tracer)] == [
        "accept",
        "accept",
        "reject",
        "accept",
        "accept",
        "accept",
        "reject",
    ]
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
    class Broken(production.QueueTracer):
        def apply(self, state, request):
            raise AssertionError("candidate-only crash")

    class Module:
        QueueTracer = Broken

    with pytest.raises(ForeignCrash):
        probe(Module)
