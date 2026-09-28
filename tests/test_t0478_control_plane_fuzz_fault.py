"""T0478: bounded seeded fault schedules against the shipped consumer client.

The conforming mock owns server effects; this battery never substitutes a
reference implementation for server.control_plane_client. T0479 owns restart.
"""

from __future__ import annotations

import copy
import random
import types
from pathlib import Path

import pytest

import server.control_plane_client as prod
from tests.test_t0476_control_plane_client import EMAIL, RESERVE, Clock
from tools.control_plane_mock import MockControlPlane

SEEDS = (7, 42, 1337, 90210)


def _assert_error(module, code, fn):
    with pytest.raises(module.ControlPlaneError) as caught:
        fn()
    assert caught.value.code == code
    assert caught.value.code in module.ERROR_ENUM
    assert caught.value.__cause__ is None
    return caught.value


def _snapshot(client):
    return copy.deepcopy(
        (
            client.token,
            client.token_expires_at,
            client.account_id,
            client._entitlements,
            client._reservations,
        )
    )


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("fault", ("before", "after", "retryable", "bad_success"))
def test_seeded_reserve_fault_schedules(seed, fault):
    rng = random.Random(seed)
    mock = MockControlPlane()
    calls = []
    attempts = rng.randint(2, 5)
    failing = rng.randrange(1, attempts)

    def wire(method, path, headers, body):
        if path != "/quota/reserve":
            return mock.handle(method, path, headers, body)
        calls.append((copy.deepcopy(headers), copy.deepcopy(body)))
        if len(calls) <= failing and fault == "before":
            raise ConnectionError("before delivery")
        result = mock.handle(method, path, headers, body)
        if len(calls) <= failing:
            if fault == "after":
                raise ConnectionError("after delivery")
            if fault == "retryable":
                # This synthetic answer models an unknown delivery outcome.
                return 503, {
                    "error": {"code": "internal", "message": "unavailable", "retryable": True}
                }
            return 200, {"reservation_id": "partial"}
        return result

    client = prod.ControlPlaneClient(wire, max_attempts=attempts)
    client.call("identity.register", dict(EMAIL))
    before = mock.quota_units
    body = dict(RESERVE, estimated_units=rng.randint(1, 15))
    untouched = copy.deepcopy(body)
    if fault == "bad_success":
        state = _snapshot(client)
        _assert_error(prod, "internal", lambda: client.call("quota.reserve", body))
        assert len(calls) == 1 and _snapshot(client) == state
        # A malformed 2xx is terminal; the server may already have acted,
        # so no unsafe automatic retry or claim of server-side rollback.
    else:
        response = client.call("quota.reserve", body)
        assert response["granted_units"] == body["estimated_units"]
        assert client._reservations[response["reservation_id"]] == response["expires_at"]
        assert len(calls) == failing + 1
        assert mock.quota_units == before - body["estimated_units"]
        assert len(mock.reservations) == 1
    assert body == untouched
    assert len({h["Idempotency-Key"] for h, _ in calls}) == 1
    assert all(sent == untouched for _, sent in calls)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("fault", ("before", "after"))
def test_exhausted_replies_do_not_commit_local_reservation(seed, fault):
    rng = random.Random(seed)
    mock = MockControlPlane()
    calls = []
    attempts = rng.randint(1, 5)

    def wire(method, path, headers, body):
        if path == "/quota/reserve":
            calls.append((copy.deepcopy(headers), copy.deepcopy(body)))
            if fault == "after":
                mock.handle(method, path, headers, body)
            raise TimeoutError("answer lost")
        return mock.handle(method, path, headers, body)

    client = prod.ControlPlaneClient(wire, max_attempts=attempts)
    client.call("identity.register", dict(EMAIL))
    before = _snapshot(client)
    err = _assert_error(prod, "internal", lambda: client.call("quota.reserve", dict(RESERVE)))
    assert err.retryable is True and err.status is None
    assert len(calls) == attempts
    assert len({h["Idempotency-Key"] for h, _ in calls}) == 1
    assert _snapshot(client) == before
    assert len(mock.reservations) == (1 if fault == "after" else 0)
    # Lost success is uncertain remotely. Local rollback must not be
    # mistaken for proof that the server rolled back an accepted call.


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("mode", ("down", "server_503", "server_401"))
def test_outage_cache_boundary_and_auth_fail_closed(seed, mode):
    rng = random.Random(seed)
    clock = Clock()
    mock = MockControlPlane()
    state = {"fail": False}
    calls = []

    def wire(method, path, headers, body):
        if path == "/entitlements" and state["fail"]:
            calls.append(path)
            if mode == "down":
                raise ConnectionError("offline")
            code = "auth_invalid" if mode == "server_401" else "internal"
            return (401 if mode == "server_401" else 503), {
                "error": {"code": code, "message": "unavailable", "retryable": True}
            }
        return mock.handle(method, path, headers, body)

    client = prod.ControlPlaneClient(wire, clock=clock, max_attempts=3)
    client.call("identity.register", dict(EMAIL))
    live = client.entitlements()
    cached_at = client._entitlements[1]
    ttl = live[prod.TTL_FIELD]
    # The token must outlive the cache probe; expiry is a separate preflight.
    client.token_expires_at = cached_at + ttl + 1000
    state["fail"] = True
    clock.now = cached_at + rng.randrange(0, ttl)
    if mode == "server_401":
        err = _assert_error(prod, "auth_invalid", client.entitlements)
        assert err.retryable is False and len(calls) == 1
        assert client.token is None and client._entitlements is None
    else:
        assert client.entitlements() == live
        assert client.entitlements_source == "cache"
        clock.now = cached_at + ttl
        assert client.entitlements() == prod.FREE_TIER
        assert client.entitlements_source == "free"
        assert client.token is not None
        assert len(calls) == 6  # all retry attempts on both calls


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("operation", ("quota.commit", "quota.release"))
def test_expired_reservation_shortcut_rolls_back_local_tracking(seed, operation):
    rng = random.Random(seed)
    clock = Clock()
    mock = MockControlPlane()
    paths = []

    def wire(method, path, headers, body):
        paths.append(path)
        return mock.handle(method, path, headers, body)

    client = prod.ControlPlaneClient(wire, clock=clock)
    client.call("identity.register", dict(EMAIL))
    result = client.call("quota.reserve", dict(RESERVE, hold_seconds=rng.randint(30, 90)))
    key = result["reservation_id"]
    clock.now = result["expires_at"] + rng.choice((0, 1, 10))
    before = len(paths)
    body = {"reservation_id": key}
    if operation == "quota.commit":
        body["actual_units"] = 1
        _assert_error(prod, "quota_reservation_expired", lambda: client.call(operation, body))
    else:
        assert client.call(operation, body) == {}
    assert len(paths) == before and key not in client._reservations
    # The mock's wall clock is independent of the injected client clock:
    # this asserts only local release, not server expiry or refund.


@pytest.mark.parametrize("seed", SEEDS)
def test_malformed_requests_cannot_cross_transport_or_change_session(seed):
    rng = random.Random(seed)
    mock = MockControlPlane()
    calls = []

    def wire(*args):
        calls.append(args)
        return mock.handle(*args)

    client = prod.ControlPlaneClient(wire)
    client.call("identity.register", dict(EMAIL))
    original = _snapshot(client)
    base = dict(RESERVE)
    for _ in range(60):
        body = copy.deepcopy(base)
        field = rng.choice(tuple(base) + ("extra",))
        body[field] = (
            rng.choice((None, True, 1.5, [], {}, "wrong")) if field != "extra" else "unknown"
        )
        # 'wrong' is a valid string for operation_kind; only assert
        # refusal for values outside the exact contract type.
        if prod._valid(body, prod.OPS["quota.reserve"]["request"], closed=True):
            continue
        before = len(calls)
        _assert_error(
            prod, "malformed_request", lambda body=body: client.call("quota.reserve", body)
        )
        assert len(calls) == before and _snapshot(client) == original


# Behavioral kills are run against fresh one-edit copies of production code.
# Every mutation has a dedicated assertion above; a surviving mutant fails
# this table rather than being reported as a kill without execution.
MUTANTS = {
    "retry_new_key": (
        "self._headers(op, key), copy.deepcopy(body)",
        "self._headers(op, self._key_factory()), copy.deepcopy(body)",
        test_seeded_reserve_fault_schedules,
        (7, "after"),
    ),
    "retry_once": (
        "for _attempt in range(self._max_attempts):",
        "for _attempt in range(1):",
        test_seeded_reserve_fault_schedules,
        (7, "before"),
    ),
    "retry_bad_success": (
        '                    _fail("internal", "response violates the contract", status=status)',
        "                    continue",
        test_seeded_reserve_fault_schedules,
        (7, "bad_success"),
    ),
    "lost_local_rollback": (
        '        elif name == "quota.reserve":\n            self._reservations[payload["reservation_id"]] = payload[EXPIRY_FIELD]',  # noqa: E501
        '        elif name == "quota.reserve":\n            pass',
        test_seeded_reserve_fault_schedules,
        (7, "after"),
    ),
    "auth_session_uncleared": (
        "            if code in _AUTH_CODES:\n                self._clear_session()",
        "            if code in _AUTH_CODES:\n                pass",
        test_outage_cache_boundary_and_auth_fail_closed,
        (7, "server_401"),
    ),
    "ttl_inclusive": (
        "now - cached[1] < cached[0][TTL_FIELD]",
        "now - cached[1] <= cached[0][TTL_FIELD]",
        test_outage_cache_boundary_and_auth_fail_closed,
        (7, "down"),
    ),
    "expiry_ignored": (
        "if expires_at is None or self._now() < expires_at:",
        "if True:",
        test_expired_reservation_shortcut_rolls_back_local_tracking,
        (7, "quota.commit"),
    ),
}


def _mutant(name):
    before, after, _, _ = MUTANTS[name]
    source = Path(prod.__file__).read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType("t0478_mutant_" + name)
    module.__file__ = prod.__file__
    exec(compile(source.replace(before, after), prod.__file__, "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_behavioral_mutant_is_killed(name, monkeypatch):
    module = _mutant(name)
    _, _, check, args = MUTANTS[name]
    monkeypatch.setattr(prod, "ControlPlaneClient", module.ControlPlaneClient)
    monkeypatch.setattr(prod, "ControlPlaneError", module.ControlPlaneError)
    # Only an expected behavioral failure counts as a kill.
    expected = {
        "auth_session_uncleared": "AssertionError",
        "expiry_ignored": "Failed",
        "lost_local_rollback": "KeyError",
        "retry_bad_success": "Failed",
        "retry_new_key": "AssertionError",
        "retry_once": "ControlPlaneError",
        "ttl_inclusive": "AssertionError",
    }
    with pytest.raises(BaseException) as caught:
        check(*args)
    assert type(caught.value).__name__ == expected[name]
    if name == "retry_once":
        assert caught.value.code == "internal"


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_malformed_success_is_terminal_and_does_not_poison_cache(seed):
    rng = random.Random(seed)
    mock = MockControlPlane()
    state = {"bad": False, "count": 0}

    def wire(method, path, headers, body):
        status, payload = mock.handle(method, path, headers, body)
        if path == "/entitlements" and state["bad"]:
            state["count"] += 1
            payload = copy.deepcopy(payload)
            field = rng.choice(("tier", "features", "cache_ttl_seconds", "cost_caps"))
            payload[field] = rng.choice((None, True, [], {}, float("nan")))
            # All generated values are invalid for at least one field;
            # avoid accidentally selecting the valid [] for features.
            if prod._valid(payload, prod.OPS["entitlements.get"]["response"], closed=False):
                payload.pop(field)
            return status, payload
        return status, payload

    client = prod.ControlPlaneClient(wire, max_attempts=3)
    client.call("identity.register", dict(EMAIL))
    warm = client.entitlements()
    previous = _snapshot(client)
    state["bad"] = True
    for _ in range(30):
        err = _assert_error(prod, "internal", lambda: client.call("entitlements.get"))
        assert err.retryable is False and err.status == 200
        assert _snapshot(client) == previous
        assert client._entitlements[0] == warm
    assert state["count"] == 30  # no retry on a malformed successful response


@pytest.mark.parametrize("operation", ("identity.logout", "identity.delete_account"))
def test_lost_self_destructive_reply_replays_without_reviving_session(operation):
    mock = MockControlPlane()
    calls = []
    path = prod.OPS[operation]["path"]

    def wire(method, route, headers, body):
        if route == path:
            calls.append((copy.deepcopy(headers), copy.deepcopy(body)))
            answer = mock.handle(method, route, headers, body)
            if len(calls) == 1:
                raise ConnectionError("reply lost after revocation")
            return answer
        return mock.handle(method, route, headers, body)

    client = prod.ControlPlaneClient(wire, max_attempts=2)
    client.call("identity.register", dict(EMAIL))
    body = {"confirm": "DELETE"} if operation == "identity.delete_account" else None
    assert client.call(operation, body) == {}
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert client.token is None and client.account_id is None
    assert client._entitlements is None and client._reservations == {}
