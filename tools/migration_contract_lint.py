#!/usr/bin/env python3
"""T0194: store migration contract lint - exact structured pins."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data/contracts/migration.yaml"

ROLE = {
    "kind":
        "store-schema-migration-over-content-addressed-graph-states",
    "serves": "position-graph-store-durability",
    "scope": "one-registered-step-per-schema-pair",
    "owns": "migration-semantics-and-receipt-shape",
    "not_scope": "automatic-multi-hop-chaining",
}
RECORD = {
    "fields": ["migration_id", "from_schema", "to_schema",
               "source_id", "target_id"],
    "exact": True,
}
IDENTIFIERS = {
    "schema_id": {
        "kind": "pinned-store-schema-name",
        "grammar": "^store-v[1-9][0-9]*$",
        "source": "declared-in-registry-never-free-form",
    },
    "state_id": {
        "kind": "canonical-state-content-digest",
        "grammar": "^gs1:[0-9a-f]{64}$",
        "derivation":
            "sha256-over-canonical-serialization-of-sorted-"
            "identity-record-map",
        "source": "derived-from-validated-state-never-caller-supplied",
    },
    "migration_id": {
        "kind": "content-addressed-migration-receipt-id",
        "grammar": "^mg1:[0-9a-f]{64}$",
        "derivation": "sha256-over-from-schema-to-schema-source-id-"
                      "target-id",
        "source": "derived-never-caller-supplied",
    },
}
REGISTRY = {
    "schemas": [
        {"id": "store-v1",
         "record_fields": ["variant", "digest", "snapshot_fen"],
         "digest_grammar": "^pdv1:[0-9a-f]{64}$"},
        {"id": "store-v2",
         "record_fields": ["variant", "digest", "snapshot_fen"],
         "digest_grammar": "^pdv2:[0-9a-f]{64}$"},
    ],
    "steps": [
        {"from_schema": "store-v1", "to_schema": "store-v2",
         "transform": "re-digest-every-record-under-target-oracle-"
                      "identities-unchanged"},
    ],
    "chaining": "single-step-only-no-implicit-multi-hop",
}
SEMANTICS = {
    "source_check":
        "recomputed-source-state-id-must-equal-request-source-id-"
        "before-migration",
    "target_verification":
        "recomputed-migrated-state-id-is-the-receipt-target-id",
    "cardinality": "record-count-preserved-exactly",
    "identity_preservation":
        "canonical-identities-unchanged-under-re-digest",
    "commit": "atomic-staged-copy-source-never-mutated",
}
ORACLE_BOUNDARY = {
    "role": "target-digest-oracle-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-record-retained-key",
    "frozen_snapshots": "records-frozen-before-oracle-never-re-read",
    "output_validation":
        "exact-built-in-string-pinned-format-or-fail-closed",
}
FAILURE_CLASSES = ["malformed_migration_record", "unknown_migration",
                   "conflicting_source", "divergent_target"]
FAILURE_TRIGGERS = {
    "malformed_migration_record":
        "request-grammar-state-or-record-violation",
    "unknown_migration":
        "no-registered-step-for-schema-pair-including-noop-or-"
        "downgrade",
    "conflicting_source":
        "recomputed-source-state-id-differs-from-request-source-id",
    "divergent_target":
        "target-oracle-inconsistent-or-cardinality-or-identity-drift",
}
FAILURE_MAPPING = {
    "malformed_migration_record": "malformed_request",
    "unknown_migration": "unknown_migration",
    "conflicting_source": "conflicting_source",
    "divergent_target": "divergent_target",
}
ERROR_ENUM = ["malformed_request", "unknown_migration",
              "conflicting_source", "divergent_target", "internal"]
PROPERTIES = {
    "total": "hostile-requests-states-oracles-fail-closed-typed-never-raw",
    "atomic": "rejected-migration-leaves-inputs-bit-identical",
    "deterministic": "same-inputs-same-receipt",
    "rollback": "staged-copy-only-commit-after-full-validation",
}
VERSIONING = {
    "base_path": "/store/migration/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "diff_contract": "data/contracts/diff.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = doc.get("contract")
    if not isinstance(cc, dict):
        raise ContractError("migration contract missing")

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("registry", REGISTRY, cc.get("registry"))
    check("semantics", SEMANTICS, cc.get("semantics"))
    check("oracle_boundary", ORACLE_BOUNDARY,
          cc.get("oracle_boundary"))
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
    print(f"migration contract lint ok: {path}")


if __name__ == "__main__":
    lint()
