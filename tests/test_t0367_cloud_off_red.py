"""T0367 permanent red battery for cloud_off.

Standalone green uses the contract reference; production replaces BINDING only.
The landed fixture supplies closed inputs and pinned outputs, not execution or
assertions. This runner passes the actual request to the binding and checks
exact types, fresh mapped refusals, request immutability, deep state rollback,
read-only verdicts and detached receipts. Raw crashes never count as mutant kills.
"""

from __future__ import annotations

import copy

import pytest

from tests import test_t0366_cloud_off_fixture as fx

BINDING = (
    lambda: __import__("desktop.privacy.cloud_off", fromlist=["CloudSwitch"]).CloudSwitch(
        __import__("desktop.privacy.sensitive_flag", fromlist=["FlagStore"]).FlagStore()
    ),
    __import__("desktop.privacy.cloud_off", fromlist=["CloudError"]).CloudError,
)
ROWS = [(section, row) for section in fx.SECTIONS for row in fx.CASES[section]]


class Crash(Exception):
    """A foreign exception, never a semantic kill."""


def call(fn, error, *args):
    """Only the closed refusal type may escape an implementation boundary."""
    try:
        return fn(*args)
    except error:
        raise
    except Exception as exc:  # noqa: BLE001 - includes implementation AssertionError
        raise Crash(type(exc).__name__) from exc


def state_of(store):
    return copy.deepcopy((store._state, store._flags._state))


def exact(actual, expected):
    assert type(actual) is type(expected)
    if type(expected) is dict:
        assert set(actual) == set(expected)
        assert {(type(key), key) for key in actual} == {(type(key), key) for key in expected}
        for key in expected:
            exact(actual[key], expected[key])
    elif type(expected) in (list, tuple):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected, strict=True):
            exact(left, right)
    else:
        assert actual == expected


def containers(value):
    found = set()
    if type(value) in (dict, list, tuple):
        found.add(id(value))
        for child in value.values() if type(value) is dict else value:
            found.update(containers(child))
    return found


def invoke(store, step, error, expected=None):
    """No request copy at the call boundary: mutations must be observable."""
    request = copy.deepcopy(step.get("request"))
    before = copy.deepcopy(request)
    prior = state_of(store)
    op = step["op"]
    if op == "seed":
        store._state[request["install_id"]] = (request["cloud_mode"], request["revision"])
        return {"seeded": True}
    try:
        if op == "flag_register":
            value = call(store._flags.register, fx.FlagError, request["collection_id"])
            got = {"flag": value}
        elif op == "flag_transition":
            value = call(store._flags.transition, fx.FlagError, request)
            got = {"flag": value}
        elif op == "register":
            value = call(store.register, error, request["install_id"])
            got = {"record": value}
        elif op == "record":
            value = call(store.record, error, request["install_id"])
            got = {"record": value}
        elif op == "verdict":
            value = call(store.verdict, error, request)
            got = {"verdict": value}
        else:
            value = call(getattr(store, op), error, request)
            got = {"record": value}
    except error as exc:
        assert type(exc) is error
        assert exc.failure_class in fx.FAILURE_CLASSES
        assert exc.code == fx.FAILURE_MAPPING[exc.failure_class]
        assert exc.__cause__ is None and exc.__context__ is None
        exact(state_of(store), prior)
        got = {"failure": exc.failure_class}
    else:
        assert not (
            containers(value)
            & (containers(request) | containers(store._state) | containers(store._flags._state))
        )
        if op in ("record", "verdict"):
            exact(state_of(store), prior)
    exact(request, before)
    if expected is not None:
        exact(got, expected)
    return got


def setup(store, steps, error):
    for step in steps:
        result = invoke(store, step, error, step.get("expect"))
        if "expect" not in step:
            assert "failure" not in result


def check_row(factory, error, section, row):
    store = call(factory, error)
    if section in ("happy", "boundary"):
        for step in row["steps"]:
            invoke(store, step, error, step["expect"])
    elif section == "malformed":
        setup(store, row["setup"], error)
        invoke(store, row["request"], error, {"failure": row["expect_failure"]})
        store = call(factory, error)
        repair = row["minimal_repair"]
        setup(store, repair["setup"], error)
        assert "failure" not in invoke(store, repair["request"], error)
    else:
        setup(store, row["setup"], error)
        invoke(store, row["rejected"], error, {"failure": row["expect_failure"]})
        invoke(store, row["follow_up"], error, row["follow_up"]["expect"])


def check_hostile(factory, error, label, step):
    section, name, index = label.split("/")
    row = next(row for row in fx.CASES[section] if row["name"] == name)
    for _tag, request in fx._variants(step):
        store = call(factory, error)
        setup(store, row["steps"][: int(index)], error)
        prior = state_of(store)
        snap = fx._snapshot(request)
        fx.CALLS.clear()
        try:
            call(getattr(store, step["op"]), error, request)
        except error as exc:
            assert type(exc) is error
            assert exc.failure_class == "malformed_cloud_request"
            assert exc.code == fx.FAILURE_MAPPING["malformed_cloud_request"]
            assert exc.__cause__ is None and exc.__context__ is None
        else:
            raise AssertionError("hostile request accepted")
        assert fx.CALLS == []
        assert fx._snapshot(request) == snap
        exact(state_of(store), prior)


def evidence(factory, error):
    """Separate semantic assertion failures from foreign exceptions."""
    semantic, crashes = [], []
    probes = [(section + "/" + row["name"], check_row, (section, row)) for section, row in ROWS]
    probes += [(label, check_hostile, (label, step)) for label, step in fx._ACCEPTED]
    for label, probe, args in probes:
        try:
            probe(factory, error, *args)
        except AssertionError:
            semantic.append(label)
        except Exception as exc:  # noqa: BLE001 - explicitly not a semantic kill
            crashes.append((label, type(exc).__name__))
    return semantic, crashes


@pytest.mark.parametrize("section,row", ROWS, ids=[s + "/" + r["name"] for s, r in ROWS])
def test_closed_rows(section, row):
    check_row(*BINDING, section, row)


@pytest.mark.parametrize("label,step", fx._ACCEPTED, ids=[x[0] for x in fx._ACCEPTED])
def test_hostile_totality(label, step):
    check_hostile(*BINDING, label, step)


def test_closed_corpus_and_green_binding():
    fx.validate_structure(fx.CASES)
    fx.validate_closure(fx.CASES, fx.RAW)
    assert evidence(*BINDING) == ([], [])


@pytest.mark.parametrize("name", sorted(fx.MUTANTS))
def test_semantic_mutants(name, monkeypatch):
    factory = fx.MUTANTS[name](monkeypatch)
    semantic, _crashes = evidence(factory, fx.CloudError)
    assert semantic, name


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_crash_is_not_a_kill(exception):
    class Broken(fx.CloudSwitch):
        def transition(self, request):
            raise exception("synthetic crash")

    semantic, crashes = evidence(lambda: Broken(fx.FlagStore()), fx.CloudError)
    assert crashes and semantic == []


def test_binding_is_used_for_every_row():
    calls = []

    def factory():
        calls.append(1)
        return BINDING[0]()

    for section, row in ROWS:
        check_row(factory, BINDING[1], section, row)
    assert len(calls) == len(ROWS) + len(fx.CASES["malformed"])


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_composed_flag_crash_is_not_a_semantic_kill(exception):
    class BrokenFlags(fx.FlagStore):
        def register(self, cid):
            raise exception("synthetic composed crash")

    semantic, crashes = evidence(lambda: fx.CloudSwitch(BrokenFlags()), fx.CloudError)
    assert crashes and semantic == []


def test_composed_flag_wrong_receipts_are_detected():
    class WrongFlags(fx.FlagStore):
        def register(self, cid):
            super().register(cid)
            return {"wrong": "receipt"}

        def transition(self, rq):
            super().transition(rq)
            return {"wrong": "receipt"}

    semantic, _crashes = evidence(lambda: fx.CloudSwitch(WrongFlags()), fx.CloudError)
    assert semantic
