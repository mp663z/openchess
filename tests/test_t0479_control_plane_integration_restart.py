"""T0479: integration/restart battery for the consumer control-plane client.

One session across the full operation surface of
data/contracts/control-plane.yaml against the T0474 conforming mock, then
restart semantics. The shipped client holds no durable state: every piece of
session state is a per-instance attribute, so a fresh ControlPlaneClient over
the same server IS a process restart - proven faithful by running the
identical scripted scenario in a fresh interpreter with byte-identical
transcripts. T0476 owns the declarative contract, T0477 properties, T0478
multi-attempt fault trajectories in one process. This suite owns integration
and restart; no production or contract change.
"""

from __future__ import annotations

import copy
import itertools
import json
import os
import random
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server.control_plane_client as prod  # noqa: E402
import tools.control_plane_mock as mock_mod  # noqa: E402
from tests.test_t0476_control_plane_client import EMAIL, RESERVE, SECRET, Clock  # noqa: E402
from tools.control_plane_mock import MockControlPlane  # noqa: E402

SEEDS = (7, 42, 1337, 90210)
T0 = 1_700_000_000  # fixed clock for the deterministic fresh-process runs


class Wire:
    """Transport recorder over a server; `server` is swappable to model a
    control-plane restart, `fail(method, path)` models an outage."""

    def __init__(self, server, fail=None):
        self.server = server
        self.fail = fail
        self.calls = []

    def __call__(self, method, path, headers, body):
        self.calls.append((method, path, dict(headers), copy.deepcopy(body)))
        if self.fail is not None and self.fail(method, path):
            raise ConnectionError("outage")
        return self.server.handle(method, path, headers, body)


class _PoisonEntitlements:
    """Drops the tier field from entitlements responses (a malformed 2xx)."""

    def __init__(self, server):
        self.server = server

    def handle(self, method, path, headers, body):
        status, payload = self.server.handle(method, path, headers, body)
        if path == "/entitlements":
            payload = {k: v for k, v in payload.items() if k != "tier"}
        return status, payload


def _keys(prefix):
    seq = itertools.count(1)
    return lambda: f"{prefix}-{next(seq)}"


def _client(server, **kw):
    wire = Wire(server, fail=kw.pop("fail", None))
    clock = kw.pop("clock", None) or Clock()
    client = prod.ControlPlaneClient(wire, clock=clock, **kw)
    return client, wire


def _registered(server, **kw):
    client, wire = _client(server, **kw)
    client.call("identity.register", dict(EMAIL))
    return client, wire


def _restart(client, wire, **kw):
    """A process restart for this client: a fresh instance over the same
    server and wire, sharing nothing with the previous instance."""
    kw.setdefault("clock", client._clock)
    return prod.ControlPlaneClient(wire, **kw)


def _err(code, fn):
    with pytest.raises(prod.ControlPlaneError) as caught:
        fn()
    assert caught.value.code == code
    assert caught.value.code in prod.ERROR_ENUM
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


# -- R1: one integrated session over the full operation surface ----------------


def test_full_session_flow_integration():
    server = MockControlPlane()
    client, wire = _registered(server)
    ent = client.entitlements()
    assert client.entitlements_source == "live" and ent["tier"] == "free"
    r = client.call("quota.reserve", dict(RESERVE, estimated_units=5))
    out = client.call("quota.commit", {"reservation_id": r["reservation_id"], "actual_units": 3})
    assert out == {"remaining_units": 997} and server.quota_units == 997
    key = client.call(
        "provider_routing.register_key", {"provider_kind": "generic-a", "key_material": SECRET}
    )
    route = client.call("provider_routing.route", {"capability": "analysis-fast"})
    assert route["provider_kind"] == "generic-a" and route["key_ref"] == key["key_ref"]
    checkout = client.call(
        "billing.create_checkout",
        {
            "price_id": "price_monthly",
            "success_url": "https://app.example/ok",
            "cancel_url": "https://app.example/cancel",
        },
    )
    assert checkout["checkout_url"].startswith("https://")
    err = _err("not_found", lambda: client.call("billing.get_subscription"))
    assert err.status == 404 and err.retryable is False
    old_token = client.token
    client.call("identity.refresh")
    assert client.token != old_token and client.account_id is not None
    assert client.call("identity.logout") == {}
    assert client.token is None and client.account_id is None
    assert client._entitlements is None and client._reservations == {}
    by_route = {(op["method"], op["path"]): op for op in prod.OPS.values()}
    idem_keys = []
    for method, path, headers, _body in wire.calls:
        op = by_route[(method, path)]
        assert ("Authorization" in headers) == (op["auth"] == "required")
        assert ("Idempotency-Key" in headers) == op["mutating"]
        if "Idempotency-Key" in headers:
            idem_keys.append(headers["Idempotency-Key"])
    assert len(idem_keys) == len(set(idem_keys))  # one fresh key per logical call


# -- R3/R4: consumer restart (fresh instance, same server) ---------------------


def test_restart_shares_no_state_with_the_previous_instance():
    server = MockControlPlane()
    client, wire = _registered(server)
    client.call("quota.reserve", dict(RESERVE))
    fresh = _restart(client, wire)
    assert _snapshot(fresh) == (None, None, None, None, {})
    fresh._reservations["x"] = 1
    assert "x" not in client._reservations
    assert not [k for k in vars(prod.ControlPlaneClient) if k.startswith("_shared")]


def test_restart_requires_new_login_before_authed_calls():
    server = MockControlPlane()
    client, wire = _registered(server)
    sends = len(wire.calls)
    fresh = _restart(client, wire)
    err = _err("auth_invalid", lambda: fresh.call("quota.reserve", dict(RESERVE)))
    assert err.status == 401 and err.retryable is False
    # A preflight refusal is not an outage: no cache, no free-tier degradation.
    _err("auth_invalid", fresh.entitlements)
    assert len(wire.calls) == sends  # nothing crossed the transport
    assert _snapshot(fresh) == (None, None, None, None, {})


def test_restart_resume_commits_prerestart_reservation_on_server_authority():
    server = MockControlPlane()
    client, wire = _registered(server)
    r = client.call("quota.reserve", dict(RESERVE, estimated_units=5))
    assert server.quota_units == 995
    fresh = _restart(client, wire)
    fresh.call("identity.login", dict(EMAIL))
    assert fresh.account_id == client.account_id  # the account persisted
    assert fresh.token != client.token
    before = len(wire.calls)
    out = fresh.call("quota.commit", {"reservation_id": r["reservation_id"], "actual_units": 3})
    assert out == {"remaining_units": 997}
    assert len(wire.calls) == before + 1  # no local shortcut: the server decided
    assert server.reservations == {} and server.quota_units == 997


def test_server_idempotency_replay_after_client_restart():
    server = MockControlPlane()
    body = dict(RESERVE, estimated_units=5, hold_seconds=3600)
    client, wire = _client(server, key_factory=_keys("a"))
    client.call("identity.register", dict(EMAIL))
    r1 = client.call("quota.reserve", dict(body))
    replay_key = wire.calls[-1][2]["Idempotency-Key"]
    keys = iter(("b-1", replay_key))
    fresh = _restart(client, wire, key_factory=lambda: next(keys))
    fresh.call("identity.login", dict(EMAIL))
    r2 = fresh.call("quota.reserve", dict(body))
    assert r2 == r1  # the original outcome, replayed byte-identically
    assert server.quota_units == 995 and len(server.reservations) == 1  # exactly one effect
    # The replayed success re-establishes local tracking on the fresh client.
    assert fresh._reservations == {r1["reservation_id"]: r1["expires_at"]}
    fresh._key_factory = lambda: replay_key
    before = len(wire.calls)
    err = _err(
        "idempotency_conflict",
        lambda: fresh.call("quota.reserve", dict(body, estimated_units=7)),
    )
    assert err.status == 409 and err.retryable is False
    assert len(wire.calls) == before + 1  # typed conflict, no retry
    assert server.quota_units == 995 and len(server.reservations) == 1


def test_no_local_expiry_shortcut_after_restart():
    server = MockControlPlane()
    clock = Clock()
    client, wire = _client(server, clock=clock)
    client.call("identity.register", dict(EMAIL))
    r = client.call("quota.reserve", dict(RESERVE, estimated_units=5, hold_seconds=3600))
    fresh = _restart(client, wire, clock=clock)
    fresh.call("identity.login", dict(EMAIL))
    clock.now = r["expires_at"] + 5  # past expiry for any LOCAL record ...
    fresh.token_expires_at = clock.now + 10000  # ... but not for the held token
    before = len(wire.calls)
    out = fresh.call("quota.commit", {"reservation_id": r["reservation_id"], "actual_units": 3})
    assert out == {"remaining_units": 997}
    # Local tracking died with the process: the commit crossed the transport
    # and the server's own wall clock (well before expires_at) decided.
    assert len(wire.calls) == before + 1


def test_server_expired_commit_after_restart_retries_one_key_one_effect(monkeypatch):
    server = MockControlPlane()
    t0 = time.time()
    fake = types.SimpleNamespace(time=lambda: t0)
    monkeypatch.setattr(mock_mod, "time", fake)
    client, wire = _registered(server)
    r = client.call("quota.reserve", dict(RESERVE, estimated_units=5, hold_seconds=60))
    assert server.quota_units == 995
    fresh = _restart(client, wire)
    fresh.call("identity.login", dict(EMAIL))
    fake.time = lambda: t0 + 120  # server clock past the reservation expiry only
    before = len(wire.calls)
    err = _err(
        "quota_reservation_expired",
        lambda: fresh.call(
            "quota.commit", {"reservation_id": r["reservation_id"], "actual_units": 3}
        ),
    )
    assert err.retryable is True and err.status == 409
    sends = wire.calls[before:]
    assert len(sends) == 3  # retryable server answer, exhausted attempts
    assert len({c[2]["Idempotency-Key"] for c in sends}) == 1  # one key per logical call
    # The refund happened exactly once: retries replayed the recorded outcome.
    assert server.quota_units == 1000 and server.reservations == {}


def test_entitlements_cache_does_not_cross_restart():
    server = MockControlPlane()
    state = {"down": False}
    client, wire = _client(server, fail=lambda _m, p: state["down"] and p == "/entitlements")
    client.call("identity.register", dict(EMAIL))
    warm = client.entitlements()
    assert client.entitlements_source == "live"
    state["down"] = True
    fresh = _restart(client, wire)
    fresh.call("identity.login", dict(EMAIL))  # the login path is not down
    degraded = fresh.entitlements()
    assert degraded == prod.FREE_TIER and fresh.entitlements_source == "free"
    assert degraded != warm  # the pre-restart cache died with the process
    assert fresh._entitlements is None


# -- R6/R7: malformed and rollback after a consumer restart --------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_malformed_requests_after_restart_are_refused_unsent(seed):
    rng = random.Random(seed)
    server = MockControlPlane()
    client, wire = _registered(server)
    fresh = _restart(client, wire)
    fresh.call("identity.login", dict(EMAIL))
    original = _snapshot(fresh)
    sends = len(wire.calls)
    refused = 0
    for _ in range(25):
        body = copy.deepcopy(RESERVE)
        field = rng.choice(tuple(RESERVE) + ("extra",))
        body[field] = rng.choice((None, True, 1.5, [], {})) if field != "extra" else "unknown"
        if prod._valid(body, prod.OPS["quota.reserve"]["request"], closed=True):
            continue
        refused += 1
        _err("malformed_request", lambda body=body: fresh.call("quota.reserve", body))
    assert refused > 0
    assert len(wire.calls) == sends and _snapshot(fresh) == original


def test_malformed_success_after_restart_is_terminal_and_caches_nothing():
    server = MockControlPlane()
    client, wire = _registered(server)
    fresh = _restart(client, wire)
    fresh.call("identity.login", dict(EMAIL))
    wire.server = _PoisonEntitlements(server)
    before = len(wire.calls)
    err = _err("internal", lambda: fresh.call("entitlements.get"))
    assert err.status == 200 and err.retryable is False
    assert len(wire.calls) == before + 1  # terminal: no retry of a malformed 2xx
    assert fresh._entitlements is None  # nothing cached from an invalid payload


def test_failed_calls_after_restart_roll_back_nothing_and_do_not_poison():
    server = MockControlPlane()
    client, wire = _registered(server)
    fresh = _restart(client, wire)
    fresh.call("identity.login", dict(EMAIL))
    original = _snapshot(fresh)
    err = _err(
        "not_found",
        lambda: fresh.call("quota.commit", {"reservation_id": "rsv-never", "actual_units": 1}),
    )
    assert err.status == 404 and err.retryable is False
    err = _err(
        "quota_exhausted",
        lambda: fresh.call("quota.reserve", dict(RESERVE, estimated_units=100000)),
    )
    assert err.retryable is True and err.status == 429
    assert _snapshot(fresh) == original  # failed calls changed nothing locally
    assert server.quota_units == 1000 and server.reservations == {}
    r = fresh.call("quota.reserve", dict(RESERVE, estimated_units=5))
    assert fresh.call("quota.release", {"reservation_id": r["reservation_id"]}) == {}
    assert server.quota_units == 1000  # a later valid call continues the flow


# -- R5: control-plane restart (fresh server, same client) ---------------------


def test_server_restart_invalidates_the_held_token_fail_closed():
    mock1 = MockControlPlane()
    client, wire = _registered(mock1)
    client.entitlements()  # warm cache
    client.token_expires_at += 100000
    wire.server = MockControlPlane()  # the control plane restarted, state lost
    before = len(wire.calls)
    err = _err("auth_invalid", client.entitlements)
    assert err.status == 401 and err.retryable is False
    assert len(wire.calls) == before + 1  # an auth failure is final: no retry
    # The session is cleared and the warm cache was NOT served (the raise,
    # not a payload, reached the caller): fail closed, never resurrected.
    assert client.token is None and client._entitlements is None


def test_server_restart_loses_reservations_and_idempotency_store():
    mock1 = MockControlPlane()
    client, wire = _client(mock1, key_factory=_keys("k"))
    client.call("identity.register", dict(EMAIL))
    r1 = client.call("quota.reserve", dict(RESERVE, estimated_units=5, hold_seconds=3600))
    reserve_key = wire.calls[-1][2]["Idempotency-Key"]
    wire.server = mock2 = MockControlPlane()
    _err(
        "auth_invalid",
        lambda: client.call(
            "quota.commit", {"reservation_id": r1["reservation_id"], "actual_units": 3}
        ),
    )
    _err("auth_invalid", lambda: client.call("identity.login", dict(EMAIL)))
    client.call("identity.register", dict(EMAIL))  # re-register on the restarted plane
    err = _err(
        "not_found",
        lambda: client.call(
            "quota.commit", {"reservation_id": r1["reservation_id"], "actual_units": 3}
        ),
    )
    assert err.status == 404 and err.retryable is False
    # A failed commit deletes no local tracking (conservative).
    assert client._reservations == {r1["reservation_id"]: r1["expires_at"]}
    client._key_factory = lambda: reserve_key
    r2 = client.call("quota.reserve", dict(RESERVE, estimated_units=5, hold_seconds=3600))
    # No cross-server-restart dedupe in the in-memory reference plane: the
    # effect happened again. Documented durability boundary, contract-silent.
    assert r2["reservation_id"] != r1["reservation_id"]
    assert mock2.quota_units == 995


# -- R8: in-place session restarts ---------------------------------------------


def test_logout_is_an_inplace_session_restart_and_forgets_reservations():
    server = MockControlPlane()
    clock = Clock()
    client, wire = _client(server, clock=clock)
    client.call("identity.register", dict(EMAIL))
    r = client.call("quota.reserve", dict(RESERVE, estimated_units=5, hold_seconds=3600))
    assert client.call("identity.logout") == {}
    assert client._reservations == {} and client.token is None
    client.call("identity.login", dict(EMAIL))
    clock.now = r["expires_at"] + 5  # past expiry for any retained local record
    client.token_expires_at = clock.now + 10000
    before = len(wire.calls)
    out = client.call("quota.commit", {"reservation_id": r["reservation_id"], "actual_units": 3})
    assert out == {"remaining_units": 997}
    assert len(wire.calls) == before + 1  # sent: logout forgot the local record


def test_relogin_is_a_session_restart_and_drops_the_cache():
    server = MockControlPlane()
    state = {"down": False}
    client, wire = _client(server, fail=lambda _m, p: state["down"] and p == "/entitlements")
    client.call("identity.register", dict(EMAIL))
    client.token_expires_at += 100000
    warm = client.entitlements()
    state["down"] = True
    assert client.entitlements() == warm and client.entitlements_source == "cache"
    client.call("identity.login", dict(EMAIL))  # in-place session restart
    assert client._entitlements is None
    assert client.entitlements() == prod.FREE_TIER and client.entitlements_source == "free"


# -- R2: fresh-interpreter proof ------------------------------------------------


def _deterministic_patches(monkeypatch=None):
    """Pin the mock's secrets and wall clock so transcripts are reproducible."""
    counter = itertools.count(1)

    def token_hex(n):
        return format(next(counter), f"0{2 * n}x")

    shim_time = types.SimpleNamespace(time=lambda: T0)
    shim_secrets = types.SimpleNamespace(token_hex=token_hex)
    if monkeypatch is None:
        mock_mod.time = shim_time
        mock_mod.secrets = shim_secrets
    else:
        monkeypatch.setattr(mock_mod, "time", shim_time)
        monkeypatch.setattr(mock_mod, "secrets", shim_secrets)


def _mock_state(server):
    """A JSON-safe snapshot of the reference plane's state."""
    return {
        "accounts": server.accounts,
        "tokens": server.tokens,
        "idempotency": [
            [list(scope), [fingerprint, [status, payload]]]
            for scope, (fingerprint, (status, payload)) in server.idempotency.items()
        ],
        "reservations": server.reservations,
        "quota_units": server.quota_units,
        "keys": server.keys,
        "subscription": server.subscription,
    }


def _restore_mock(state):
    server = MockControlPlane()
    server.accounts = state["accounts"]
    server.tokens = state["tokens"]
    server.idempotency = {
        tuple(scope): (fingerprint, (status, payload))
        for scope, (fingerprint, (status, payload)) in state["idempotency"]
    }
    server.reservations = state["reservations"]
    server.quota_units = state["quota_units"]
    server.keys = state["keys"]
    server.subscription = state["subscription"]
    return server


def _scenario_part1():
    """Register, read entitlements, reserve; returns (mock state, transcript)."""
    server = MockControlPlane()
    client, _wire = _client(server, clock=Clock(T0), key_factory=_keys("c"))
    transcript = []
    ok = client.call("identity.register", dict(EMAIL))
    transcript.append(("identity.register", "ok", ok["account_id"]))
    ent = client.entitlements()
    transcript.append(("entitlements.get", "ok", ent["tier"], tuple(ent["features"])))
    r = client.call("quota.reserve", dict(RESERVE, estimated_units=5, hold_seconds=3600))
    transcript.append(("quota.reserve", "ok", r["reservation_id"], r["granted_units"]))
    transcript.append(("quota_units", server.quota_units))
    return _mock_state(server), transcript


def _scenario_part2(state):
    """A fresh client over the restored server: login, entitlements, commit."""
    server = _restore_mock(state)
    client, _wire = _client(server, clock=Clock(T0), key_factory=_keys("d"))
    transcript = []
    ok = client.call("identity.login", dict(EMAIL))
    transcript.append(("identity.login", "ok", ok["account_id"]))
    ent = client.entitlements()
    transcript.append(("entitlements.get", "ok", ent["tier"], client.entitlements_source))
    (reservation_id,) = tuple(server.reservations)
    out = client.call("quota.commit", {"reservation_id": reservation_id, "actual_units": 3})
    transcript.append(("quota.commit", "ok", out["remaining_units"]))
    transcript.append(("quota_units", server.quota_units))
    return transcript


def _child_main():
    """Child entry point: {"part": 1|2, "mock_state": ...} on stdin."""
    request = json.load(sys.stdin)
    _deterministic_patches()
    if request["part"] == 1:
        state, transcript = _scenario_part1()
        out = {"mock_state": state, "transcript": transcript}
    else:
        out = {"transcript": _scenario_part2(request["mock_state"])}
    sys.stdout.write(json.dumps(out, sort_keys=True))


_CHILD = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from tests.test_t0479_control_plane_integration_restart import _child_main; "
    "_child_main()"
)


def _run_child(part, mock_state=None, hash_seed="0"):
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    run = subprocess.run(
        [sys.executable, "-c", _CHILD, str(ROOT)],
        input=json.dumps({"part": part, "mock_state": mock_state}),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        timeout=100,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_fresh_process_restart_matches_the_in_process_run(monkeypatch):
    _deterministic_patches(monkeypatch)
    state, part1 = _scenario_part1()
    part2 = _scenario_part2(json.loads(json.dumps(state)))  # the child's round trip
    child1 = json.loads(_run_child(1))
    child2 = json.loads(_run_child(2, mock_state=child1["mock_state"]))
    norm = json.loads(json.dumps([part1, part2], sort_keys=True))
    assert [child1["transcript"], child2["transcript"]] == norm
    # The process boundary behaved like a restart, not a new world: the login
    # landed on the SAME account and the reservation committed exactly once.
    assert child2["transcript"][0][2] == child1["transcript"][0][2]
    assert child1["transcript"][3] == ["quota_units", 995]
    assert child2["transcript"][2:] == [["quota.commit", "ok", 997], ["quota_units", 997]]


@pytest.mark.parametrize("part", (1, 2))
def test_fresh_process_output_is_deterministic_across_hash_seeds(part):
    state = json.loads(_run_child(1))["mock_state"] if part == 2 else None
    run0 = _run_child(part, mock_state=state, hash_seed="0")
    run1 = _run_child(part, mock_state=state, hash_seed="1")
    assert run0 == run1


# -- R9: behavioral kills against fresh one-edit copies of production code -----

MUTANTS = {
    "shared_reservations": (
        "        self._reservations = {}\n",
        "        self._reservations = globals().setdefault('_SHARED_RSV', {})\n",
        test_no_local_expiry_shortcut_after_restart,
        (),
    ),
    "cold_cache_must_degrade": (
        "if cached is not None and now - cached[1] < cached[0][TTL_FIELD]:",
        "if cached is not None or now - cached[1] < cached[0][TTL_FIELD]:",
        test_entitlements_cache_does_not_cross_restart,
        (),
    ),
    "logout_keeps_reservations": (
        "            self._reservations.clear()",
        "            pass",
        test_logout_is_an_inplace_session_restart_and_forgets_reservations,
        (),
    ),
    "login_keeps_cache": (
        "            self._entitlements = None\n",
        "            pass\n",
        test_relogin_is_a_session_restart_and_drops_the_cache,
        (),
    ),
}

EXPECTED_FAILURE = {
    "shared_reservations": ("ControlPlaneError", "quota_reservation_expired"),
    "cold_cache_must_degrade": ("TypeError", None),
    "logout_keeps_reservations": ("AssertionError", None),
    "login_keeps_cache": ("AssertionError", None),
}


def _mutant(name):
    before, after, _, _ = MUTANTS[name]
    source = Path(prod.__file__).read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType("t0479_mutant_" + name)
    module.__file__ = prod.__file__
    exec(compile(source.replace(before, after), prod.__file__, "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_behavioral_mutant_is_killed(name, monkeypatch):
    module = _mutant(name)
    _, _, check, args = MUTANTS[name]
    monkeypatch.setattr(prod, "ControlPlaneClient", module.ControlPlaneClient)
    monkeypatch.setattr(prod, "ControlPlaneError", module.ControlPlaneError)
    expected_type, expected_code = EXPECTED_FAILURE[name]
    # Only the expected behavioral failure counts as a kill.
    with pytest.raises(BaseException) as caught:
        check(*args)
    assert type(caught.value).__name__ == expected_type
    if expected_code is not None:
        assert caught.value.code == expected_code
