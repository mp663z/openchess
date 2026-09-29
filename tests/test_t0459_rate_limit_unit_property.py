"""T0459: seeded unit/property checks of the independent production admission decision.

The ledger oracle records admitted attempts, not production buckets or the T0455
reference function. Count an attempt only when it shares operation, scope key,
and the window derived from the injected policy. This tests crossing windows,
independent scopes, refusal atomicity, replay and returned-state isolation.
This is a pure decision battery, not concurrency or live enforcement testing.
"""

from __future__ import annotations

import copy
import random

import pytest

from server.identity_admission import AdmissionRefusal, decide

OPS = ("identity.login", "identity.register")


def _policy(ac=2, aw=10, sc=3, sw=7):
    return {
        "version": 1,
        "account": {"capacity": ac, "window_ms": aw},
        "source": {"capacity": sc, "window_ms": sw},
    }


def _request(
    operation="identity.login",
    account="opaque:a",
    source="opaque:x",
    now=0,
    policy=None,
    state=None,
    **kw,
):
    return dict(
        operation=operation,
        account=account,
        source=source,
        now=now,
        policy=_policy() if policy is None else policy,
        state={} if state is None else state,
        **kw,
    )


def _outcome(request):
    try:
        kind, state = decide(**request)
        return kind, state
    except AdmissionRefusal as error:
        assert type(error) is AdmissionRefusal
        assert error.code in {
            "rate_limited",
            "malformed_request",
            "idempotency_conflict",
            "internal",
        }
        return error.code, None


def _ledger_room(ledger, operation, account, source, now, policy):
    """Independent history-based rule: a scope has room iff fewer admitted
    attempts with the same key occurred in its current fixed time window."""
    for scope, key in (("account", account), ("source", source)):
        window_ms = policy[scope]["window_ms"]
        matching = sum(
            1
            for op, acc, src, at in ledger
            if op == operation
            and (acc if scope == "account" else src) == key
            and at // window_ms == now // window_ms
        )
        if matching >= policy[scope]["capacity"]:
            return False
    return True


@pytest.mark.parametrize("seed", [7, 47, 459, 20260929])
def test_seeded_history_matches_independent_admission_ledger(seed):
    rng = random.Random(seed)
    for _series in range(12):
        policy = _policy(
            rng.randint(1, 4), rng.randint(2, 17), rng.randint(1, 4), rng.randint(2, 17)
        )
        state = {("identity.other", "unrelated", "opaque:hold"): (3, 9)}
        ledger = []
        now = 0
        for _step in range(70):
            now += rng.choice((0, 0, 1, 2, 7, 13))
            operation = rng.choice(OPS)
            account = f"opaque:a{rng.randrange(4)}"
            source = f"opaque:s{rng.randrange(4)}"
            request = _request(operation, account, source, now, policy, state)
            before = copy.deepcopy(request)
            expected = _ledger_room(ledger, operation, account, source, now, policy)
            kind, result = _outcome(request)
            assert kind == ("admit" if expected else "rate_limited"), (seed, request)
            assert request == before  # input and caller-owned state untouched on every path
            if expected:
                assert result is not state
                assert result[("identity.other", "unrelated", "opaque:hold")] == (3, 9)
                for scope, key in (("account", account), ("source", source)):
                    duration = policy[scope]["window_ms"]
                    count = sum(
                        1
                        for op, acc, src, at in ledger
                        if op == operation
                        and (acc if scope == "account" else src) == key
                        and at // duration == now // duration
                    )
                    assert result[(operation, scope, key)] == (now // duration, count + 1)
                # Commit the returned snapshot just as an atomic caller would.
                state = result
                ledger.append((operation, account, source, now))
            else:
                assert result is None


@pytest.mark.parametrize("capacity", [1, 2, 5])
@pytest.mark.parametrize("operation", OPS)
def test_exact_window_and_failure_recovery(capacity, operation):
    policy = _policy(capacity, 5, capacity, 5)
    state = {}
    for _ in range(capacity):
        kind, state = _outcome(_request(operation=operation, policy=policy, state=state, now=4))
        assert kind == "admit"
    full = copy.deepcopy(state)
    assert (
        _outcome(_request(operation=operation, policy=policy, state=state, now=4))[0]
        == "rate_limited"
    )
    assert state == full
    for fault in ({"store_available": False}, {"effect_ok": False}):
        assert (
            _outcome(_request(operation=operation, policy=policy, state=state, now=5, **fault))[0]
            == "internal"
        )
        assert state == full
    assert _outcome(_request(operation=operation, policy=policy, state=state, now=5))[0] == "admit"
    assert state == full


@pytest.mark.parametrize(
    "flags,expected",
    [
        ({"replay": "cached", "body_same": True}, "replay"),
        ({"replay": "cached", "body_same": False}, "idempotency_conflict"),
        ({"replay": "cached", "body_same": 1}, "malformed_request"),
        ({"store_available": False}, "internal"),
        ({"effect_ok": False}, "internal"),
    ],
)
def test_refusal_and_replay_do_not_charge_and_replay_detaches(flags, expected):
    state = {("identity.login", "account", "opaque:a"): (0, 1)}
    request = _request(state=state, **flags)
    original = copy.deepcopy(request)
    kind, result = _outcome(request)
    assert kind == expected
    assert request == original
    if expected == "replay":
        assert result == state and result is not state
        result.clear()
        assert state == original["state"]
    else:
        assert result is None


class _Hostile:
    def __eq__(self, other):
        raise AssertionError("hostile equality executed")

    def __bool__(self):
        raise AssertionError("hostile truthiness executed")


class _Str(str):
    def __eq__(self, other):
        raise AssertionError("subclass equality executed")

    __hash__ = str.__hash__


class _Int(int):
    pass


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("operation", _Str("identity.login"), "malformed_request"),
        ("account", _Str("opaque:a"), "malformed_request"),
        ("source", _Str("opaque:x"), "malformed_request"),
        ("now", True, "malformed_request"),
        ("now", _Int(0), "malformed_request"),
        ("replay", _Str("cached"), "malformed_request"),
        ("replay", _Hostile(), "malformed_request"),
        ("body_same", _Hostile(), "malformed_request"),
        (
            "policy",
            {
                "version": True,
                "account": {"capacity": 2, "window_ms": 10},
                "source": {"capacity": 3, "window_ms": 7},
            },
            "malformed_request",
        ),
        (
            "policy",
            {
                "version": 1,
                "account": {"capacity": _Int(2), "window_ms": 10},
                "source": {"capacity": 3, "window_ms": 7},
            },
            "malformed_request",
        ),
        ("state", {("identity.login", "account", "opaque:a"): (True, 0)}, "internal"),
        ("store_available", _Hostile(), "internal"),
        ("effect_ok", _Hostile(), "internal"),
    ],
)
def test_hostile_boundary_is_typed_refusal(field, value, code):
    request = _request(**{field: value})
    assert _outcome(request)[0] == code


def test_cached_replay_skips_request_and_store_validation_but_checks_exact_flags():
    # The upstream resolution is trusted in this synthetic boundary. This
    # deliberately does not declare a public key namespace or transport rule.
    state = {"unvalidated": [1]}
    request = _request(
        operation=object(),
        policy=None,
        state=state,
        replay="cached",
        body_same=True,
        store_available=_Hostile(),
    )
    kind, result = _outcome(request)
    assert kind == "replay" and result == state and result is not state
    assert _outcome({**request, "body_same": _Hostile()})[0] == "malformed_request"
