"""T0474: reference fixture conforms; idempotent replay returns the
identical outcome; additive MINOR fields never break conformance
(rollback rule)."""

from __future__ import annotations

from tools.contract_conformance import run
from tools.control_plane_mock import MockControlPlane


def test_reference_fixture_conforms():
    assert run(MockControlPlane()) == []


def test_idempotent_replay_returns_identical_outcome():
    mock = MockControlPlane()
    headers = {"Idempotency-Key": "same-key"}
    body = {"email": "replay@example.test", "password_hash_client": "h"}
    first = mock.handle("POST", "/identity/register", headers, body)
    replay = mock.handle("POST", "/identity/register", headers, body)
    assert first[0] == 200
    assert replay == first  # original outcome, never a duplicate effect
    assert len(mock.accounts) == 1


def test_additive_minor_fields_do_not_break_conformance():
    class NewerMinor(MockControlPlane):
        def _entitlements(self, body):
            status, payload = super()._entitlements(body)
            payload["future_field_v1_1"] = {"new": True}
            return status, payload

    assert run(NewerMinor()) == []
