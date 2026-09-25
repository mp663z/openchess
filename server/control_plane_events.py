"""Local control-plane metadata outbox; no network sink or game payloads.

The SQLite ledger is the committed outbox. Consumers must read committed rows,
never a staged batch. A file-backed Ledger can be reopened after process loss.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from contextlib import suppress
from pathlib import Path

_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
_FIELDS = frozenset({
    "version", "name", "operation_id", "event_id", "occurred_at",
    "correlation_id", "outcome", "error_code",
})
_REQUIRED = _FIELDS - {"error_code"}
# Pinned v1 catalog and per-operation error scope from control-plane.yaml.
_ERRORS = {
    "identity.register": (
        "malformed_request",
        "conflict",
        "rate_limited",
        "idempotency_conflict",
        "internal",
    ),
    "identity.login": (
        "malformed_request",
        "auth_invalid",
        "rate_limited",
        "idempotency_conflict",
        "internal",
    ),
    "identity.refresh": (
        "auth_expired",
        "auth_invalid",
        "idempotency_conflict",
        "internal",
    ),
    "identity.logout": (
        "auth_invalid",
        "idempotency_conflict",
        "internal",
    ),
    "identity.delete_account": (
        "auth_invalid",
        "malformed_request",
        "idempotency_conflict",
        "internal",
    ),
    "entitlements.get": (
        "auth_expired",
        "auth_invalid",
        "internal",
    ),
    "quota.reserve": (
        "auth_expired",
        "auth_invalid",
        "quota_exhausted",
        "malformed_request",
        "idempotency_conflict",
        "internal",
    ),
    "quota.commit": (
        "auth_expired",
        "auth_invalid",
        "quota_reservation_expired",
        "not_found",
        "malformed_request",
        "idempotency_conflict",
        "internal",
    ),
    "quota.release": (
        "auth_expired",
        "auth_invalid",
        "not_found",
        "idempotency_conflict",
        "internal",
    ),
    "billing.create_checkout": (
        "auth_expired",
        "auth_invalid",
        "malformed_request",
        "provider_unavailable",
        "idempotency_conflict",
        "internal",
    ),
    "billing.get_subscription": (
        "auth_expired",
        "auth_invalid",
        "not_found",
        "internal",
    ),
    "provider_routing.register_key": (
        "auth_expired",
        "auth_invalid",
        "provider_key_invalid",
        "malformed_request",
        "idempotency_conflict",
        "internal",
    ),
    "provider_routing.revoke_key": (
        "auth_expired",
        "auth_invalid",
        "not_found",
        "idempotency_conflict",
        "internal",
    ),
    "provider_routing.route": (
        "auth_expired",
        "auth_invalid",
        "entitlement_missing",
        "cost_cap_exceeded",
        "provider_unavailable",
        "malformed_request",
        "internal",
    ),
}


class Refusal(ValueError):
    """Typed refusal; never includes caller-supplied content."""

    def __init__(self, failure_class: str):
        self.failure_class = failure_class
        super().__init__(failure_class)


def validate(event: object) -> dict:
    """Detach a strictly closed v1 metadata envelope without calling user code."""
    if type(event) is not dict:
        raise Refusal("malformed_event")
    # dict iteration over an exact dict does not invoke subclass operators;
    # check key types before hashing/comparing any caller-supplied key.
    if any(type(key) is not str for key in event):
        raise Refusal("malformed_event")
    if set(event) - _FIELDS or _REQUIRED - set(event):
        raise Refusal("malformed_event")
    if type(event["version"]) is not int:
        raise Refusal("malformed_event")
    if event["version"] != 1:
        raise Refusal("unsupported_version")
    op = event["operation_id"]
    if type(op) is not str or op not in _ERRORS:
        raise Refusal("unknown_name")
    outcome = event["outcome"]
    if type(outcome) is not str or outcome not in ("succeeded", "failed"):
        raise Refusal("malformed_event")
    name = event["name"]
    if type(name) is not str or name != f"control_plane.{op}.{outcome}":
        raise Refusal("unknown_name")
    for field in ("event_id", "correlation_id"):
        ident = event[field]
        if type(ident) is not str or _IDENTIFIER.fullmatch(ident) is None:
            raise Refusal("malformed_event")
    timestamp = event["occurred_at"]
    if type(timestamp) is not int or not 0 <= timestamp <= 253402300799999:
        raise Refusal("malformed_event")
    if outcome == "failed":
        code = event.get("error_code")
        if type(code) is not str or code not in _ERRORS[op]:
            raise Refusal("malformed_event")
    elif "error_code" in event:
        raise Refusal("malformed_event")
    return {field: event[field] for field in event}


def new_event(operation_id: str, outcome: str, *, error_code: str | None = None) -> dict:
    """Create identifiers independently of private operation inputs.

    Call only after the operation's effect commits or its terminal error is
    decided. Never pass request IDs, account IDs or body-derived values here.
    """
    if type(operation_id) is not str or operation_id not in _ERRORS:
        raise Refusal("unknown_name")
    if type(outcome) is not str or outcome not in ("succeeded", "failed"):
        raise Refusal("malformed_event")
    if error_code is not None and type(error_code) is not str:
        raise Refusal("malformed_event")
    event = {
        "version": 1,
        "name": f"control_plane.{operation_id}.{outcome}",
        "operation_id": operation_id,
        "event_id": secrets.token_urlsafe(24),
        "occurred_at": time.time_ns() // 1_000_000,
        "correlation_id": secrets.token_urlsafe(24),
        "outcome": outcome,
    }
    if error_code is not None:
        event["error_code"] = error_code
    return validate(event)


class Ledger:
    """Ordered durable outbox and dedupe index in a single SQLite transaction.

    Pass a local path for crash-safe storage. The default in-memory database is
    convenient for contract clients and tests; it is not restart-durable.
    """

    def __init__(self, path: str | Path = ":memory:"):
        if type(path) is not str and type(path) is not type(Path()):
            raise Refusal("publication_failure")
        try:
            self._db = sqlite3.connect(str(path), isolation_level=None)
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS outbox ("
                "ordinal INTEGER PRIMARY KEY AUTOINCREMENT, "
                "event_id TEXT NOT NULL UNIQUE, envelope TEXT NOT NULL)"
            )
        except (sqlite3.Error, ValueError):
            raise Refusal("publication_failure") from None

    def close(self) -> None:
        self._db.close()

    @property
    def events(self) -> list[dict]:
        try:
            return [
                _decode(key, envelope)
                for key, envelope in self._db.execute(
                    "SELECT event_id, envelope FROM outbox ORDER BY ordinal"
                )
            ]
        except sqlite3.Error:
            raise Refusal("publication_failure") from None

    @events.setter
    def events(self, values: list[dict]) -> None:
        # Legacy fixture's explicit restart hydration; normal restarts use path.
        if type(values) is not list:
            raise Refusal("malformed_event")
        validated = [validate(value) for value in values]
        try:
            self._db.execute("BEGIN IMMEDIATE")
            self._db.execute("DELETE FROM outbox")
            for event in validated:
                self._db.execute(
                    "INSERT INTO outbox(event_id, envelope) VALUES (?, ?)",
                    (event["event_id"], _encode(event)),
                )
            self._db.execute("COMMIT")
        except sqlite3.Error:
            if self._db.in_transaction:
                with suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
            raise Refusal("publication_failure") from None

    @property
    def seen(self) -> dict[str, dict]:
        return {event["event_id"]: event for event in self.events}

    @seen.setter
    def seen(self, values: dict[str, dict]) -> None:
        # The dedupe index is derived atomically from the unique outbox rows.
        # The historical fixture sets this after restoring events.
        if type(values) is not dict or values != self.seen:
            raise Refusal("malformed_event")

    def publish(self, batch: list[dict], *, fail_commit: bool = False) -> int:
        if type(batch) is not list:
            raise Refusal("malformed_event")
        proposed = [validate(event) for event in batch]
        staged = {}
        for event in proposed:
            key = event["event_id"]
            if key in staged and staged[key] != event:
                raise Refusal("duplicate_conflict")
            staged[key] = event
        try:
            self._db.execute("BEGIN IMMEDIATE")
            existing = {
                key: _decode(key, envelope)
                for key, envelope in self._db.execute(
                    "SELECT event_id, envelope FROM outbox"
                )
            }
            for key, event in staged.items():
                if key in existing and existing[key] != event:
                    raise Refusal("duplicate_conflict")
            for key, event in staged.items():
                if key not in existing:
                    self._db.execute(
                        "INSERT INTO outbox(event_id, envelope) VALUES (?, ?)",
                        (key, _encode(event)),
                    )
            if fail_commit:
                raise Refusal("publication_failure")
            count = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
            self._db.execute("COMMIT")
            return count
        except (sqlite3.Error, Refusal) as exc:
            if self._db.in_transaction:
                with suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
            if isinstance(exc, Refusal):
                raise
            raise Refusal("publication_failure") from None


def _encode(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decode(key: object, envelope: object) -> dict:
    """Refuse corrupt persisted rows before exposing or deduplicating them."""
    if type(key) is not str or type(envelope) is not str:
        raise Refusal("publication_failure")
    try:
        event = validate(json.loads(envelope))
    except (ValueError, TypeError, Refusal):
        raise Refusal("publication_failure") from None
    if event["event_id"] != key:
        raise Refusal("publication_failure")
    return event
