"""T0475 v3: the harness is RED on every non-compliant implementation.
Permanent mutants for every reproduced gap: auth collapse, inert
lifecycle ops, caller-unbound refresh, malformed returns, BaseException
escapes, optional/array item types, privacy, fingerprint removal."""

from __future__ import annotations

from tools.contract_conformance import run
from tools.control_plane_mock import MockControlPlane


def test_clean_run_passes():
    assert run(MockControlPlane()) == []


class NoDeleteConfirm(MockControlPlane):
    """Models an implementation that skips request validation entirely:
    delete succeeds with no confirm field at all."""

    def _logic(self, name, body, account=None, headers=None):
        if name == "identity.delete_account":
            return 200, {}
        return super()._logic(name, body, account, headers)


class LeakKey(MockControlPlane):
    def _op_provider_routing_register_key(self, body, account=None,
                                          headers=None):
        status, payload = super()._op_provider_routing_register_key(
            body, account, headers)
        payload["key_material"] = body["key_material"]  # privacy leak
        return status, payload


class UnauthenticatedAccess(MockControlPlane):
    def _bearer(self, headers):
        return "ok", {"account_id": "acct-any"}  # every caller in


class NoFingerprint(MockControlPlane):
    def handle(self, method, path, headers, body):
        from tools.control_plane_mock import OPS
        op = OPS.get((method, path))
        if op and op["mutating"]:
            key = headers.get("Idempotency-Key", "")
            scope = ("public", method, path, key)
            if scope in self.idempotency:
                return self.idempotency[scope][1]  # any body replays
        return super().handle(method, path, headers, body)


class WrongErrorCode(MockControlPlane):
    def _op_identity_register(self, body, account=None, headers=None):
        status, payload = super()._op_identity_register(
            body, account, headers)
        if status == 409:
            payload["error"]["code"] = "conflictz"
        return status, payload


class WrongResponseType(MockControlPlane):
    def _op_entitlements_get(self, body, account=None, headers=None):
        status, payload = super()._op_entitlements_get(
            body, account, headers)
        payload["cache_ttl_seconds"] = "300"  # string, not integer
        return status, payload


class CrashOnRoute(MockControlPlane):
    def _op_provider_routing_route(self, body, account=None,
                                   headers=None):
        raise RuntimeError("boom")


class ExpiredAsUnknown(MockControlPlane):
    """Collapses the expired/unknown distinction: every bad token
    answers auth_invalid."""

    def _bearer(self, headers):
        reason, record = super()._bearer(headers)
        if reason == "expired":
            return "unknown", None
        return reason, record


class InertLogout(MockControlPlane):
    def _op_identity_logout(self, body, account=None, headers=None):
        return 200, {}  # token never revoked


class InertDelete(MockControlPlane):
    def _op_identity_delete_account(self, body, account=None,
                                    headers=None):
        return 200, {}  # account never removed


class RefreshForeignAccount(MockControlPlane):
    def _op_identity_refresh(self, body, account=None, headers=None):
        return 200, self._mint("acct-foreign")  # not the caller


class StatusAsString(MockControlPlane):
    def _op_entitlements_get(self, body, account=None, headers=None):
        _, payload = super()._op_entitlements_get(body, account,
                                                  headers)
        return "200", payload  # status must be an exact int


class ThreeTuple(MockControlPlane):
    def _op_entitlements_get(self, body, account=None, headers=None):
        status, payload = super()._op_entitlements_get(
            body, account, headers)
        return status, payload, None  # must be an exact 2-tuple


class PayloadNotDict(MockControlPlane):
    def _op_entitlements_get(self, body, account=None, headers=None):
        return 200, ["free"]  # payload must be an object


class ErrorCodeNotString(MockControlPlane):
    def _op_billing_get_subscription(self, body, account=None,
                                     headers=None):
        # unhashable code: a membership test before a type check would
        # crash the harness itself
        return 404, {"error": {"code": {"bad": 1}, "message": "x",
                               "retryable": False}}


class SystemExitZero(MockControlPlane):
    def _op_provider_routing_route(self, body, account=None,
                                   headers=None):
        raise SystemExit(0)  # must fail the run, never abort it


class OptionalFieldWrongType(MockControlPlane):
    def _op_identity_register(self, body, account=None, headers=None):
        status, payload = super()._op_identity_register(
            body, account, headers)
        if status == 200:
            payload["display_name"] = 42  # optional, but typed
        return status, payload


class ArrayItemWrongType(MockControlPlane):
    def _op_entitlements_get(self, body, account=None, headers=None):
        status, payload = super()._op_entitlements_get(
            body, account, headers)
        payload["features"] = ["local-analysis", 7]
        return status, payload


MUTANTS = {
    "no_delete_confirm": NoDeleteConfirm,
    "leak_key_material": LeakKey,
    "unauthenticated_access": UnauthenticatedAccess,
    "no_idempotency_fingerprint": NoFingerprint,
    "error_code_outside_enum": WrongErrorCode,
    "response_wrong_value_type": WrongResponseType,
    "implementation_crash": CrashOnRoute,
    "expired_collapsed_to_unknown": ExpiredAsUnknown,
    "inert_logout": InertLogout,
    "inert_delete": InertDelete,
    "refresh_not_caller_bound": RefreshForeignAccount,
    "status_not_exact_int": StatusAsString,
    "return_not_2_tuple": ThreeTuple,
    "payload_not_object": PayloadNotDict,
    "error_code_not_string": ErrorCodeNotString,
    "system_exit_zero_escape": SystemExitZero,
    "optional_field_wrong_type": OptionalFieldWrongType,
    "array_item_wrong_type": ArrayItemWrongType,
}


def test_every_mutant_fails_conformance():
    for name, cls in sorted(MUTANTS.items()):
        problems = run(cls())
        assert problems, f"{name}: mutant passed conformance"
