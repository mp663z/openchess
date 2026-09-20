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
FAILURE_CLASSES = ["malformed_edit_record", "conflicting_base",
                   "unknown_identity"]
FAILURE_TRIGGERS = {
    "malformed_edit_record":
        "request-operation-grammar-record-or-duplicate-identity-"
        "violation",
    "conflicting_base":
        "recomputed-base-state-id-differs-from-request-base-id",
    "unknown_identity":
        "delete-targets-an-identity-absent-from-the-base",
}
FAILURE_MAPPING = {
    "malformed_edit_record": "malformed_request",
    "conflicting_base": "conflicting_base",
    "unknown_identity": "unknown_identity",
}
ERROR_ENUM = ["malformed_request", "conflicting_base",
              "unknown_identity", "internal"]
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
    cc = doc.get("contract")
    if not isinstance(cc, dict):
        raise ContractError("atomic edit contract missing")

    def check(name, expected, actual):
        if isinstance(expected, dict) and isinstance(actual, dict):
            exp = {k: v for k, v in expected.items()
                   if k not in PROSE_KEYS}
            act = {k: v for k, v in actual.items()
                   if k not in PROSE_KEYS}
            if act != exp:
                raise ContractError(f"{name} drifted: {actual!r}")
            return
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("operation", OPERATION, cc.get("operation"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("semantics", SEMANTICS, cc.get("semantics"))
    failures = cc.get("failures") or {}
    check("failures.classes", FAILURE_CLASSES,
          failures.get("classes"))
    check("failures.triggers", FAILURE_TRIGGERS,
          failures.get("triggers"))
    check("failures.mapping", FAILURE_MAPPING,
          failures.get("mapping"))
    if failures.get("closed") is not True:
        raise ContractError("failure model must be closed")
    if set(failures.get("mapping", {})) != set(FAILURE_CLASSES):
        raise ContractError(
            "failures: mapping keys must equal declared classes")
    if set(failures.get("triggers", {})) != set(FAILURE_CLASSES):
        raise ContractError(
            "failures: triggers keys must equal declared classes")
    errors = cc.get("errors") or {}
    check("errors.closed_enum", ERROR_ENUM,
          errors.get("closed_enum"))
    shape = errors.get("shape") or {}
    check("errors.shape.retryable_true_only_for", ["internal"],
          shape.get("retryable_true_only_for"))
    check("properties", PROPERTIES, cc.get("properties"))
    check("versioning", VERSIONING, cc.get("versioning"))
    check("links", LINKS, cc.get("links"))
    print(f"atomic edit contract lint ok: {path}")


if __name__ == "__main__":
    lint()
