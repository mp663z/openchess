"""T0394 permanent redaction red battery.

Standalone green uses the contract reference. Replace BINDING only to bind
production. Closed rows and manifests come from T0393; this runner owns
execution, assertions and foreign-crash isolation. Implementation AssertionError
and KeyError never count as semantic evidence. Actual requests are passed into
the binding, recursive receipt aliases and deep rollback are checked.
Request-retention scope: this runner checks output, request immutability,
no caller methods, stateless output semantics and fresh refusals. It does not
inspect closure/global retention of a request or secrets between calls; a
callable that retains a request while returning correct results remains outside
this tested scope, as in T0393. No no-retention guarantee is claimed.
"""

from __future__ import annotations

import copy

import pytest

from desktop.privacy import redaction as production
from tests import test_t0393_redaction_fixture as fx

ref = fx.ref
BINDING = ((production.redact, production.emit_text), production.RedactionError)
exact = fx.exact
ROWS = fx.CASES["rows"]


def world(config, binding, error):
    assert config == {}
    return (ref.redact, ref.emit_text) if binding is None else binding


def state(context):
    return None


def execute(context, op, request):
    fn = context[0] if op == "redact" else context[1]
    return {"text": fn(request)}


def containers(value):
    found = set()
    if type(value) in (dict, list, tuple, set):
        found.add(id(value))
        children = value.values() if type(value) is dict else value
        for child in children:
            found.update(containers(child))
    return found


def owned_containers(context):
    return set()


class ForeignCrash(Exception):
    """An implementation exception, never a semantic kill."""


def call(fn, *args, error):
    try:
        return fn(*args)
    except error:
        raise
    except Exception as exc:  # noqa: BLE001 - includes implementation AssertionError
        raise ForeignCrash(type(exc).__name__) from exc


def replay(row, binding, error):
    context = world(row["config"], binding, error)
    for step in row["steps"]:
        rq = copy.deepcopy(step["request"])
        before = copy.deepcopy(rq)
        prior = state(context)
        try:
            got = call(execute, context, step["op"], rq, error=error)
        except error as exc:
            assert type(exc) is error
            assert exc.failure_class in ref.FAILURE_CLASSES
            assert exc.code == ref.FAILURE_MAPPING[exc.failure_class]
            assert exc.__cause__ is None and exc.__context__ is None
            got = {"failure": exc.failure_class}
            exact(state(context), prior)
        assert not (containers(got) & (containers(rq) | owned_containers(context)))
        exact(rq, before)
        exact(got, step["expect"])


def hostile(binding, error, item):
    _name, rq = item
    snap = ref._snap(rq)
    ref.CALLS.clear()
    try:
        call(binding[0], rq, error=error)
    except error as exc:
        assert type(exc) is error
        assert exc.failure_class == "malformed_redaction_request"
        assert exc.code == "malformed_request"
        assert exc.__cause__ is None and exc.__context__ is None
    else:
        raise AssertionError("hostile request accepted")
    assert ref.CALLS == [] and ref._unchanged(rq, snap)
    assert call(binding[1], rq, error=error) == "redacted"
    assert ref.CALLS == [] and ref._unchanged(rq, snap)


def evidence(binding, error):
    semantic, crashes = [], []
    probes = [(row["name"], replay, (row, binding, error)) for row in ROWS]
    probes += [
        ("hostile/" + str(index), hostile, (binding, error, item))
        for index, item in enumerate(ref.ROWS)
    ]

    for label, probe, args in probes:
        try:
            probe(*args)
        except AssertionError:
            semantic.append(label)
        except Exception as exc:  # noqa: BLE001 - never counted as semantic
            crashes.append((label, type(exc).__name__))
    return semantic, crashes


@pytest.mark.parametrize("row", ROWS, ids=[row["name"] for row in ROWS])
def test_pinned_row(row):
    replay(row, *BINDING)


@pytest.mark.parametrize("item", ref.ROWS, ids=[str(index) for index in range(len(ref.ROWS))])
def test_hostile_totality(item):
    hostile(*BINDING, item)


def test_closed_fixture_and_green_binding():
    fx.closure(fx.CASES)
    assert [n for n, _ in ref.ROWS] == fx.HOSTILE_NAMES
    assert evidence(*BINDING) == ([], [])


@pytest.mark.parametrize("name", sorted(fx.MUTANTS))
def test_named_mutants(name):
    semantic, _crashes = evidence(fx.MUTANTS[name], ref.RedactionError)
    assert semantic, name


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_crash_is_not_semantic(exception):
    def fn(rq):
        raise exception("implementation-only crash")

    broken = (fn, fn)
    semantic, crashes = evidence(broken, ref.RedactionError)
    assert crashes and semantic == []


def test_wrong_receipts_are_detected():
    wrong = (lambda rq: {"wrong": "receipt"}, lambda rq: {"wrong": "receipt"})
    semantic, _crashes = evidence(wrong, ref.RedactionError)
    assert semantic


def test_retention_is_explicitly_outside_tested_scope():
    retained = []

    def retaining(rq):
        retained.append(rq)
        return ref.redact(rq)

    assert evidence((retaining, ref.emit_text), ref.RedactionError) == ([], [])
    assert retained
