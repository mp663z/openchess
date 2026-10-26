"""T0327 content-free progress instrumentation, identity/parity and red accounting."""

from __future__ import annotations

import copy
import json

import pytest

from server import jobs_progress as policy
from server import jobs_progress_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant

JOB = "job1:" + "0" * 64
MAX_NOW = 2**53 - 1


class Engine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def report(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def boundary(fn, *args):
    try:
        return ("ok", fn(*args))
    except policy.ProgressError as exc:
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
    tracer = ok(boundary(module.ProgressTracer, engine))
    assert ok(boundary(tracer.report, request)) is result
    assert engine.calls[0] is request
    assert view(tracer) == ({"seq": 0, "operation": "report", "outcome": "accept"},)
    records = view(tracer)
    records[0]["outcome"] = "tamper"
    assert view(tracer)[0]["outcome"] == "accept"
    sentinel = policy.ProgressError("malformed_progress_request")
    engine.error = sentinel
    got = boundary(tracer.report, request)
    if got[0] == "crash":
        raise ForeignCrash(type(got[1]).__name__) from got[1]
    assert got[0] == "reject" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 1, "operation": "report", "outcome": "reject"}
    sentinel = AssertionError("synthetic-private-crash")
    engine.error = sentinel
    got = boundary(tracer.report, request)
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer)[-1] == {"seq": 2, "operation": "report", "outcome": "crash"}
    assert all(set(row) == {"seq", "operation", "outcome"} for row in view(tracer))
    visible = json.dumps(view(tracer))
    assert "synthetic-private" not in visible and "fixture" not in visible

    class HostileTracer(module.ProgressTracer):
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
    got = boundary(hostile.report, request)
    assert got[0] == "ok" and got[1] is result
    reject = policy.ProgressError("malformed_progress_request")
    hostile_engine.error = reject
    got = boundary(hostile.report, request)
    assert got[0] == "reject" and got[1] is reject
    crash = AssertionError("synthetic-private-crash")
    hostile_engine.error = crash
    got = boundary(hostile.report, request)
    assert got[0] == "crash" and got[1] is crash
    assert HostileTracer.getter_calls == ["get", "get", "get"]


def test_identity_exact_outcomes_and_content_free_detached_trace():
    probe(production)


@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_foreign_exception_identity_is_preserved(exception):
    sentinel = exception("private fixture")
    tracer = production.ProgressTracer(Engine(error=sentinel))
    got = boundary(tracer.report, {})
    assert got[0] == "crash" and got[1] is sentinel
    assert view(tracer) == ({"seq": 0, "operation": "report", "outcome": "crash"},)


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
        else policy.ProgressError("malformed_progress_request")
        if kind == "reject"
        else AssertionError("crash")
    )
    tracer = production.ProgressTracer(Engine(result, error))
    tracer._trace = Bomb()
    assert view(tracer) == ()
    got = boundary(tracer.report, {})
    assert got[0] == ("ok" if kind == "accept" else kind)
    assert got[1] is (result if kind == "accept" else error)
    assert view(tracer) == ({"seq": 0, "operation": "report", "outcome": kind},)
    assert calls == []


def test_real_report_stream_agrees_with_bare_module_and_keeps_requests_pure():
    tracer = production.ProgressTracer(policy)

    def req(done, total, now, previous, worker="w1"):
        return {
            "op": "report",
            "job_id": JOB,
            "worker": worker,
            "done": done,
            "total": total,
            "now": now,
            "previous": previous,
        }

    first = req(0, 2, 0, None)
    r1 = policy.report(copy.deepcopy(first))
    done_req = req(2, 2, 1, r1)  # done == total boundary
    r2 = policy.report(copy.deepcopy(done_req))
    stream = [
        first,
        done_req,
        req(2, 2, 2, r2),  # progress_conflict: previous already complete
        req(1, 2, 1, {}),  # corrupt_previous_record
        req(1, 2, 1, r1, worker="w2"),  # progress_conflict: worker differs
        req(0, 0, 0, None),  # malformed: total below bound
        {},
    ]
    outcomes = []
    for request in stream:
        original = copy.deepcopy(request)
        a = boundary(tracer.report, request)
        b = boundary(policy.report, request)
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
    class Broken(production.ProgressTracer):
        def report(self, request):
            raise AssertionError("candidate-only crash")

    class Module:
        ProgressTracer = Broken

    with pytest.raises(ForeignCrash):
        probe(Module)
