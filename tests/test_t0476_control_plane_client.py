"""T0476: consumer-side control-plane client (server/control_plane_client.py).

Coordinator ruling: T0476 on the public track is the CLIENT that talks to
any conforming control plane; a self-hosted control plane stays private.
The client is exercised against the T0474 reference mock (certified here
by the T0475 conformance harness) and against every T0475 mutant server,
where it must never hand back a contract-violating payload.

Every behaviour check is a function of the client MODULE, so the same
checks run against one-edit source mutants of the production file.
"""

from __future__ import annotations

import copy
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server.control_plane_client as prod  # noqa: E402
from tests.test_t0475_red import MUTANTS as SERVER_MUTANTS  # noqa: E402
from tools.contract_conformance import run  # noqa: E402
from tools.control_plane_mock import MockControlPlane  # noqa: E402

EMAIL = {"email": "a@example.com", "password_hash_client": "h"}
SECRET = "sk-live-0123456789abcdef"
RESERVE = {"operation_kind": "analysis", "estimated_units": 5, "hold_seconds": 60}


class Clock:
    def __init__(self, now=None):
        import time

        self.now = time.time() if now is None else now

    def __call__(self):
        return self.now


class Wire:
    """Transport recorder around a server's handle()."""

    def __init__(self, server, fail=None):
        self.server = server
        self.calls = []
        self.fail = fail  # fail(n, method, path, result) -> raise or pass

    def __call__(self, method, path, headers, body):
        self.calls.append((method, path, dict(headers), copy.deepcopy(body)))
        result = self.server.handle(method, path, headers, body)
        if self.fail is not None:
            self.fail(len(self.calls), method, path, result)
        return result


def _client(M, server=None, **kw):
    server = server or MockControlPlane()
    wire = Wire(server, kw.pop("fail", None))
    clock = kw.pop("clock", None) or Clock()
    logs = []
    c = M.ControlPlaneClient(wire, clock=clock, log=lambda *a: logs.append(a), **kw)
    return c, wire, server, clock, logs


def _registered(M, **kw):
    c, wire, server, clock, logs = _client(M, **kw)
    c.call("identity.register", dict(EMAIL))
    return c, wire, server, clock, logs


def _expect(M, code, fn, *args):
    with pytest.raises(M.ControlPlaneError) as ei:
        fn(*args)
    err = ei.value
    assert err.code == code, (err.code, code)
    assert err.code in M.ERROR_ENUM
    assert err.__cause__ is None
    return err


def _paths(wire):
    return [p for _, p, _, _ in wire.calls]


# ---- behaviour checks (module-parametric) -----------------------------------


def check_happy_session(M):
    c, wire, server, clock, logs = _registered(M)
    assert c.token and c.account_id
    ent = c.entitlements()
    assert c.entitlements_source == "live" and ent["tier"] == "free"
    r = c.call("quota.reserve", dict(RESERVE))
    assert c.call("quota.commit", {"reservation_id": r["reservation_id"], "actual_units": 3}) == {
        "remaining_units": 997
    }
    k = c.call("provider_routing.register_key", {"provider_kind": "x", "key_material": SECRET})
    assert set(k) == {"key_ref"}
    assert c.call("identity.logout") == {}
    assert c.token is None and c.account_id is None


def check_bearer_and_key_headers(M):
    c, wire, *_ = _registered(M)
    c.call("quota.reserve", dict(RESERVE))
    c.call("quota.reserve", dict(RESERVE))
    c.call("entitlements.get")
    reg, r1, r2, ent = wire.calls
    assert "Authorization" not in reg[2] and reg[2]["Idempotency-Key"]
    assert r1[2]["Authorization"] == f"Bearer {c.token}"
    assert r1[2]["Idempotency-Key"] != r2[2]["Idempotency-Key"]  # one key per logical op
    assert "Idempotency-Key" not in ent[2]  # read-only op


def check_retry_reuses_key_single_effect(M):
    def fail(n, method, path, result):
        if path == "/quota/reserve" and n == 2:  # delivered, answer lost
            raise ConnectionError("reset after delivery")

    c, wire, server, *_ = _registered(M, fail=fail)
    before = server.quota_units
    out = c.call("quota.reserve", dict(RESERVE))
    tries = [h for _, p, h, _ in wire.calls if p == "/quota/reserve"]
    assert len(tries) == 2 and tries[0]["Idempotency-Key"] == tries[1]["Idempotency-Key"]
    assert server.quota_units == before - 5  # replay, no duplicate effect
    assert out["granted_units"] == 5


def check_retry_exhaustion_is_retryable_internal(M):
    def fail(n, method, path, result):
        if path == "/provider-routing/route":
            raise TimeoutError

    c, wire, *_ = _registered(M, fail=fail, max_attempts=3)
    err = _expect(M, "internal", c.call, "provider_routing.route", {"capability": "explain"})
    assert err.retryable is True and err.status is None
    assert _paths(wire).count("/provider-routing/route") == 3


def check_non_retryable_not_retried(M):
    c, wire, *_ = _registered(M)
    _expect(M, "not_found", c.call, "quota.release", {"reservation_id": "rsv-none"})
    assert _paths(wire).count("/quota/release") == 1


def check_server_retryable_error_is_retried(M):
    class Flaky(MockControlPlane):
        n = 0

        def _op_entitlements_get(self, body, account=None, headers=None):
            Flaky.n += 1
            if Flaky.n == 1:
                return self._err("internal", "blip", retryable=True, status=503)
            return super()._op_entitlements_get(body, account, headers)

    c, wire, *_ = _registered(M, server=Flaky())
    assert c.call("entitlements.get")["tier"] == "free"
    assert _paths(wire).count("/entitlements") == 2


def check_refresh_before_expiry(M):
    c, wire, server, clock, _ = _registered(M, refresh_skew_seconds=60)
    old = c.token
    clock.now = c.token_expires_at - 30  # inside the skew window
    c.call("entitlements.get")
    assert _paths(wire)[-2:] == ["/identity/refresh", "/entitlements"]
    assert c.token != old
    assert wire.calls[-1][2]["Authorization"] == f"Bearer {c.token}"


def check_refresh_at_exact_skew_edge(M):
    c, wire, server, clock, _ = _registered(M, refresh_skew_seconds=60)
    clock.now = c.token_expires_at - 60  # exactly on the edge: refresh (inclusive)
    c.call("entitlements.get")
    assert _paths(wire)[-2:] == ["/identity/refresh", "/entitlements"]


def check_no_refresh_outside_skew(M):
    c, wire, server, clock, _ = _registered(M, refresh_skew_seconds=60)
    clock.now = c.token_expires_at - 61
    c.call("entitlements.get")
    assert "/identity/refresh" not in _paths(wire)


def check_locally_expired_token_is_auth_expired_unsent(M):
    c, wire, server, clock, _ = _registered(M)
    clock.now = c.token_expires_at
    n = len(wire.calls)
    _expect(M, "auth_expired", c.call, "entitlements.get")
    assert len(wire.calls) == n and c.token is None


def check_no_token_is_auth_invalid_unsent(M):
    c, wire, *_ = _client(M)
    _expect(M, "auth_invalid", c.call, "entitlements.get")
    assert wire.calls == []


def check_server_auth_expired_clears_token(M):
    c, wire, server, *_ = _registered(M)
    server.fixture_expire_token(c.token)
    err = _expect(M, "auth_expired", c.call, "entitlements.get")
    assert err.status == 401 and c.token is None
    assert _paths(wire).count("/entitlements") == 1  # never retried


def check_server_auth_invalid_clears_token(M):
    c, wire, server, *_ = _registered(M)
    server.tokens.clear()
    _expect(M, "auth_invalid", c.call, "quota.reserve", dict(RESERVE))
    assert c.token is None


def check_entitlements_cache_then_free_tier(M):
    state = {"down": False}

    def fail(n, method, path, result):
        if state["down"]:
            raise ConnectionError

    c, wire, server, clock, _ = _registered(M, fail=fail)
    live = c.entitlements()
    t0 = clock.now
    state["down"] = True
    clock.now = t0 + live["cache_ttl_seconds"] - 1
    assert c.entitlements() == live and c.entitlements_source == "cache"
    clock.now = t0 + live["cache_ttl_seconds"]  # the TTL boundary is expired
    free = c.entitlements()
    assert c.entitlements_source == "free" and free == prod.FREE_TIER
    assert free["features"] == [] and free["cost_caps"] == {
        "per_request_usd": 0.0,
        "per_day_usd": 0.0,
    }


def check_free_tier_without_cache(M):
    def fail(n, method, path, result):
        if path == "/entitlements":
            raise OSError

    c, *_ = _registered(M, fail=fail)
    assert c.entitlements() == prod.FREE_TIER and c.entitlements_source == "free"


def check_auth_error_not_masked_by_cache(M):
    c, wire, server, *_ = _registered(M)
    c.entitlements()
    server.fixture_expire_token(c.token)
    _expect(M, "auth_expired", c.entitlements)


def check_logout_drops_cache(M):
    c, wire, server, clock, _ = _registered(M)
    c.entitlements()
    c.call("identity.logout")
    _expect(M, "auth_invalid", c.entitlements)


def check_expired_reservation_is_released(M):
    c, wire, server, clock, _ = _registered(M)
    r = c.call("quota.reserve", dict(RESERVE))
    clock.now = r["expires_at"]  # the expiry instant counts as expired
    n = len(wire.calls)
    _expect(
        M,
        "quota_reservation_expired",
        c.call,
        "quota.commit",
        {"reservation_id": r["reservation_id"], "actual_units": 1},
    )
    assert len(wire.calls) == n
    r2 = c.call("quota.reserve", dict(RESERVE))
    clock.now = r2["expires_at"] + 5
    n = len(wire.calls)
    assert c.call("quota.release", {"reservation_id": r2["reservation_id"]}) == {}
    assert len(wire.calls) == n


def check_live_reservation_commits(M):
    c, wire, server, clock, _ = _registered(M)
    r = c.call("quota.reserve", dict(RESERVE))
    clock.now = r["expires_at"] - 1
    c.call("quota.commit", {"reservation_id": r["reservation_id"], "actual_units": 1})
    assert _paths(wire)[-1] == "/quota/commit"


def check_key_material_never_logged_or_echoed(M):
    c, wire, server, clock, logs = _registered(M)
    c.call("provider_routing.register_key", {"provider_kind": "x", "key_material": SECRET})
    assert SECRET not in repr(logs)
    assert all(len(entry) == 3 for entry in logs)


def check_error_message_echo_redacted(M):
    class Echo(MockControlPlane):
        def _op_provider_routing_register_key(self, body, account=None, headers=None):
            return self._err(
                "provider_key_invalid",
                f"bad key {body['key_material']}",
                retryable=False,
                status=422,
            )

    c, *_ = _registered(M, server=Echo())
    err = _expect(
        M,
        "provider_key_invalid",
        c.call,
        "provider_routing.register_key",
        {"provider_kind": "x", "key_material": SECRET},
    )
    assert SECRET not in str(err) and SECRET not in err.message


def check_response_echo_refused(M):
    c, *_ = _registered(M, server=SERVER_MUTANTS["leak_key_material"]())
    err = _expect(
        M,
        "internal",
        c.call,
        "provider_routing.register_key",
        {"provider_kind": "x", "key_material": SECRET},
    )
    assert SECRET not in str(err)


def check_response_contract_enforced(M):
    for name in (
        "response_wrong_value_type",
        "status_not_exact_int",
        "return_not_2_tuple",
        "payload_not_object",
        "array_item_wrong_type",
    ):
        c, *_ = _registered(M, server=SERVER_MUTANTS[name]())
        _expect(M, "internal", c.call, "entitlements.get")


def check_error_contract_enforced(M):
    c, *_ = _registered(M, server=SERVER_MUTANTS["error_code_not_string"]())
    _expect(M, "internal", c.call, "billing.get_subscription")
    server = SERVER_MUTANTS["error_code_outside_enum"]()
    c, *_ = _registered(M, server=server)
    c2 = M.ControlPlaneClient(server.handle)
    _expect(M, "internal", c2.call, "identity.register", dict(EMAIL))  # 409 as 'conflictz'


def check_error_code_must_be_declared_for_op(M):
    class Undeclared(MockControlPlane):
        def _op_entitlements_get(self, body, account=None, headers=None):
            return self._err("quota_exhausted", "x", retryable=False, status=429)

    c, *_ = _registered(M, server=Undeclared())
    _expect(M, "internal", c.call, "entitlements.get")


def check_optional_field_type_enforced(M):
    server = SERVER_MUTANTS["optional_field_wrong_type"]()
    c = M.ControlPlaneClient(server.handle)
    _expect(M, "internal", c.call, "identity.register", {**EMAIL, "display_name": "A"})
    assert c.token is None  # a refused response never starts a session


def check_crashing_servers_fail_closed(M):
    for name in ("implementation_crash", "system_exit_zero_escape"):
        c, *_ = _registered(M, server=SERVER_MUTANTS[name]())
        err = _expect(M, "internal", c.call, "provider_routing.route", {"capability": "x"})
        assert err.retryable is True


CHECKS = {
    name[len("check_") :]: fn for name, fn in sorted(globals().items()) if name.startswith("check_")
}


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_behaviour(name):
    CHECKS[name](prod)


# ---- conformance binding ---------------------------------------------------------


def test_reference_mock_is_certified_by_the_harness():
    mock = MockControlPlane()
    assert run(mock, fixture=mock) == []


@pytest.mark.parametrize("name", sorted(SERVER_MUTANTS))
def test_every_mutant_server_never_yields_a_contract_violating_payload(name):
    """Drive a full session against each T0475 mutant: every call either
    returns a contract-valid payload or raises a closed-enum error."""
    server = SERVER_MUTANTS[name]()
    c = prod.ControlPlaneClient(server.handle, max_attempts=1)
    script = [
        ("identity.register", dict(EMAIL)),
        ("identity.register", dict(EMAIL)),
        ("entitlements.get", None),
        ("quota.reserve", dict(RESERVE)),
        ("provider_routing.register_key", {"provider_kind": "x", "key_material": SECRET}),
        ("provider_routing.route", {"capability": "x"}),
        ("billing.get_subscription", None),
        ("identity.refresh", None),
        ("identity.delete_account", {"confirm": "DELETE"}),
    ]
    for op, body in script:
        try:
            out = c.call(op, body)
        except prod.ControlPlaneError as err:
            assert err.code in prod.ERROR_ENUM and err.__cause__ is None
            assert SECRET not in str(err)
            continue
        assert prod._valid(out, prod.OPS[op]["response"], closed=False), (name, op)
        assert SECRET not in repr(out)


# ---- hostile inputs --------------------------------------------------------------

CALLS = []


class StrSub(str):
    pass


class DictSub(dict):
    pass


class ListSub(list):
    pass


class EqRaises(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("eq")

    __hash__ = str.__hash__


class HashCollide(str):
    def __hash__(self):
        CALLS.append("hash")
        return hash("reservation_id")

    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)


class IntSub(int):
    pass


def _hostile_bodies():
    rows = []
    base_op, base = "quota.reserve", dict(RESERVE)
    rows.append(("dict_sub", base_op, DictSub(base)))
    rows.append(("list", base_op, list(base.items())))
    rows.append(("str", base_op, "operation_kind"))
    for key in base:
        r = dict(base)
        r[key] = StrSub(r[key]) if type(r[key]) is str else IntSub(r[key])
        rows.append((f"{key}_subclass", base_op, r))
        r = dict(base)
        r[key] = DictSub({"v": r[key]})
        rows.append((f"{key}_dictsub", base_op, r))
        r = dict(base)
        r[key] = ListSub([r[key]])
        rows.append((f"{key}_listsub", base_op, r))
        r = dict(base)
        del r[key]
        rows.append((f"missing_{key}", base_op, r))
    for form, cls in (("plain", str), ("eq_raises", EqRaises), ("hash_collide", HashCollide)):
        r = dict(base)
        r[cls("extra")] = 1
        rows.append((f"extra_key_{form}", base_op, r))
        if cls is not str:  # a plain-str real key is the valid body
            r = dict(base)
            r[cls("operation_kind")] = r.pop("operation_kind")
            rows.append((f"real_key_{form}", base_op, r))
    rows.append(("bool_for_int", base_op, dict(base, estimated_units=True)))
    rows.append(("float_for_int", base_op, dict(base, hold_seconds=60.0)))
    route = {"capability": "x", "policy": DictSub()}
    rows.append(("nested_object_dictsub", "provider_routing.route", route))
    rows.append(
        (
            "bytes_key",
            base_op,
            {b"operation_kind": "a", **{k: base[k] for k in base if k != "operation_kind"}},
        )
    )
    return rows


HOSTILE = _hostile_bodies()


def _snap(body):
    if isinstance(body, dict):
        return [(k, copy.deepcopy(v)) for k, v in body.items()]
    return copy.deepcopy(body)


def _unchanged(body, snap):
    if isinstance(body, dict):
        now = list(body.items())
        return len(now) == len(snap) and all(
            k1 is k2 and type(v1) is type(v2) and v1 == v2
            for (k1, v1), (k2, v2) in zip(now, snap, strict=True)
        )
    return body == snap


def hostile_request(M, label, op, body):
    c, wire, *_ = _registered(M)
    n = len(wire.calls)
    snap = _snap(body)
    CALLS.clear()
    _expect(M, "malformed_request", c.call, op, body)
    assert CALLS == []
    assert len(wire.calls) == n
    assert _unchanged(body, snap)


@pytest.mark.parametrize("label,op,body", HOSTILE, ids=[r[0] for r in HOSTILE])
def test_hostile_request_is_malformed_unsent(label, op, body):
    hostile_request(prod, label, op, body)


@pytest.mark.parametrize(
    "name", [StrSub("entitlements.get"), "entitlements.GET", None, ["entitlements.get"]]
)
def test_hostile_operation_name_is_malformed_unsent(name):
    c, wire, *_ = _registered(prod)
    n = len(wire.calls)
    _expect(prod, "malformed_request", c.call, name)
    assert len(wire.calls) == n


class _Answer:
    def __init__(self, result):
        self.result = result

    def __call__(self, method, path, headers, body):
        if path == "/identity/register":
            return MockControlPlane().handle(method, path, headers, body)
        return self.result


def _err(code="not_found", message="x", retryable=False):
    return {"error": {"code": code, "message": message, "retryable": retryable}}


HOSTILE_RESULTS = {
    "list": [200, {}],
    "tuple_sub": type("T", (tuple,), {})((200, {"status": "active"})),
    "three": (200, {}, None),
    "bool_status": (True, {}),
    "intsub_status": (IntSub(200), {}),
    "intsub_status_valid_payload": (
        IntSub(200),
        {"status": "a", "tier": "f", "current_period_end": 1, "cancel_at_period_end": False},
    ),
    "status_600": (600, {}),
    "status_99": (99, {}),
    "payload_dictsub": (200, DictSub()),
    "error_not_dict": (404, {"error": "not_found"}),
    "error_dictsub": (404, {"error": DictSub(_err()["error"])}),
    "code_strsub": (404, _err(code=StrSub("not_found"))),
    "code_unknown": (404, _err(code="gone")),
    "code_undeclared_for_op": (404, _err(code="quota_exhausted")),
    "message_not_str": (404, {"error": {"code": "not_found", "message": 1, "retryable": False}}),
    "retryable_int": (404, _err(retryable=0)),
    "error_missing": (404, {}),
    "response_missing_field": (
        200,
        {"status": "active", "tier": "free", "current_period_end": 1},
    ),
    "response_bool_for_int": (
        200,
        {"status": "a", "tier": "f", "current_period_end": True, "cancel_at_period_end": False},
    ),
    "response_strsub": (
        200,
        {
            "status": StrSub("a"),
            "tier": "f",
            "current_period_end": 1,
            "cancel_at_period_end": False,
        },
    ),
}


def hostile_result(M, label):
    c = M.ControlPlaneClient(_Answer(HOSTILE_RESULTS[label]), max_attempts=1)
    c.call("identity.register", dict(EMAIL))
    _expect(M, "internal", c.call, "billing.get_subscription")


@pytest.mark.parametrize("label", sorted(HOSTILE_RESULTS))
def test_hostile_transport_result_is_internal(label):
    hostile_result(prod, label)


def test_extra_response_fields_are_ignored():
    ok = {"status": "a", "tier": "f", "current_period_end": 1, "cancel_at_period_end": False}
    c = prod.ControlPlaneClient(_Answer((200, dict(ok, added_in_minor=1))))
    c.call("identity.register", dict(EMAIL))
    assert c.call("billing.get_subscription")["tier"] == "f"


# ---- forge set: raised errors are fresh ---------------------------------------------

FORGED = prod.ControlPlaneError("not_found", "forged", status=404)


RAISED = {
    "forged": FORGED,
    "value": ValueError("v"),
    "type": TypeError("t"),
    "keyboard": KeyboardInterrupt(),
    "system_exit": SystemExit(0),
    "generator_exit": GeneratorExit(),
}


def transport_raises(M, label):
    exc = RAISED[label]
    forged = exc if label == "forged" else None

    def transport(method, path, headers, body):
        if path == "/identity/register":
            return MockControlPlane().handle(method, path, headers, body)
        raise exc

    c = M.ControlPlaneClient(transport, max_attempts=2)
    c.call("identity.register", dict(EMAIL))
    err = _expect(M, "internal", c.call, "billing.get_subscription")
    assert err is not forged and err.retryable is True


@pytest.mark.parametrize("label", sorted(RAISED))
def test_transport_raising_anything_is_a_fresh_internal(label):
    transport_raises(prod, label)


@pytest.mark.parametrize("where", ["clock", "key_factory"])
@pytest.mark.parametrize("exc", [FORGED, KeyboardInterrupt(), ValueError()])
def test_raising_clock_or_key_factory_is_a_fresh_internal(where, exc):
    def boom():
        raise exc

    mock = MockControlPlane()
    kw = {where: boom}
    c = prod.ControlPlaneClient(mock.handle, **kw)
    if where == "clock":
        c.token, c.token_expires_at = "t", 10**12
        err = _expect(prod, "internal", c.call, "entitlements.get")
    else:
        err = _expect(prod, "internal", c.call, "identity.register", dict(EMAIL))
    assert err is not FORGED


BAD_CLOCKS = {"nan": float("nan"), "bool": True, "str": "1", "none": None}
BAD_KEYS = {"empty": "", "blank": "  ", "strsub": StrSub("k"), "int": 5, "none": None}


def bad_clock(M, label):
    bad = BAD_CLOCKS[label]
    c = M.ControlPlaneClient(MockControlPlane().handle, clock=lambda: bad)
    c.token, c.token_expires_at = "t", 10**12
    _expect(M, "internal", c.call, "entitlements.get")


def bad_key(M, label):
    bad = BAD_KEYS[label]
    wire = Wire(MockControlPlane())
    c = M.ControlPlaneClient(wire, key_factory=lambda: bad)
    _expect(M, "internal", c.call, "identity.register", dict(EMAIL))
    assert wire.calls == []


@pytest.mark.parametrize("label", sorted(BAD_CLOCKS))
def test_bad_clock_value_is_internal(label):
    bad_clock(prod, label)


@pytest.mark.parametrize("label", sorted(BAD_KEYS))
def test_bad_idempotency_key_is_internal_unsent(label):
    bad_key(prod, label)


def test_raising_log_hook_never_changes_the_outcome():
    def log(*a):
        raise KeyboardInterrupt

    c = prod.ControlPlaneClient(MockControlPlane().handle, log=log)
    assert c.call("identity.register", dict(EMAIL))["account_id"]


@pytest.mark.parametrize(
    "kw",
    [
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"max_attempts": True},
        {"refresh_skew_seconds": -1},
        {"refresh_skew_seconds": 1.5},
    ],
)
def test_bad_constructor_arguments_are_refused(kw):
    with pytest.raises(ValueError):
        prod.ControlPlaneClient(MockControlPlane().handle, **kw)


# ---- executable kill check: one-edit source mutants of production ---------------

MUTANTS = {
    "request_open": (
        'if not _valid(body, op["request"], closed=True):',
        'if not _valid(body, op["request"], closed=False):',
    ),
    "str_isinstance": (
        '"string": lambda v: type(v) is str,',
        '"string": lambda v: isinstance(v, str),',
    ),
    "int_isinstance": (
        '"integer": lambda v: type(v) is int,',
        '"integer": lambda v: isinstance(v, int),',
    ),
    "body_isinstance": (
        "    if type(payload) is not dict:\n        return False",
        "    if not isinstance(payload, dict):\n        return False",
    ),
    "key_type_skip": ("        if type(key) is not str:\n            return False\n", ""),
    "required_skip": (
        '            if spec["required"]:\n                return False\n            continue',
        "            continue",
    ),
    "array_items_skip": (
        "            if type(item) is str and not all(_TYPE_OK[item](v) for v in value):",
        "            if False:",
    ),
    "response_unchecked": (
        '                if not _valid(payload, op["response"], closed=False) or _contains(',
        "                if False and _contains(",
    ),
    "echo_allowed": (
        '                if not _valid(payload, op["response"], closed=False) or _contains(\n                    payload, secrets_\n                ):',  # noqa: E501 - verbatim source line
        '                if not _valid(payload, op["response"], closed=False):',
    ),
    "message_not_redacted": ('            message = "redacted"', "            pass"),
    "error_code_any_for_op": ('            or code not in op["errors"]\n', ""),
    "pair_len_skip": (
        "if type(result) is not tuple or len(result) != 2:",
        "if type(result) is not tuple:",
    ),
    "transport_exception_only": (
        "            except BaseException:  # noqa: BLE001 - untrusted boundary: fail closed typed",
        "            except Exception:  # noqa: BLE001 - untrusted boundary: fail closed typed",
    ),
    "no_retry": (
        "        for _attempt in range(self._max_attempts):",
        "        for _attempt in range(1):",
    ),
    "retry_non_retryable": (
        "            if not retryable:\n                _fail(code, message, retryable=False, status=status)",  # noqa: E501 - verbatim source line
        "            if False:\n                _fail(code, message, retryable=False, status=status)",  # noqa: E501 - verbatim source line
    ),
    "key_per_attempt": (
        '                result = self._transport(\n                    op["method"], op["path"], self._headers(op, key), copy.deepcopy(body)',  # noqa: E501 - verbatim source line
        '                result = self._transport(\n                    op["method"], op["path"], self._headers(op, key and self._key_factory()), copy.deepcopy(body)',  # noqa: E501 - verbatim source line
    ),
    "auth_not_cleared": (
        '            if code in ("auth_expired", "auth_invalid"):\n                self._clear_session()',  # noqa: E501 - verbatim source line
        "            if False:\n                self._clear_session()",
    ),
    "no_proactive_refresh": (
        '        if name != "identity.refresh" and now >= self.token_expires_at - self._skew:',
        "        if False:",
    ),
    "refresh_skew_inclusive": (
        "now >= self.token_expires_at - self._skew:",
        "now > self.token_expires_at - self._skew:",
    ),
    "expired_token_sent": (
        '        if now >= self.token_expires_at:\n            self._clear_session()\n            _fail("auth_expired", "bearer token expired", status=401)\n',  # noqa: E501 - verbatim source line
        "",
    ),
    "expiry_exclusive": (
        "        if now >= self.token_expires_at:\n",
        "        if now > self.token_expires_at:\n",
    ),
    "no_token_sent": (
        '        if self.token is None:\n            _fail("auth_invalid", "no bearer token held", status=401)\n        now',  # noqa: E501 - verbatim source line
        "        now",
    ),
    "cache_ttl_inclusive": (
        "now - cached[1] < cached[0][TTL_FIELD]",
        "now - cached[1] <= cached[0][TTL_FIELD]",
    ),
    "cache_never_used": (
        "            if cached is not None and now - cached[1] < cached[0][TTL_FIELD]:",
        "            if False:",
    ),
    "unreachable_any_error": (
        "        return err.status is None or err.status >= 500",
        "        return True",
    ),
    "logout_keeps_session": (
        "        elif name in SELF_DESTRUCTIVE:\n            self._clear_session()",
        "        elif name in SELF_DESTRUCTIVE:\n            pass",
    ),
    "reservation_expiry_ignored": (
        "        if expires_at is None or self._now() < expires_at:",
        "        if True:",
    ),
    "reservation_expiry_exclusive": ("self._now() < expires_at:", "self._now() <= expires_at:"),
    "reservation_not_recorded": (
        '            self._reservations[payload["reservation_id"]] = payload[EXPIRY_FIELD]',
        "            pass",
    ),
    "key_blank_ok": (
        "            if type(key) is not str or not key.strip():",
        "            if type(key) is not str:",
    ),
    "clock_nan_ok": (
        "if type(now) not in (int, float) or now != now:",
        "if type(now) not in (int, float):",
    ),
}

# Equivalent under the pinned contract (documented, not executed as kills).
EQUIVALENT = {
    "error_code_any": "every op's declared errors are a subset of the enum, so the op check subsumes it",  # noqa: E501 - verbatim source line
    "object_isinstance": "_valid re-checks type(payload) is dict on the nested object",
}


def _mutant_module(name):
    before, after = MUTANTS[name]
    source = Path(prod.__file__).read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType(f"cp_client_mutant_{name}")
    module.__file__ = prod.__file__
    exec(compile(source.replace(before, after), prod.__file__, "exec"), module.__dict__)
    return module


def _battery(module):
    yield from ((fn,) for fn in CHECKS.values())
    yield from ((hostile_request, *row) for row in HOSTILE)
    yield from ((hostile_result, label) for label in HOSTILE_RESULTS)
    yield from ((transport_raises, label) for label in RAISED)
    yield from ((bad_clock, label) for label in BAD_CLOCKS)
    yield from ((bad_key, label) for label in BAD_KEYS)


def _battery_red(module):
    for fn, *args in _battery(module):
        try:
            fn(module, *args)
        except BaseException:  # noqa: BLE001 - pytest's Failed is a BaseException
            return True
    return False


def test_identity_module_is_green():
    assert not _battery_red(prod)


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_source_mutant_is_killed(name):
    assert _battery_red(_mutant_module(name))


def test_equivalents_are_not_in_mutants():
    assert not set(EQUIVALENT) & set(MUTANTS)
