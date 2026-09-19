"""T0474 v2: reference fixture conforms; idempotent replay identical;
same key + different body conflicts; additive MINOR fields tolerated;
bearer auth real; required fields enforced."""

from __future__ import annotations

from tools.contract_conformance import run
from tools.control_plane_mock import MockControlPlane


def test_reference_fixture_conforms():
    assert run(MockControlPlane()) == []


def _registered(mock):
    status, payload = mock.handle(
        "POST", "/identity/register", {"Idempotency-Key": "t-reg"},
        {"email": "t@example.test", "password_hash_client": "h0"})
    assert status == 200
    return {"Authorization": f"Bearer {payload['token']}"}


def test_idempotent_replay_identical_outcome():
    mock = MockControlPlane()
    headers = {"Idempotency-Key": "same-key"}
    body = {"email": "replay@example.test", "password_hash_client": "h"}
    first = mock.handle("POST", "/identity/register", headers, body)
    replay = mock.handle("POST", "/identity/register", headers, body)
    assert first[0] == 200 and replay == first
    assert len(mock.accounts) == 1


def test_same_key_different_body_conflicts():
    mock = MockControlPlane()
    headers = {"Idempotency-Key": "k-conflict"}
    mock.handle("POST", "/identity/register", headers,
                {"email": "one@example.test",
                 "password_hash_client": "h0"})
    status, payload = mock.handle(
        "POST", "/identity/register", headers,
        {"email": "two@example.test", "password_hash_client": "h0"})
    assert status == 409
    assert payload["error"]["code"] == "idempotency_conflict"
    assert len(mock.accounts) == 1


def test_unauthenticated_access_rejected():
    mock = MockControlPlane()
    status, payload = mock.handle("GET", "/entitlements", {}, {})
    assert status == 401
    assert payload["error"]["code"] in ("auth_invalid", "auth_expired")


def test_required_fields_enforced():
    mock = MockControlPlane()
    auth = _registered(mock)
    auth["Idempotency-Key"] = "q1"
    status, _ = mock.handle("POST", "/quota/reserve", auth,
                            {"estimated_units": 10, "hold_seconds": 60})
    assert status == 400  # operation_kind missing
    status, _ = mock.handle("POST", "/identity/delete-account",
                            {"Authorization": auth["Authorization"],
                             "Idempotency-Key": "d1"}, {})
    assert status == 400  # confirm missing


def test_additive_minor_fields_tolerated():
    class NewerMinor(MockControlPlane):
        def _op_entitlements_get(self, body):
            status, payload = super()._op_entitlements_get(body)
            payload["future_field_v1_1"] = {"new": True}
            return status, payload

    assert run(NewerMinor()) == []
