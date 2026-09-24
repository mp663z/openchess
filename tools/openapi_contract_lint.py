#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0419: OpenAPI contract lint - exact structured pins, plus the
bindings to the linked control-plane contract and its merged mock.

Beyond the pins it checks, over the live sibling files:
- the status tables partition the control-plane closed enum exactly
  (contract-fixed, mock reading, unmapped), with unmapped in enum order;
- the mock file hashes to the pinned sha256, and its _err status
  literals inside declared operation handling equal the mock reading
  (plus the contract-fixed codes it also emits), one status per code,
  every emitted code inside the closed enum;
- the source error shape is exactly code/message/retryable, all
  required, with the pinned types;
- write_only fields occur only in request field tables;
- chess_content: forbidden - no field name at any depth of any request
  or response table (the sources of every derived schema property)
  contains a chess-content token.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract_lint_closure import (  # noqa: E402
    close_envelope,
    close_errors,
    close_failures,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data/contracts/openapi.yaml"
SECTIONS = {
    "auth",
    "document",
    "error_responses",
    "errors",
    "failures",
    "id",
    "idempotency",
    "links",
    "privacy",
    "properties",
    "role",
    "semantics",
    "source",
    "versioning",
}

ROLE = {
    "kind": "pure-derivation-of-one-openapi-3-1-document-from-the-linked-control-plane-contract",
    "serves": "public-product-http-api-description",
    "scope": "every-operation-of-the-linked-control-plane-contract-with-its-auth-idempotency-request-response-and-error-schemas",
    "owns": "the-derivation-rules-and-the-canonical-document-bytes",
    "not_scope": "serving-routing-validating-live-traffic-webhooks-or-any-operation-absent-from-the-linked-contract",
}

SOURCE = {
    "contract": "data/contracts/control-plane.yaml",
    "schema_version": 2,
    "exact": "every-section-read-by-the-derivation-is-validated-with-exact-types-and-exact-keys-first-failure-wins",
    "read": [
        "contract.versioning.base_path",
        "contract.transport.auth.public_operations",
        "contract.transport.read_only_operations",
        "contract.transport.errors.shape",
        "contract.transport.errors.closed_enum",
        "areas",
    ],
    "top_level": "exactly-schema-version-contract-and-areas",
    "grammars": {
        "base_path": "^/[a-z]+/v([1-9][0-9]{0,2})$",
        "area": "^[a-z][a-z_]{0,31}$",
        "op": "^[a-z][a-z_]{0,31}$",
        "path": "^(/[a-z0-9-]{1,32}){1,8}$",
        "field": "^[a-z][a-z0-9_]{0,63}$",
    },
    "bounds": {
        "object_depth": 8,
        "areas": 64,
        "ops_per_area": 64,
        "fields_per_object": 256,
        "example_integer_magnitude": 9007199254740991,
    },
    "op_keys": ["method", "path", "auth", "mutating", "request", "response", "errors"],
    "methods": ["GET", "POST"],
    "auth_values": ["public", "required"],
    "field_keys": ["type", "required", "example", "items", "fields", "write_only"],
    "field_types": ["string", "integer", "number", "boolean", "array", "object"],
    "item_types": ["string", "integer", "number", "boolean"],
    "area_extras": "fail-closed-reading-an-area-may-carry-notes-besides-ops-only-as-plain-strings",
    "errors_rule": "an-op-lists-one-or-more-distinct-codes-of-the-source-closed-enum-and-every-closed-enum-code-must-have-a-contract-fixed-mock-or-unmapped-reading",
    "rules": "an-op-with-method-get-must-be-read-only-and-declare-no-request-fields-an-object-field-has-fields-and-no-example-only-arrays-have-items-an-example-must-have-the-declared-type-a-bool-is-never-an-integer-or-number-fail-closed-reading-an-integer-or-number-example-lies-within-the-example-integer-magnitude-and-a-float-example-is-finite-so-the-document-bytes-stay-interoperable-json-a-string-or-list-example-is-copied-fail-closed-reading-every-string-example-and-string-array-item-is-utf-8-encodable-with-no-lone-surrogate-and-the-error-shape-code-example-is-one-of-the-closed-enum-codes",
}

DOCUMENT = {
    "openapi": "3.1.0",
    "top_level": ["openapi", "info", "servers", "paths", "components", "x-unmapped-error-codes"],
    "info": {
        "title": "replaceable-control-plane",
        "version": "the-major-of-the-source-base-path-as-a-decimal-string",
    },
    "servers": "exactly-one-server-whose-url-is-the-source-base-path",
    "paths": "one-path-item-per-operation-in-source-area-order-then-op-order-keyed-by-the-op-path-with-exactly-one-lowercase-method",
    "operation_fields": [
        "operationId",
        "security",
        "parameters",
        "requestBody",
        "responses",
        "x-unmapped-errors",
    ],
    "operation_id": "area-name-dot-op-name",
    "request_body": "post-operations-only-required-application-json-object-schema-get-operations-have-none-and-must-declare-no-request-fields",
    "success": "exactly-one-200-response-with-the-application-json-object-schema-of-the-response-fields",
    "schema": {
        "object": "type-object-properties-in-source-field-order-required-lists-the-required-fields-in-source-order-additional-properties-true",
        "string": "type-string",
        "integer": "type-integer",
        "number": "type-number",
        "boolean": "type-boolean",
        "array": "type-array-items-of-the-declared-scalar-item-type",
        "nested_object": "the-object-rule-applied-to-the-nested-fields",
        "example": "examples-holds-the-one-declared-example-when-present",
        "write_only": "write-only-fields-carry-write-only-true-and-may-appear-only-in-request-schemas",
    },
    "additional_properties": {
        "value": True,
        "request_grounding": "control-plane.yaml versioning.rule: A server MUST "
        "accept any client minor within its major and MUST "
        "ignore unknown request fields.",
        "response_grounding": "control-plane.yaml versioning.rule: MINOR is "
        "additive-only: new optional request fields, new "
        "response fields ... conformance never asserts "
        "absence of extra response fields; "
        "transport.errors.additive_note: Error payloads "
        "MAY carry extra fields beyond the declared shape "
        "(MINOR additive).",
    },
    "canonical_bytes": "utf-8-json-in-the-pinned-key-order-no-key-sorting-compact-separators-non-ascii-kept-no-trailing-newline",
}

AUTH = {
    "scheme": "components-security-schemes-bearer-auth-type-http-scheme-bearer",
    "required": "operations-with-auth-required-carry-security-bearer-auth",
    "public": "operations-listed-in-public-operations-carry-an-empty-security-list",
    "rule": "an-op-is-public-exactly-when-its-auth-is-public-and-it-is-listed-in-public-operations-else-the-source-is-malformed",
}

IDEMPOTENCY = {
    "parameter": "components-parameters-idempotency-key-in-header-name-idempotency-key-required-true-nonempty-string",
    "schema": '{type: string, minLength: 1, pattern: "\\\\S"}',
    "nonempty_reading": "control-plane.yaml transport.idempotency: 'Every mutating operation REQUIRES "
    "an Idempotency-Key header (unique nonempty string per logical operation).' "
    "The merged mock "
    "tools/control_plane_mock.py@sha256:111605f10523eb4662dc870e2c61ddb7659be46f5374ec3b50e428e7da06bcac "
    "answers 400 malformed_request when the key is not a str or key.strip() is "
    "empty, so nonempty is read as at least one non-whitespace character: "
    "minLength 1 plus pattern \\S.",
    "mutating": "every-mutating-operation-references-the-idempotency-key-parameter",
    "read_only": "an-operation-with-mutating-false-has-an-empty-parameters-list-and-must-be-listed-in-read-only-operations-and-vice-versa",
}

ERROR_RESPONSES = {
    "schema": "components-schemas-error-is-exactly-the-source-error-shape-code-message-retryable-all-required-code-enum-is-the-source-closed-enum-in-source-order",
    "responses": "each-mapped-error-code-of-an-op-lands-under-its-status-grouped-by-status-ascending-codes-in-op-order-in-x-error-codes-schema-ref-error",
    "status": {
        "contract_fixed": {"auth_expired": 401, "auth_invalid": 401, "idempotency_conflict": 409},
        "contract_grounding": "control-plane.yaml transport.auth.rule answers 401 for missing, "
        "unknown or expired tokens; transport.idempotency answers 409 "
        "idempotency_conflict.",
        "mock_reading": {
            "quota_exhausted": 429,
            "quota_reservation_expired": 409,
            "provider_key_invalid": 400,
            "malformed_request": 400,
            "not_found": 404,
            "conflict": 409,
        },
        "mock_source": {
            "path": "tools/control_plane_mock.py",
            "sha256": "111605f10523eb4662dc870e2c61ddb7659be46f5374ec3b50e428e7da06bcac",
            "reading": "the-merged-mock-is-the-executable-reading-of-every-status-the-contract-does-not-fix-taken-from-its-status-literals-inside-declared-operation-handling",
            "unknown_route": "the-mock-answers-an-unknown-method-and-path-with-malformed-request-404-that-answer-is-outside-every-declared-operation-and-is-not-carried-into-the-document",
        },
        "unmapped": [
            "entitlement_missing",
            "provider_unavailable",
            "cost_cap_exceeded",
            "rate_limited",
            "internal",
        ],
        "unmapped_rule": "fail-closed-reading-a-code-with-no-contract-fixed-status-that-the-mock-never-emits-gets-no-invented-status-it-is-listed-in-the-op-x-unmapped-errors-in-op-order-and-in-the-document-x-unmapped-error-codes-in-closed-enum-order-for-the-owner-batch",
        "closed": "a-mock-status-literal-for-a-code-outside-the-source-closed-enum-or-two-statuses-for-one-code-inside-declared-operations-fail-the-lint",
    },
}

PRIVACY = {
    "chess_content": "forbidden",
    "rule": "no-derived-schema-property-name-contains-a-chess-content-token-game-games-move-moves-fen-pgn-san-uci-analysis-note-notes-position-board-eval-split-on-underscore-checked-over-every-source-field-at-every-depth",
    "write_only": "a-write-only-field-never-appears-in-any-response-schema",
}

SEMANTICS = {
    "validation": "source-shape-then-cross-references-first-failure-wins",
    "atomic": "a-rejected-derivation-returns-no-document-and-leaves-the-source-bit-identical",
    "deterministic": "same-source-same-document-same-bytes",
    "detached": "the-document-shares-no-container-with-the-source",
    "rollback": "fail-closed-reading-a-pure-derivation-has-no-state-to-roll-back-rollback-is-read-as-atomic-rejection-and-an-unchanged-source",
}

PROPERTIES = {
    "total": "hostile-sources-fail-closed-typed-never-raw",
    "atomic": "a-rejected-derivation-leaves-the-source-bit-identical-and-returns-nothing",
    "deterministic": "byte-identical-output-for-equal-sources",
    "complete": "every-source-operation-appears-exactly-once",
    "faithful": "every-declared-field-type-required-flag-example-and-error-code-appears-in-the-document",
}

VERSIONING = {
    "base_path": "/contracts/openapi/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new "
    "endpoints. Never change the meaning of an existing field in place.",
}

LINKS = {
    "control_plane_contract": "data/contracts/control-plane.yaml",
    "control_plane_mock": "tools/control_plane_mock.py",
}

FAILURE_CLASSES = ["malformed_control_plane"]
FAILURE_TRIGGERS = {
    "malformed_control_plane": "source-shape-type-key-grammar-or-cross-reference-violation",
}
FAILURE_MAPPING = {"malformed_control_plane": "malformed_request"}
ERROR_ENUM = ["malformed_request", "internal"]
# the token list is read from the pinned privacy rule text, so the rule
# and the check cannot drift apart
CHESS_TOKENS = frozenset(
    PRIVACY["rule"].split("-token-", 1)[1].split("-split-on-underscore", 1)[0].split("-")
)
GROUNDING_PHRASES = (
    "MUST ignore unknown request fields",
    "MAY carry extra fields beyond the declared shape",
)
ERROR_FIELD_TYPES = {"code": "string", "message": "string", "retryable": "boolean"}
UNKNOWN_ROUTE_MESSAGE = "unknown operation"


def _has_surrogate(text):
    return any("\ud800" <= ch <= "\udfff" for ch in text)


def _check(name, expected, actual):
    if actual != expected:
        raise ContractError(f"{name} drifted: {actual!r}")


def _walk_fields(fields, where, out):
    """(path, name, spec) for every field at every depth."""
    if type(fields) is not dict:
        raise ContractError(f"{where}: fields must be a mapping")
    for name, spec in fields.items():
        if type(name) is not str or type(spec) is not dict:
            raise ContractError(f"{where}: field {name!r} malformed")
        out.append((where, name, spec))
        if spec.get("type") == "object":
            _walk_fields(spec.get("fields"), f"{where}.{name}", out)
    return out


def mock_statuses(path):
    """{code: {status, ...}} over the mock's _err calls with a literal
    code, excluding the unknown-route answer; the one call with a
    computed code (the bearer check) is returned under None."""
    tree = ast.parse(Path(path).read_text())
    out = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_err"
        ):
            continue
        status = [kw.value for kw in node.keywords if kw.arg == "status"]
        if (
            len(status) != 1
            or not isinstance(status[0], ast.Constant)
            or type(status[0].value) is not int
        ):
            raise ContractError(f"mock line {node.lineno}: status is not an int literal")
        msg = node.args[1] if len(node.args) > 1 else None
        if isinstance(msg, ast.Constant) and msg.value == UNKNOWN_ROUTE_MESSAGE:
            continue
        code = node.args[0] if node.args else None
        key = code.value if isinstance(code, ast.Constant) and type(code.value) is str else None
        out.setdefault(key, set()).add(status[0].value)
    return out


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="contracts-openapi", sections=SECTIONS)
    for name, expected in (
        ("role", ROLE),
        ("source", SOURCE),
        ("document", DOCUMENT),
        ("auth", AUTH),
        ("idempotency", IDEMPOTENCY),
        ("error_responses", ERROR_RESPONSES),
        ("privacy", PRIVACY),
        ("semantics", SEMANTICS),
        ("properties", PROPERTIES),
        ("versioning", VERSIONING),
        ("links", LINKS),
    ):
        _check(name, expected, cc.get(name))
    failures = cc["failures"]
    close_failures(failures)
    _check("failures.classes", FAILURE_CLASSES, failures.get("classes"))
    _check("failures.triggers", FAILURE_TRIGGERS, failures.get("triggers"))
    _check("failures.mapping", FAILURE_MAPPING, failures.get("mapping"))
    if failures.get("closed") is not True:
        raise ContractError("failure model must be closed")
    errors = cc["errors"]
    close_errors(errors, shape_keys=("retryable_true_only_for",))
    _check("errors.closed_enum", ERROR_ENUM, errors.get("closed_enum"))
    _check(
        "errors.shape.retryable_true_only_for",
        ["internal"],
        errors["shape"].get("retryable_true_only_for"),
    )
    if set(FAILURE_MAPPING.values()) | {"internal"} != set(ERROR_ENUM):
        raise ContractError("error enum must equal mapped codes + internal")
    for rel in cc["links"].values():
        if not (ROOT / rel).is_file():
            raise ContractError(f"linked file missing: {rel}")
    if cc["links"]["control_plane_contract"] != cc["source"]["contract"]:
        raise ContractError("source contract must be the linked control-plane contract")
    src_doc = yaml.safe_load((ROOT / cc["source"]["contract"]).read_text())
    if type(src_doc) is not dict or src_doc.get("schema_version") != cc["source"]["schema_version"]:
        raise ContractError("control-plane schema_version drifted")
    flat_text = " ".join((ROOT / cc["source"]["contract"]).read_text().split())
    for phrase in GROUNDING_PHRASES:
        if phrase not in flat_text:
            raise ContractError(f"additionalProperties grounding missing: {phrase!r}")
    src = src_doc["contract"]
    closed = src["transport"]["errors"]["closed_enum"]
    status = cc["error_responses"]["status"]
    fixed, mock, unmapped = status["contract_fixed"], status["mock_reading"], status["unmapped"]
    parts = [list(fixed), list(mock), list(unmapped)]
    flat = [c for part in parts for c in part]
    if len(flat) != len(set(flat)) or set(flat) != set(closed):
        raise ContractError("status tables must partition the control-plane closed enum")
    if unmapped != [c for c in closed if c in set(unmapped)]:
        raise ContractError("unmapped codes must be in closed-enum order")
    for table in (fixed, mock):
        for code, value in table.items():
            if type(value) is not int or not 400 <= value <= 599:
                raise ContractError(f"status for {code} must be an int 4xx/5xx")
    # the merged mock is the executable reading: pinned bytes, and its
    # literals inside declared operations say exactly the mock table
    mock_path = ROOT / status["mock_source"]["path"]
    if status["mock_source"]["path"] != cc["links"]["control_plane_mock"]:
        raise ContractError("mock source must be the linked mock")
    if (
        f"{status['mock_source']['path']}@sha256:{status['mock_source']['sha256']}"
        not in (cc["idempotency"]["nonempty_reading"])
    ):
        raise ContractError("idempotency nonempty reading must cite the pinned mock")
    digest = hashlib.sha256(mock_path.read_bytes()).hexdigest()
    if digest != status["mock_source"]["sha256"]:
        raise ContractError(f"mock sha256 drifted: {digest}")
    seen = mock_statuses(mock_path)
    computed = seen.pop(None, set())
    if computed and computed != {fixed["auth_invalid"]}:
        raise ContractError("the computed-code bearer answer must be the contract 401")
    for code, values in seen.items():
        if code not in closed:
            raise ContractError(f"mock emits {code!r} outside the closed enum")
        if len(values) != 1:
            raise ContractError(f"mock emits {code!r} with statuses {sorted(values)}")
    reading = {code: next(iter(values)) for code, values in seen.items()}
    for code, value in fixed.items():
        if code in reading and reading.pop(code) != value:
            raise ContractError(f"mock status for {code!r} contradicts the contract")
    if reading != mock:
        raise ContractError(f"mock reading drifted: {reading!r}")
    # error shape is the control-plane's, exactly
    shape = src["transport"]["errors"]["shape"]
    if list(shape) != ["error"] or list(shape["error"]) != ["fields"]:
        raise ContractError("control-plane error shape drifted")
    efields = shape["error"]["fields"]
    if list(efields) != list(ERROR_FIELD_TYPES) or any(
        efields[k].get("type") != t or efields[k].get("required") is not True
        for k, t in ERROR_FIELD_TYPES.items()
    ):
        raise ContractError("control-plane error fields drifted")
    code_example = efields["code"].get("example")
    if "example" in efields["code"] and code_example not in closed:
        raise ContractError("control-plane error code example is outside the closed enum")
    # privacy over every source field table
    for area, spec in src_doc["areas"].items():
        for op, ospec in spec["ops"].items():
            for side in ("request", "response"):
                for where, name, fspec in _walk_fields(
                    ospec[side]["fields"], f"{area}.{op}.{side}", []
                ):
                    if CHESS_TOKENS & set(name.lower().split("_")):
                        raise ContractError(f"{where}.{name}: chess content is forbidden")
                    if "write_only" in fspec and side != "request":
                        raise ContractError(f"{where}.{name}: write_only outside a request")
                    example = fspec.get("example")
                    items = example if type(example) is list else [example]
                    if any(type(v) is str and _has_surrogate(v) for v in items):
                        raise ContractError(f"{where}.{name}: example is not UTF-8 encodable")
    print(f"openapi contract lint ok: {path}")


if __name__ == "__main__":
    lint()
