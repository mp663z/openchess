"""T0477: deterministic property families over the shipped control-plane client.

No private server implementation is imported. The T0474 mock is a
contract-conforming transport; adversarial transports test the public
consumer boundary, not a copied reference client.
"""
from __future__ import annotations

# ruff: noqa: E501  (property row literals)
import copy
import itertools
import math
import random
from pathlib import Path

import pytest
import yaml

import server.control_plane_client as prod
from tests.test_t0476_control_plane_client import EMAIL, RESERVE, SECRET, Clock, Wire, _client
from tools.control_plane_mock import MockControlPlane

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data/contracts/control-plane.yaml").read_text())


def _outcome(fn):
    try:
        return ("ok", fn())
    except prod.ControlPlaneError as e:
        assert type(e.code) is str and e.code in prod.ERROR_ENUM
        assert e.__cause__ is None
        return ("error", e.code)


def _session(clock=None, fail=None):
    c, wire, server, clock, logs = _client(prod, clock=clock, fail=fail)
    c.call("identity.register", dict(EMAIL))
    return c, wire, server, clock, logs


@pytest.mark.parametrize("seed", range(12))
def test_retries_are_one_logical_effect_and_one_key(seed):
    rng = random.Random(seed)
    delivered_failures = rng.randint(0, 3)
    attempt_limit = delivered_failures + 1
    seen = {"n": 0}

    def lost_answer(n, method, path, result):
        if path == "/quota/reserve" and seen["n"] < delivered_failures:
            seen["n"] += 1
            raise ConnectionError("reply lost after delivery")

    mock = MockControlPlane()
    wire = Wire(mock, lost_answer)
    c = prod.ControlPlaneClient(wire, max_attempts=attempt_limit)
    c.call("identity.register", dict(EMAIL))
    server = mock
    before = server.quota_units
    result = c.call("quota.reserve", dict(RESERVE))
    calls = [(h, b) for _, path, h, b in wire.calls if path == "/quota/reserve"]
    assert len(calls) == attempt_limit
    assert len({h["Idempotency-Key"] for h, _ in calls}) == 1
    assert all(b == RESERVE for _, b in calls)
    assert result["granted_units"] == RESERVE["estimated_units"]
    assert server.quota_units == before - RESERVE["estimated_units"]
    assert len(server.reservations) == 1


@pytest.mark.parametrize("ttl,step", list(itertools.product((1, 300, 1800), (-1, 0, 1))))
def test_cache_exact_ttl_property(ttl, step):
    class TTLMock(MockControlPlane):
        def _op_entitlements_get(self, body, account=None, headers=None):
            status, payload = super()._op_entitlements_get(body, account, headers)
            payload["cache_ttl_seconds"] = ttl
            return status, payload

    state = {"down": False}
    mock = TTLMock()

    def transport(method, path, headers, body):
        if state["down"] and path == "/entitlements":
            raise ConnectionError("offline")
        return mock.handle(method, path, headers, body)

    clock = Clock()
    c = prod.ControlPlaneClient(transport, clock=clock, max_attempts=1)
    c.call("identity.register", dict(EMAIL))
    expected = c.entitlements()
    state["down"] = True
    clock.now += ttl + step
    got = c.entitlements()
    assert got == (expected if step < 0 else prod.FREE_TIER)
    assert c.entitlements_source == ("cache" if step < 0 else "free")


@pytest.mark.parametrize("skew,delta", list(itertools.product((0, 1, 60, 120), (-1, 0, 1))))
def test_refresh_boundary_property(skew, delta):
    c, wire, _, clock, _ = _client(prod, refresh_skew_seconds=skew)
    c.call("identity.register", dict(EMAIL))
    expiry = c.token_expires_at
    clock.now = expiry - skew + delta
    expired = clock.now >= expiry
    prior = len(wire.calls)
    result = _outcome(lambda: c.call("entitlements.get"))
    paths = [p for _, p, _, _ in wire.calls[prior:]]
    if expired:
        assert result == ("error", "auth_expired") and paths == []
    elif delta < 0:
        assert result[0] == "ok" and paths == ["/entitlements"]
    else:
        assert result[0] == "ok" and paths == ["/identity/refresh", "/entitlements"]


@pytest.mark.parametrize("delta,operation", list(itertools.product((-1, 0, 1), ("quota.commit", "quota.release"))))
def test_reservation_expiry_property(delta, operation):
    c, wire, server, clock, _ = _session()
    reservation = c.call("quota.reserve", dict(RESERVE))
    clock.now = reservation["expires_at"] + delta
    before = len(wire.calls)
    body = {"reservation_id": reservation["reservation_id"]}
    if operation == "quota.commit":
        body["actual_units"] = 1
    result = _outcome(lambda: c.call(operation, body))
    if delta >= 0:
        assert len(wire.calls) == before
        assert result == (("error", "quota_reservation_expired") if operation == "quota.commit" else ("ok", {}))
    else:
        assert len(wire.calls) == before + 1
        assert result[0] == "ok"


def _examples(fields):
    result = {}
    for name, spec in fields.items():
        if spec["required"]:
            result[name] = _examples(spec["fields"]) if spec["type"] == "object" else copy.deepcopy(spec["example"])
    return result


@pytest.mark.parametrize("area,op", [(a, name) for a, spec in DOC["areas"].items() for name in spec["ops"]])
def test_required_leaf_never_coerces_wrong_type(area, op):
    fields = DOC["areas"][area]["ops"][op]["request"]["fields"]
    if not fields:
        pytest.skip("operation declares no request fields")
    name = next(iter(fields))
    body = _examples(fields)
    invalid = (True if fields[name]["type"] in ("integer", "number")
               else 1 if fields[name]["type"] == "string" else "invalid")
    body[name] = invalid
    c, wire, _, _, _ = _session()
    before = len(wire.calls)
    assert _outcome(lambda: c.call(f"{area}.{op}", body)) == ("error", "malformed_request")
    assert len(wire.calls) == before


@pytest.mark.parametrize("depth", (0, 1, 8, 32))
def test_undeclared_response_extras_are_never_returned(depth):
    class Extra(MockControlPlane):
        def _op_entitlements_get(self, body, account=None, headers=None):
            status, payload = super()._op_entitlements_get(body, account, headers)
            extra = {"future": "metadata"}
            for _ in range(depth):
                extra = [extra]
            payload["future_field"] = extra
            return status, payload

    mock = Extra()
    c = prod.ControlPlaneClient(mock.handle)
    c.call("identity.register", dict(EMAIL))
    got = c.call("entitlements.get")
    assert "future_field" not in got and prod._valid(got, prod.OPS["entitlements.get"]["response"], closed=False)


def test_sensitive_key_never_in_log_across_operations():
    c, wire, server, _, logs = _session()
    c.call("provider_routing.register_key", {"provider_kind": "generic-a", "key_material": SECRET})
    c.call("provider_routing.route", {"capability": "analysis-fast"})
    assert SECRET not in repr(logs)
    assert all(len(entry) == 3 for entry in logs)


@pytest.mark.parametrize("seed", range(12))
def test_generated_optional_request_does_not_change_caller_input(seed):
    rng = random.Random(seed + 100)
    body = dict(EMAIL)
    if rng.choice((True, False)):
        body["display_name"] = "User" + str(seed)
    snapshot = copy.deepcopy(body)
    c, wire, server, _, _ = _client(prod)
    result = c.call("identity.register", body)
    assert body == snapshot
    assert type(result["token"]) is str and result["account_id"] == c.account_id
    assert wire.calls[0][3] == snapshot


@pytest.mark.parametrize("seed", range(12))
def test_generated_fresh_calls_use_distinct_keys(seed):
    rng = random.Random(seed + 500)
    count = rng.randrange(2, 7)
    c, wire, server, _, _ = _session()
    start = len(wire.calls)
    for _i in range(count):
        units = 1 + rng.randrange(30)
        result = c.call("quota.reserve", {"operation_kind": "analysis", "estimated_units": units, "hold_seconds": 60})
        assert result["granted_units"] == units
    keys = [headers["Idempotency-Key"] for _, path, headers, _ in wire.calls[start:] if path == "/quota/reserve"]
    assert len(keys) == count and len(set(keys)) == count


@pytest.mark.parametrize("sample", (0, 1, 2**53 - 1, True, 3.5, float("nan"), float("inf")))
def test_number_response_type_boundary(sample):
    class Override(MockControlPlane):
        def _op_entitlements_get(self, body, account=None, headers=None):
            status, result = super()._op_entitlements_get(body, account, headers)
            result["cost_caps"]["per_day_usd"] = sample
            return status, result

    c, _, _, _, _ = _client(prod, server=Override())
    c.call("identity.register", dict(EMAIL))
    outcome = _outcome(lambda: c.call("entitlements.get"))
    if type(sample) is bool or (type(sample) is float and not math.isfinite(sample)):
        assert outcome == ("error", "internal")
    else:
        assert outcome[0] == "ok" and outcome[1]["cost_caps"]["per_day_usd"] == sample
