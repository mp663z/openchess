#!/usr/bin/env python3
"""T0437: exact control-plane versioning contract pins and source links."""
from __future__ import annotations

# ruff: noqa: E501  (exact pinned contract literals)
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract_lint_closure import close_envelope, close_errors, close_failures  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data/contracts/control_plane_versioning.yaml"
SOURCE = ROOT / "data/contracts/control-plane.yaml"
OPENAPI = ROOT / "data/contracts/openapi.yaml"
SECTIONS = {"id", "role", "source", "request", "compatibility", "result", "failures", "errors", "semantics", "versioning", "links"}
# The structured pins are deliberately enumerated here instead of deriving
# them from the file being checked. A mutation to that file cannot self-approve.
PINS = {
    "role": {
        "kind": "pure-compatibility-verdict-for-two-control-plane-snapshots",
        "serves": "replaceable-control-plane-consumers",
        "scope": "same-major-minor-upgrades-and-major-base-path-transitions",
        "owns": "compatibility-of-declared-operation-and-field-shapes",
        "not_scope": "runtime-routing-openapi-derivation-graph-content-versions-or-undeclared-provider-behavior",
    },
    "source": {
        "contract": "data/contracts/control-plane.yaml",
        "schema_version": 2,
        "rule_path": "contract.versioning.rule",
        "base_path": "contract.versioning.base_path",
        "scheme": "semver",
        "source_structure": "control-plane-schema-v2-validated-by-t0419-strict-derivation-with-additive-comparison-of-operations-and-fields",
    },
    "request": {
        "arguments": ["old", "new", "old_minor", "new_minor"],
        "minors": "exact-built-in-nonnegative-integers-at-most-2147483647-old-minor-at-most-new-minor-within-same-major",
        "snapshots": "independent-validated-control-plane-contract-documents",
    },
    "compatibility": {
        "major": "same-major-requires-the-same-base-path-a-higher-major-requires-a-new-base-path-and-allows-breaking-shape-changes",
        "minor": "only-enumerated-additions-are-minor-any-other-change-is-major",
        "request": "existing-request-fields-unchanged-new-fields-optional-only",
        "response": "existing-response-fields-unchanged-new-fields-optional-only",
        "operations": "new-operations-allowed-only-at-new-method-and-path-pairs-existing-operations-unchanged-except-permitted-field-additions",
        "allowlists": "public-and-read-only-lists-may-only-append-exactly-the-new-operations-with-matching-auth-and-mutating-existing-membership-is-immutable",
        "provider_kind": "open-provider-kind-values-are-consumer-tolerated-not-a-fixed-list-of-values-to-pin",
        "unknown_request_fields": "servers-ignore-unknown-request-fields",
        "response_extras": "consumers-do-not-assert-absence-of-extra-response-fields",
        "rollback": "a-client-on-an-earlier-minor-can-consume-a-later-same-major-server-no-data-migration-required",
        "reading": "Coordinator fail-closed ruling at 2026-09-25 07:59 IST, reading control-plane.yaml lines 13-19: MINOR is additive-only followed by a closed list; a new response field is optional because servers MUST accept any client minor within its major; optional-to-required is not in the list and needs a new MAJOR base path. The same applies to optional request fields. Rollback permits extras, not guaranteed presence.",
        "excluded": "runtime-behavior-and-semantic-changes-cannot-be-proven-from-schema-snapshots",
    },
    "result": {
        "compatible": "true-for-valid-minor-addition-or-higher-major-with-new-base-path",
        "incompatible": "false-on-same-major-breaking-change-or-major-without-new-base-path",
        "output": "exact-built-in-boolean",
    },
    "semantics": {
        "purity": "source-documents-and-nested-containers-unchanged",
        "determinism": "identical-documents-and-minors-give-identical-verdict",
        "validation": "validate-both-snapshots-and-minors-before-comparing-first-failure-wins",
        "rollback": "rejected-comparison-has-no-effects-on-either-snapshot",
    },
    "versioning": {
        "base_path": "/contracts/control-plane-versioning/v1",
        "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new endpoints. Never change the meaning of an existing field in place.",
    },
    "links": {
        "control_plane_contract": "data/contracts/control-plane.yaml",
        "openapi_derivation": "data/contracts/openapi.yaml",
    },
}
FAILURES = {
    "classes": ["malformed_version_request"],
    "triggers": {"malformed_version_request": "hostile-type-missing-key-bad-source-contract-or-minor-bound-violation"},
    "mapping": {"malformed_version_request": "malformed_request"},
    "closed": True,
}
ERRORS = {"closed_enum": ["malformed_request", "internal"], "shape": {"retryable_true_only_for": ["internal"]}}
GROUNDING = (
    "Clients pin MAJOR. MINOR is additive-only: new optional request fields, new response fields, new operations or new provider_kind values.",
    "A server MUST accept any client minor within its major and MUST ignore unknown request fields.",
    "Rollback: a consumer may downgrade to any earlier MINOR within the same MAJOR without migration",
    "MAJOR bumps require a new base path.",
)


def lint(path: Path | None = None) -> None:
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="contracts-control-plane-versioning", sections=SECTIONS)
    for section, expected in PINS.items():
        if cc[section] != expected or type(cc[section]) is not dict or list(cc[section]) != list(expected):
            raise ContractError(f"{section}: structured pin drift")
    close_failures(cc["failures"])
    close_errors(cc["errors"], shape_keys=("retryable_true_only_for",))
    if cc["failures"] != FAILURES or cc["errors"] != ERRORS:
        raise ContractError("failure or error pin drift")
    if any(type(v) is not bool for v in (cc["failures"]["closed"],)):
        raise ContractError("failure closed must be exact bool")
    if not SOURCE.is_file() or not OPENAPI.is_file():
        raise ContractError("source or OpenAPI link missing")
    source = yaml.safe_load(SOURCE.read_text())
    if type(source) is not dict or type(source.get("schema_version")) is not int or source["schema_version"] != 2:
        raise ContractError("source schema_version drift")
    versioning = source["contract"]["versioning"]
    if versioning["scheme"] != "semver":
        raise ContractError("source scheme drift")
    if versioning["base_path"] != "/cp/v1":
        raise ContractError("source base path drift")
    rule = " ".join(versioning["rule"].split())
    for phrase in GROUNDING:
        if phrase not in rule:
            raise ContractError(f"source grounding absent: {phrase}")
    # T0419's derivation uses the same live source, not a copied grammar.
    op = yaml.safe_load(OPENAPI.read_text())["contract"]
    if op["source"]["contract"] != cc["source"]["contract"]:
        raise ContractError("OpenAPI source link drift")
    print(f"control-plane versioning lint ok: {path}")


if __name__ == "__main__":
    lint()
