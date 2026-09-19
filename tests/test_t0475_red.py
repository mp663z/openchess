"""T0475: the conformance harness is RED on every non-compliant
implementation and green only on a conforming one. Each mutation below
fails for its own reason - a harness that cannot catch these cannot
certify a real control plane."""

from __future__ import annotations

from tools.contract_conformance import run
from tools.control_plane_mock import MockControlPlane


def test_clean_run_passes():
    assert run(MockControlPlane()) == []


class DropEntitlements(MockControlPlane):
    def handle(self, method, path, headers, body):
        if path == "/entitlements":
            return self._err("malformed_request", "unknown operation",
                             retryable=False, status=404)
        return super().handle(method, path, headers, body)


class WrongErrorCode(MockControlPlane):
    def _register(self, body):
        status, payload = super()._register(body)
        if status == 409:
            payload["error"]["code"] = "conflictz"
        return status, payload


class NoIdempotencyReplay(MockControlPlane):
    def handle(self, method, path, headers, body):
        routes_key = (method, path)
        if method in ("POST", "DELETE") and routes_key != (
                "POST", "/identity/teleport"):
            # bypass the idempotency cache entirely
            saved = self.idempotency
            self.idempotency = {}
            try:
                return super().handle(method, path, headers, body)
            finally:
                self.idempotency = saved
        return super().handle(method, path, headers, body)


class ShapelessError(MockControlPlane):
    def _login(self, body):
        status, payload = super()._login(body)
        if status == 401:
            return status, {"oops": "no error shape"}
        return status, payload


class CrashOnRoute(MockControlPlane):
    def _route(self, body):
        raise RuntimeError("boom")


MUTANTS = {
    "dropped_operation": DropEntitlements,
    "error_code_outside_enum": WrongErrorCode,
    "no_idempotent_replay": NoIdempotencyReplay,
    "error_without_shape": ShapelessError,
    "implementation_crash": CrashOnRoute,
}


def test_every_mutant_fails_conformance():
    for name, cls in sorted(MUTANTS.items()):
        problems = run(cls())
        assert problems, f"{name}: mutant passed conformance"
