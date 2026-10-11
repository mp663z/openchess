"""T0408 content-free instrument proof, named edit accounting and real policy parity.

Candidate construction, method calls and record access are guarded separately
from oracle assertions: implementation AssertionError/KeyError are crashes.
Trace values are only seq, fixed operation and accept/reject/crash. No request,
response, refusal detail or identifier is logged. No external sink exists.
"""

from __future__ import annotations

import copy
import json

import pytest

from desktop.privacy import delete as policy
from desktop.privacy import delete_instrument as production
from tests.privacy_probe_support import ForeignCrash, load_mutant

METHODS = ["register", "record", "access", "delete"]
PRIMARY = "delete"


class Engine:
    def __init__(self, result=None, exception=None):
        self.result = result
        self.exception = exception
        self.calls = []

    def invoke(self, *args):
        self.calls.append(args)
        if self.exception is not None:
            raise self.exception
        return self.result


for _method in METHODS:
    setattr(Engine, _method, Engine.invoke)


def boundary(fn, *args):
    """Only candidate code is inside this guard; assertions remain outside."""
    try:
        return ("ok", fn(*args))
    except policy.DeleteError as exc:
        return ("reject", exc)
    except BaseException as exc:
        return ("crash", exc)


def require_ok(result):
    if result[0] == "crash":
        raise ForeignCrash(type(result[1]).__name__) from result[1]
    assert result[0] == "ok", result[0]
    return result[1]


def records(tracer):
    return require_ok(boundary(lambda: tracer.records))


def probe(module):
    receipt = {"synthetic-private-receipt": [1]}
    rq = {"synthetic-private-request": "fixture"}
    engine = Engine(receipt)
    tracer = require_ok(boundary(module.DeleteTracer, engine))
    for index, method in enumerate(METHODS):
        got = require_ok(boundary(getattr(tracer, method), rq))
        assert got is receipt and engine.calls[-1][0] is rq
        assert records(tracer)[-1] == {"seq": index, "operation": method, "outcome": "accept"}
    view = records(tracer)
    assert all(set(row) == {"seq", "operation", "outcome"} for row in view)
    assert "synthetic-private" not in json.dumps(view) and "fixture" not in json.dumps(view)
    view[0]["outcome"] = "tamper"
    assert records(tracer)[0]["outcome"] == "accept"
    sentinel = policy.DeleteError("malformed_delete_request")
    engine.exception = sentinel
    result = boundary(getattr(tracer, PRIMARY), rq)
    if result[0] == "crash":
        raise ForeignCrash(type(result[1]).__name__) from result[1]
    assert result[0] == "reject" and result[1] is sentinel
    assert records(tracer)[-1] == {"seq": len(METHODS), "operation": PRIMARY, "outcome": "reject"}
    crash = AssertionError("candidate sentinel")
    engine.exception = crash
    result = boundary(getattr(tracer, PRIMARY), rq)
    assert result[0] == "crash" and result[1] is crash
    assert records(tracer)[-1] == {
        "seq": len(METHODS) + 1,
        "operation": PRIMARY,
        "outcome": "crash",
    }


def test_all_public_methods_identity_content_exclusion_and_detached_view():
    probe(production)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("exception", [KeyError, AssertionError, KeyboardInterrupt])
def test_exact_foreign_error_identity_and_trace(method, exception):
    sentinel = exception("synthetic-private-error")
    tracer = production.DeleteTracer(Engine(exception=sentinel))
    got = boundary(getattr(tracer, method), {})
    assert got == ("crash", sentinel)
    assert records(tracer) == ({"seq": 0, "operation": method, "outcome": "crash"},)


@pytest.mark.parametrize("result_kind", ["accept", "reject", "crash"])
def test_bad_trace_container_never_calls_hook_or_changes_result(result_kind):
    calls = []

    class Bomb(list):
        def append(self, row):
            calls.append("append")
            raise AssertionError("caller hook")

        def __iter__(self):
            calls.append("iter")
            raise KeyError("caller hook")

    receipt = {"ok": []}
    sentinel = (
        None
        if result_kind == "accept"
        else policy.DeleteError("malformed_delete_request")
        if result_kind == "reject"
        else KeyError("crash")
    )
    tracer = production.DeleteTracer(Engine(receipt, sentinel))
    tracer._trace = Bomb()
    before = boundary(lambda: tracer.records)
    assert before == ("ok", ()) and calls == []
    got = boundary(getattr(tracer, PRIMARY), {})
    assert got[0] == ("ok" if result_kind == "accept" else result_kind)
    assert got[1] is (receipt if result_kind == "accept" else sentinel)
    assert calls == []
    assert records(tracer) == ({"seq": 0, "operation": PRIMARY, "outcome": result_kind},)


EDITS = [
    ("copied-result", "return result", "return dict(result)"),
    ("wrong-refusal", 'self._append(operation, "reject")', 'self._append(operation, "accept")'),
    ("wrong-crash", 'self._append(operation, "crash")', 'self._append(operation, "accept")'),
    (
        "aliased-records",
        "return tuple(dict(row) for row in trace) if type(trace) is list else ()",
        "return tuple(trace) if type(trace) is list else ()",
    ),
    ("sequence", '"seq": list.__len__(trace)', '"seq": 0'),
    ("disclosure", '"outcome": outcome', '"outcome": outcome, "private": "fixture"'),
]


@pytest.mark.parametrize("name,old,new", EDITS, ids=[e[0] for e in EDITS])
def test_named_red_controls_have_semantic_not_crash_kills(name, old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        probe(mutant)


def test_crash_only_mutant_is_not_an_oracle_assertion():
    class Broken(production.DeleteTracer):
        def _call(self, operation, args):
            raise AssertionError("implementation-only crash")

    class Module:
        DeleteTracer = Broken

    with pytest.raises(ForeignCrash):
        probe(Module)


def test_real_partial_delete_retry_and_refusal_parity():
    cid = "col1:" + "a" * 64

    class Backend:
        def __init__(self, fail):
            self.fail = fail
            self.calls = []

        def erase(self, cid):
            self.calls.append(cid)
            if self.fail:
                self.fail = False
                raise OSError("synthetic")

    names = ("content", "index", "analysis", "training", "ciphertext")
    stores1 = {n: Backend(n == "index") for n in names}
    stores2 = {n: Backend(n == "index") for n in names}
    wrapped, bare = policy.Deleter(stores1), policy.Deleter(stores2)
    tracer = production.DeleteTracer(wrapped)
    rq = {"collection_id": cid, "expected_revision": 0, "decision": policy.decision_for(cid, 0)}
    retry = {"collection_id": cid, "expected_revision": 1, "decision": None}
    for method, args in [
        ("register", (cid,)),
        ("record", (cid,)),
        ("delete", (rq,)),
        ("access", ({"collection_id": cid},)),
        ("delete", (retry,)),
    ]:
        before = copy.deepcopy(args)
        left = boundary(getattr(tracer, method), *args)
        right = boundary(getattr(bare, method), *args)
        assert left[0] == right[0]
        if left[0] == "ok":
            assert left[1] == right[1]
        else:
            assert left[1].failure_class == right[1].failure_class and left[1].code == right[1].code
        assert wrapped._state == bare._state and args == before
        assert {n: s.calls for n, s in stores1.items()} == {n: s.calls for n, s in stores2.items()}
    assert [r["outcome"] for r in records(tracer)] == [
        "accept",
        "accept",
        "accept",
        "reject",
        "accept",
    ]
