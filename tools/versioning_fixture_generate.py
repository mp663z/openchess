#!/usr/bin/env python3
"""T0438: deterministic generator for the closed versioning fixture corpus.

Rebuilds tests/fixtures/control-plane-versioning/cases.json from the landed
v1 versioning contract (data/contracts/control_plane_versioning.yaml) and its
operation source (data/contracts/control-plane.yaml). Every expectation is
authored here from the contract's structured rules; the generator never runs
the T0437 reference or any implementation to compute a verdict. The corpus
carries the base snapshot once plus exact mutation lists that materialize
the old/new snapshots deterministically; embedding two full ~900-line
snapshots per row would duplicate the same document over a hundred times
without adding information.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "data/contracts/control_plane_versioning.yaml"
SOURCE = "data/contracts/control-plane.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "control-plane-versioning" / "cases.json"
FAILURE = "malformed_version_request"
CEILING = 2147483647

_CONTRACT_DOC = yaml.safe_load((ROOT / CONTRACT).read_text())
_SOURCE_DOC = yaml.safe_load((ROOT / SOURCE).read_text())

NOTES = (
    "Closed fixture for the v1 control-plane versioning contract "
    "(data/contracts/control_plane_versioning.yaml), executed against the "
    "T0437 test-only reference as a separate binding; a later implement "
    "task must rebind these rows to its production compare. Expectations "
    "are authored from the structured contract, never computed by the "
    "reference. base_snapshot is the single embedded source document; each "
    "row's old/new lists are exact mutations materializing that row's "
    "snapshots. All values are plain JSON data; Python-level hostile "
    "objects are out of representational scope and owned by the T0437 "
    "reference tests."
)

# -- paths into the source document -------------------------------------------

SCHEMA_VERSION = ["schema_version"]
BASE_PATH = ["contract", "versioning", "base_path"]
RULE = ["contract", "versioning", "rule"]
READ_ONLY = ["contract", "transport", "read_only_operations"]
PUBLIC = ["contract", "transport", "auth", "public_operations"]
REG_REQ = ["areas", "identity", "ops", "register", "request", "fields"]
REG_RESP = ["areas", "identity", "ops", "register", "response", "fields"]
LOGIN = ["areas", "identity", "ops", "login"]
LOGIN_RESP = ["areas", "identity", "ops", "login", "response", "fields"]
REFRESH = ["areas", "identity", "ops", "refresh"]
ENT_GET = ["areas", "entitlements", "ops", "get"]
ENT_CAPS = ["areas", "entitlements", "ops", "get", "response", "fields", "cost_caps"]
ENT_CAPS_FIELDS = ENT_CAPS + ["fields"]
ROUTE_POLICY_FIELDS = [
    "areas",
    "provider_routing",
    "ops",
    "route",
    "request",
    "fields",
    "policy",
    "fields",
]
BILLING_META = ["areas", "billing", "pci_boundary"]


def _get(path):
    node = _SOURCE_DOC
    for key in path:
        node = node[key]
    return copy.deepcopy(node)


# -- mutation constructors ----------------------------------------------------


def _set(path, value):
    return {"set": {"path": path, "value": value}}


def _del(path):
    return {"del": {"path": path}}


def _append(path, value):
    return {"append": {"path": path, "value": value}}


def _remove(path, value):
    return {"remove": {"path": path, "value": value}}


def _extend(path, values):
    return {"extend": {"path": path, "values": values}}


def _reverse(path):
    return {"reverse": {"path": path}}


def _row(name, why, *, new=None, old=None, minors=(0, 1), expect=True):
    return {
        "name": name,
        "why": why,
        "old": old or [],
        "new": new or [],
        "old_minor": minors[0],
        "new_minor": minors[1],
        "expect": expect,
    }


def _op(op_path, **changes):
    spec = _get(op_path)
    spec.update(changes)
    return spec


MAJOR_V2 = [_set(BASE_PATH, "/cp/v2")]
NEW_FLAG_OPT = {"type": "boolean", "required": False, "example": True}
NEW_FLAG_REQ = {"type": "boolean", "required": True, "example": True}


def _happy():
    rows = [
        _row(
            "optional-request-field-added",
            "MINOR: a new optional request field",
            new=[_set(REG_REQ + ["new_flag"], NEW_FLAG_OPT)],
            minors=(3, 4),
        ),
        _row(
            "optional-response-field-added",
            "MINOR: a new optional response field",
            new=[_set(REG_RESP + ["avatar_url"], {"type": "string", "required": False})],
            minors=(3, 4),
        ),
        _row(
            "nested-optional-request-field-added",
            "MINOR: a new optional field inside a nested request object",
            new=[
                _set(
                    ROUTE_POLICY_FIELDS + ["timeout_seconds"],
                    {"type": "integer", "required": False},
                )
            ],
            minors=(1, 2),
        ),
        _row(
            "nested-optional-response-field-added",
            "MINOR: a new optional field inside a nested response object",
            new=[
                _set(
                    ENT_CAPS_FIELDS + ["display_currency"],
                    {"type": "string", "required": False},
                )
            ],
            minors=(1, 2),
        ),
        _row(
            "new-readonly-operation-with-allowlist",
            "MINOR: a new read-only operation at a new route, listed in read_only_operations",
            new=[
                _set(
                    ["areas", "entitlements", "ops", "refresh_status"],
                    _op(ENT_GET, path="/entitlements/refresh-status"),
                ),
                _append(READ_ONLY, "entitlements.refresh_status"),
            ],
        ),
        _row(
            "new-public-operation-with-allowlist",
            "MINOR: a new public operation at a new route, listed in public_operations",
            new=[
                _set(
                    ["areas", "identity", "ops", "recover"],
                    _op(LOGIN, path="/identity/recover"),
                ),
                _append(PUBLIC, "identity.recover"),
            ],
        ),
        _row(
            "new-mutating-operation-no-allowlist-change",
            "MINOR: a new authenticated mutating operation joins no allowlist",
            new=[
                _set(
                    ["areas", "identity", "ops", "restore"],
                    _op(REFRESH, path="/identity/restore"),
                )
            ],
        ),
        _row(
            "new-area-with-operation-and-allowlist",
            "coordinator ruling: a new area with new operations is a MINOR "
            "addition, allowlist extended to match",
            new=[
                _set(["areas", "zones"], {"ops": {"get": _op(ENT_GET, path="/zones")}}),
                _append(READ_ONLY, "zones.get"),
            ],
        ),
        _row(
            "multiple-new-operations-reverse-allowlist-order",
            "allowlists are sets: several new operations may be appended in any order",
            new=[
                _set(
                    ["areas", "entitlements", "ops", "summary"],
                    _op(ENT_GET, path="/entitlements/summary"),
                ),
                _set(
                    ["areas", "entitlements", "ops", "totals"],
                    _op(ENT_GET, path="/entitlements/totals"),
                ),
                _extend(READ_ONLY, ["entitlements.totals", "entitlements.summary"]),
            ],
        ),
        _row(
            "allowlist-order-insignificant",
            "allowlists are sets in meaning; reversing both lists is no change",
            new=[_reverse(READ_ONLY), _reverse(PUBLIC)],
        ),
        _row(
            "major-required-request-field",
            "a higher MAJOR at a new base path admits a required request field",
            new=[_set(REG_REQ + ["new_flag"], NEW_FLAG_REQ), *MAJOR_V2],
            minors=(3, 0),
        ),
        _row(
            "major-optional-to-required-request",
            "a higher MAJOR admits optional-to-required on a request field",
            new=[_set(REG_REQ + ["display_name", "required"], True), *MAJOR_V2],
            minors=(3, 0),
        ),
        _row(
            "major-optional-to-required-response",
            "a higher MAJOR admits optional-to-required on a response field",
            new=[
                _set(REG_RESP + ["display_name", "required"], True),
                _set(REG_RESP + ["display_name", "example"], "Ada"),
                *MAJOR_V2,
            ],
            minors=(3, 0),
        ),
        _row(
            "major-field-removed",
            "a higher MAJOR admits removing a request field",
            new=[_del(REG_REQ + ["display_name"]), *MAJOR_V2],
            minors=(3, 0),
        ),
        _row(
            "major-operation-removed",
            "a higher MAJOR admits removing an operation",
            new=[_del(REFRESH), *MAJOR_V2],
            minors=(0, 0),
        ),
        _row(
            "major-area-removed",
            "a higher MAJOR admits removing a whole area",
            new=[_del(["areas", "quota"]), *MAJOR_V2],
            minors=(0, 0),
        ),
        _row(
            "major-operation-path-changed",
            "a higher MAJOR admits moving an operation's route",
            new=[_set(REFRESH + ["path"], "/identity/renew"), *MAJOR_V2],
            minors=(0, 0),
        ),
        _row(
            "major-contract-text-changed",
            "a higher MAJOR admits changing non-operation contract text",
            new=[_set(RULE, _get(RULE) + " More text."), *MAJOR_V2],
            minors=(0, 0),
        ),
        _row(
            "major-base-path-v10",
            "multi-digit MAJOR base paths are valid transitions",
            new=[_set(BASE_PATH, "/cp/v10")],
            minors=(0, 0),
        ),
    ]
    return rows


def _boundary():
    return [
        _row("equal-snapshots-zero-zero", "identical snapshots at minor 0", minors=(0, 0)),
        _row("equal-snapshots-same-minor", "identical snapshots at one minor", minors=(3, 3)),
        _row(
            "equal-snapshots-minor-step",
            "identical snapshots may still advance the minor",
            minors=(3, 4),
        ),
        _row(
            "equal-snapshots-to-ceiling",
            "the new minor may reach the exact 2147483647 ceiling",
            minors=(0, CEILING),
        ),
        _row(
            "equal-snapshots-at-ceiling",
            "both minors may sit at the exact ceiling",
            minors=(CEILING, CEILING),
        ),
        _row(
            "major-upgrade-minor-reset",
            "a MAJOR transition resets the minor to 0",
            new=MAJOR_V2,
            minors=(3, 0),
        ),
        _row(
            "major-upgrade-from-ceiling",
            "the old-minor ceiling holds even across a MAJOR transition",
            new=MAJOR_V2,
            minors=(CEILING, 0),
        ),
    ]


def _incompatible():
    return [
        _row(
            "required-request-field-added",
            "a new required request field is not on the MINOR allowlist",
            new=[_set(REG_REQ + ["new_flag"], NEW_FLAG_REQ)],
            minors=(3, 4),
            expect=False,
        ),
        _row(
            "required-response-field-added",
            "a new required response field is not on the MINOR allowlist",
            new=[
                _set(
                    REG_RESP + ["session_id"],
                    {"type": "string", "required": True, "example": "s1"},
                )
            ],
            minors=(3, 4),
            expect=False,
        ),
        _row(
            "optional-to-required-request",
            "optional-to-required on a request field needs a new MAJOR",
            new=[_set(REG_REQ + ["display_name", "required"], True)],
            minors=(3, 4),
            expect=False,
        ),
        _row(
            "optional-to-required-response",
            "optional-to-required on a response field needs a new MAJOR",
            new=[
                _set(REG_RESP + ["display_name", "required"], True),
                _set(REG_RESP + ["display_name", "example"], "Ada"),
            ],
            minors=(3, 4),
            expect=False,
        ),
        _row(
            "nested-required-response-field-added",
            "a new required field inside a nested response object is not MINOR",
            new=[
                _set(
                    ENT_CAPS_FIELDS + ["display_currency"],
                    {"type": "string", "required": True, "example": "USD"},
                )
            ],
            minors=(1, 2),
            expect=False,
        ),
        _row(
            "nested-required-request-field-added",
            "a new required field inside a nested request object is not MINOR",
            new=[
                _set(
                    ROUTE_POLICY_FIELDS + ["timeout_seconds"],
                    {"type": "integer", "required": True, "example": 30},
                )
            ],
            minors=(1, 2),
            expect=False,
        ),
        _row(
            "request-field-removed",
            "removing a request field breaks existing clients",
            new=[_del(REG_REQ + ["email"])],
            expect=False,
        ),
        _row(
            "response-field-removed",
            "removing a response field breaks existing clients",
            new=[_del(LOGIN_RESP + ["token"])],
            expect=False,
        ),
        _row(
            "field-type-changed",
            "changing a field's declared type breaks existing clients",
            new=[_set(REG_REQ + ["email"], {"type": "integer", "required": True, "example": 1})],
            expect=False,
        ),
        _row(
            "field-example-changed",
            "an existing field's declaration is immutable, example included",
            new=[_set(REG_REQ + ["email", "example"], "other@example.test")],
            expect=False,
        ),
        _row(
            "object-metadata-changed",
            "a nested object's own declaration is immutable in a MINOR",
            new=[_set(ENT_CAPS + ["required"], False)],
            expect=False,
        ),
        _row(
            "operation-removed",
            "removing an operation is not MINOR",
            new=[_del(REFRESH)],
            expect=False,
        ),
        _row(
            "area-removed",
            "removing an area is not MINOR",
            new=[_del(["areas", "quota"])],
            expect=False,
        ),
        _row(
            "operation-path-changed",
            "moving an existing operation's route is not MINOR",
            new=[_set(REFRESH + ["path"], "/identity/renew")],
            expect=False,
        ),
        _row(
            "operation-errors-removed",
            "an existing operation's declared errors are immutable",
            new=[_remove(REFRESH + ["errors"], "idempotency_conflict")],
            expect=False,
        ),
        _row(
            "contract-rule-text-changed",
            "non-operation contract declarations are immutable in a MINOR",
            new=[_set(RULE, _get(RULE) + " More text.")],
            expect=False,
        ),
        _row(
            "area-metadata-changed",
            "area metadata outside ops is immutable in a MINOR",
            new=[_set(BILLING_META, _get(BILLING_META) + " New text.")],
            expect=False,
        ),
    ]


def _malformed():
    rows = [
        _row("old-minor-negative", "minors are nonnegative", minors=(-1, 0), expect=FAILURE),
        _row("new-minor-negative", "minors are nonnegative", minors=(0, -1), expect=FAILURE),
        _row(
            "old-minor-over-ceiling",
            "the 2147483647 ceiling is inclusive, one past it refuses even "
            "when a MAJOR transition would make the verdict True",
            new=MAJOR_V2,
            minors=(CEILING + 1, 0),
            expect=FAILURE,
        ),
        _row(
            "new-minor-over-ceiling",
            "the ceiling binds the new minor too",
            minors=(0, CEILING + 1),
            expect=FAILURE,
        ),
        _row(
            "old-minor-bool", "a bool is not an exact minor int", minors=(True, 1), expect=FAILURE
        ),
        _row(
            "new-minor-bool", "a bool is not an exact minor int", minors=(0, True), expect=FAILURE
        ),
        _row(
            "new-minor-float", "a float is not an exact minor int", minors=(0, 1.0), expect=FAILURE
        ),
        _row(
            "old-minor-string",
            "a string is not an exact minor int",
            minors=("1", 1),
            expect=FAILURE,
        ),
        _row("new-minor-null", "null is not an exact minor int", minors=(0, None), expect=FAILURE),
        _row(
            "minor-downgrade-same-major",
            "within one MAJOR the minor may not go backwards",
            minors=(2, 1),
            expect=FAILURE,
        ),
        _row(
            "major-downgrade",
            "a lower MAJOR base path in the new snapshot refuses",
            old=MAJOR_V2,
            expect=FAILURE,
        ),
        _row(
            "precedence-minor-bound-beats-comparison",
            "validation runs before any comparison: a minor downgrade refuses "
            "typed even when the snapshots also carry a breaking change",
            new=[_set(REG_REQ + ["new_flag"], NEW_FLAG_REQ)],
            minors=(2, 1),
            expect=FAILURE,
        ),
        _row(
            "schema-version-bool",
            "the source schema_version is an exact int",
            new=[_set(SCHEMA_VERSION, True)],
            expect=FAILURE,
        ),
        _row(
            "schema-version-float",
            "the source schema_version is an exact int",
            new=[_set(SCHEMA_VERSION, 2.0)],
            expect=FAILURE,
        ),
        _row(
            "schema-version-string",
            "the source schema_version is an exact int",
            new=[_set(SCHEMA_VERSION, "2")],
            expect=FAILURE,
        ),
        _row(
            "schema-version-three",
            "only control-plane schema v2 documents are comparable",
            new=[_set(SCHEMA_VERSION, 3)],
            expect=FAILURE,
        ),
        _row(
            "base-path-junk",
            "the base path must match the pinned /cp/v<n> grammar",
            new=[_set(BASE_PATH, "/cp/v1junk")],
            expect=FAILURE,
        ),
        _row(
            "base-path-v0",
            "MAJOR numbering starts at 1",
            new=[_set(BASE_PATH, "/cp/v0")],
            expect=FAILURE,
        ),
        _row(
            "base-path-nonstring",
            "the base path is a string",
            new=[_set(BASE_PATH, 1)],
            expect=FAILURE,
        ),
        _row(
            "versioning-section-missing",
            "a snapshot without its versioning section is not a source contract",
            new=[_del(["contract", "versioning"])],
            expect=FAILURE,
        ),
        _row(
            "areas-section-missing",
            "a snapshot without areas is not a source contract",
            new=[_del(["areas"])],
            expect=FAILURE,
        ),
        _row(
            "field-required-nonbool",
            "a field's required flag is an exact bool",
            new=[_set(REG_REQ + ["new_flag"], {"type": "string", "required": "yes"})],
            expect=FAILURE,
        ),
        _row(
            "field-type-undeclared",
            "a field's type must be one of the declared scalar kinds",
            new=[_set(REG_REQ + ["new_flag"], {"type": "money", "required": False})],
            expect=FAILURE,
        ),
        _row(
            "mutating-nonbool",
            "an operation's mutating flag is an exact bool",
            new=[_set(REFRESH + ["mutating"], "yes")],
            expect=FAILURE,
        ),
        _row(
            "auth-undeclared-value",
            "an operation's auth is one of the declared values",
            new=[_set(REFRESH + ["auth"], "sometimes")],
            expect=FAILURE,
        ),
        _row(
            "method-undeclared",
            "an operation's method is one of the declared verbs",
            new=[_set(LOGIN + ["method"], "post")],
            expect=FAILURE,
        ),
        _row(
            "errors-member-nonstring",
            "an operation's errors are strings",
            new=[_append(REFRESH + ["errors"], [])],
            expect=FAILURE,
        ),
        _row(
            "errors-outside-closed-enum",
            "an operation's errors stay inside the closed enum",
            new=[_append(REFRESH + ["errors"], "weird_code")],
            expect=FAILURE,
        ),
        _row(
            "allowlist-duplicate-entry",
            "allowlists carry no duplicates",
            new=[_append(READ_ONLY, "entitlements.get")],
            expect=FAILURE,
        ),
        _row(
            "allowlist-member-removed",
            "existing allowlist membership is immutable",
            new=[_remove(READ_ONLY, "entitlements.get")],
            expect=FAILURE,
        ),
        _row(
            "allowlist-nonpublic-member-added",
            "public_operations lists exactly the auth:public operations",
            new=[_append(PUBLIC, "quota.reserve")],
            expect=FAILURE,
        ),
        _row(
            "allowlist-mutating-member-added",
            "read_only_operations lists exactly the non-mutating operations",
            new=[_append(READ_ONLY, "identity.refresh")],
            expect=FAILURE,
        ),
        _row(
            "new-operation-route-collision",
            "a new operation may not reuse an existing route",
            new=[_set(["areas", "identity", "ops", "new_login"], _op(LOGIN))],
            expect=FAILURE,
        ),
        _row(
            "new-operations-share-route",
            "two new operations may not share one route",
            new=[
                _set(
                    ["areas", "identity", "ops", "recover_a"],
                    _op(LOGIN, path="/identity/recover"),
                ),
                _set(
                    ["areas", "identity", "ops", "recover_b"],
                    _op(LOGIN, path="/identity/recover"),
                ),
            ],
            expect=FAILURE,
        ),
        _row(
            "new-readonly-operation-missing-allowlist",
            "a new read-only operation must join read_only_operations",
            new=[
                _set(
                    ["areas", "entitlements", "ops", "refresh_status"],
                    _op(ENT_GET, path="/entitlements/refresh-status"),
                )
            ],
            expect=FAILURE,
        ),
        _row(
            "new-public-operation-missing-allowlist",
            "a new public operation must join public_operations",
            new=[
                _set(
                    ["areas", "identity", "ops", "recover"],
                    _op(LOGIN, path="/identity/recover"),
                )
            ],
            expect=FAILURE,
        ),
        _row(
            "write-only-outside-request",
            "write_only fields may not appear on the response side",
            new=[
                _set(
                    REG_RESP + ["session_secret"],
                    {"type": "string", "required": False, "write_only": True},
                )
            ],
            expect=FAILURE,
        ),
        _row(
            "object-with-example",
            "an object field carries no example",
            new=[_set(ENT_CAPS + ["example"], {})],
            expect=FAILURE,
        ),
        _row(
            "hostile-old-snapshot",
            "both snapshots are validated, old included",
            old=[_set(SCHEMA_VERSION, 3)],
            expect=FAILURE,
        ),
    ]
    return rows


def _canon(case):
    return json.dumps(case, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def build():
    sections = {
        section: sorted(rows, key=lambda row: row["name"])
        for section, rows in (
            ("happy", _happy()),
            ("boundary", _boundary()),
            ("incompatible", _incompatible()),
            ("malformed", _malformed()),
        )
    }
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "contract_schema_version": _CONTRACT_DOC["schema_version"],
        "source": SOURCE,
        "notes": NOTES,
        "base_snapshot": _SOURCE_DOC,
        "base_manifest": hashlib.sha256(_canon(_SOURCE_DOC)).hexdigest(),
        "section_manifests": {
            section: {case["name"]: hashlib.sha256(_canon(case)).hexdigest() for case in rows}
            for section, rows in sections.items()
        },
        **sections,
    }


def dump(cases):
    return json.dumps(cases, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main():
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(dump(build()))
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
