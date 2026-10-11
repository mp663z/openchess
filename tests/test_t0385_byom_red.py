"""T0385 permanent byom red battery.

Standalone green uses the contract reference. Replace BINDING only to bind
production. Closed rows and manifests come from T0384; this runner owns
execution, assertions and foreign-crash isolation. Implementation AssertionError
and KeyError never count as semantic evidence. Actual requests are passed into
the binding, recursive receipt aliases and deep rollback are checked.
"""

from __future__ import annotations

import copy
import types

import pytest

from desktop.privacy import byom as production
from tests import test_t0384_byom_fixture as fx

ref = fx.ref
BINDING = (production.ByomStore, production.ByomError)
exact = fx.exact
ROWS = fx.CASES["rows"]


def world(config, binding, error):
    store, cloud, flags = ref._world(config.get("cloud_on", True), config.get("open_a", True))
    if error is production.ByomError:
        from desktop.privacy.cloud_off import CloudSwitch, opt_in_for
        from desktop.privacy.sensitive_flag import FlagStore, approval_for

        flags = FlagStore()
        for cid in (ref.A, ref.B):
            flags.register(cid)
        if config.get("open_a", True):
            flags.transition(
                {
                    "collection_id": ref.A,
                    "sensitive": False,
                    "expected_revision": 0,
                    "approval": approval_for(ref.A, 0),
                }
            )
        cloud = CloudSwitch(flags)
        cloud.register(ref.INS)
        if config.get("cloud_on", True):
            cloud.transition(
                {
                    "install_id": ref.INS,
                    "cloud_mode": "on",
                    "expected_revision": 0,
                    "opt_in": opt_in_for(ref.INS, 0),
                }
            )
    if binding is not None:
        store = call(binding, cloud, error=error)
        receipt = call(store.register, ref.INS, ref.P, error=error)
        exact(
            receipt,
            {"install_id": ref.INS, "provider_id": ref.P, "accepted_terms": None, "revision": 0},
        )
        assert not (containers(receipt) & owned_containers((store, cloud, flags)))
    store._state[(ref.INS, ref.P)] = (config.get("accepted_terms"), config.get("revision", 0))
    return store, cloud, flags


def state(context):
    store, cloud, flags = context
    return copy.deepcopy((store._state, cloud._state, flags._state))


def execute(context, op, request):
    store = context[0]
    if op == "register":
        return {"record": store.register(request["install_id"], request["provider_id"])}
    if op == "send":
        return {"verdict": store.send(request)}
    return {"record": store.transition(request)}


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
    for base in type.__getattribute__(cls, "__mro__"):
        namespace = type.__getattribute__(base, "__dict__")
        if "__dict__" in namespace:
            if type(namespace["__dict__"]) is not types.GetSetDescriptorType:
                raise AssertionError("nonstandard instance dictionary is outside inspection scope")
            break
    else:
        raise AssertionError("instance has no raw dictionary")
    fields = object.__getattribute__(instance, "__dict__")
    assert type(fields) is dict
    return fields


def owned_containers(context):
    store, cloud, flags = context
    return set().union(*(containers(raw_fields(owner)) for owner in (store, cloud, flags)))


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
        if step["op"] == "send":
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
        assert exc.failure_class == "malformed_byom_request"
        assert exc.code == "malformed_request"
        assert exc.__cause__ is None and exc.__context__ is None
    else:
        raise AssertionError("hostile request accepted")
    assert ref.CALLS == []
    assert ref._snap(rq) == snap
    exact(state(context), before)


def evidence(binding, error):
    semantic, crashes = [], []
    probes = [(row["name"], replay, (row, binding, error)) for row in ROWS]
    probes += [
        ("hostile/" + str(index), hostile, (binding, error, item))
        for index, item in enumerate(ref.ROWS)
    ]
    probes += [
        ("payload/" + name, hostile_payload, (binding, error, payload))
        for name, payload in ref.PAYLOAD_ROWS
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
    assert [n for n, _ in ref.PAYLOAD_ROWS] == fx.PAYLOAD_NAMES
    assert evidence(*BINDING) == ([], [])


@pytest.mark.parametrize("name", sorted(fx.MUTANTS))
def test_named_mutants(name):
    semantic, _crashes = evidence(fx.MUTANTS[name], ref.ByomError)
    assert semantic, name


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_crash_is_not_semantic(exception):
    class Broken(ref.ByomStore):
        def transition(self, rq):
            raise exception("implementation-only crash")

    broken = Broken
    semantic, crashes = evidence(broken, ref.ByomError)
    assert crashes and semantic == []


def test_wrong_receipts_are_detected():
    class Wrong(ref.ByomStore):
        def transition(self, rq):
            super().transition(rq)
            return {"wrong": "receipt"}

    wrong = Wrong
    semantic, _crashes = evidence(wrong, ref.ByomError)
    assert semantic


def hostile_payload(binding, error, payload):
    context = world({}, binding, error)
    before = state(context)
    snap = ref._snap(payload)
    ref.CALLS.clear()
    rq = {
        "install_id": ref.INS,
        "provider_id": ref.P,
        "collection_id": ref.A,
        "terms": copy.deepcopy(ref.T),
        "payload": payload,
    }
    try:
        call(context[0].send, rq, error=error)
    except error as exc:
        assert type(exc) is error
        assert exc.failure_class == "payload_rejected"
        assert exc.code == "payload_rejected"
        assert exc.__cause__ is None and exc.__context__ is None
    else:
        raise AssertionError("hostile payload accepted")
    assert ref.CALLS == [] and ref._snap(payload) == snap
    exact(state(context), before)


@pytest.mark.parametrize("name,payload", ref.PAYLOAD_ROWS, ids=[n for n, _ in ref.PAYLOAD_ROWS])
def test_hostile_payload(name, payload):
    hostile_payload(*BINDING, payload)
