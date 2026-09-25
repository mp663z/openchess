#!/usr/bin/env python3
"""T0429: deterministic generator for the closed control-plane event fixture.

Rebuilds tests/fixtures/control-plane-events/cases.json from the landed v1
event contract (data/contracts/events.yaml) and its operation source
(data/contracts/control-plane.yaml). Every identifier in the corpus is an
independent synthetic value derived only from the case name (sha256 labels);
no identifier is a runtime value, and the fixture claims no runtime
provenance detection - field validation cannot prove how a value was
generated (contract envelope.value_policy).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "data/contracts/events.yaml"
SOURCE = "data/contracts/control-plane.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "control-plane-events" / "cases.json"
MAX_OCCURRED_AT = 253402300799999

_CONTRACT = yaml.safe_load((ROOT / CONTRACT).read_text())["contract"]
_SOURCE = yaml.safe_load(ROOT / SOURCE.read_text() if False else (ROOT / SOURCE).read_text())
CATALOG = list(_CONTRACT["catalog"]["operation_ids"])
ERRORS = {
    f"{area}.{op}": spec["errors"]
    for area, detail in _SOURCE["areas"].items()
    for op, spec in detail["ops"].items()
}

NOTES = (
    "Closed fixture for the v1 control-plane operation metadata event "
    "contract (data/contracts/events.yaml), contract-only: no shipped event "
    "emission is claimed or exercised. All event_id/correlation_id values "
    "are independent synthetic identifiers derived only from the case name "
    "via sha256 labels; they are not runtime values, user values or "
    "body-derived values, and nothing here claims runtime provenance "
    "detection (contract envelope.value_policy). Secret-looking payloads in "
    "malformed rows exist only to prove typed refusal without echo."
)


def _ident(kind, *parts):
    digest = hashlib.sha256(":".join((kind, *parts)).encode("ascii")).hexdigest()[:24]
    return f"{kind}-{digest}"


def _event(op, outcome, tag, code=None, occurred_at=None, event_id=None, correlation_id=None):
    event = {
        "version": 1,
        "name": f"control_plane.{op}.{outcome}",
        "operation_id": op,
        "event_id": event_id if event_id is not None else _ident("evt", tag),
        "occurred_at": occurred_at,
        "correlation_id": correlation_id if correlation_id is not None else _ident("cor", tag),
        "outcome": outcome,
    }
    if code is not None:
        event["error_code"] = code
    return event


def _happy():
    rows = []
    tick = 1_758_000_000_000
    for op in CATALOG:
        event = _event(op, "succeeded", op, occurred_at=tick)
        rows.append({"name": f"{op}-succeeded", "event": event})
        tick += 1000
        for code in ERRORS[op]:
            tag = f"{op}:{code}"
            rows.append(
                {
                    "name": f"{op}-failed-{code}",
                    "event": _event(op, "failed", tag, code=code, occurred_at=tick),
                }
            )
            tick += 1000
    return rows


def _boundary():
    op = "identity.login"
    return [
        {
            "name": "occurred-at-zero",
            "event": _event(op, "succeeded", "boundary-zero", occurred_at=0),
        },
        {
            "name": "occurred-at-max",
            "event": _event(op, "succeeded", "boundary-max", occurred_at=MAX_OCCURRED_AT),
        },
        {
            "name": "event-id-min-length",
            "event": _event(op, "succeeded", "boundary-eid-min", occurred_at=1, event_id="e"),
        },
        {
            "name": "event-id-max-length",
            "event": _event(
                op,
                "succeeded",
                "boundary-eid-max",
                occurred_at=2,
                event_id="E" + "x" * 63,
            ),
        },
        {
            "name": "correlation-id-min-length",
            "event": _event(op, "succeeded", "boundary-cid-min", occurred_at=3, correlation_id="c"),
        },
        {
            "name": "correlation-id-max-length",
            "event": _event(
                op,
                "succeeded",
                "boundary-cid-max",
                occurred_at=4,
                correlation_id="C" + "y" * 63,
            ),
        },
        {
            "name": "identifier-all-classes",
            "event": _event(
                op,
                "succeeded",
                "boundary-classes",
                occurred_at=5,
                event_id="AZaz09_-" * 8,
                correlation_id="-_90zaZA" * 8,
            ),
        },
        {
            "name": "failed-at-max-timestamp",
            "event": _event(
                "quota.commit",
                "failed",
                "boundary-failed-max",
                code="not_found",
                occurred_at=MAX_OCCURRED_AT,
            ),
        },
        {
            "name": "failed-at-zero-timestamp",
            "event": _event(
                "identity.refresh",
                "failed",
                "boundary-failed-zero",
                code="auth_expired",
                occurred_at=0,
            ),
        },
    ]


def _malformed():
    base = _event("identity.login", "succeeded", "repair-login", occurred_at=1000)
    failed_base = _event(
        "identity.login", "failed", "repair-login-failed", code="auth_invalid", occurred_at=1000
    )

    def row(name, why, event, expect_failure, repair):
        return {
            "name": name,
            "why": why,
            "event": event,
            "expect_failure": expect_failure,
            "minimal_repair": repair,
        }

    def tweak(name, why, field, value, expect_failure="malformed_event", source=None):
        event = dict(base if source is None else source)
        event[field] = value
        return row(name, why, event, expect_failure, dict(base if source is None else source))

    rows = [
        row("event-null", "the event is not an object", None, "malformed_event", dict(base)),
        row("event-list", "the event is a list, not an object", [], "malformed_event", dict(base)),
        row(
            "event-string",
            "the event is a bare string, not an object",
            "control_plane.identity.login.succeeded",
            "malformed_event",
            dict(base),
        ),
        tweak("version-bool-true", "version must be an exact int, not bool", "version", True),
        tweak("version-float", "version must be an exact int, not float", "version", 1.0),
        tweak("version-string", "version must be an exact int, not str", "version", "1"),
        tweak(
            "version-two",
            "envelope version 2 is not declared; refused before publication",
            "version",
            2,
            "unsupported_version",
        ),
        tweak(
            "version-zero",
            "envelope version 0 is not declared; refused before publication",
            "version",
            0,
            "unsupported_version",
        ),
        row(
            "operation-unknown",
            "identity.restore is not in the pinned v1 catalog",
            _event("identity.restore", "succeeded", "mal-op-unknown", occurred_at=1000),
            "unknown_name",
            dict(base),
        ),
        row(
            "operation-outside-scope",
            "import.run belongs to the excluded import telemetry surface",
            _event("import.run", "succeeded", "mal-op-import", occurred_at=1000),
            "unknown_name",
            dict(base),
        ),
        tweak(
            "operation-nonstring",
            "operation_id must be a declared string",
            "operation_id",
            7,
            "unknown_name",
        ),
        tweak(
            "name-outcome-mismatch",
            "name claims succeeded while the outcome is failed",
            "name",
            "control_plane.identity.login.succeeded",
            "unknown_name",
            source=failed_base,
        ),
        tweak(
            "name-operation-mismatch",
            "name names a different operation than operation_id",
            "name",
            "control_plane.identity.logout.succeeded",
            "unknown_name",
        ),
        tweak(
            "name-wrong-prefix",
            "name must use the control_plane prefix",
            "name",
            "import.identity.login.succeeded",
            "unknown_name",
        ),
        tweak("name-nonstring", "name must be a string", "name", 1, "unknown_name"),
        row(
            "outcome-queued",
            "outcome is closed to succeeded/failed; name matches, so only the "
            "outcome check can refuse",
            _event("identity.login", "queued", "mal-outcome-queued", occurred_at=1000),
            "malformed_event",
            dict(base),
        ),
        tweak("outcome-nonstring", "outcome must be a string", "outcome", 1, source=None),
        tweak("occurred-at-minus-one", "occurred_at below 0", "occurred_at", -1),
        tweak(
            "occurred-at-past-max",
            "occurred_at past the 253402300799999 ceiling",
            "occurred_at",
            MAX_OCCURRED_AT + 1,
        ),
        tweak(
            "occurred-at-bool", "occurred_at must be an exact int, not bool", "occurred_at", True
        ),
        tweak(
            "occurred-at-float", "occurred_at must be an exact int, not float", "occurred_at", 1.0
        ),
        tweak(
            "occurred-at-string", "occurred_at must be an exact int, not str", "occurred_at", "0"
        ),
        tweak("event-id-empty", "event_id is 1-64 identifier characters", "event_id", ""),
        tweak("event-id-too-long", "event_id caps at 64 characters", "event_id", "a" * 65),
        tweak("event-id-space", "event_id allows no spaces", "event_id", "has space"),
        tweak("event-id-at-sign", "event_id allows no @", "event_id", "x@y"),
        tweak("event-id-non-ascii", "event_id is ASCII only", "event_id", "événement"),
        tweak(
            "event-id-trailing-newline",
            "event_id must fullmatch; a trailing newline is refused",
            "event_id",
            "abc\n",
        ),
        tweak("event-id-nonstring", "event_id must be a string", "event_id", 1),
        tweak(
            "correlation-id-empty",
            "correlation_id is 1-64 identifier characters",
            "correlation_id",
            "",
        ),
        tweak(
            "correlation-id-too-long",
            "correlation_id caps at 64 characters",
            "correlation_id",
            "c" * 65,
        ),
        tweak(
            "correlation-id-bool",
            "correlation_id must be a string",
            "correlation_id",
            False,
        ),
    ]
    for field in (
        "version",
        "name",
        "operation_id",
        "event_id",
        "occurred_at",
        "correlation_id",
        "outcome",
    ):
        event = dict(base)
        del event[field]
        rows.append(
            row(
                f"missing-{field.replace('_', '-')}",
                f"required field {field} is absent",
                event,
                "malformed_event",
                dict(base),
            )
        )
    rows += [
        row(
            "failed-missing-error-code",
            "a failed outcome requires error_code",
            {k: v for k, v in failed_base.items() if k != "error_code"},
            "malformed_event",
            dict(failed_base),
        ),
        tweak(
            "failed-undeclared-error-code",
            "quota_exhausted is not a declared error for identity.login",
            "error_code",
            "quota_exhausted",
            source=failed_base,
        ),
        tweak(
            "failed-error-code-nonstring",
            "error_code must be a declared string",
            "error_code",
            7,
            source=failed_base,
        ),
        tweak(
            "succeeded-with-error-code",
            "error_code is forbidden on success",
            "error_code",
            "internal",
        ),
        tweak(
            "extra-trace-field",
            "the envelope is closed; undeclared fields are refused",
            "trace_id",
            "trace-1",
        ),
        tweak(
            "chess-content-field",
            "chess content is prohibited in every field",
            "chess_content",
            "1. e4 e5 2. Nf3 Nc6",
        ),
        tweak(
            "fen-field",
            "a FEN is chess content and prohibited",
            "fen",
            "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        ),
        tweak(
            "pgn-field",
            "a PGN movetext is chess content and prohibited",
            "pgn",
            '[Event "x"] 1. d4 d5 1-0',
        ),
        tweak(
            "provider-key-field",
            "provider key material is prohibited and must not echo",
            "provider_key",
            "sk-live-9f8e7d6c5b4a-fixture",
        ),
        tweak(
            "credentials-field",
            "credentials are prohibited and must not echo",
            "password",
            "hunter2-fixture",
        ),
        tweak(
            "request-body-field",
            "request bodies are prohibited",
            "request_body",
            '{"operation_kind": "analysis"}',
        ),
        tweak(
            "email-field",
            "a raw email is prohibited and must not echo",
            "email",
            "user@example.com",
        ),
    ]
    return rows


def _rollback():
    def ev(op, outcome, tag, code=None, occurred_at=1000):
        return _event(op, outcome, tag, code=code, occurred_at=occurred_at)

    a = ev("identity.register", "succeeded", "rb-a", occurred_at=10)
    b = ev("quota.reserve", "failed", "rb-b", code="quota_exhausted", occurred_at=20)
    c = ev("entitlements.get", "succeeded", "rb-c", occurred_at=30)
    a_shifted = dict(a, occurred_at=11)
    bad_extra = dict(c, trace_id="trace-undeclared")
    unknown = _event("identity.restore", "succeeded", "rb-unknown", occurred_at=40)

    def row(name, why, published, batch, expect_failure, expect_published, follow_up, after, **kw):
        return {
            "name": name,
            "why": why,
            "published": published,
            "batch": batch,
            "inject_fault": kw.get("inject_fault", False),
            "restart": kw.get("restart", False),
            "expect_failure": expect_failure,
            "expect_published": expect_published,
            "follow_up": follow_up,
            "expect_after_follow_up": after,
        }

    return [
        row(
            "replay-identical-noop",
            "replaying the same ids with identical envelopes is a no-op",
            [[a, b]],
            [a, b],
            None,
            2,
            [c],
            3,
        ),
        row(
            "replay-across-restart",
            "the dedupe ledger survives a restart: identical replay stays a no-op",
            [[a, b]],
            [a, b],
            None,
            2,
            [c],
            3,
            restart=True,
        ),
        row(
            "duplicate-within-batch-identical",
            "the same envelope twice inside one batch publishes once",
            [],
            [a, a],
            None,
            1,
            [b],
            2,
        ),
        row(
            "conflict-with-ledger",
            "same event_id, different envelope: refused, nothing changes",
            [[a]],
            [a_shifted],
            "duplicate_conflict",
            1,
            [b],
            2,
        ),
        row(
            "conflict-within-batch",
            "same event_id twice with different envelopes: whole batch refused",
            [],
            [a, a_shifted],
            "duplicate_conflict",
            0,
            [a],
            1,
        ),
        row(
            "conflict-after-restart",
            "the replay ledger still refuses a conflicting envelope after restart",
            [[a]],
            [a_shifted],
            "duplicate_conflict",
            1,
            [b],
            2,
            restart=True,
        ),
        row(
            "mixed-valid-invalid-all-or-none",
            "one invalid event rolls back the whole batch; both then commit",
            [],
            [a, bad_extra],
            "malformed_event",
            0,
            [a, c],
            2,
        ),
        row(
            "publication-fault-rolls-back",
            "an injected publication fault leaves no partial batch; the same "
            "ids and envelopes then commit on retry",
            [],
            [a, b],
            "publication_failure",
            0,
            [a, b],
            2,
            inject_fault=True,
        ),
        row(
            "unknown-name-batch-all-or-none",
            "an undeclared operation in a batch rolls the valid events back too",
            [],
            [a, unknown],
            "unknown_name",
            0,
            [a],
            1,
        ),
    ]


def _canon(case):
    return json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def build():
    sections = {
        section: sorted(rows, key=lambda row: row["name"])
        for section, rows in (
            ("happy", _happy()),
            ("boundary", _boundary()),
            ("malformed", _malformed()),
            ("rollback", _rollback()),
        )
    }
    cases = {
        "schema_version": 1,
        "contract": CONTRACT,
        "contract_schema_version": yaml.safe_load((ROOT / CONTRACT).read_text())["schema_version"],
        "notes": NOTES,
        "section_manifests": {
            section: {case["name"]: hashlib.sha256(_canon(case)).hexdigest() for case in rows}
            for section, rows in sections.items()
        },
        **sections,
    }
    return cases


def dump(cases):
    return json.dumps(cases, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main():
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(dump(build()))
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
