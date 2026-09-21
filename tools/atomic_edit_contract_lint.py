#!/usr/bin/env python3
"""T0203: store atomic edit contract lint - exact structured pins."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data/contracts/atomic_edit.yaml"

ROLE = {
    "kind":
        "atomic-single-state-edit-over-content-addressed-graph-"
        "states",
    "serves": "all-or-nothing-writes-for-the-store-front-door",
    "scope": "one-base-state-plus-one-edit-request",
    "owns": "edit-validation-atomic-staging-commit-and-derived-"
            "receipt",
    "not_scope": "concurrency-control-durability-wal-and-crash-"
                 "recovery",
}
RECORD = {
    "fields": ["edit_id", "base_id", "target_id", "state"],
    "exact": True,
}
OPERATION = {
    "rule": None,  # prose: key required, value exempt
    "fields": ["kind", "identity", "record"],
    "exact": True,
    "kinds": ["put", "delete"],
    "put":
        "record-required-exact-node-record-whose-derived-identity-"
        "equals-the-operation-identity",
    "delete": "record-must-be-null-identity-an-exact-string",
    "uniqueness": "exactly-one-operation-per-identity-per-edit",
    "order":
        "insignificant-under-uniqueness-receipt-derives-from-"
        "canonical-identity-order",
}
IDENTIFIERS = {
    "state_id": {
        "kind": "canonical-state-content-digest",
        "grammar": "^gs1:[0-9a-f]{64}$",
        "derivation":
            "sha256-over-canonical-serialization-of-sorted-"
            "identity-record-map",
        "source": "derived-from-validated-state-never-caller-supplied",
    },
    "edit_id": {
        "kind": "content-addressed-edit-receipt-id",
        "grammar": "^ae1:[0-9a-f]{64}$",
        "derivation":
            "sha256-over-base-id-target-id-and-canonical-operation-"
            "list",
        "source": "derived-never-caller-supplied",
    },
}
SEMANTICS = {
    "base_check":
        "recomputed-base-state-id-must-equal-request-base-id-"
        "before-staging",
    "staging":
        "whole-edit-staged-on-a-detached-copy-before-any-"
        "observable-mutation",
    "commit":
        "single-commit-point-after-total-validation-of-request-"
        "base-and-every-operation",
    "put_upsert":
        "put-inserts-or-replaces-the-record-at-its-derived-"
        "identity",
    "delete_rule":
        "delete-removes-a-present-identity-absent-is-"
        "unknown_identity",
    "result_state":
        "fresh-deep-copied-state-never-aliases-base-records",
    "target_verification":
        "recomputed-staged-state-id-is-the-receipt-target-id",
}
FAILURES = {
    "classes": ["malformed_edit_record", "conflicting_base",
                "unknown_identity"],
    "triggers": {
        "malformed_edit_record":
            "request-operation-grammar-record-or-duplicate-"
            "identity-violation",
        "conflicting_base":
            "recomputed-base-state-id-differs-from-request-base-"
            "id",
        "unknown_identity":
            "delete-targets-an-identity-absent-from-the-base",
    },
    "mapping": {
        "malformed_edit_record": "malformed_request",
        "conflicting_base": "conflicting_base",
        "unknown_identity": "unknown_identity",
    },
    "closed": True,
}
ERRORS = {
    "closed_enum": ["malformed_request", "conflicting_base",
                    "unknown_identity", "internal"],
    "shape": {"retryable_true_only_for": ["internal"]},
}
FAILURE_CLASSES = ["malformed_edit_record", "conflicting_base",
                   "unknown_identity"]
PROPERTIES = {
    "total":
        "hostile-requests-operations-and-states-fail-closed-typed-"
        "never-raw",
    "atomic": "whole-edit-commits-or-base-left-bit-identical",
    "deterministic": "same-base-and-edit-same-receipt",
    "input_preservation":
        "base-and-request-never-mutated-result-fresh",
    "rollback": "rejected-edit-leaves-base-bit-identical",
}
FAILURE_MAPPING = dict(FAILURES["mapping"])
ERROR_ENUM = list(ERRORS["closed_enum"])
VERSIONING = {
    "base_path": "/store/atomic-edit/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "position_digest_contract":
        "data/contracts/position_digest.yaml",
    "variant_contract": "data/contracts/variant.yaml",
}
PROSE_KEYS = {"rule"}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    if not isinstance(doc, dict):
        raise ContractError("contract document must be a mapping")
    # the file envelope is CLOSED: exact built-in-integer schema
    # version, and no undeclared top-level keys
    if set(doc) != {"schema_version", "contract"}:
        raise ContractError(
            f"top level must be exactly "
            f"{{schema_version, contract}}: {sorted(doc)!r}")
    if type(doc["schema_version"]) is not int or \
            doc["schema_version"] != 1:
        raise ContractError(
            "schema_version must be exact built-in int 1")
    cc = doc["contract"]
    if not isinstance(cc, dict):
        raise ContractError("atomic edit contract missing")
    # the contract section is CLOSED to its declared key set
    declared = {"id", "role", "record", "operation",
                "identifiers", "semantics", "failures", "errors",
                "properties", "versioning", "links"}
    if set(cc) != declared:
        raise ContractError(
            f"contract sections drifted: {sorted(cc)!r}")
    if cc["id"] != "store-atomic-edit":
        raise ContractError("contract id drifted")

    def check(name, expected, actual):
        """Exact structured comparison with KEY-SET CLOSURE: an
        undeclared key is as much drift as a changed value.
        Declared prose keys (value None in the expectation) are
        required to exist but their text is documentation."""
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                raise ContractError(
                    f"{name} must be a mapping: {actual!r}")
            if set(actual) != set(expected):
                raise ContractError(
                    f"{name} keys drifted: "
                    f"missing={sorted(set(expected) - set(actual))}"
                    f" extra={sorted(set(actual) - set(expected))}")
            for key, ev in expected.items():
                if ev is None and key in PROSE_KEYS:
                    continue
                check(f"{name}.{key}", ev, actual[key])
            return
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc["role"])
    check("record", RECORD, cc["record"])
    check("operation", OPERATION, cc["operation"])
    check("identifiers", IDENTIFIERS, cc["identifiers"])
    check("semantics", SEMANTICS, cc["semantics"])
    check("failures", FAILURES, cc["failures"])
    if set(cc["failures"]["mapping"]) != set(FAILURE_CLASSES):
        raise ContractError(
            "failures: mapping keys must equal declared classes")
    if set(cc["failures"]["triggers"]) != set(FAILURE_CLASSES):
        raise ContractError(
            "failures: triggers keys must equal declared classes")
    check("errors", ERRORS, cc["errors"])
    check("properties", PROPERTIES, cc["properties"])
    check("versioning", VERSIONING, cc["versioning"])
    check("links", LINKS, cc["links"])
    print(f"atomic edit contract lint ok: {path}")


if __name__ == "__main__":
    lint()
