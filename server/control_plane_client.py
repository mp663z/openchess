"""Production consumer-side control-plane client (T0476).

Codes to data/contracts/control-plane.yaml, never to an implementation:
any conforming control plane (hosted, self-hosted or the T0474 mock) is
reached through one ``transport(method, path, headers, body) ->
(status, payload)`` callable. A self-hosted control plane stays private
(licensing-boundary.yaml); this module is only the public consumer side.

Pinned behaviour, each from the contract:
- bearer token lifecycle: register/login issue the token, refresh
  replaces it before expiry, logout/delete_account clear it; a 401
  auth_expired or auth_invalid clears it and is raised as that code;
- every mutating operation carries ONE Idempotency-Key per logical
  operation, reused byte-identically across retries of that operation;
- entitlements are cached for cache_ttl_seconds and served from cache
  while the control plane is unreachable, then degrade to the free tier;
- a reservation past its expires_at is treated as released;
- key material (write_only fields) is never logged, never echoed in an
  error message, and a response that echoes it is refused;
- every failure is a fresh ControlPlaneError whose code is in the
  closed enum; a contract-violating response is ``internal``.

Fail-closed readings where the contract is silent are listed in
requirements/tasks/T0476.md.
"""

from __future__ import annotations

import copy
import secrets
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "control-plane.yaml"

_DOC = yaml.safe_load(CONTRACT.read_text())
_TRANSPORT = _DOC["contract"]["transport"]
ERROR_ENUM = tuple(_TRANSPORT["errors"]["closed_enum"])
SELF_DESTRUCTIVE = frozenset(_TRANSPORT["self_destructive_operations"])
TTL_FIELD = _DOC["contract"]["recovery"]["entitlements_cache_ttl_field"]
EXPIRY_FIELD = _DOC["contract"]["recovery"]["reservation_expiry_field"]


def _load_ops():
    ops = {}
    for area, spec in _DOC["areas"].items():
        for name, op in spec["ops"].items():
            ops[f"{area}.{name}"] = {
                "method": op["method"],
                # relative to the pinned base path; the transport owns the
                # base URL (https://<host> + /cp/v1)
                "path": op["path"],
                "auth": op["auth"],
                "mutating": op["mutating"],
                "request": op["request"]["fields"] or {},
                "response": op["response"]["fields"] or {},
                "errors": frozenset(op["errors"]),
            }
    return ops


OPS = _load_ops()

# Fail-closed free tier (the contract names the degradation, not its
# contents): no paid features and zero spend caps.
FREE_TIER = {
    "tier": "free",
    "features": [],
    TTL_FIELD: 0,
    "cost_caps": {"per_request_usd": 0.0, "per_day_usd": 0.0},
}

_TYPE_OK = {
    "string": lambda v: type(v) is str,
    "integer": lambda v: type(v) is int,
    "number": lambda v: type(v) in (int, float),
    "boolean": lambda v: type(v) is bool,
    "object": lambda v: type(v) is dict,
    "array": lambda v: type(v) is list,
}


class ControlPlaneError(Exception):
    """A typed control-plane failure; ``code`` is in the closed enum."""

    def __init__(self, code, message, *, retryable=False, status=None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status


def _fail(code, message, *, retryable=False, status=None):
    raise ControlPlaneError(code, message, retryable=retryable, status=status)


def _write_only_values(fields, body):
    out = []
    for name, spec in fields.items():
        if name not in body:
            continue
        if spec.get("write_only"):
            out.append(body[name])
        elif spec["type"] == "object":
            out.extend(_write_only_values(spec.get("fields") or {}, body[name]))
    return out


def _write_only_names(fields):
    names = set()
    for name, spec in fields.items():
        if spec.get("write_only"):
            names.add(name)
        if spec.get("type") == "object":
            names |= _write_only_names(spec.get("fields") or {})
    return names


_SECRET_NAMES = frozenset().union(*(_write_only_names(op["request"]) for op in OPS.values()))


def _valid(payload, fields, *, closed):
    """Exact-type check of PAYLOAD against contract FIELDS. CLOSED (the
    request side) also refuses undeclared keys; the response side ignores
    them (MINOR is additive). Never calls a method of a caller object."""
    if type(payload) is not dict:
        return False
    for key in list(payload.keys()):
        if type(key) is not str:
            return False
        if closed and key not in fields:
            return False
    for name, spec in fields.items():
        if name not in payload:
            if spec["required"]:
                return False
            continue
        value = payload[name]
        ftype = spec["type"]
        if not _TYPE_OK[ftype](value):
            return False
        if ftype == "object" and not _valid(value, spec.get("fields") or {}, closed=closed):
            return False
        if ftype == "array":
            item = spec.get("items")
            if type(item) is str and not all(_TYPE_OK[item](v) for v in value):
                return False
    return True


def _contains(payload, needles):
    if type(payload) is str:
        return any(n and n in payload for n in needles)
    if type(payload) is dict:
        return any(
            (type(k) is str and k in _SECRET_NAMES)
            or _contains(k, needles)
            or _contains(v, needles)
            for k, v in payload.items()
        )
    if type(payload) is list:
        return any(_contains(v, needles) for v in payload)
    return False


class ControlPlaneClient:
    """Consumer of any conforming control plane.

    TRANSPORT is ``transport(method, path, headers, body) -> (status,
    payload)``; raising anything (any BaseException, as at every
    untrusted boundary in this repo) means the control plane is
    unreachable. CLOCK returns epoch seconds; KEY_FACTORY returns a fresh
    Idempotency-Key; LOG receives ``(operation, status, code)`` only.
    """

    def __init__(
        self,
        transport,
        *,
        clock=time.time,
        key_factory=None,
        max_attempts=3,
        refresh_skew_seconds=60,
        log=None,
    ):
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be an int in 1..10")
        if type(refresh_skew_seconds) is not int or refresh_skew_seconds < 0:
            raise ValueError("refresh_skew_seconds must be a non-negative int")
        self._transport = transport
        self._clock = clock
        self._key_factory = key_factory or (lambda: secrets.token_hex(16))
        self._max_attempts = max_attempts
        self._skew = refresh_skew_seconds
        self._log = log
        self.token = None
        self.token_expires_at = None
        self.account_id = None
        self._entitlements = None  # (payload, fetched_at)
        self.entitlements_source = None
        self._reservations = {}

    # -- public -----------------------------------------------------------

    def call(self, name, body=None):
        """Run one logical operation; returns a deep copy of the
        contract-valid response payload or raises ControlPlaneError."""
        if type(name) is not str or name not in OPS:
            _fail("malformed_request", "unknown operation")
        op = OPS[name]
        body = {} if body is None else body
        if not _valid(body, op["request"], closed=True):
            _fail("malformed_request", "request does not match the contract")
        body = copy.deepcopy(body)
        early = self._reservation_shortcut(name, body)
        if early is not None:
            return early
        if op["auth"] == "required":
            self._ensure_token(name)
        payload = self._send(name, op, body)
        self._after_success(name, body, payload)
        return copy.deepcopy(payload)

    def entitlements(self):
        """Live entitlements; while the control plane is unreachable, the
        cached copy for cache_ttl_seconds, then the free tier."""
        try:
            payload = self.call("entitlements.get")
        except ControlPlaneError as err:
            if not self._unreachable(err):
                raise
            now = self._now()
            cached = self._entitlements
            if cached is not None and now - cached[1] < cached[0][TTL_FIELD]:
                self.entitlements_source = "cache"
                return copy.deepcopy(cached[0])
            self.entitlements_source = "free"
            return copy.deepcopy(FREE_TIER)
        self.entitlements_source = "live"
        return payload

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _unreachable(err):
        return err.status is None or err.status >= 500

    def _now(self):
        try:
            now = self._clock()
        except BaseException:  # noqa: BLE001 - untrusted clock: fail closed typed
            _fail("internal", "clock failed")
        if type(now) not in (int, float) or now != now:
            _fail("internal", "clock returned a non-number")
        return now

    def _ensure_token(self, name):
        if self.token is None:
            _fail("auth_invalid", "no bearer token held", status=401)
        now = self._now()
        if now >= self.token_expires_at:
            self._clear_session()
            _fail("auth_expired", "bearer token expired", status=401)
        if name != "identity.refresh" and now >= self.token_expires_at - self._skew:
            self.call("identity.refresh")

    def _clear_session(self):
        self.token = None
        self.token_expires_at = None
        self._entitlements = None

    def _reservation_shortcut(self, name, body):
        if name not in ("quota.commit", "quota.release"):
            return None
        expires_at = self._reservations.get(body["reservation_id"])
        if expires_at is None or self._now() < expires_at:
            return None
        del self._reservations[body["reservation_id"]]
        if name == "quota.release":
            return {}  # expired: already released
        _fail("quota_reservation_expired", "reservation expired; treated as released")

    def _headers(self, op, key):
        headers = {}
        if op["auth"] == "required":
            headers["Authorization"] = f"Bearer {self.token}"
        if op["mutating"]:
            headers["Idempotency-Key"] = key
        return headers

    def _send(self, name, op, body):
        key = None
        if op["mutating"]:
            try:
                key = self._key_factory()
            except BaseException:  # noqa: BLE001 - untrusted key factory
                _fail("internal", "key factory failed")
            if type(key) is not str or not key.strip():
                _fail("internal", "key factory returned an unusable key")
        secrets_ = _write_only_values(op["request"], body)
        last = None
        for _attempt in range(self._max_attempts):
            try:
                result = self._transport(
                    op["method"], op["path"], self._headers(op, key), copy.deepcopy(body)
                )
            except BaseException:  # noqa: BLE001 - untrusted boundary: fail closed typed
                last = ("internal", "control plane unreachable", True, None)
                self._emit(name, None, "internal")
                continue
            status, payload = self._check_result(result)
            if 200 <= status < 300:
                if not _valid(payload, op["response"], closed=False) or _contains(
                    payload, secrets_
                ):
                    self._emit(name, status, "internal")
                    _fail("internal", "response violates the contract", status=status)
                self._emit(name, status, None)
                return payload
            code, message, retryable = self._check_error(op, payload, secrets_)
            self._emit(name, status, code)
            if code in ("auth_expired", "auth_invalid"):
                self._clear_session()
            if not retryable:
                _fail(code, message, retryable=False, status=status)
            last = (code, message, True, status)
        code, message, retryable, status = last
        _fail(code, message, retryable=retryable, status=status)

    @staticmethod
    def _check_result(result):
        if type(result) is not tuple or len(result) != 2:
            _fail("internal", "transport result is not a (status, payload) pair")
        status, payload = result
        if type(status) is not int or not 100 <= status <= 599 or type(payload) is not dict:
            _fail("internal", "transport result has a bad status or payload")
        return status, payload

    @staticmethod
    def _check_error(op, payload, secrets_):
        error = payload.get("error") if "error" in payload else None
        if type(error) is not dict:
            _fail("internal", "error response without an error object")
        code = error.get("code") if "code" in error else None
        message = error.get("message") if "message" in error else None
        retryable = error.get("retryable") if "retryable" in error else None
        if (
            type(code) is not str
            or code not in ERROR_ENUM
            or code not in op["errors"]
            or type(message) is not str
            or type(retryable) is not bool
        ):
            _fail("internal", "error response violates the contract")
        if _contains(message, secrets_):
            message = "redacted"
        return code, message, retryable

    def _emit(self, name, status, code):
        if self._log is not None:
            try:
                self._log(name, status, code)
            except BaseException:  # noqa: BLE001, SIM105 - logging never changes the outcome
                return

    def _after_success(self, name, body, payload):
        if name in ("identity.register", "identity.login"):
            self.account_id = payload["account_id"]
            self.token = payload["token"]
            self.token_expires_at = payload["token_expires_at"]
            self._entitlements = None
        elif name == "identity.refresh":
            self.token = payload["token"]
            self.token_expires_at = payload["token_expires_at"]
        elif name in SELF_DESTRUCTIVE:
            self._clear_session()
            self.account_id = None
            self._reservations.clear()
        elif name == "entitlements.get":
            self._entitlements = (copy.deepcopy(payload), self._now())
        elif name == "quota.reserve":
            self._reservations[payload["reservation_id"]] = payload[EXPIRY_FIELD]
        elif name in ("quota.commit", "quota.release"):
            self._reservations.pop(body["reservation_id"], None)
