#!/usr/bin/env python3
# ruff: noqa: E501  (exact contract literals and FEN/token-run probes)
"""T0447: deterministic generator for the closed control-plane errors fixture.

Rebuilds tests/fixtures/control-plane-errors/cases.json from the landed v1
errors contract (data/contracts/control_plane_errors.yaml) and its operation
source (data/contracts/control-plane.yaml). Every expectation is authored
here from the contract's structured rules; the generator never runs the
T0446 reference classifier or any implementation to compute a verdict. All
corpus values are plain JSON data: Python-level hostiles (lone surrogates,
non-string keys, NaN/infinity, built-in subclasses, plain objects) are out
of representational scope and are owned by the derived probes in
tests/test_t0447_control_plane_errors_fixture.py and the T0446 reference
battery.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "data/contracts/control_plane_errors.yaml"
SOURCE = "data/contracts/control-plane.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "control-plane-errors" / "cases.json"
FAILURE = "malformed_error_result"

_CONTRACT_DOC = yaml.safe_load((ROOT / CONTRACT).read_text())
_SOURCE_DOC = yaml.safe_load((ROOT / SOURCE).read_text())
ENUM = list(_SOURCE_DOC["contract"]["transport"]["errors"]["closed_enum"])
OPERATIONS = {
    f"{area}.{op}": spec
    for area, detail in _SOURCE_DOC["areas"].items()
    for op, spec in detail["ops"].items()
}

NOTES = (
    "Closed fixture for the v1 control-plane errors contract "
    "(data/contracts/control_plane_errors.yaml), executed against the "
    "T0446 test-only reference classify() as a separate binding; a later "
    "implement task (T0449) must independently implement the classifier and "
    "rebind these rows. Expectations are authored from the structured "
    "contract, never computed by the reference. No production semantics are "
    "shipped here: no HTTP middleware, no status-to-code mapping (T0419 "
    "owns status grouping), no retry schedule. Representative statuses on "
    "enum rows are fixture data, not a declared mapping. The source "
    "document is pinned by source_manifest; source mutations use set/del "
    "mutation lists materialized against the pinned document. All values "
    "are plain JSON data; Python-level hostile objects are out of "
    "representational scope and owned by the fixture battery's derived "
    "probes and the T0446 reference tests. Short SAN/move text and "
    "key-shaped values are declared owner gaps (contract result.privacy), "
    "pinned here as allowed rows. Secret-looking values exist only to "
    "prove typed refusal without echo."
)

# Representative statuses for the closed enum: fixture data, not a declared
# code-to-status mapping (the contract's role.not_scope leaves mapping to
# T0419). retryable is payload-declared per row.
ENUM_STATUS = {
    "auth_expired": 401,
    "auth_invalid": 401,
    "idempotency_conflict": 409,
    "quota_exhausted": 429,
    "quota_reservation_expired": 410,
    "entitlement_missing": 403,
    "provider_key_invalid": 400,
    "provider_unavailable": 503,
    "cost_cap_exceeded": 402,
    "rate_limited": 429,
    "malformed_request": 400,
    "not_found": 404,
    "conflict": 409,
    "internal": 500,
}
ENUM_RETRYABLE = {"provider_unavailable", "rate_limited", "internal"}

PLACEMENT = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"


def _envelope(code="not_found", message="fixture detail", retryable=False, error_extra=None, **top):
    error = {"code": code, "message": message, "retryable": retryable}
    if error_extra:
        error.update(error_extra)
    return {"error": error, **top}


def _verdict(payload):
    error = payload["error"]
    return {
        "kind": "error",
        "code": error["code"],
        "message": error["message"],
        "retryable": error["retryable"],
    }


SUCCESS = {"kind": "success"}


def _row(name, why, status, operation, payload, expect):
    return {
        "name": name,
        "why": why,
        "status": status,
        "operation": operation,
        "payload": payload,
        "expect": expect,
    }


def _vrow(name, why, status, operation, payload):
    """A verdict row whose expectation is authored from the payload."""
    return _row(name, why, status, operation, payload, _verdict(payload))


def _srow(name, why, status, operation, payload):
    return _row(name, why, status, operation, payload, SUCCESS)


def _mrow(name, why, status, operation, payload, source=None):
    return {
        "name": name,
        "why": why,
        "status": status,
        "operation": operation,
        "payload": payload,
        "source": source or [],
        "expect_failure": FAILURE,
    }


def _set(path, value):
    return {"set": {"path": path, "value": value}}


def _example(fields):
    out = {}
    for key, field in fields.items():
        if field["required"]:
            out[key] = _example(field["fields"]) if field["type"] == "object" else field["example"]
    return out


def _nest(depth):
    value = 1
    for _ in range(depth):
        value = {"x": value}
    return value


def _happy():
    rows = []
    for code in ENUM:
        status = ENUM_STATUS[code]
        retryable = code in ENUM_RETRYABLE
        payload = _envelope(code, f"fixture detail for {code}", retryable)
        rows.append(
            _vrow(
                f"enum-{code.replace('_', '-')}",
                f"every closed-enum member classifies at a representative status "
                f"({status}); status grouping is T0419's, retryable is payload-declared",
                status,
                None,
                payload,
            )
        )
    for op, spec in sorted(OPERATIONS.items()):
        rows.append(
            _srow(
                f"operation-{op.replace('.', '-')}-example",
                "the source-authored required-field response example of every "
                "declared operation classifies as success",
                200,
                op,
                _example(spec["response"]["fields"]),
            )
        )
    rows += [
        _srow(
            "success-2xx-with-error-property",
            "a 2xx is success even when the payload has an error property "
            "(contract result.success)",
            200,
            None,
            _envelope(),
        ),
        _srow(
            "success-clean-empty-payload",
            "an empty object at 2xx is success",
            200,
            None,
            {},
        ),
        _srow(
            "success-declared-token-exemption",
            "a declared 2xx response field such as identity.login's token is "
            "exempt only from the name-based secret check",
            200,
            "identity.login",
            {"token": "tok-1"},
        ),
        _srow(
            "success-declared-register-fields",
            "identity.register declares the same token field; declared fields "
            "pass structural and chess checks",
            200,
            "identity.register",
            {"account_id": "acct-1", "token": "tok-1", "token_expires_at": 1893456000},
        ),
        _srow(
            "success-key-token-run-32-allowed",
            "the 32-character token-run rule applies to error responses only; "
            "a 32-run key on a clean 2xx payload is allowed",
            200,
            "identity.login",
            {"token": "ok", "k" * 32: 1},
        ),
        _srow(
            "success-nested-key-token-run-32-allowed",
            "a nested 32-run key on a clean 2xx payload is allowed",
            200,
            "identity.login",
            {"token": "ok", "extra": {"a" * 32: 1}},
        ),
        _srow(
            "success-without-operation",
            "operation is optional; without it no declared-field exemption exists",
            200,
            None,
            {"account_id": "acct-1"},
        ),
        _vrow(
            "error-additive-extra-fields",
            "unknown extra fields are permitted in error and top-level payload "
            "(contract result.additive)",
            400,
            None,
            _envelope(error_extra={"support_id": "s-1"}, trace_id="trace-1"),
        ),
        _vrow(
            "error-null-extra-allowed",
            "a JSON null extra is JSON-like and allowed on the error path",
            400,
            None,
            _envelope(x=None),
        ),
        _vrow(
            "error-provider-kind-safe",
            "provider_kind is neither a chess token nor the provider+key "
            "intersection; it is allowed",
            400,
            None,
            _envelope(provider_kind="generic-a"),
        ),
    ]
    return rows


def _boundary():
    rows = [
        _srow("status-200-edge", "lowest supported success status", 200, None, {}),
        _srow("status-299-edge", "highest supported success status", 299, None, {}),
        _vrow("status-400-edge", "lowest supported error status", 400, None, _envelope()),
        _vrow("status-599-edge", "highest supported error status", 599, None, _envelope()),
        _srow(
            "depth-eight-nesting-accepted",
            "payload depth 0 with the last scalar at depth 8 is the deepest accepted nesting",
            200,
            None,
            _nest(8),
        ),
        _vrow(
            "dict-256-extras-accepted",
            "a 256-member object is the accepted collection bound",
            400,
            None,
            _envelope(extra={f"x{i}": i for i in range(256)}),
        ),
        _vrow(
            "list-256-extras-accepted",
            "a 256-element list is the accepted collection bound",
            400,
            None,
            _envelope(extra=list(range(256))),
        ),
        _vrow(
            "error-message-token-run-31-allowed",
            "a 31-character run is below the 32-character token-run bound",
            400,
            None,
            _envelope(message="A" * 31),
        ),
        _vrow(
            "error-key-token-run-31-allowed",
            "a 31-character key run is below the bound on the error path",
            400,
            None,
            _envelope(**{"k" * 31: "ok"}),
        ),
        _vrow(
            "error-token-run-broken-by-period",
            "a period is not a token-run character and breaks the run",
            400,
            None,
            _envelope(message="a" * 16 + "." + "a" * 16),
        ),
        _vrow(
            "error-token-run-broken-by-space",
            "a space breaks the run",
            400,
            None,
            _envelope(message="a" * 16 + " " + "a" * 16),
        ),
        _vrow(
            "error-token-run-broken-by-non-ascii",
            "a non-ASCII character breaks the run",
            400,
            None,
            _envelope(message="a" * 16 + "é" + "a" * 16),
        ),
        _srow(
            "fen-seven-ranks-allowed",
            "seven ranks is a near miss: no valid eight-rank window exists",
            200,
            None,
            {"extra": "8/8/8/8/8/8/8"},
        ),
        _srow(
            "fen-rank-sum-seven-allowed",
            "a rank sum below eight is a near miss",
            200,
            None,
            {"extra": "7/8/8/8/8/8/8/8"},
        ),
        _srow(
            "fen-rank-sum-nine-allowed",
            "every rank sums to nine, so no valid eight-rank window exists: "
            "a near miss that only a rank-sum-nine mutant refuses",
            200,
            None,
            {"extra": "8p/8p/8p/8p/8p/8p/8p/8p"},
        ),
        _srow(
            "fen-44-near-miss-allowed",
            "44/8/8/8/8/8/8/8 has no eight-rank substring whose ranks sum "
            "correctly (adjacent digits are not a run)",
            200,
            None,
            {"extra": "44/8/8/8/8/8/8/8"},
        ),
        _srow(
            "fen-middle-rank-bad-allowed",
            "one invalid middle rank makes the only candidate window a near miss",
            200,
            None,
            {"extra": "8/8/8/8/8/8/x/8"},
        ),
        _srow(
            "short-san-text-gap-allowed-2xx",
            "short SAN/move text is a declared owner gap (contract "
            "result.privacy): Nf3 in a 2xx value is allowed",
            200,
            None,
            {"extra": "Nf3"},
        ),
        _vrow(
            "short-san-text-gap-allowed-error",
            "short SAN/move text in an error message is allowed (declared gap)",
            400,
            None,
            _envelope(message="Nf3"),
        ),
    ]
    return rows


def _malformed():
    rows = []
    for status, label in (
        (0, "zero"),
        (100, "100"),
        (199, "199"),
        (300, "300"),
        (399, "399"),
        (600, "600"),
    ):
        rows.append(
            _mrow(
                f"status-{label}",
                "1xx and 3xx have no declared payload semantics and statuses "
                "outside 200-299/400-599 are refused as an unsupported reading",
                status,
                None,
                _envelope(),
            )
        )
    rows += [
        _mrow(
            "status-bool-true",
            "status must be an exact built-in int, not bool",
            True,
            None,
            _envelope(),
        ),
        _mrow(
            "status-float",
            "status must be an exact built-in int, not float",
            400.0,
            None,
            _envelope(),
        ),
        _mrow(
            "status-string",
            "status must be an exact built-in int, not str",
            "400",
            None,
            _envelope(),
        ),
        _mrow(
            "status-null", "status must be an exact built-in int, not null", None, None, _envelope()
        ),
        _mrow("payload-list", "payload must be an exact dict", 400, None, []),
        _mrow("payload-string", "payload must be an exact dict", 400, None, "error"),
        _mrow("payload-null", "payload must be an exact dict", 400, None, None),
        _mrow("error-missing", "a 4xx requires the error object", 400, None, {}),
        _mrow("error-null", "the error object must be a dict", 400, None, {"error": None}),
        _mrow("error-list", "the error object must be a dict", 400, None, {"error": []}),
        _mrow(
            "error-string",
            "the error object must be a dict",
            400,
            None,
            {"error": "code message retryable"},
        ),
        _mrow(
            "error-missing-code",
            "code is required",
            400,
            None,
            {"error": {"message": "fixture detail", "retryable": False}},
        ),
        _mrow(
            "error-missing-message",
            "message is required",
            400,
            None,
            {"error": {"code": "not_found", "retryable": False}},
        ),
        _mrow(
            "error-missing-retryable",
            "retryable is required",
            400,
            None,
            {"error": {"code": "not_found", "message": "fixture detail"}},
        ),
        _mrow("error-code-nonstring", "code must be a string", 400, None, _envelope(code=1)),
        _mrow(
            "error-code-outside-enum",
            "code must be in the source closed enum",
            400,
            None,
            _envelope(code="outside_enum"),
        ),
        _mrow(
            "error-message-nonstring",
            "message must be a string",
            400,
            None,
            _envelope(message=True),
        ),
        _mrow(
            "error-retryable-int",
            "retryable must be an exact bool, not int",
            400,
            None,
            _envelope(retryable=1),
        ),
        _mrow(
            "error-retryable-string",
            "retryable must be an exact bool",
            400,
            None,
            _envelope(retryable="no"),
        ),
        _mrow(
            "error-secret-key-material",
            "the error path gets full secret-name checks; key_material is refused",
            400,
            None,
            _envelope(error_extra={"key_material": "s3cr3t-fixture"}),
        ),
        _mrow(
            "error-chess-key-analysis",
            "chess-content key tokens are refused on every response",
            400,
            None,
            _envelope(error_extra={"analysis": "private"}),
        ),
        _mrow(
            "error-message-secret",
            "a secret named in the error message is refused",
            400,
            None,
            _envelope(message="key_material = s3cr3t-fixture"),
        ),
        _mrow(
            "error-message-password-hash",
            "password_hash_client named in the error message is refused",
            400,
            None,
            _envelope(message="password_hash_client leaked"),
        ),
        _mrow(
            "error-message-uppercase-secret",
            "the message secret check is case-insensitive",
            400,
            None,
            _envelope(message="KEY_MATERIAL=x"),
        ),
        _mrow(
            "top-level-secret-extra",
            "secret-named top-level extras are refused on the error path",
            400,
            None,
            _envelope(secret="s3cr3t-fixture"),
        ),
        _mrow(
            "top-level-password-hash-extra",
            "password_hash_client extras are refused",
            400,
            None,
            _envelope(password_hash_client="hunter2-fixture"),
        ),
        _mrow(
            "top-level-case-secret-key",
            "the secret-key check is case-insensitive",
            400,
            None,
            _envelope(Key_Material="s3cr3t-fixture"),
        ),
        _mrow(
            "top-level-token-extra-2xx-without-operation",
            "without an operation there are no declared fields, so a token key is refused on 2xx",
            200,
            None,
            {"token": "tok-1"},
        ),
        _mrow(
            "undeclared-token-2xx",
            "token is not a declared field of entitlements.get",
            200,
            "entitlements.get",
            {"token": "tok-1"},
        ),
        _mrow(
            "undeclared-token-error-with-operation",
            "the declared-field exemption never applies to the error path, "
            "even for the operation that declares token",
            400,
            "identity.login",
            _envelope(token="tok-1"),
        ),
        _mrow(
            "nested-declared-token-2xx",
            "the declared-field exemption is top-level only",
            200,
            "identity.login",
            {"x": {"token": "leak"}},
        ),
    ]
    for key, why in (
        ("gameMoves", "camelCase boundaries split before chess-token comparison"),
        ("GameMoves", "leading-uppercase camelCase splits the same way"),
        ("GAMEMoves", "acronym-to-title-case boundaries split"),
        ("game.moves", "dots split key tokens"),
        ("game-id", "hyphens split key tokens"),
        ("game.id", "dotted chess keys are refused"),
        ("top3Moves", "digit-to-uppercase boundaries split"),
        ("FENString", "acronym boundaries split before comparison"),
        ("providerKey", "the provider+key token intersection is refused"),
        ("provider.key", "the provider+key intersection splits on dots"),
    ):
        rows.append(
            _mrow(
                "chess-key-" + key.replace(".", "-dot-").replace("_", "-"),
                why,
                400,
                None,
                _envelope(**{key: "private"}),
            )
        )
    rows += [
        _mrow(
            "operation-three-segments",
            "operation must name exactly one area and one op",
            200,
            "identity.login.x",
            {},
        ),
        _mrow(
            "operation-unknown-operation",
            "the operation must exist in the source",
            200,
            "identity.nope",
            {},
        ),
        _mrow(
            "operation-unknown-area",
            "the area must exist in the source",
            200,
            "nope.login",
            {},
        ),
        _mrow(
            "operation-nonstring",
            "operation must be a string when present",
            200,
            7,
            {},
        ),
        _mrow(
            "depth-nine-nesting-refused",
            "a scalar at depth 9 exceeds the declared bound",
            200,
            None,
            _nest(9),
        ),
        _mrow(
            "dict-257-extras-refused",
            "a 257-member object exceeds the declared collection bound",
            400,
            None,
            _envelope(extra={f"x{i}": i for i in range(257)}),
        ),
        _mrow(
            "list-257-extras-refused",
            "a 257-element list exceeds the declared collection bound",
            400,
            None,
            _envelope(extra=list(range(257))),
        ),
        _mrow(
            "hostile-source-schema-version-bool",
            "a hostile source fails strict T0419 derivation before any classification",
            400,
            None,
            _envelope(),
            source=[_set(["schema_version"], True)],
        ),
        _mrow(
            "hostile-source-enum-nonstring-member",
            "a closed enum with a non-string member fails source validation",
            400,
            None,
            _envelope(),
            source=[_set(["contract", "transport", "errors", "closed_enum"], ["not_found", 1])],
        ),
        _mrow(
            "hostile-source-shape-required-flipped",
            "an error shape that no longer requires code fails source validation",
            400,
            None,
            _envelope(),
            source=[
                _set(
                    [
                        "contract",
                        "transport",
                        "errors",
                        "shape",
                        "error",
                        "fields",
                        "code",
                        "required",
                    ],
                    False,
                )
            ],
        ),
    ]
    for name, why, value, status, operation in (
        (
            "fen-plain-2xx",
            "a valid eight-rank placement in any response string value is refused",
            PLACEMENT,
            200,
            None,
        ),
        (
            "fen-glued-suffix-letter",
            "a placement glued to a letter suffix is still refused",
            PLACEMENT + "b",
            200,
            None,
        ),
        (
            "fen-glued-colon-prefix",
            "a placement after a colon prefix is refused; the scan is unanchored",
            "fen:" + PLACEMENT,
            200,
            None,
        ),
        (
            "fen-slash-prefix",
            "a valid window after a slash-separated prefix is refused",
            "x/" + PLACEMENT,
            200,
            None,
        ),
        (
            "fen-spaced-slash-prefix",
            "a valid window after a spaced slash prefix is refused",
            "1/2 " + PLACEMENT,
            200,
            None,
        ),
        (
            "fen-left-edge-digit",
            "a digit glued to the left edge does not hide a placement",
            "1" + PLACEMENT,
            200,
            None,
        ),
        (
            "fen-right-edge-digit",
            "a digit glued to the right edge does not hide a placement",
            PLACEMENT + "9",
            200,
            None,
        ),
        (
            "fen-trailing-piece",
            "a valid shorter window inside a greedy candidate is refused",
            "4k3/8/8/8/8/8/8/4K3p",
            200,
            None,
        ),
        (
            "fen-nine-ranks",
            "nine ranks containing a valid eight-rank window are refused",
            "8/8/8/8/8/8/8/8/8",
            200,
            None,
        ),
        (
            "fen-zero-digit-08",
            "a zero-digit prefix does not hide the valid suffix window",
            "08/8/8/8/8/8/8/8",
            200,
            None,
        ),
        (
            "fen-zero-digit-0p7",
            "a zero-digit piece prefix does not hide the valid suffix window",
            "0p7/8/8/8/8/8/8/8",
            200,
            None,
        ),
        ("fen-in-list-2xx", "placements inside list values are refused", [PLACEMENT], 200, None),
        (
            "fen-in-declared-token-2xx",
            "the declared-field exemption is name-only: chess-content checks still apply to declared values",
            PLACEMENT,
            200,
            "identity.login",
        ),
    ):
        payload = {"token": value} if name == "fen-in-declared-token-2xx" else {"extra": value}
        rows.append(_mrow(name, why, status, operation, payload))
    rows += [
        _mrow(
            "fen-in-error-message-4xx",
            "a placement in the error message is refused",
            400,
            None,
            _envelope(message=PLACEMENT),
        ),
        _mrow(
            "fen-nested-extra-4xx",
            "a placement nested inside extra structures is refused",
            400,
            None,
            _envelope(extra={"detail": ["x", PLACEMENT]}),
        ),
        _mrow(
            "error-message-token-run-32",
            "32 contiguous token-run characters in an error value are refused",
            400,
            None,
            _envelope(message="A" * 32),
        ),
        _mrow(
            "error-message-token-run-32-camelcase",
            "camelCase boundaries do not break a token run",
            400,
            None,
            _envelope(message="camelCase" + "X" * 23),
        ),
        _mrow(
            "error-message-token-run-32-digit",
            "digit boundaries do not break a token run",
            400,
            None,
            _envelope(message="top3Moves" + "Z" * 23),
        ),
        _mrow(
            "error-message-token-run-32-mixed-alphabet",
            "every declared token-run character joins a run",
            400,
            None,
            _envelope(message="a" * 16 + "+" + "a" * 15),
        ),
        _mrow(
            "error-key-token-run-32",
            "a 32-run top-level key on the error path is refused",
            400,
            None,
            _envelope(**{"k" * 32: "ok"}),
        ),
        _mrow(
            "error-key-token-run-32-dotted-prefix",
            "a dotted prefix does not hide a 32-run inside a key",
            400,
            None,
            _envelope(**{"x." + "k" * 32: "ok"}),
        ),
        _mrow(
            "error-key-token-run-32-nested",
            "a nested 32-run key on the error path is refused",
            400,
            None,
            _envelope(extra={"a" * 32: 1}),
        ),
        _mrow(
            "error-extra-token-run-32-nested-list",
            "a nested 32-run value on the error path is refused",
            400,
            None,
            _envelope(extra={"detail": ["a" * 32]}),
        ),
    ]
    return rows


def _rollback():
    """Rollback semantics for a pure classifier: no state exists, so every
    refusal is atomic and later classifications are unaffected. Each call is
    independent; the corpus pins the whole sequence."""

    def call(status, operation, payload, expect=None, failure=None, source=None):
        out = {"status": status, "operation": operation, "payload": payload, "source": source or []}
        if failure is not None:
            out["expect_failure"] = failure
        else:
            out["expect"] = expect
        return out

    nested = {"extra": {"detail": ["x", {"y": [1, 2, None]}]}}
    return [
        {
            "name": "refuse-then-classify-unchanged",
            "why": "a refusal carries no state: the same valid payloads "
            "classify identically afterwards",
            "calls": [
                call(300, None, _envelope(), failure=FAILURE),
                call(
                    400,
                    None,
                    _envelope("not_found", "fixture detail"),
                    expect=_verdict(_envelope("not_found", "fixture detail")),
                ),
                call(200, None, {"account_id": "acct-1"}, expect=SUCCESS),
            ],
        },
        {
            "name": "classify-refuse-classify",
            "why": "a refusal in the middle of a sequence leaves both sides "
            "of the sequence unchanged",
            "calls": [
                call(200, None, {"account_id": "acct-1"}, expect=SUCCESS),
                call(400, None, {}, failure=FAILURE),
                call(
                    503,
                    None,
                    _envelope("provider_unavailable", "down", True),
                    expect=_verdict(_envelope("provider_unavailable", "down", True)),
                ),
            ],
        },
        {
            "name": "repeated-refusals-fresh-errors",
            "why": "repeated identical refusals each yield a fresh typed "
            "error; the harness asserts the objects are distinct",
            "calls": [
                call(600, None, _envelope(), failure=FAILURE),
                call(600, None, _envelope(), failure=FAILURE),
                call(600, None, _envelope(), failure=FAILURE),
            ],
        },
        {
            "name": "refusal-leaves-nested-inputs-bit-identical",
            "why": "a refusal on an unsupported status leaves a nested "
            "payload bit-identical, and the same clean payload then "
            "classifies at a supported status",
            "calls": [
                call(300, None, nested, failure=FAILURE),
                call(200, None, nested, expect=SUCCESS),
            ],
        },
        {
            "name": "accepted-result-detached-from-payload",
            "why": "the returned classification is a fresh dict, not a "
            "reference into the payload (contract result.detached); the "
            "harness mutates a copy after the first call and re-runs it",
            "calls": [
                call(
                    404,
                    None,
                    _envelope("not_found", "fixture detail"),
                    expect=_verdict(_envelope("not_found", "fixture detail")),
                ),
                call(
                    404,
                    None,
                    _envelope("not_found", "fixture detail"),
                    expect=_verdict(_envelope("not_found", "fixture detail")),
                ),
            ],
        },
        {
            "name": "refuse-with-hostile-source-then-clean-source",
            "why": "a source-validation refusal poisons nothing: the pinned "
            "clean source classifies immediately afterwards",
            "calls": [
                call(
                    400, None, _envelope(), failure=FAILURE, source=[_set(["schema_version"], True)]
                ),
                call(
                    400,
                    None,
                    _envelope("conflict", "fixture detail for conflict"),
                    expect=_verdict(_envelope("conflict", "fixture detail for conflict")),
                ),
            ],
        },
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
        "contract_schema_version": _CONTRACT_DOC["schema_version"],
        "source": SOURCE,
        "source_manifest": hashlib.sha256(_canon(_SOURCE_DOC)).hexdigest(),
        "notes": NOTES,
        "section_manifests": {
            section: {row["name"]: hashlib.sha256(_canon(row)).hexdigest() for row in rows}
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
