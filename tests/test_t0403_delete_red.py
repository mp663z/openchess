"""T0403 permanent delete red battery.

Standalone green uses the contract reference. Replace BINDING only to bind
production. Closed rows and manifests come from T0402; this runner owns
execution, assertions and foreign-crash isolation. Implementation AssertionError
and KeyError never count as semantic evidence. Actual requests are passed into
the binding, recursive receipt aliases and deep rollback are checked.
"""

from __future__ import annotations

import copy
import types

import pytest

from desktop.privacy import delete as production
from tests import test_t0402_delete_fixture as fx

ref = fx.ref
BINDING = (production.Deleter, production.DeleteError)
exact = fx.exact
ROWS = fx.CASES["rows"]


def world(config, binding, error):
    store, stores = ref._world(config.get("fails"))
    if binding is not None:
        store = call(binding, stores, error=error)
        for cid in (ref.A, ref.B):
            receipt = call(store.register, cid, error=error)
            exact(receipt, {"collection_id": cid, "state": "live", "revision": 0, "pending": []})
            assert not (containers(receipt) & owned_containers((store, stores, [])))
    store._state[ref.A] = ("live", config.get("revision", 0), ())
    ref.erase_log.clear()
    violations = []

    # Every erase independently witnesses the tombstone-before-effects rule.
    def probe(_name, cid):
        if store._state[cid][0] != "deleted":
            violations.append(("tombstone", cid))

    for backend in stores.values():
        backend.probe = probe
    return store, stores, violations


def state(context):
    store, stores, violations = context
    return copy.deepcopy(
        (store._state, {n: (set(s.data), s.fail) for n, s in stores.items()}, ref.erase_log)
    )


def execute(context, op, request):
    store = context[0]
    if op == "register":
        return {"record": store.register(request["collection_id"])}
    if op == "access":
        return {"verdict": store.access(request)}
    return {"record": store.delete(request)}


def containers(value):
    found = set()
    if type(value) in (dict, list, tuple, set):
        found.add(id(value))
        children = value.values() if type(value) is dict else value
        for child in children:
            found.update(containers(child))
    return found


def raw_fields(instance):
    """Inspect only ordinary instance dictionaries, never caller descriptors."""
    cls = type(instance)
    mro = type.__dict__["__mro__"].__get__(cls, type(cls))
    for base in mro:
        namespace = type.__dict__["__dict__"].__get__(base, type(base))
        if "__dict__" in namespace:
            descriptor = namespace["__dict__"]
            if type(descriptor) is not types.GetSetDescriptorType:
                raise AssertionError("nonstandard instance dictionary is outside inspection scope")
            break
    else:
        raise AssertionError("instance has no raw dictionary")
    fields = descriptor.__get__(instance, cls)
    assert type(fields) is dict
    return fields


def owned_containers(context):
    store, stores, violations = context
    owners = [store, *stores.values()]
    return containers((ref.erase_log, violations)) | set().union(
        *(containers(raw_fields(owner)) for owner in owners)
    )


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
        if step["op"] == "delete" and "record" in got:
            store, stores, violations = context
            cid = rq["collection_id"]
            record = got["record"]
            prior_lifecycle, _rev, prior_pending = prior[0][cid]
            targets = tuple(ref._STORES) if prior_lifecycle == "live" else prior_pending
            assert violations == []
            calls = ref.erase_log[len(prior[2]) :]
            assert calls == [(name, cid) for name in targets]
            for name in targets:
                assert (cid in stores[name].data) == (name in record["pending"])
            assert store._state[ref.B] == prior[0][ref.B]
        elif step["op"] == "access":
            exact(state(context), prior)


def hostile(binding, error, item):
    op, _name, rq = item
    context = world({}, binding, error)
    before = state(context)
    snap = ref._snap(rq)
    ref.CALLS.clear()
    try:
        call(getattr(context[0], op), rq, error=error)
    except error as exc:
        assert type(exc) is error
        assert exc.failure_class == "malformed_delete_request"
        assert exc.code == "malformed_request"
        assert exc.__cause__ is None and exc.__context__ is None
    else:
        raise AssertionError("hostile request accepted")
    assert ref.CALLS == []
    assert ref._unchanged(rq, snap)
    exact(state(context), before)


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
    assert [o + "/" + n for o, n, _ in ref.ROWS] == fx.HOSTILE_NAMES
    assert evidence(*BINDING) == ([], [])


@pytest.mark.parametrize("name", sorted(fx.MUTANTS))
def test_named_mutants(name):
    semantic, _crashes = evidence(fx.MUTANTS[name], ref.DeleteError)
    assert semantic, name


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_crash_is_not_semantic(exception):
    class Broken(ref.Deleter):
        def delete(self, rq):
            raise exception("implementation-only crash")

    broken = Broken
    semantic, crashes = evidence(broken, ref.DeleteError)
    assert crashes and semantic == []


def test_wrong_receipts_are_detected():
    class Wrong(ref.Deleter):
        def delete(self, rq):
            super().delete(rq)
            return {"wrong": "receipt"}

    wrong = Wrong
    semantic, _crashes = evidence(wrong, ref.DeleteError)
    assert semantic
