"""T0474: reference control-plane fixture.

An in-memory implementation of data/contracts/control-plane.yaml used by
the conformance harness (tools/contract_conformance.py) and by consumers
that need a replaceable control plane in tests. Interface:

    handle(method, path, headers, body) -> (status, payload)
"""

from __future__ import annotations

import secrets
import time


class MockControlPlane:
    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {}
        self.tokens: dict[str, str] = {}
        self.idempotency: dict[str, tuple[int, dict]] = {}
        self.entitlements = {
            "tier": "free", "features": ["local-analysis"],
            "cache_ttl_seconds": 300,
            "cost_caps": {"per_request_usd": 0.10, "per_day_usd": 2.00},
        }
        self.reservations: dict[str, dict] = {}
        self.quota_units = 1000
        self.keys: dict[str, dict] = {}
        self.subscription = None

    # -- plumbing -----------------------------------------------------
    def handle(self, method: str, path: str, headers: dict,
               body: dict) -> tuple[int, dict]:
        routes = {
            ("POST", "/identity/register"): self._register,
            ("POST", "/identity/login"): self._login,
            ("POST", "/identity/refresh"): self._refresh,
            ("POST", "/identity/logout"): self._logout,
            ("POST", "/identity/delete-account"): self._delete,
            ("GET", "/entitlements"): self._entitlements,
            ("POST", "/quota/reserve"): self._reserve,
            ("POST", "/quota/commit"): self._commit,
            ("POST", "/quota/release"): self._release,
            ("POST", "/billing/checkout"): self._checkout,
            ("GET", "/billing/subscription"): self._subscription_get,
            ("POST", "/provider-keys/register"): self._key_register,
            ("POST", "/provider-keys/revoke"): self._key_revoke,
            ("POST", "/provider-routing/route"): self._route,
        }
        handler = routes.get((method, path))
        if handler is None:
            return self._err("malformed_request", "unknown operation",
                             retryable=False, status=404)
        if method in ("POST", "DELETE"):
            key = headers.get("Idempotency-Key", "")
            if type(key) is not str or not key.strip():
                return self._err("malformed_request",
                                 "Idempotency-Key header required",
                                 retryable=False, status=400)
            prior = self.idempotency.get(f"{method} {path} {key}")
            if prior is not None:
                return prior
            result = handler(body)
            self.idempotency[f"{method} {path} {key}"] = result
            return result
        return handler(body)

    @staticmethod
    def _err(code: str, message: str, *, retryable: bool,
             status: int) -> tuple[int, dict]:
        return status, {"error": {"code": code, "message": message,
                                  "retryable": retryable}}

    def _token(self, account_id: str) -> str:
        token = secrets.token_hex(16)
        self.tokens[token] = account_id
        return token

    def _auth(self, body: dict) -> str | None:
        token = body.get("token", "")
        return self.tokens.get(token) if type(token) is str else None

    # -- identity ------------------------------------------------------
    def _register(self, body: dict) -> tuple[int, dict]:
        email = body.get("email")
        if type(email) is not str or "@" not in email:
            return self._err("malformed_request", "email required",
                             retryable=False, status=400)
        if email in self.accounts:
            return self._err("conflict", "account exists",
                             retryable=False, status=409)
        account_id = "acct-" + secrets.token_hex(8)
        self.accounts[email] = {"account_id": account_id,
                                "password": body.get(
                                    "password_hash_client", "")}
        return 200, {"account_id": account_id, "token":
                     self._token(account_id),
                     "token_expires_at": int(time.time()) + 3600}

    def _login(self, body: dict) -> tuple[int, dict]:
        account = self.accounts.get(body.get("email", ""))
        if account is None or account["password"] != body.get(
                "password_hash_client"):
            return self._err("auth_invalid", "bad credentials",
                             retryable=False, status=401)
        return 200, {"account_id": account["account_id"],
                     "token": self._token(account["account_id"]),
                     "token_expires_at": int(time.time()) + 3600}

    def _refresh(self, body: dict) -> tuple[int, dict]:
        account_id = self._auth(body)
        if account_id is None:
            return self._err("auth_expired", "token unknown or expired",
                             retryable=False, status=401)
        return 200, {"token": self._token(account_id),
                     "token_expires_at": int(time.time()) + 3600}

    def _logout(self, body: dict) -> tuple[int, dict]:
        if self._auth(body) is None:
            return self._err("auth_invalid", "unknown token",
                             retryable=False, status=401)
        self.tokens.pop(body["token"], None)
        return 200, {}

    def _delete(self, body: dict) -> tuple[int, dict]:
        account_id = self._auth(body)
        if account_id is None:
            return self._err("auth_invalid", "unknown token",
                             retryable=False, status=401)
        if body.get("confirm") != "DELETE":
            return self._err("malformed_request", "confirm must be DELETE",
                             retryable=False, status=400)
        self.accounts = {e: a for e, a in self.accounts.items()
                         if a["account_id"] != account_id}
        self.tokens = {t: a for t, a in self.tokens.items()
                       if a != account_id}
        return 200, {}

    # -- entitlements ---------------------------------------------------
    def _entitlements(self, body: dict) -> tuple[int, dict]:
        return 200, dict(self.entitlements)

    # -- quota -----------------------------------------------------------
    def _reserve(self, body: dict) -> tuple[int, dict]:
        units = body.get("estimated_units")
        if type(units) is not int or units <= 0:
            return self._err("malformed_request",
                             "positive estimated_units required",
                             retryable=False, status=400)
        if units > self.quota_units:
            return self._err("quota_exhausted", "not enough quota",
                             retryable=True, status=429)
        hold = body.get("hold_seconds", 60)
        self.quota_units -= units
        reservation_id = "rsv-" + secrets.token_hex(8)
        self.reservations[reservation_id] = {
            "units": units, "expires_at": time.time() + hold}
        return 200, {"reservation_id": reservation_id,
                     "granted_units": units,
                     "expires_at": int(time.time()) + hold}

    def _commit(self, body: dict) -> tuple[int, dict]:
        reservation = self.reservations.pop(body.get("reservation_id", ""),
                                            None)
        if reservation is None:
            return self._err("not_found", "unknown reservation",
                             retryable=False, status=404)
        if time.time() > reservation["expires_at"]:
            self.quota_units += reservation["units"]
            return self._err("quota_reservation_expired",
                             "reservation expired", retryable=True,
                             status=409)
        actual = body.get("actual_units", reservation["units"])
        self.quota_units += max(0, reservation["units"] - actual)
        return 200, {"remaining_units": self.quota_units}

    def _release(self, body: dict) -> tuple[int, dict]:
        reservation = self.reservations.pop(body.get("reservation_id", ""),
                                            None)
        if reservation is None:
            return self._err("not_found", "unknown reservation",
                             retryable=False, status=404)
        self.quota_units += reservation["units"]
        return 200, {}

    # -- billing ---------------------------------------------------------
    def _checkout(self, body: dict) -> tuple[int, dict]:
        if not body.get("price_id") or not body.get("success_url"):
            return self._err("malformed_request",
                             "price_id and success_url required",
                             retryable=False, status=400)
        return 200, {"checkout_url":
                     "https://payments.example/checkout/"
                     + secrets.token_hex(8),
                     "expires_at": int(time.time()) + 1800}

    def _subscription_get(self, body: dict) -> tuple[int, dict]:
        if self.subscription is None:
            return self._err("not_found", "no subscription",
                             retryable=False, status=404)
        return 200, self.subscription

    # -- provider routing -------------------------------------------------
    def _key_register(self, body: dict) -> tuple[int, dict]:
        material = body.get("key_material")
        if type(material) is not str or len(material) < 8:
            return self._err("provider_key_invalid", "key too short",
                             retryable=False, status=400)
        key_ref = "key-" + secrets.token_hex(8)
        self.keys[key_ref] = {"provider_kind": body.get("provider_kind",
                                                        "generic")}
        return 200, {"key_ref": key_ref}

    def _key_revoke(self, body: dict) -> tuple[int, dict]:
        if self.keys.pop(body.get("key_ref", ""), None) is None:
            return self._err("not_found", "unknown key_ref",
                             retryable=False, status=404)
        return 200, {}

    def _route(self, body: dict) -> tuple[int, dict]:
        if not body.get("capability"):
            return self._err("malformed_request", "capability required",
                             retryable=False, status=400)
        if self.keys:
            key_ref = sorted(self.keys)[0]
            return 200, {"provider_kind": self.keys[key_ref]
                         ["provider_kind"], "key_ref": key_ref,
                         "estimated_cost_usd": 0.0}
        return 200, {"provider_kind": "platform",
                     "estimated_cost_usd": 0.02}
