"""T0435: opt-in control-plane operation-event instrumentation
(server/control_plane_client.py + server/control_plane_events.py).

Connects the shipped local metadata publisher to the real replaceable
control-plane boundary this repo has: the consumer client's LOGICAL
operation call. No hosted or self-hosted server ships here, so this is
not hosted-service instrumentation; envelopes are operation metadata
decided at the consumer boundary, never transport events (the
per-attempt (operation, status, code) log seam is untouched) and never
a network sink (the ledger is a local SQLite outbox).

Pinned behaviour, grounded in data/contracts/events.yaml and
data/contracts/control-plane.yaml:
- exactly one closed v1 envelope per EXECUTED logical call: success
  after the local session effect is applied (_after_success), failure
  as the ControlPlaneError that would propagate - whether the server
  decided it or the contract assigned the terminal decision to this
  consumer (the expired-reservation shortcut, whose outcomes the
  contract's recovery rule owns);
- consumer-side preflight refusals record NOTHING and never change the
  caller's error: validation refusals (unknown operation, body
  mismatch, hostile types) and token preflight refusals (no held
  token, a locally expired token) all leave an empty outbox, an empty
  transport log, an empty hostile-call log and the input unchanged.
  identity.logout/identity.delete_account declare no auth_expired
  outcome, so a locally-expired-token refusal for them must raise the
  preexisting ControlPlaneError(auth_expired), never a Refusal;
- retries of one logical call record one event with the terminal
  code, not one per attempt; a nested identity.refresh records its own
  event in order; consumer-side recovery (entitlements cache/free
  tier) records only the underlying call's event;
- envelopes are metadata-only: exact closed field set, independent
  opaque ids (never the idempotency key, token, account id,
  reservation id or key material), error_code exactly the terminal
  code and always inside the operation's contract-declared scope;
- publisher failure is fail-closed: a typed, non-echo
  control_plane_events.Refusal propagates unconverted (never a
  ControlPlaneError), an already-committed effect is not rolled back,
  hidden or retro-published, and the outbox holds no partial row;
- the event_ledger boundary is hostile-safe: anything but an exact
  control_plane_events.Ledger is refused with ValueError before any
  attribute access, and no caller code runs;
- opt-in and neutral: with no ledger wired, nothing is recorded and
  behaviour is unchanged (the T0476 battery covers neutrality).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
import yaml

from server import control_plane_client as client_mod
from server import control_plane_events as events
from tools.control_plane_mock import MockControlPlane

ROOT = Path(__file__).resolve().parents[1]
_CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "control-plane.yaml").read_text())
# Error scope derived from the contract source, never from the
# production module under test.
_OP_ERRORS = {
    f"{area}.{name}": frozenset(op["errors"])
    for area, spec in _CONTRACT["areas"].items()
    for name, op in spec["ops"].items()
}
# Identifier grammar restated from the contract: ASCII [A-Za-z0-9_-]{1,64}.
_IDENT_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

EMAIL = {"email": "ada@example.test", "password_hash_client": "pw-hash-1"}
EMAIL2 = {"email": "grace@example.test", "password_hash_client": "pw-hash-2"}
RESERVE = {"operation_kind": "analysis", "estimated_units": 2, "hold_seconds": 3600}
KEY_MATERIAL = "unit-test-key-material"


class Clock:
    def __init__(self):
        self.now = time.time()

    def __call__(self):
        return self.now


class Wire:
    """Transport recorder around the reference mock's handle(). Setting
    DOWN makes every attempt raise (an unreachable control plane)."""

    def __init__(self, server):
        self.server = server
        self.calls = []
        self.down = False

    def __call__(self, method, path, headers, body):
        self.calls.append((method, path, dict(headers), dict(body)))
        if self.down:
            raise ConnectionError("control plane unreachable")
        return self.server.handle(method, path, headers, body)


def _client(tmp_path, server=None, *, ledger="memory", **kw):
    server = server or MockControlPlane()
    wire = Wire(server)
    clock = kw.pop("clock", None) or Clock()
    if ledger == "memory":
        ledger = events.Ledger()
    elif ledger == "file":
        ledger = events.Ledger(tmp_path / "outbox.sqlite")
    client = client_mod.ControlPlaneClient(wire, clock=clock, event_ledger=ledger, **kw)
    return client, wire, server, clock, ledger


def _names(ledger):
    return [event["name"] for event in ledger.events]


def _assert_envelope(event, operation, outcome, code=None):
    """Exact closed v1 envelope, derived from the contract - never from
    the production module under test."""
    assert type(event) is dict
    fields = {
        "version",
        "name",
        "operation_id",
        "event_id",
        "occurred_at",
        "correlation_id",
        "outcome",
    }
    assert set(event) == fields | ({"error_code"} if code else set())
    assert type(event["version"]) is int and event["version"] == 1
    assert event["name"] == f"control_plane.{operation}.{outcome}"
    assert event["operation_id"] == operation
    assert event["outcome"] == outcome
    for field in ("event_id", "correlation_id"):
        value = event[field]
        assert type(value) is str and 1 <= len(value) <= 64
        assert frozenset(value) <= _IDENT_ALPHABET
    occurred = event["occurred_at"]
    assert type(occurred) is int
    assert 0 <= occurred <= 253402300799999
    assert abs(occurred - time.time() * 1000) < 60_000
    if code is not None:
        assert event["error_code"] == code
        assert code in _OP_ERRORS[operation]


def test_success_event_after_effect_commit_metadata_only(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path)
    payload = client.call("identity.register", dict(EMAIL))
    assert client.token == payload["token"]  # session effect applied
    (event,) = ledger.events
    _assert_envelope(event, "identity.register", "succeeded")
    raw = json.dumps(event)
    for private in (
        EMAIL["email"],
        EMAIL["password_hash_client"],
        payload["token"],
        payload["account_id"],
    ):
        assert private not in raw


def test_terminal_failure_records_exact_declared_code(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path)
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        client.call("identity.login", dict(EMAIL))  # unknown account
    assert caught.value.code == "auth_invalid"
    (event,) = ledger.events
    _assert_envelope(event, "identity.login", "failed", "auth_invalid")


def test_server_rejection_is_an_event_client_validation_is_not(tmp_path):
    """The same malformed_request code records an event when the
    OPERATION decided it (executed and rejected) and none when local
    validation refused the call before execution."""
    client, wire, server, clock, ledger = _client(tmp_path)
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        client.call("identity.register", {"email": "not-an-email", "password_hash_client": "pw"})
    assert caught.value.code == "malformed_request"
    (event,) = ledger.events
    _assert_envelope(event, "identity.register", "failed", "malformed_request")
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        client.call("identity.register", {**EMAIL, "undeclared": True})
    assert caught.value.code == "malformed_request"
    assert ledger.events == [event]
    assert len(wire.calls) == 1  # only the executed operation


def test_validation_refusals_record_nothing(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path)
    hostile_calls = []

    class HostileKey(str):
        def __eq__(self, other):
            hostile_calls.append(other)
            return NotImplemented

        __hash__ = str.__hash__

    class HostileDict(dict):
        pass

    body = {"email": "a@b.test", "password_hash_client": "pw"}
    snapshot = dict(body)
    refused = (
        lambda: client.call("not.an.operation", {}),
        lambda: client.call("identity.login", {"email": 1, "password_hash_client": "pw"}),
        lambda: client.call("identity.login", {**body, "extra": 1}),
        lambda: client.call("identity.login", HostileDict(body)),
        lambda: client.call(
            "identity.login", {HostileKey("email"): "a@b.test", "password_hash_client": "pw"}
        ),
    )
    for call in refused:
        with pytest.raises(client_mod.ControlPlaneError) as caught:
            call()
        assert caught.value.code == "malformed_request"
    assert ledger.events == []
    assert wire.calls == []
    assert hostile_calls == []
    assert body == snapshot


def test_retries_record_one_event_not_one_per_attempt(tmp_path):
    logs = []
    client, wire, server, clock, ledger = _client(tmp_path, log=lambda *a: logs.append(a))
    client.call("identity.register", dict(EMAIL))
    attempts = {"n": 0}
    real = wire.server.handle

    def flaky(method, path, headers, body):
        attempts["n"] += 1
        if path == "/entitlements" and attempts["n"] <= 2:
            raise ConnectionError("down")
        return real(method, path, headers, body)

    wire.server.handle = flaky
    payload = client.call("entitlements.get")
    assert payload["tier"] == "free"
    assert _names(ledger) == [
        "control_plane.identity.register.succeeded",
        "control_plane.entitlements.get.succeeded",
    ]
    attempt_logs = [entry for entry in logs if entry[0] == "entitlements.get"]
    assert len(attempt_logs) == 3  # the transport seam saw every attempt
    assert [entry[2] for entry in attempt_logs] == ["internal", "internal", None]


def test_exhausted_retryable_error_records_last_code_once(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path, max_attempts=3)
    client.call("identity.register", dict(EMAIL))
    server.quota_units = 1  # every reserve attempt answers retryable quota_exhausted
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        client.call("quota.reserve", dict(RESERVE))
    assert caught.value.code == "quota_exhausted"
    assert caught.value.retryable is True
    assert _names(ledger) == [
        "control_plane.identity.register.succeeded",
        "control_plane.quota.reserve.failed",
    ]
    _assert_envelope(ledger.events[-1], "quota.reserve", "failed", "quota_exhausted")
    assert len(wire.calls) == 1 + 3


AUTH_REQUIRED_BODIES = {
    "identity.refresh": {},
    "identity.logout": {},
    "identity.delete_account": {"confirm": "DELETE"},
    "entitlements.get": {},
    "quota.reserve": RESERVE,
    "quota.commit": {"reservation_id": "rsv-x", "actual_units": 1},
    "quota.release": {"reservation_id": "rsv-x"},
    "billing.create_checkout": {
        "price_id": "price_monthly",
        "success_url": "https://app.example/ok",
        "cancel_url": "https://app.example/cancel",
    },
    "billing.get_subscription": {},
    "provider_routing.register_key": {"provider_kind": "generic-a", "key_material": KEY_MATERIAL},
    "provider_routing.revoke_key": {"key_ref": "key-x"},
    "provider_routing.route": {"capability": "analysis-fast"},
}
# Derived from the contract source, and the whole set is pinned so a
# future operation silently joining the auth-required class fails here.
assert (
    frozenset(AUTH_REQUIRED_BODIES)
    == frozenset(
        f"{area}.{name}"
        for area, spec in _CONTRACT["areas"].items()
        for name, op in spec["ops"].items()
        if op["auth"] == "required"
    )
    and len(AUTH_REQUIRED_BODIES) == 12
)


@pytest.mark.parametrize("operation", sorted(AUTH_REQUIRED_BODIES))
def test_expired_token_preflight_records_nothing_and_never_masks(tmp_path, operation):
    """Every auth-required operation under a locally expired token: the
    preexisting ControlPlaneError(auth_expired) reaches the caller
    UNCHANGED (never a Refusal - identity.logout/delete_account declare
    no auth_expired outcome), the session clears, nothing is sent and
    the outbox holds only the register event."""
    client, wire, server, clock, ledger = _client(tmp_path)
    client.call("identity.register", dict(EMAIL))
    sends = len(wire.calls)
    clock.now = client.token_expires_at + 1  # locally expired
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        client.call(operation, dict(AUTH_REQUIRED_BODIES[operation]))
    assert type(caught.value) is client_mod.ControlPlaneError
    assert not isinstance(caught.value, events.Refusal)
    assert caught.value.code == "auth_expired"
    assert client.token is None
    assert len(wire.calls) == sends  # preflight: nothing was sent
    assert _names(ledger) == ["control_plane.identity.register.succeeded"]


@pytest.mark.parametrize("operation", sorted(AUTH_REQUIRED_BODIES))
def test_no_token_preflight_records_nothing(tmp_path, operation):
    client, wire, server, clock, ledger = _client(tmp_path)
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        client.call(operation, dict(AUTH_REQUIRED_BODIES[operation]))
    assert type(caught.value) is client_mod.ControlPlaneError
    assert not isinstance(caught.value, events.Refusal)
    assert caught.value.code == "auth_invalid"
    assert wire.calls == []
    assert ledger.events == []


def test_expired_reservation_shortcut_outcomes_recorded(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path)
    client.call("identity.register", dict(EMAIL))
    hold = {**RESERVE, "hold_seconds": 10}
    first = client.call("quota.reserve", dict(hold))
    second = client.call("quota.reserve", dict(hold))
    sends = len(wire.calls)
    clock.now = first["expires_at"] + 1  # both reservations expired
    assert client.call("quota.release", {"reservation_id": first["reservation_id"]}) == {}
    with pytest.raises(client_mod.ControlPlaneError) as caught:
        client.call("quota.commit", {"reservation_id": second["reservation_id"], "actual_units": 1})
    assert caught.value.code == "quota_reservation_expired"
    assert len(wire.calls) == sends  # both decided locally, nothing sent
    assert _names(ledger)[-2:] == [
        "control_plane.quota.release.succeeded",
        "control_plane.quota.commit.failed",
    ]
    _assert_envelope(ledger.events[-2], "quota.release", "succeeded")
    _assert_envelope(ledger.events[-1], "quota.commit", "failed", "quota_reservation_expired")


def test_nested_refresh_records_each_operation_in_order(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path)
    client.call("identity.register", dict(EMAIL))
    clock.now = client.token_expires_at - 30  # inside the 60s refresh skew
    client.call("entitlements.get")
    assert _names(ledger) == [
        "control_plane.identity.register.succeeded",
        "control_plane.identity.refresh.succeeded",
        "control_plane.entitlements.get.succeeded",
    ]


def test_self_destructive_logout_recorded_after_session_cleared(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path)
    client.call("identity.register", dict(EMAIL))
    client.call("identity.logout")
    assert client.token is None
    assert _names(ledger) == [
        "control_plane.identity.register.succeeded",
        "control_plane.identity.logout.succeeded",
    ]


def test_consumer_recovery_records_the_underlying_failure_only(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path)
    client.call("identity.register", dict(EMAIL))
    client.call("entitlements.get")  # primes the cache (ttl 300)
    wire.down = True
    cached = client.entitlements()
    assert client.entitlements_source == "cache"
    assert cached["tier"] == "free"
    assert _names(ledger) == [
        "control_plane.identity.register.succeeded",
        "control_plane.entitlements.get.succeeded",
        "control_plane.entitlements.get.failed",
    ]
    _assert_envelope(ledger.events[-1], "entitlements.get", "failed", "internal")
    # with no cache the free tier is also recovery, not an operation outcome
    client2, wire2, server2, clock2, ledger2 = _client(tmp_path)
    client2.call("identity.register", dict(EMAIL2))
    wire2.down = True
    assert client2.entitlements()["tier"] == "free"
    assert client2.entitlements_source == "free"
    assert _names(ledger2) == [
        "control_plane.identity.register.succeeded",
        "control_plane.entitlements.get.failed",
    ]


def test_publisher_failure_fail_closed_committed_effect_untouched(tmp_path):
    path = tmp_path / "outbox.sqlite"
    ledger = events.Ledger(path)
    ledger._db.execute("PRAGMA busy_timeout=0")  # fail at once, like the client must
    locker = sqlite3.connect(str(path), isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    client, wire, server, clock, _ = _client(tmp_path, ledger=ledger)
    with pytest.raises(events.Refusal) as caught:
        client.call("identity.register", dict(EMAIL))
    refusal = caught.value
    assert type(refusal) is events.Refusal
    assert refusal.failure_class == "publication_failure"
    assert str(refusal) == "publication_failure"
    assert not isinstance(refusal, client_mod.ControlPlaneError)
    # the effect committed and is neither rolled back nor hidden
    assert client.token is not None
    assert EMAIL["email"] in server.accounts
    locker.execute("ROLLBACK")  # an EXCLUSIVE lock blocks readers too;
    locker.close()  # state is asserted after release
    assert ledger.events == []  # no partial, no retro-published row
    client.call("identity.login", dict(EMAIL))
    assert _names(ledger) == ["control_plane.identity.login.succeeded"]
    ledger.close()


def test_publisher_failure_on_failure_path_keeps_decided_code_as_context(tmp_path):
    path = tmp_path / "outbox.sqlite"
    ledger = events.Ledger(path)
    ledger._db.execute("PRAGMA busy_timeout=0")
    locker = sqlite3.connect(str(path), isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    client, wire, server, clock, _ = _client(tmp_path, ledger=ledger)
    with pytest.raises(events.Refusal) as caught:
        client.call("identity.login", dict(EMAIL))  # decided auth_invalid
    assert type(caught.value) is events.Refusal
    assert caught.value.failure_class == "publication_failure"
    assert str(caught.value) == "publication_failure"
    assert len(wire.calls) == 1  # the terminal decision was reached
    assert client.token is None  # and no effect was ever committed
    locker.execute("ROLLBACK")
    locker.close()
    assert ledger.events == []
    ledger.close()


def test_event_ledger_boundary_refused_before_any_lookup(tmp_path):
    calls = []

    class Recorder:
        def __getattr__(self, name):
            calls.append(name)
            raise AttributeError(name)

    class SubLedger(events.Ledger):
        pass

    def transport(*_args):
        raise AssertionError("transport must never run")

    sub = SubLedger()
    for bad in (object(), 0, True, "ledger", Recorder(), sub):
        with pytest.raises(ValueError):
            client_mod.ControlPlaneClient(transport, event_ledger=bad)
    sub.close()
    assert calls == []  # the exact-type check ran no caller code


def test_event_ids_opaque_and_independent_of_private_values(tmp_path):
    key_factory = lambda: "fixed-idempotency-key"  # noqa: E731
    client, wire, server, clock, ledger = _client(tmp_path, key_factory=key_factory)
    client.call("identity.register", dict(EMAIL))
    client.call(
        "provider_routing.register_key", {"provider_kind": "openai", "key_material": KEY_MATERIAL}
    )
    reservation = client.call("quota.reserve", dict(RESERVE))
    raw = json.dumps(ledger.events)
    for private in (
        "fixed-idempotency-key",
        EMAIL["email"],
        EMAIL["password_hash_client"],
        client.token,
        client.account_id,
        KEY_MATERIAL,
        reservation["reservation_id"],
    ):
        assert private not in raw
    identifiers = [
        event[field] for event in ledger.events for field in ("event_id", "correlation_id")
    ]
    assert len(set(identifiers)) == len(identifiers)  # all independent
    for event, operation, outcome in zip(
        ledger.events,
        ["identity.register", "provider_routing.register_key", "quota.reserve"],
        ["succeeded"] * 3,
        strict=True,
    ):
        _assert_envelope(event, operation, outcome)


def test_events_durable_and_ordered_across_restart(tmp_path):
    client, wire, server, clock, ledger = _client(tmp_path, ledger="file")
    client.call("identity.register", dict(EMAIL))
    client.call("identity.logout")
    with pytest.raises(client_mod.ControlPlaneError):
        client.call("identity.login", dict(EMAIL2))  # unknown account
    ledger.close()
    reopened = events.Ledger(tmp_path / "outbox.sqlite")
    assert _names(reopened) == [
        "control_plane.identity.register.succeeded",
        "control_plane.identity.logout.succeeded",
        "control_plane.identity.login.failed",
    ]
    assert reopened.events[2]["error_code"] == "auth_invalid"
    reopened.close()


def test_events_are_opt_in_and_default_off(tmp_path):
    server = MockControlPlane()
    client = client_mod.ControlPlaneClient(Wire(server))
    assert client._event_ledger is None
    client.call("identity.register", dict(EMAIL))
    assert client.token is not None
