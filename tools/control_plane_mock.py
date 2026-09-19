"""T0474: reference control-plane fixture, contract schema v4.

In-memory implementation of data/contracts/control-plane.yaml. Request
validation is GENERATED from the contract's structured schemas (required
fields, exact types, unknown fields ignored for forward compatibility);
auth is real bearer token checking per transport.auth; idempotency is
fingerprinted (same key + same canonical body replays the original
outcome, same key + different body answers idempotency_conflict).

    handle(method, path, headers, body) -> (status, payload)
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "control-plane.yaml"


def load_ops() -> dict[tuple[str, str], dict]:
    doc = yaml.safe_load(CONTRACT.read_text())
    ops = {}
    for area_name, area in doc["areas"].items():
        for op_name, op in area["ops"].items():
            ops[(op["method"], op["path"])] = {
                "auth": op["auth"], "mutating": op["mutating"],
                "request": op["request"].get("fields", {}),
                "response": op["response"].get("fields", {}),
                "name": f"{area_name}.{op_name}"}
    return ops


OPS = load_ops()


def load_self_destructive() -> frozenset:
    doc = yaml.safe_load(CONTRACT.read_text())
    return frozenset(doc["contract"]["transport"][
        "self_destructive_operations"])


SELF_DESTRUCTIVE = load_self_destructive()

_ITEM_OK = {
    "string": lambda v: type(v) is str,
    "integer": lambda v: type(v) is int,
    "number": lambda v: type(v) in (int, float) and type(v) is not bool,
    "boolean": lambda v: type(v) is bool,
}


def _bearer_token(headers: dict) -> str | None:
    auth = headers.get("Authorization", "")
    if type(auth) is str and auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


def _fingerprint(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _validate(fields: dict, body: dict, where: str) -> str | None:
    """Schema-driven request validation; unknown fields ignored."""
    for name, spec in fields.items():
        value = body.get(name)
        if value is None:
            if spec["required"]:
                return f"{where}.{name}: required field missing"
            continue
        ftype = spec["type"]
        ok = {"string": type(value) is str and bool(value.strip()),
              "integer": type(value) is int,
              "number": type(value) in (int, float)
              and type(value) is not bool,
              "boolean": type(value) is bool,
              "object": type(value) is dict,
              "array": type(value) is list}.get(ftype, False)
        if not ok:
            return f"{where}.{name}: wrong type"
        if ftype == "object":
            sub = _validate(spec.get("fields", {}), value,
                            f"{where}.{name}")
            if sub:
                return sub
        if ftype == "array":
            item_type = spec.get("items")
            if type(item_type) is str:
                for item in value:
                    if not _ITEM_OK.get(item_type, lambda v: True)(item):
                        return f"{where}.{name}: wrong item type"
    return None


class MockControlPlane:
    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}
        self.tokens: dict[str, dict] = {}
        self.idempotency: dict[tuple, tuple[str, tuple[int, dict]]] = {}
        self.reservations: dict[str, dict] = {}
        self.quota_units = 1000
        self.keys: dict[str, dict] = {}
        self.subscription = None

    @staticmethod
    def _err(code: str, message: str, *, retryable: bool,
             status: int) -> tuple[int, dict]:
        return status, {"error": {"code": code, "message": message,
                                  "retryable": retryable}}

    def _bearer(self, headers: dict) -> tuple[str, dict | None]:
        """Four-way token check: missing/unknown -> auth_invalid,
        expired -> auth_expired, ok -> the token record. Expired tokens
        stay recorded so the two failure modes stay distinct."""
        token = _bearer_token(headers)
        if token is None:
            return "missing", None
        record = self.tokens.get(token)
        if record is None:
            return "unknown", None
        if time.time() > record["expires_at"]:
            return "expired", record
        return "ok", record

    SELF_DESTRUCTIVE_OPS = SELF_DESTRUCTIVE

    def fixture_expire_token(self, token: str) -> None:
        """Fixture adapter seam (NOT part of the handle() interface):
        force a token into the expired state so the conformance harness
        can certify the auth_expired branch. Supplied to the harness as
        a separate adapter, never required of the implementation."""
        record = self.tokens.get(token)
        if record is not None:
            record["expires_at"] = 0.0

    def handle(self, method: str, path: str, headers: dict,
               body: dict) -> tuple[int, dict]:
        op = OPS.get((method, path))
        if op is None:
            return self._err("malformed_request", "unknown operation",
                             retryable=False, status=404)
        name = op["name"]
        # Contract transport rule: for self-destructive ops the replay
        # lookup runs BEFORE credential validity checks, scoped by the
        # presented credential itself, never its validity.
        if op["mutating"] and name in self.SELF_DESTRUCTIVE_OPS:
            key = headers.get("Idempotency-Key", "")
            token = _bearer_token(headers)
            if type(key) is str and key.strip() and token is not None:
                scope = ("cred", token, method, path, key)
                prior = self.idempotency.get(scope)
                if prior is not None:
                    if prior[0] != _fingerprint(body):
                        return self._err(
                            "idempotency_conflict",
                            "same Idempotency-Key with a different body",
                            retryable=False, status=409)
                    return prior[1]
        account = None
        if op["auth"] == "required":
            reason, account = self._bearer(headers)
            if reason != "ok":
                code = ("auth_expired" if reason == "expired"
                        else "auth_invalid")
                return self._err(code, f"bearer token {reason}",
                                 retryable=False, status=401)
        if op["mutating"]:
            key = headers.get("Idempotency-Key", "")
            if type(key) is not str or not key.strip():
                return self._err("malformed_request",
                                 "Idempotency-Key header required",
                                 retryable=False, status=400)
            if name in self.SELF_DESTRUCTIVE_OPS:
                scope = ("cred", _bearer_token(headers), method, path,
                         key)
            else:
                scope = (account["account_id"] if account else "public",
                         method, path, key)
            fingerprint = _fingerprint(body)
            prior = self.idempotency.get(scope)
            if prior is not None:
                if prior[0] != fingerprint:
                    return self._err(
                        "idempotency_conflict",
                        "same Idempotency-Key with a different body",
                        retryable=False, status=409)
                return prior[1]
            result = self._logic(name, body, account, headers)
            self.idempotency[scope] = (fingerprint, result)
            return result
        return self._logic(name, body, account, headers)

    def _logic(self, name: str, body: dict, account: dict | None = None,
               headers: dict | None = None) -> tuple[int, dict]:
        op = next(o for o in OPS.values() if o["name"] == name)
        problem = _validate(op["request"], body, name)
        if problem:
            return self._err("malformed_request", problem,
                             retryable=False, status=400)
        handler = getattr(self, "_op_" + name.replace(".", "_"))
        return handler(body, account, headers)

    # -- identity ------------------------------------------------------
    def _mint(self, account_id: str) -> dict:
        token = secrets.token_hex(16)
        self.tokens[token] = {"account_id": account_id,
                              "expires_at": time.time() + 3600}
        return {"token": token,
                "token_expires_at": int(time.time()) + 3600}

    def _op_identity_register(self, body: dict, account=None, headers=None) -> tuple[int, dict]:
        if "@" not in body["email"]:
            return self._err("malformed_request", "invalid email",
                             retryable=False, status=400)
        if body["email"] in self.accounts:
            return self._err("conflict", "account exists",
                             retryable=False, status=409)
        account_id = "acct-" + secrets.token_hex(8)
        self.accounts[body["email"]] = {
            "account_id": account_id,
            "password": body["password_hash_client"]}
        payload = {"account_id": account_id, **self._mint(account_id)}
        return 200, payload

    def _op_identity_login(self, body: dict, account=None, headers=None) -> tuple[int, dict]:
        account = self.accounts.get(body["email"])
        if account is None or account["password"] != body[
                "password_hash_client"]:
            return self._err("auth_invalid", "bad credentials",
                             retryable=False, status=401)
        return 200, {"account_id": account["account_id"],
                     **self._mint(account["account_id"])}

    def _op_identity_refresh(self, body: dict, account=None,
                             headers=None) -> tuple[int, dict]:
        # refresh mints for the AUTHENTICATED account, never a constant
        return 200, self._mint(account["account_id"])

    def _op_identity_logout(self, body: dict, account=None,
                            headers=None) -> tuple[int, dict]:
        # logout revokes the bearer token that made the call
        token = _bearer_token(headers or {})
        if token is not None:
            self.tokens.pop(token, None)
        return 200, {}

    def _op_identity_delete_account(self, body: dict, account=None,
                                    headers=None) -> tuple[int, dict]:
        if body["confirm"] != "DELETE":
            return self._err("malformed_request",
                             "confirm must be exactly DELETE",
                             retryable=False, status=400)
        # delete removes the account AND every token bound to it
        account_id = account["account_id"] if account else None
        for email, rec in list(self.accounts.items()):
            if rec["account_id"] == account_id:
                del self.accounts[email]
        for tok, rec in list(self.tokens.items()):
            if rec["account_id"] == account_id:
                del self.tokens[tok]
        return 200, {}

    # -- entitlements ---------------------------------------------------
    def _op_entitlements_get(self, body: dict, account=None, headers=None) -> tuple[int, dict]:
        return 200, {"tier": "free", "features": ["local-analysis"],
                     "cache_ttl_seconds": 300,
                     "cost_caps": {"per_request_usd": 0.10,
                                   "per_day_usd": 2.00}}

    # -- quota -----------------------------------------------------------
    def _op_quota_reserve(self, body: dict, account=None, headers=None) -> tuple[int, dict]:
        if body["estimated_units"] <= 0 or body["hold_seconds"] <= 0:
            return self._err("malformed_request",
                             "units and hold must be positive",
                             retryable=False, status=400)
        if body["estimated_units"] > self.quota_units:
            return self._err("quota_exhausted", "not enough quota",
                             retryable=True, status=429)
        self.quota_units -= body["estimated_units"]
        reservation_id = "rsv-" + secrets.token_hex(8)
        expires = int(time.time()) + body["hold_seconds"]
        self.reservations[reservation_id] = {
            "units": body["estimated_units"], "expires_at": expires}
        return 200, {"reservation_id": reservation_id,
                     "granted_units": body["estimated_units"],
                     "expires_at": expires}

    def _op_quota_commit(self, body: dict, account=None, headers=None) -> tuple[int, dict]:
        if body["actual_units"] < 0:
            return self._err("malformed_request",
                             "actual_units may not be negative",
                             retryable=False, status=400)
        reservation = self.reservations.pop(body["reservation_id"], None)
        if reservation is None:
            return self._err("not_found", "unknown reservation",
                             retryable=False, status=404)
        if time.time() > reservation["expires_at"]:
            self.quota_units += reservation["units"]
            return self._err("quota_reservation_expired",
                             "reservation expired", retryable=True,
                             status=409)
        self.quota_units += max(0, reservation["units"]
                                - body["actual_units"])
        return 200, {"remaining_units": self.quota_units}

    def _op_quota_release(self, body: dict, account=None, headers=None) -> tuple[int, dict]:
        reservation = self.reservations.pop(body["reservation_id"], None)
        if reservation is None:
            return self._err("not_found", "unknown reservation",
                             retryable=False, status=404)
        self.quota_units += reservation["units"]
        return 200, {}

    # -- billing ---------------------------------------------------------
    def _op_billing_create_checkout(self, body: dict, account=None,
                             headers=None) -> tuple[int, dict]:
        for field in ("success_url", "cancel_url"):
            if not body[field].startswith("https://"):
                return self._err("malformed_request",
                                 f"{field} must be an https URL",
                                 retryable=False, status=400)
        return 200, {"checkout_url": "https://payments.example/checkout/"
                     + secrets.token_hex(8),
                     "expires_at": int(time.time()) + 1800}

    def _op_billing_get_subscription(self, body: dict, account=None,
                             headers=None) -> tuple[int, dict]:
        if self.subscription is None:
            return self._err("not_found", "no subscription",
                             retryable=False, status=404)
        return 200, self.subscription

    # -- provider routing -------------------------------------------------
    def _op_provider_routing_register_key(self, body: dict, account=None,
                             headers=None) -> tuple[int, dict]:
        if len(body["key_material"]) < 8:
            return self._err("provider_key_invalid", "key too short",
                             retryable=False, status=400)
        key_ref = "key-" + secrets.token_hex(8)
        self.keys[key_ref] = {"provider_kind": body["provider_kind"]}
        return 200, {"key_ref": key_ref}

    def _op_provider_routing_revoke_key(self, body: dict, account=None,
                             headers=None) -> tuple[int, dict]:
        if self.keys.pop(body["key_ref"], None) is None:
            return self._err("not_found", "unknown key_ref",
                             retryable=False, status=404)
        return 200, {}

    def _op_provider_routing_route(self, body: dict, account=None,
                             headers=None) -> tuple[int, dict]:
        if self.keys:
            key_ref = sorted(self.keys)[0]
            return 200, {"provider_kind": self.keys[key_ref]
                         ["provider_kind"], "key_ref": key_ref,
                         "estimated_cost_usd": 0.0}
        return 200, {"provider_kind": "platform",
                     "estimated_cost_usd": 0.02}
