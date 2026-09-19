"""T0475 v2: the harness is RED on every non-compliant implementation.
Includes the verifier's direct mutants (NoDeleteConfirm, LeakKey) plus
auth-bypass and fingerprint-removal mutants."""

from __future__ import annotations

from tools.contract_conformance import run
from tools.control_plane_mock import MockControlPlane


def test_clean_run_passes():
    assert run(MockControlPlane()) == []


class NoDeleteConfirm(MockControlPlane):
    """Models an implementation that skips request validation entirely:
    delete succeeds with no confirm field at all."""

    def _logic(self, name, body):
        if name == "identity.delete_account":
            return 200, {}
        return super()._logic(name, body)


class LeakKey(MockControlPlane):
    def _op_provider_routing_register_key(self, body):
        status, payload = super()._op_provider_routing_register_key(body)
        payload["key_material"] = body["key_material"]  # privacy leak
        return status, payload


class UnauthenticatedAccess(MockControlPlane):
    def _bearer(self, headers):
        return {"account_id": "acct-any"}  # every caller authenticated


class NoFingerprint(MockControlPlane):
    def handle(self, method, path, headers, body):
        op = None
        from tools.control_plane_mock import OPS
        op = OPS.get((method, path))
        if op and op["mutating"]:
            key = headers.get("Idempotency-Key", "")
            scope = ("public", method, path, key)
            if scope in self.idempotency:
                return self.idempotency[scope][1]  # any body replays
        return super().handle(method, path, headers, body)


class WrongErrorCode(MockControlPlane):
    def _op_identity_register(self, body):
        status, payload = super()._op_identity_register(body)
        if status == 409:
            payload["error"]["code"] = "conflictz"
        return status, payload


class WrongResponseType(MockControlPlane):
    def _op_entitlements_get(self, body):
        status, payload = super()._op_entitlements_get(body)
        payload["cache_ttl_seconds"] = "300"  # string, not integer
        return status, payload


class CrashOnRoute(MockControlPlane):
    def _op_provider_routing_route(self, body):
        raise RuntimeError("boom")


MUTANTS = {
    "no_delete_confirm": NoDeleteConfirm,
    "leak_key_material": LeakKey,
    "unauthenticated_access": UnauthenticatedAccess,
    "no_idempotency_fingerprint": NoFingerprint,
    "error_code_outside_enum": WrongErrorCode,
    "response_wrong_value_type": WrongResponseType,
    "implementation_crash": CrashOnRoute,
}


def test_every_mutant_fails_conformance():
    for name, cls in sorted(MUTANTS.items()):
        problems = run(cls())
        assert problems, f"{name}: mutant passed conformance"
