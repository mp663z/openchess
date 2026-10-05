#!/usr/bin/env python3
"""T0420: deterministic generator for the closed OpenAPI derivation fixture.

Rebuilds tests/fixtures/openapi/cases.json from the landed derivation
contract (data/contracts/openapi.yaml) and its operation source
(data/contracts/control-plane.yaml). Every expectation is authored here from
the contract's structured rules; the generator never runs the T0419
reference or any implementation. Rows are exact mutation lists over the
shipped source (no embedded copy), so a row names the one edit it makes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "data/contracts/openapi.yaml"
SOURCE = "data/contracts/control-plane.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "openapi" / "cases.json"
FAILURE = "malformed_control_plane"
CODE = "malformed_request"

_SOURCE_DOC = yaml.safe_load((ROOT / SOURCE).read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")

NOTES = (
    "Closed fixture for the OpenAPI derivation contract "
    "(data/contracts/openapi.yaml), executed against the T0419 test-only "
    "reference derive() as a separate binding; a later implement task must "
    "rebind these rows to its production derivation. Expectations are "
    "authored from the structured contract, never computed by the reference. "
    "Rows are mutation lists over the shipped control-plane source, whose "
    "canonical digest is pinned in source_manifest."
)

REG = ["areas", "identity", "ops", "register"]
RESERVE = ["areas", "quota", "ops", "reserve"]
LOGIN = ["areas", "identity", "ops", "login"]
ENT = ["areas", "entitlements", "ops", "get"]
REG_REQ = [*REG, "request", "fields"]
REG_RESP = [*REG, "response", "fields"]
BASE_PATH = ["contract", "versioning", "base_path"]
ROOT_PATHS = ["paths"]


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _set(path, value):
    return {"set": {"path": path, "value": value}}


def _del(path):
    return {"del": {"path": path}}


def _append(path, value):
    return {"append": {"path": path, "value": value}}


def _field(kind="string", **extra):
    return {"type": kind, **extra}


def eq(at, value):
    return {"at": at, "equals": value}


def absent(at):
    return {"at": at, "absent": True}


def keys(at, ordered):
    return {"at": at, "keys": ordered}


def doc(*checks):
    return {"kind": "document", "checks": list(checks)}


REFUSAL = {"kind": "refusal"}


def row(name, why, mutations, expect, **extra):
    return {"name": name, "why": why, "mutations": mutations, "expect": expect, **extra}


def _op_ids():
    return [f"{a}.{o}" for a, spec in _SOURCE_DOC["areas"].items() for o in spec["ops"]]


ROUTES = [op["path"] for spec in _SOURCE_DOC["areas"].values() for op in spec["ops"].values()]
UNMAPPED_CODES = [
    "entitlement_missing",
    "provider_unavailable",
    "cost_cap_exceeded",
    "rate_limited",
    "internal",
]


def happy():
    return [
        row(
            "baseline-document-shape",
            "The shipped source derives the one 3.1.0 document with the pinned top level, "
            "info, server, route order and unmapped-code list.",
            [],
            doc(
                eq(["openapi"], "3.1.0"),
                keys(["paths"], ROUTES),
                eq(["info"], {"title": "replaceable-control-plane", "version": "1"}),
                eq(["servers"], [{"url": "/cp/v1"}]),
                eq(["x-unmapped-error-codes"], UNMAPPED_CODES),
                keys(
                    [],
                    ["openapi", "info", "servers", "paths", "components", "x-unmapped-error-codes"],
                ),
            ),
        ),
        row(
            "baseline-public-and-bearer-security",
            "Public operations carry an empty security list; auth-required ones "
            "reference bearerAuth.",
            [],
            doc(
                eq(["paths", "/identity/register", "post", "security"], []),
                eq(["paths", "/identity/login", "post", "security"], []),
                eq(["paths", "/identity/refresh", "post", "security"], [{"bearerAuth": []}]),
                eq(["paths", "/entitlements", "get", "security"], [{"bearerAuth": []}]),
                eq(
                    ["components", "securitySchemes"],
                    {"bearerAuth": {"type": "http", "scheme": "bearer"}},
                ),
            ),
        ),
        row(
            "baseline-idempotency-only-on-mutating",
            "Every mutating operation references the idempotency header; read-only ones have none.",
            [],
            doc(
                eq(
                    ["paths", "/quota/reserve", "post", "parameters"],
                    [{"$ref": "#/components/parameters/IdempotencyKey"}],
                ),
                eq(["paths", "/entitlements", "get", "parameters"], []),
                eq(["paths", "/billing/subscription", "get", "parameters"], []),
                eq(
                    ["components", "parameters", "IdempotencyKey"],
                    {
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1, "pattern": "\\S"},
                    },
                ),
                absent(["paths", "/entitlements", "get", "requestBody"]),
            ),
        ),
        row(
            "baseline-request-schema-rules",
            "Request properties keep source order; required lists required fields in source "
            "order; additionalProperties is true; examples are carried.",
            [],
            doc(
                eq(
                    ["paths", "/identity/register", "post", "requestBody", "required"],
                    True,
                ),
                keys(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "properties",
                    ],
                    ["email", "password_hash_client", "display_name"],
                ),
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "required",
                    ],
                    ["email", "password_hash_client"],
                ),
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "additionalProperties",
                    ],
                    True,
                ),
            ),
        ),
        row(
            "baseline-error-responses-grouped-by-status",
            "Contract-fixed statuses 401 and 409 hold their codes in op order; codes with no "
            "status stay unmapped, never invented.",
            [],
            doc(
                keys(
                    ["paths", "/identity/refresh", "post", "responses"],
                    ["200", "401", "409"],
                ),
                eq(
                    ["paths", "/identity/refresh", "post", "responses", "401", "x-error-codes"],
                    ["auth_expired", "auth_invalid"],
                ),
                eq(
                    ["paths", "/identity/refresh", "post", "responses", "409", "x-error-codes"],
                    ["idempotency_conflict"],
                ),
                eq(["paths", "/identity/refresh", "post", "x-unmapped-errors"], ["internal"]),
                eq(
                    ["paths", "/identity/register", "post", "x-unmapped-errors"],
                    ["rate_limited", "internal"],
                ),
            ),
        ),
        row(
            "new-optional-request-field",
            "A new optional request field is appended to the properties and left out of required.",
            [_set([*REG_REQ, "new_flag"], _field("boolean", required=False))],
            doc(
                keys(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "properties",
                    ],
                    ["email", "password_hash_client", "display_name", "new_flag"],
                ),
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "required",
                    ],
                    ["email", "password_hash_client"],
                ),
            ),
        ),
        row(
            "new-required-request-field",
            "A new required request field also lands in required, after the existing ones.",
            [_set([*REG_REQ, "new_flag"], _field("boolean", required=True, example=True))],
            doc(
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "required",
                    ],
                    ["email", "password_hash_client", "new_flag"],
                ),
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "properties",
                        "new_flag",
                    ],
                    {"type": "boolean", "examples": [True]},
                ),
            ),
        ),
        row(
            "write-only-request-field",
            "A write-only request field carries writeOnly true in the request schema.",
            [_set([*REG_REQ, "secret"], _field("string", required=False, write_only=True))],
            doc(
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "properties",
                        "secret",
                    ],
                    {"type": "string", "writeOnly": True},
                ),
            ),
        ),
        row(
            "new-response-field-is-optional",
            "A new optional response field appears in the success schema and not in required.",
            [_set([*REG_RESP, "trace_label"], _field("string", required=False))],
            doc(
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "responses",
                        "200",
                        "content",
                        "application/json",
                        "schema",
                        "required",
                    ],
                    ["account_id", "token", "token_expires_at"],
                ),
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "responses",
                        "200",
                        "content",
                        "application/json",
                        "schema",
                        "properties",
                        "trace_label",
                    ],
                    {"type": "string"},
                ),
            ),
        ),
        row(
            "base-path-major-two",
            "A major-2 base path becomes the one server URL and the decimal info version.",
            [_set(BASE_PATH, "/cp/v2")],
            doc(
                eq(["servers"], [{"url": "/cp/v2"}]),
                eq(["info", "version"], "2"),
            ),
        ),
    ]


def _deep(depth):
    node = {"type": "string", "required": False}
    for _ in range(depth):
        node = {"type": "object", "required": False, "fields": {"x": node}}
    return node


REG_SCHEMA = [
    "paths",
    "/identity/register",
    "post",
    "requestBody",
    "content",
    "application/json",
    "schema",
    "properties",
]


def _nest(levels):
    """register.request gains `box` holding `levels` wrapping objects above a string leaf."""
    inner = {"leaf": _field("string", required=False)}
    for _ in range(levels):
        inner = {"wrap": {"type": "object", "required": False, "fields": inner}}
    return _set([*REG_REQ, "box"], {"type": "object", "required": False, "fields": inner})


def _nest_leaf_path(levels):
    return [*REG_SCHEMA, "box", "properties", *(["wrap", "properties"] * levels), "leaf"]


def _new_op(path):
    return {
        "method": "POST",
        "path": path,
        "auth": "required",
        "mutating": True,
        "request": {"fields": {}},
        "response": {"fields": {}},
        "errors": ["malformed_request"],
    }


def _extra_areas(total):
    """Grow the source to exactly `total` areas (the shipped source has five)."""
    existing = 5
    return [
        _set(
            ["areas", f"extra_{chr(97 + i // 26)}{chr(97 + i % 26)}"],
            {"ops": {"do": _new_op(f"/extra/a{i}")}},
        )
        for i in range(total - existing)
    ]


def _extra_ops(total):
    """Grow quota to exactly `total` ops (the shipped quota area has three)."""
    existing = 3
    return [
        _set([*QUOTA_OPS, f"op_{chr(97 + i // 26)}{chr(97 + i % 26)}"], _new_op(f"/quota/x{i}"))
        for i in range(total - existing)
    ]


def _fields_of(total):
    return _set(
        [*RESERVE, "request", "fields"],
        {f"f{i}": _field("integer", required=False) for i in range(total)},
    )


QUOTA_OPS = ["areas", "quota", "ops"]
RESERVE_SCHEMA = [
    "paths",
    "/quota/reserve",
    "post",
    "requestBody",
    "content",
    "application/json",
    "schema",
    "properties",
]


def _single_code_mutations():
    muts = [
        _set(["contract", "transport", "errors", "closed_enum"], ["malformed_request"]),
        _set(
            ["contract", "transport", "errors", "shape", "error", "fields", "code", "example"],
            "malformed_request",
        ),
    ]
    for area, spec in _SOURCE_DOC["areas"].items():
        for op in spec["ops"]:
            muts.append(_set(["areas", area, "ops", op, "errors"], ["malformed_request"]))
    return muts


def boundary():
    return [
        row(
            "object-depth-at-ceiling",
            "Objects nested to exactly the depth ceiling of 8 are accepted.",
            [_nest(6)],
            doc(eq(_nest_leaf_path(6), {"type": "string"})),
        ),
        row(
            "object-depth-over-ceiling",
            "One object level beyond the ceiling of 8 is refused.",
            [_nest(7)],
            REFUSAL,
        ),
        row(
            "fields-per-object-at-ceiling",
            "An object with exactly 256 fields is accepted.",
            [_fields_of(256)],
            doc(
                eq([*RESERVE_SCHEMA, "f255"], {"type": "integer"}),
                absent([*RESERVE_SCHEMA, "f256"]),
            ),
        ),
        row(
            "fields-per-object-over-ceiling",
            "An object with 257 fields is refused.",
            [_fields_of(257)],
            REFUSAL,
        ),
        row(
            "areas-at-ceiling",
            "Exactly 64 areas are accepted.",
            _extra_areas(64),
            doc(eq(["paths", "/extra/a58", "post", "operationId"], "extra_cg.do")),
        ),
        row(
            "areas-over-ceiling",
            "65 areas are refused.",
            _extra_areas(65),
            REFUSAL,
        ),
        row(
            "ops-per-area-at-ceiling",
            "Exactly 64 operations in one area are accepted.",
            _extra_ops(64),
            doc(eq(["paths", "/quota/x60", "post", "operationId"], "quota.op_ci")),
        ),
        row(
            "ops-per-area-over-ceiling",
            "65 operations in one area are refused.",
            _extra_ops(65),
            REFUSAL,
        ),
        row(
            "example-integer-negative-at-magnitude",
            "The magnitude bound is inclusive on the negative side: -(2^53-1) is accepted.",
            [
                _set(
                    [*RESERVE, "request", "fields", "estimated_units", "example"], -9007199254740991
                )
            ],
            doc(eq([*RESERVE_SCHEMA, "estimated_units", "examples"], [-9007199254740991])),
        ),
        row(
            "example-non-ascii-string",
            "A non-ASCII string example is kept as written.",
            [_set([*REG_REQ, "email", "example"], "caf\u00e9 \u2603")],
            doc(eq([*REG_SCHEMA, "email", "examples"], ["caf\u00e9 \u2603"])),
        ),
        row(
            "array-example-copied",
            "A string array example is carried into the schema in order.",
            [
                _set(
                    [*REG_REQ, "tags"],
                    _field("array", required=False, items="string", example=["a", "b"]),
                )
            ],
            doc(eq([*REG_SCHEMA, "tags", "examples"], [["a", "b"]])),
        ),
        row(
            "single-code-closed-enum",
            "A closed enum of one code is accepted when every op and the shape use only it.",
            _single_code_mutations(),
            doc(
                eq(
                    ["paths", "/identity/register", "post", "responses", "400", "x-error-codes"],
                    ["malformed_request"],
                ),
                eq(["x-unmapped-error-codes"], []),
            ),
        ),
        row(
            "base-path-major-ceiling",
            "Major 999 is the largest three-digit major and is accepted.",
            [_set(BASE_PATH, "/cp/v999")],
            doc(eq(["info", "version"], "999"), eq(["servers"], [{"url": "/cp/v999"}])),
        ),
        row(
            "base-path-major-over-ceiling",
            "Major 1000 has four digits, outside the base path grammar.",
            [_set(BASE_PATH, "/cp/v1000")],
            REFUSAL,
        ),
        row(
            "base-path-major-zero",
            "Major 0 is outside [1-9][0-9]{0,2}.",
            [_set(BASE_PATH, "/cp/v0")],
            REFUSAL,
        ),
        row(
            "base-path-leading-zero-major",
            "A leading zero is outside the major grammar.",
            [_set(BASE_PATH, "/cp/v01")],
            REFUSAL,
        ),
        row(
            "example-integer-at-magnitude",
            "An integer example of exactly 2^53-1 is the largest interoperable example.",
            [_set([*RESERVE, "request", "fields", "estimated_units", "example"], 9007199254740991)],
            doc(
                eq(
                    [
                        "paths",
                        "/quota/reserve",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "properties",
                        "estimated_units",
                        "examples",
                    ],
                    [9007199254740991],
                ),
            ),
        ),
        row(
            "example-integer-over-magnitude",
            "An integer example of 2^53 is outside the interoperable range.",
            [_set([*RESERVE, "request", "fields", "estimated_units", "example"], 9007199254740992)],
            REFUSAL,
        ),
        row(
            "example-integer-negative-over-magnitude",
            "The magnitude bound is symmetric: -(2^53) is refused.",
            [
                _set(
                    [*RESERVE, "request", "fields", "estimated_units", "example"], -9007199254740992
                )
            ],
            REFUSAL,
        ),
        row(
            "field-name-at-64-characters",
            "A 64-character field name is the grammar ceiling.",
            [_set([*REG_REQ, "a" * 64], _field("string", required=False))],
            doc(
                eq(
                    [
                        "paths",
                        "/identity/register",
                        "post",
                        "requestBody",
                        "content",
                        "application/json",
                        "schema",
                        "properties",
                        "a" * 64,
                    ],
                    {"type": "string"},
                ),
            ),
        ),
        row(
            "field-name-over-64-characters",
            "A 65-character field name is outside the grammar.",
            [_set([*REG_REQ, "a" * 65], _field("string", required=False))],
            REFUSAL,
        ),
        row(
            "area-name-at-32-characters",
            "A 32-character area name is the grammar ceiling.",
            [
                _set(
                    ["areas", "b" * 32],
                    {
                        "ops": {
                            "ping": {
                                "method": "GET",
                                "path": "/ping",
                                "auth": "required",
                                "mutating": False,
                                "request": {"fields": {}},
                                "response": {"fields": {}},
                                "errors": ["internal"],
                            }
                        }
                    },
                ),
                _append(["contract", "transport", "read_only_operations"], "b" * 32 + ".ping"),
            ],
            doc(
                eq(["paths", "/ping", "get", "operationId"], "b" * 32 + ".ping"),
                eq(["paths", "/ping", "get", "parameters"], []),
            ),
        ),
        row(
            "area-name-over-32-characters",
            "A 33-character area name is outside the grammar.",
            [
                _set(
                    ["areas", "b" * 33],
                    {
                        "ops": {
                            "ping": {
                                "method": "GET",
                                "path": "/ping",
                                "auth": "required",
                                "mutating": False,
                                "request": {"fields": {}},
                                "response": {"fields": {}},
                                "errors": ["internal"],
                            }
                        }
                    },
                ),
                _append(["contract", "transport", "read_only_operations"], "b" * 33 + ".ping"),
            ],
            REFUSAL,
        ),
        row(
            "route-with-eight-segments",
            "A route of eight segments is the path grammar ceiling.",
            [_set([*LOGIN, "path"], "/a/b/c/d/e/f/g/h")],
            doc(eq(["paths", "/a/b/c/d/e/f/g/h", "post", "operationId"], "identity.login")),
        ),
        row(
            "route-with-nine-segments",
            "A route of nine segments is outside the grammar.",
            [_set([*LOGIN, "path"], "/a/b/c/d/e/f/g/h/i")],
            REFUSAL,
        ),
        row(
            "route-segment-at-32-characters",
            "A 32-character route segment is the ceiling.",
            [_set([*LOGIN, "path"], "/" + "s" * 32)],
            doc(eq(["paths", "/" + "s" * 32, "post", "operationId"], "identity.login")),
        ),
        row(
            "route-segment-over-32-characters",
            "A 33-character route segment is outside the grammar.",
            [_set([*LOGIN, "path"], "/" + "s" * 33)],
            REFUSAL,
        ),
    ]


def malformed():
    return [
        row(
            "schema-version-one",
            "Only schema_version 2 is read.",
            [_set(["schema_version"], 1)],
            REFUSAL,
        ),
        row(
            "schema-version-boolean-true",
            "A boolean is never the integer schema version.",
            [_set(["schema_version"], True)],
            REFUSAL,
        ),
        row(
            "missing-areas",
            "The top level is exactly schema_version, contract, areas.",
            [_del(["areas"])],
            REFUSAL,
        ),
        row(
            "extra-top-level-key",
            "An extra top-level key breaks the exact key set.",
            [_set(["extra"], 1)],
            REFUSAL,
        ),
        row(
            "base-path-uppercase",
            "The base path grammar is lowercase.",
            [_set(BASE_PATH, "/CP/v1")],
            REFUSAL,
        ),
        row(
            "base-path-trailing-newline",
            "A trailing newline is outside the anchored grammar.",
            [_set(BASE_PATH, "/cp/v1\n")],
            REFUSAL,
        ),
        row(
            "base-path-not-a-string",
            "The base path must be a string.",
            [_set(BASE_PATH, 1)],
            REFUSAL,
        ),
        row(
            "get-op-with-request-fields",
            "A GET operation must declare no request fields.",
            [_set([*ENT, "request", "fields"], {"q": _field("string", required=False)})],
            REFUSAL,
        ),
        row(
            "unknown-method",
            "Only GET and POST are declared methods.",
            [_set([*LOGIN, "method"], "DELETE")],
            REFUSAL,
        ),
        row(
            "unknown-auth-value",
            "auth is public or required.",
            [_set([*LOGIN, "auth"], "optional")],
            REFUSAL,
        ),
        row(
            "public-op-missing-from-public-list",
            "An op is public exactly when its auth is public and it is listed.",
            [_set(["contract", "transport", "auth", "public_operations"], ["identity.register"])],
            REFUSAL,
        ),
        row(
            "required-op-listed-public",
            "An auth-required op listed as public breaks the public rule.",
            [_append(["contract", "transport", "auth", "public_operations"], "identity.refresh")],
            REFUSAL,
        ),
        row(
            "read-only-flag-without-list-entry",
            "mutating false and the read-only list must agree both ways.",
            [
                _set(
                    ["contract", "transport", "read_only_operations"],
                    ["entitlements.get", "billing.get_subscription"],
                )
            ],
            REFUSAL,
        ),
        row(
            "error-code-outside-closed-enum",
            "An op error code outside the closed enum is malformed.",
            [_append([*LOGIN, "errors"], "teapot")],
            REFUSAL,
        ),
        row(
            "duplicate-error-code-in-op",
            "An op lists distinct error codes.",
            [_append([*LOGIN, "errors"], "internal")],
            REFUSAL,
        ),
        row(
            "example-bool-for-integer",
            "A boolean is never an integer example.",
            [_set([*RESERVE, "request", "fields", "estimated_units", "example"], True)],
            REFUSAL,
        ),
        row(
            "example-string-for-integer",
            "An example must have the declared type.",
            [_set([*RESERVE, "request", "fields", "estimated_units", "example"], "10")],
            REFUSAL,
        ),
        row(
            "example-lone-surrogate",
            "A string example must be UTF-8 encodable, with no lone surrogate.",
            [_set([*REG_REQ, "email", "example"], "\ud800")],
            REFUSAL,
        ),
        row(
            "array-example-with-a-bad-item",
            "Every item of an array example must have the declared item type.",
            [
                _set(
                    [*REG_REQ, "tags"],
                    _field("array", required=False, items="string", example=["a", 5]),
                )
            ],
            REFUSAL,
        ),
        row(
            "array-example-first-item-bad",
            "One valid later item does not excuse an invalid first item.",
            [
                _set(
                    [*REG_REQ, "tags"],
                    _field("array", required=False, items="string", example=[5, "a"]),
                )
            ],
            REFUSAL,
        ),
        row(
            "field-without-type",
            "A field descriptor must declare its type.",
            [_set([*REG_REQ, "nick"], {"required": False})],
            REFUSAL,
        ),
        row(
            "field-without-required",
            "A field descriptor must declare required.",
            [_set([*REG_REQ, "nick"], {"type": "string"})],
            REFUSAL,
        ),
        row(
            "closed-enum-empty",
            "The closed error enum must list at least one code.",
            [_set(["contract", "transport", "errors", "closed_enum"], [])],
            REFUSAL,
        ),
        row(
            "unknown-field-key",
            "A field descriptor carries only the declared keys.",
            [_set([*REG_REQ, "email", "format"], "email")],
            REFUSAL,
        ),
        row(
            "unknown-field-type",
            "Field types are a closed set.",
            [_set([*REG_REQ, "email", "type"], "uuid")],
            REFUSAL,
        ),
        row(
            "array-without-items",
            "Only arrays carry items and an array must declare them.",
            [_set([*REG_REQ, "tags"], _field("array", required=False))],
            REFUSAL,
        ),
        row(
            "items-on-a-string-field",
            "Only arrays have items.",
            [_set([*REG_REQ, "email", "items"], "string")],
            REFUSAL,
        ),
        row(
            "object-field-with-example",
            "An object field has fields and no example.",
            [
                _set(
                    [*REG_REQ, "prefs"],
                    {"type": "object", "required": False, "fields": {}, "example": {}},
                )
            ],
            REFUSAL,
        ),
        row(
            "write-only-in-a-response",
            "A write-only field may appear only in request schemas.",
            [_set([*REG_RESP, "secret"], _field("string", required=False, write_only=True))],
            REFUSAL,
        ),
        row(
            "chess-token-field-name",
            "No derived property name contains a chess content token.",
            [_set([*REG_REQ, "fen"], _field("string", required=False))],
            REFUSAL,
        ),
        row(
            "chess-token-nested-field-name",
            "The chess token rule holds at every depth.",
            [
                _set(
                    [*REG_REQ, "prefs"],
                    {
                        "type": "object",
                        "required": False,
                        "fields": {"best_move": _field("string", required=False)},
                    },
                )
            ],
            REFUSAL,
        ),
        row(
            "duplicate-route",
            "Two operations may not share a method and path.",
            [_set([*LOGIN, "path"], "/identity/register")],
            REFUSAL,
        ),
        row(
            "op-with-unknown-key",
            "An operation carries exactly the declared keys.",
            [_set([*LOGIN, "deprecated"], True)],
            REFUSAL,
        ),
        row(
            "hostile-source-is-a-list",
            "The source must be a mapping.",
            [{"replace_root": {"value": []}}],
            REFUSAL,
        ),
    ]


def rollback():
    return [
        row(
            "refusal-then-baseline-is-identical",
            "A refused derivation leaves no state: deriving the shipped source afterwards "
            "gives the pinned golden bytes.",
            [_set(BASE_PATH, "/cp/v0")],
            REFUSAL,
            then_baseline=True,
        ),
        row(
            "accept-then-refusal-then-accept",
            "An accepted derivation, a refusal and another accepted derivation do not "
            "disturb one another.",
            [_set(BASE_PATH, "/cp/v2")],
            doc(eq(["info", "version"], "2")),
            then_baseline=True,
        ),
        row(
            "late-failure-returns-no-partial-document",
            "The last operation is malformed: the whole derivation is refused, never a "
            "partial document.",
            [_set(["areas", "provider_routing", "ops", "route", "auth"], "optional")],
            REFUSAL,
            then_baseline=True,
        ),
    ]


def build():
    cases = {
        "schema_version": 1,
        "contract": CONTRACT,
        "contract_schema_version": yaml.safe_load((ROOT / CONTRACT).read_text())["schema_version"],
        "source": SOURCE,
        "notes": NOTES,
        "source_manifest": hashlib.sha256(canon(_SOURCE_DOC)).hexdigest(),
        "happy": happy(),
        "boundary": boundary(),
        "malformed": malformed(),
        "rollback": rollback(),
    }
    cases["section_manifests"] = {
        s: {r["name"]: hashlib.sha256(canon(r)).hexdigest() for r in cases[s]} for s in SECTIONS
    }
    return cases


def main():
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build(), indent=1) + "\n")


if __name__ == "__main__":
    main()
