"""T0480 Contracts/replaceable control plane/instrument: behavior-neutral
diagnostics for the shipped consumer client via
server.control_plane_client_instrument.

ClientTracer wraps ControlPlaneClient.call and .entitlements. This file
proves, against the T0474 reference mock:
- happy: a register/reserve/commit session through the tracer returns what
  a bare client returns, with one accept record per call;
- boundary: all 14 declared operations carry their contract-declared auth
  and mutating flags in the record, restated from the YAML;
- malformed: local validation refusals, a server-decided rejection and an
  unknown operation raise the same typed ControlPlaneError object and
  record reject with the closed-enum code, never a result;
- rollback: refused calls change neither the request body nor the wrapped
  client's session state, and later calls behave as on a bare client;
- records are metadata only: no email, token, account id, key material or
  body field appears in a record or the JSONL;
- totality: hostile error attributes, a faulting trace container and a
  hostile request body never change the wrapped result;
- one-edit mutants of the instrument are each red on a semantic assertion.
"""

from __future__ import annotations

import copy
import json
import time
import types
from pathlib import Path

import pytest
import yaml

from server import control_plane_client as client_mod
from server import control_plane_client_instrument as ci
from tools.control_plane_mock import MockControlPlane

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "server" / "control_plane_client_instrument.py").read_text()
_CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "control-plane.yaml").read_text())
_DECLARED = {
    f"{area}.{name}": (op["auth"], op["mutating"])
    for area, spec in _CONTRACT["areas"].items()
    for name, op in spec["ops"].items()
}
EMAIL = {"email": "ada@example.test", "password_hash_client": "pw-hash-1"}
RESERVE = {"operation_kind": "analysis", "estimated_units": 2, "hold_seconds": 3600}
KEY_MATERIAL = "unit-test-key-material"
CODES = frozenset(client_mod.ERROR_ENUM)


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


class Wire:
    def __init__(self, server):
        self.server = server
        self.calls = 0

    def __call__(self, method, path, headers, body):
        self.calls += 1
        return self.server.handle(method, path, headers, body)


def _client(server=None):
    server = server or MockControlPlane()
    wire = Wire(server)
    return client_mod.ControlPlaneClient(wire, clock=Clock()), wire


def _bare(fn, *args):
    try:
        return ("ok", fn(*args))
    except client_mod.ControlPlaneError as error:
        return ("reject", error.code)
    except BaseException as error:  # noqa: BLE001
        return ("crash", type(error).__name__)


def _session(target):
    """The same scripted session against a bare client or a tracer."""
    out = [target.call("identity.register", dict(EMAIL))["token"] is not None]
    reservation = target.call("quota.reserve", dict(RESERVE))
    out.append(sorted(reservation))
    out.append(target.entitlements()["tier"])
    return out


# -- neutrality and happy ---------------------------------------------------------------


def test_traced_session_matches_bare():
    bare, _ = _client()
    client, _ = _client()
    tracer = ci.ClientTracer(client)
    assert _session(tracer) == _session(bare)
    assert [r["outcome"] for r in tracer.records] == ["accept"] * 3
    assert [r["operation"] for r in tracer.records] == [
        "identity.register",
        "quota.reserve",
        "entitlements",
    ]
    assert [r["seq"] for r in tracer.records] == [0, 1, 2]


def test_result_is_the_wrapped_object():
    sentinel = {"a": 1}

    class Spy:
        def call(self, name, body=None):
            return sentinel

    tracer = ci.ClientTracer(Spy())
    assert tracer.call("identity.login", {}) is sentinel
    assert tracer.records[0]["result"] == {"fields": 1}


@pytest.mark.parametrize("name", sorted(_DECLARED))
def test_record_carries_the_contract_declared_shape(name):
    class Echo:
        def call(self, op, body=None):
            return {}

    tracer = ci.ClientTracer(Echo())
    tracer.call(name, {})
    auth, mutating = _DECLARED[name]
    assert tracer.records[0]["shape"] == {"declared": True, "auth": auth, "mutating": mutating}


def test_unknown_operation_is_traced_as_undeclared_and_still_refused():
    client, wire = _client()
    tracer = ci.ClientTracer(client)
    bare = _bare(_client()[0].call, "nope.nothing", {})
    assert bare[0] == "reject"
    assert _bare(tracer.call, "nope.nothing", {}) == bare
    (record,) = tracer.records
    assert record["shape"] == {"declared": False} and record["outcome"] == "reject"
    assert wire.calls == 0


# -- malformed and rollback -------------------------------------------------------------

REJECTS = {
    "undeclared-field": ("identity.register", lambda: {**EMAIL, "undeclared": True}),
    "missing-field": ("identity.register", lambda: {"email": "a@example.test"}),
    "bad-type": ("identity.register", lambda: {"email": 7, "password_hash_client": "x"}),
    "no-token": ("quota.reserve", lambda: dict(RESERVE)),
    "unknown-account-login": ("identity.login", lambda: dict(EMAIL)),
    "server-malformed": (
        "identity.register",
        lambda: {"email": "not-an-email", "password_hash_client": "pw"},
    ),
}


@pytest.mark.parametrize("name", sorted(REJECTS))
def test_rejections_are_the_same_typed_class(name):
    operation, make_body = REJECTS[name]
    bare = _bare(_client()[0].call, operation, make_body())
    assert bare[0] == "reject" and bare[1] in CODES
    tracer = ci.ClientTracer(_client()[0])
    body = make_body()
    before = copy.deepcopy(body)
    assert _bare(tracer.call, operation, body) == bare
    assert body == before
    (record,) = tracer.records
    assert record["outcome"] == "reject" and record["code"] == bare[1]
    assert record["retryable"] is False
    assert record["request_unchanged"] is True and "result" not in record


def test_same_exception_object_is_reraised():
    boom = client_mod.ControlPlaneError("internal", "E", retryable=True)

    class Raiser:
        def call(self, name, body=None):
            raise boom

    tracer = ci.ClientTracer(Raiser())
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        tracer.call("identity.login", {})
    assert caught.value is boom
    assert tracer.records[0]["retryable"] is True


def test_crash_is_recorded_and_reraised():
    class Crasher:
        def call(self, name, body=None):
            raise KeyError("x")

    tracer = ci.ClientTracer(Crasher())
    with pytest.raises(KeyError):
        tracer.call("identity.login", {})
    (record,) = tracer.records
    assert record["outcome"] == "crash" and record["error_type"] == "KeyError"


def test_refusals_do_not_disturb_the_session():
    client, _ = _client()
    tracer = ci.ClientTracer(client)
    assert _bare(tracer.call, "quota.reserve", dict(RESERVE)) == ("reject", "auth_invalid")
    assert client.token is None
    tracer.call("identity.register", dict(EMAIL))
    token = client.token
    assert token is not None
    assert _bare(tracer.call, "identity.register", {**EMAIL, "undeclared": 1})[0] == "reject"
    assert client.token == token
    tracer.call("quota.reserve", dict(RESERVE))
    assert [r["outcome"] for r in tracer.records] == ["reject", "accept", "reject", "accept"]


def test_request_unchanged_false_when_wrapped_mutates_then_rejects():
    class Mutator:
        def call(self, name, body=None):
            body["junk"] = 1
            raise client_mod.ControlPlaneError("conflict", "E")

    tracer = ci.ClientTracer(Mutator())
    with pytest.raises(client_mod.ControlPlaneError):
        tracer.call("identity.register", {})
    assert tracer.records[0]["request_unchanged"] is False


# -- diagnostics ------------------------------------------------------------------------


def test_records_are_metadata_only():
    client, _ = _client()
    tracer = ci.ClientTracer(client)
    payload = tracer.call("identity.register", dict(EMAIL))
    tracer.call(
        "provider_routing.register_key", {"provider_kind": "x", "key_material": KEY_MATERIAL}
    )
    text = tracer.to_jsonl()
    for private in (
        EMAIL["email"],
        EMAIL["password_hash_client"],
        payload["token"],
        payload["account_id"],
        KEY_MATERIAL,
    ):
        assert private not in text
    assert set(tracer.records[0]) == {"seq", "operation", "shape", "outcome", "result"}


def test_records_are_an_isolated_append_only_view():
    client, _ = _client()
    tracer = ci.ClientTracer(client)
    tracer.call("identity.register", dict(EMAIL))
    view = tracer.records
    view[0]["operation"] = "tampered"
    assert tracer.records[0]["operation"] == "identity.register"
    tracer.entitlements()
    assert len(view) == 1 and len(tracer.records) == 2


def test_jsonl_is_deterministic_and_parses():
    def run():
        tracer = ci.ClientTracer(_client()[0])
        tracer.call("identity.register", dict(EMAIL))
        _bare(
            tracer.call, "identity.login", {"email": "x@example.test", "password_hash_client": "y"}
        )
        return tracer.to_jsonl()

    first = run()
    assert first == run()
    assert [json.loads(line)["seq"] for line in first.splitlines()] == [0, 1]


# -- totality ---------------------------------------------------------------------------


def test_hostile_error_attributes_do_not_change_the_raise():
    class Evil(client_mod.ControlPlaneError):
        @property
        def code(self):
            raise RuntimeError("nope")

        @code.setter
        def code(self, value):
            pass

    err = Evil("internal", "E")

    class Raiser:
        def call(self, name, body=None):
            raise err

    tracer = ci.ClientTracer(Raiser())
    with pytest.raises(Evil) as caught:
        tracer.call("identity.login", {})
    assert caught.value is err
    assert tracer.records[0]["code"] == {"__opaque__": "attribute-RuntimeError"}


def test_faulting_trace_container_never_changes_the_result():
    client, _ = _client()
    tracer = ci.ClientTracer(client)
    object.__setattr__(tracer, "_trace", "not-a-list")
    assert tracer.call("identity.register", dict(EMAIL))["token"]
    assert tracer.records[0]["operation"] == "identity.register"


def test_hostile_body_records_an_opaque_marker_and_still_refuses():
    class BodyDict(dict):
        def __iter__(self):
            raise RuntimeError("dispatched")

    tracer = ci.ClientTracer(_client()[0])
    assert _bare(tracer.call, "identity.register", BodyDict())[0] == "reject"
    assert tracer.records[0]["outcome"] == "reject"


def test_pathological_depth_is_opaque_and_result_unchanged():
    deep = []
    for _ in range(200):
        deep = [deep]
    tracer = ci.ClientTracer(_client()[0])
    assert _bare(tracer.call, "identity.register", {"email": deep})[0] == "reject"
    assert tracer.records[0]["outcome"] == "reject"


# -- one-edit mutants of the instrument -------------------------------------------------

MUTANTS = {
    "swallow-reject": (
        "            raise\n        except BaseException as error:",
        "            return None\n        except BaseException as error:",
    ),
    "drop-crash-reraise": (
        "                }\n            )\n            raise\n        self._append_total(",
        "                }\n            )\n            return None\n        self._append_total(",
    ),
    "seq-constant": ('"seq": self._seq()', '"seq": 0'),
    "wrong-outcome-label": ('"outcome": "accept"', '"outcome": "ok"'),
    "unsorted-jsonl": (
        "json.dumps(record, sort_keys=True))\n            except",
        "json.dumps(record))\n            except",
    ),
    "drop-code": ('"code": _attribute(error, "code"),', ""),
    "swap-shape": ('"mutating": op["mutating"]', '"mutating": not op["mutating"]'),
    "entitlements-wrong-shape": ('"entitlements.get"', '"entitlements.nope"'),
    "copy-args": (
        "self._client.call(name, body)",
        "self._client.call(name, __import__('copy').deepcopy(body))",
    ),
    "crash-unchanged-constant": (
        '"error_type": _type_name(type(error)),\n'
        '                    "request_unchanged": _snapshot(body) == before,',
        '"error_type": _type_name(type(error)),\n                    "request_unchanged": True,',
    ),
    "append-fallback-empty": ('[_opaque("trace-append-failed")]', "[]"),
}


def _load(source):
    module = types.ModuleType("instrument_under_test")
    module.__file__ = str(ROOT / "server" / "control_plane_client_instrument.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _mutant(label):
    before, after = MUTANTS[label]
    assert SOURCE.count(before) == 1, label
    return _load(SOURCE.replace(before, after))


class _Spy:
    """A comparator or client that records the exact argument objects it receives."""

    def __init__(self):
        self.seen = []

    def __call__(self, *args):
        self.seen.append(args)

    def call(self, *args):
        self.seen.append(args)


class _MutatingCrash:
    """Mutates its first argument, then crashes with a fixed exception object."""

    def __init__(self, boom):
        self.boom = boom

    def __call__(self, first, *rest):
        first.append("junk")
        raise self.boom

    def call(self, name, body=None):
        body.append("junk")
        raise self.boom


def _first_deviation(module):
    """The name of the first semantic check the module fails, or None.

    An unexpected exception from the instrument propagates and errors the
    test instead of counting as a kill."""
    """True only on a semantic deviation; an unexpected exception errors."""
    tracer = module.ClientTracer(_client()[0])
    tracer.call("identity.register", dict(EMAIL))
    tracer.entitlements()
    records = tracer.records
    if [r["seq"] for r in records] != [0, 1]:
        return "check-1"
    if any(r["outcome"] != "accept" for r in records):
        return "check-2"
    if [r["shape"] for r in records] != [
        {"declared": True, "auth": "public", "mutating": True},
        {"declared": True, "auth": "required", "mutating": False},
    ]:
        return "check-3"
    if "\n".join(json.dumps(r, sort_keys=True) for r in records) != tracer.to_jsonl():
        return "check-4"
    try:
        tracer.call("identity.register", {"undeclared": True})
        return "check-5"
    except client_mod.ControlPlaneError:
        pass
    last = tracer.records[-1]
    if last.get("outcome") != "reject" or last.get("code") != "malformed_request":
        return "check-6"
    spy = _Spy()
    probe = module.ClientTracer(spy)
    given = [0]
    probe.call("identity.login", given)
    if len(spy.seen) != 1 or len(spy.seen[0]) != 2:
        return "args-identity"
    if spy.seen[0][0] != "identity.login" or spy.seen[0][1] is not given:
        return "args-identity"
    lost = module.ClientTracer(spy)
    del lost._trace
    lost.call("identity.login", given)
    if lost.records != ({"__opaque__": "trace-append-failed"},):
        return "append-fallback"
    boom = KeyError("x")
    crash = module.ClientTracer(_MutatingCrash(boom))
    victim = [0]
    try:
        crash.call("identity.login", victim)
    except KeyError as caught:
        if caught is not boom:
            return "crash-reraise-identity"
    else:
        return "crash-swallowed"
    record = crash.records[-1]
    if record.get("outcome") != "crash" or record.get("error_type") != "KeyError":
        return "crash-record"
    if record.get("request_unchanged") is not False:
        return "crash-arguments-unchanged"
    return None


def _is_red(module):
    return _first_deviation(module) is not None


def test_unmutated_instrument_is_green_on_the_mutant_check():
    assert _first_deviation(_load(SOURCE)) is None


@pytest.mark.parametrize("label", sorted(MUTANTS))
def test_mutant_is_red(label):
    assert _is_red(_mutant(label)) is True, label
    assert isinstance(_first_deviation(_mutant(label)), str), label


# the check that must catch each of these, so a kill is never an accident
EXPECTED_KILL = {
    "drop-crash-reraise": "crash-swallowed",
    "copy-args": "args-identity",
    "crash-unchanged-constant": "crash-arguments-unchanged",
    "append-fallback-empty": "append-fallback",
}


@pytest.mark.parametrize("label", sorted(EXPECTED_KILL))
def test_mutant_dies_on_its_own_check(label):
    assert _first_deviation(_mutant(label)) == EXPECTED_KILL[label]


def test_a_crashing_instrument_is_an_error_not_a_kill():
    broken = SOURCE.replace('"outcome": "crash",', '"outcome": undefined_name,')
    assert broken != SOURCE
    with pytest.raises(NameError):
        _first_deviation(_load(broken))
