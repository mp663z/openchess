#!/usr/bin/env python3
"""T0446: exact pins and source grounding for control-plane error classification."""
from __future__ import annotations

import sys

# ruff: noqa: E501  (exact contract literals)
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract_lint_closure import close_envelope, close_errors, close_failures  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data/contracts/control_plane_errors.yaml"
SOURCE = ROOT / "data/contracts/control-plane.yaml"
OPENAPI = ROOT / "data/contracts/openapi.yaml"
SECTIONS = {"id", "role", "source", "request", "result", "readings", "failures", "errors", "semantics", "versioning", "links"}
PINS = {
    "role": {
        "kind": "pure-http-error-payload-classifier",
        "serves": "replaceable-control-plane-consumers",
        "scope": "control-plane-http-status-and-its-json-error-payload",
        "owns": "classification-and-validation-of-declared-error-envelope",
        "not_scope": "individual-error-status-mapping-transport-retries-or-error-message-wording",
    },
    "source": {
        "contract": "data/contracts/control-plane.yaml",
        "schema_version": 2,
        "shape_path": "contract.transport.errors.shape",
        "enum_path": "contract.transport.errors.closed_enum",
        "additive_path": "contract.transport.errors.additive_note",
        "privacy_path": "contract.privacy",
    },
    "request": {
        "arguments": ["source", "status", "payload", "operation"],
        "status": "exact-built-in-integer-200-through-299-or-400-through-599-other-ranges-refused-as-unsupported-reading",
        "payload": "exact-built-in-dict-with-exact-built-in-str-keys-at-every-validated-object-level",
    },
    "result": {
        "success": "a-2xx-is-success-even-when-payload-has-an-error-property",
        "error": "a-4xx-or-5xx-requires-error-object-code-message-retryable-with-declared-types",
        "enum": "error-code-must-be-in-the-source-closed-enum",
        "additive": "unknown-extra-fields-are-permitted-in-error-and-top-level-payload",
        "privacy": "chess-content-checks-key-tokens-and-fen-values-apply-to-every-response-secret-name-checks-apply-to-all-but-declared-fields-san-move-text-and-key-shaped-values-are-stated-gaps",
        "detached": "returned-classification-is-a-fresh-dict-not-a-reference-to-payload",
    },
    "readings": {
        "unsupported_status": "fail-closed-reading-1xx-and-3xx-have-no-declared-payload-semantics",
        "message": "fail-closed-reading-message-is-a-utf8-encodable-string-with-no-lone-surrogates",
        "extra": "fail-closed-reading-extra-values-must-be-json-like-utf8-encodable-and-privacy-safe",
        "reference": "t0419-source-derivation-gates-error-shape-and-owns-status-grouping-while-this-classifier-reads-its-pinned-chess-token-rule",
        "fen": "every-response-string-value-checked-for-eight-rank-placement-window-derived-from-data-contracts-fen-yaml-including-glued-prefix-and-punctuation",
        "san_move_gap": "san-and-move-text-values-not-closed-until-t0392-token-run-rule-lands",
        "key_value_gap": "key-shaped-string-values-not-closed-without-owner-pattern",
    },
    "semantics": {
        "atomic": "rejected-classification-returns-nothing-and-leaves-inputs-bit-identical",
        "deterministic": "same-source-status-payload-same-classification",
        "validation": "source-then-status-then-payload-first-failure-wins",
        "rollback": "pure-classifier-no-state-to-roll-back",
    },
    "versioning": {
        "base_path": "/contracts/control-plane-errors/v1",
        "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new endpoints. Never change the meaning of an existing field in place.",
    },
    "links": {
        "control_plane_contract": "data/contracts/control-plane.yaml",
        "openapi_derivation": "data/contracts/openapi.yaml",
        "fen_contract": "data/contracts/fen.yaml",
    },
}
FAILURES = {
    "classes": ["malformed_error_result"],
    "triggers": {"malformed_error_result": "source-status-payload-shape-type-code-or-privacy-violation"},
    "mapping": {"malformed_error_result": "malformed_request"},
    "closed": True,
}
ERRORS = {"closed_enum": ["malformed_request", "internal"], "shape": {"retryable_true_only_for": ["internal"]}}
ENUM = [
    "auth_expired", "auth_invalid", "idempotency_conflict", "quota_exhausted",
    "quota_reservation_expired", "entitlement_missing", "provider_key_invalid",
    "provider_unavailable", "cost_cap_exceeded", "rate_limited", "malformed_request",
    "not_found", "conflict", "internal",
]
SHAPE = {
    "error": {"fields": {
        "code": {"type": "string", "required": True, "example": "not_found"},
        "message": {"type": "string", "required": True, "example": "human-readable detail"},
        "retryable": {"type": "boolean", "required": True, "example": False},
    }}
}


def lint(path: Path | None = None) -> None:
    path = path or CONTRACT
    cc = close_envelope(yaml.safe_load(Path(path).read_text()), contract_id="contracts-control-plane-errors", sections=SECTIONS)
    for name, expected in PINS.items():
        if type(cc[name]) is not dict or cc[name] != expected or list(cc[name]) != list(expected):
            raise ContractError(f"{name}: structured pin drift")
    close_failures(cc["failures"])
    close_errors(cc["errors"], shape_keys=("retryable_true_only_for",))
    if cc["failures"] != FAILURES or cc["errors"] != ERRORS or type(cc["failures"]["closed"]) is not bool:
        raise ContractError("failure/error pin drift")
    src = yaml.safe_load(SOURCE.read_text())
    if type(src) is not dict or type(src.get("schema_version")) is not int or src["schema_version"] != 2:
        raise ContractError("source schema drift")
    errors = src["contract"]["transport"]["errors"]
    if errors["shape"] != SHAPE or errors["closed_enum"] != ENUM:
        raise ContractError("source error shape or enum drift")
    phrase = " ".join(errors["additive_note"].split())
    for marker in ("MAY carry extra fields beyond the declared shape", "error signalling keys on the HTTP status only", "a 2xx payload containing an 'error' field is data"):
        if marker not in phrase:
            raise ContractError("source additive/status reading drift")
    privacy = src["contract"]["privacy"]
    if privacy["chess_content"] != "forbidden" or privacy["key_material"] != "write-only" or privacy["logs"] != "no-secrets":
        raise ContractError("source privacy drift")
    if not OPENAPI.is_file() or yaml.safe_load(OPENAPI.read_text())["contract"]["links"]["control_plane_contract"] != cc["source"]["contract"]:
        raise ContractError("OpenAPI link drift")
    fen = yaml.safe_load((ROOT / cc["links"]["fen_contract"]).read_text())["contract"]["placement"]
    if fen["rank_count"] != 8 or fen["rank_sum"] != 8 or fen["rank_separator"] != "/" or fen["piece_letters"] != list("pnbrqkPNBRQK") or fen["empty_run_digits"] != list("12345678"):
        raise ContractError("linked FEN placement grammar drift")
    print(f"control-plane errors lint ok: {path}")


if __name__ == "__main__":
    lint()
