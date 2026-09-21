#!/usr/bin/env python3
"""T0230: store restore contract lint - exact structured pins."""

from __future__ import annotations

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

CONTRACT = ROOT / "data/contracts/restore.yaml"

ROLE = {
    "kind":
        "verified-restore-of-content-addressed-backup-bundles",
    "serves": "position-graph-store-durability",
    "scope": "single-backup-receipt-to-canonical-state",
    "owns": "restore-receipt-semantics-and-state-reconstruction",
    "not_scope": "incremental-restore-or-log-reconstruction",
}
RECORD = {
    "fields": ["restore_id", "backup_id", "state_id", "state"],
    "exact": True,
    "field_definitions": {
        "state": {
            "kind": "restored-state-snapshot",
            "type": "exact-built-in-dict-mapping-exact-built-in-"
                    "string-identities-to-validated-exact-node-"
                    "records",
            "source": "parsed-content-validated-record-by-record-"
                      "with-recomputed-state-id",
        },
    },
}
IDENTIFIERS = {
    "restore_id": {
        "kind": "content-addressed-restore-receipt-id",
        "grammar": "^rst1:[0-9a-f]{64}$",
        "derivation": "sha256-over-backup-id-state-id",
        "source": "derived-from-validated-restore-never-caller-"
                  "supplied",
    },
    "backup_id": {
        "kind": "linked-backup-receipt-id",
        "grammar": "^bck1:[0-9a-f]{64}$",
        "source": "carried-from-verified-backup-receipt-never-"
                  "free-form",
    },
    "state_id": {
        "kind": "canonical-state-content-digest",
        "grammar": "^gs1:[0-9a-f]{64}$",
        "derivation": "sha256-over-canonical-serialization-of-"
                      "sorted-identity-record-map",
        "source": "derived-from-restored-state-never-caller-"
                  "supplied",
    },
}
SEMANTICS = {
    "verify_first":
        "full-linked-backup-verification-before-any-parse",
    "parse": "canonical-bundle-parsed-exactly-once",
    "reconstruction":
        "parsed-state-validated-record-by-record-identity-exact",
    "integrity":
        "recomputed-state-id-must-equal-receipt-state-id",
    "commit": "staged-state-only-input-receipt-never-mutated",
}
ORACLE_BOUNDARY = {
    "role": "bundle-parser-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-restore",
    "frozen_snapshots":
        "receipt-fields-frozen-before-first-oracle-call-never-"
        "re-read-restored-bit-identical",
    "output_validation": "exact-built-in-mapping-or-fail-closed",
}
FAILURE_CLASSES = ["malformed_restore_record", "unverified_backup",
                   "divergent_parse", "divergent_state"]
FAILURE_TRIGGERS = {
    "malformed_restore_record":
        "receipt-grammar-or-type-violation",
    "unverified_backup":
        "receipt-fails-linked-backup-verification",
    "divergent_parse":
        "parser-raising-any-baseexception-or-non-exact-mapping-output",
    "divergent_state":
        "parsed-content-invalid-or-state-id-divergence",
}
FAILURE_MAPPING = {
    "malformed_restore_record": "malformed_request",
    "unverified_backup": "unverified_backup",
    "divergent_parse": "divergent_parse",
    "divergent_state": "divergent_state",
}
ERROR_ENUM = ["malformed_request", "unverified_backup",
              "divergent_parse", "divergent_state", "internal"]
PROPERTIES = {
    "total":
        "hostile-receipts-and-parsers-fail-closed-typed-never-raw",
    "atomic": "rejected-restore-leaves-inputs-bit-identical",
    "deterministic": "same-receipt-same-restored-state",
    "rollback": "staged-state-only-commit-after-full-validation",
}
VERSIONING = {
    "base_path": "/store/restore/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "backup_contract": "data/contracts/backup.yaml",
    "wal_contract": "data/contracts/wal.yaml",
    "migration_contract": "data/contracts/migration.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="store-restore",
        sections={"errors", "failures", "id", "identifiers", "links", "oracle_boundary",
            "properties", "record", "role", "semantics", "versioning",})

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("semantics", SEMANTICS, cc.get("semantics"))
    check("oracle_boundary", ORACLE_BOUNDARY,
          cc.get("oracle_boundary"))
    failures = cc["failures"]
    close_failures(failures)
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
    errors = cc["errors"]
    close_errors(errors, shape_keys=("retryable_true_only_for",))
    check("errors.closed_enum", ERROR_ENUM,
          errors.get("closed_enum"))
    shape = errors.get("shape") or {}
    check("errors.shape.retryable_true_only_for", ["internal"],
          shape.get("retryable_true_only_for"))
    check("properties", PROPERTIES, cc.get("properties"))
    check("versioning", VERSIONING, cc.get("versioning"))
    check("links", LINKS, cc.get("links"))
    print(f"restore contract lint ok: {path}")


if __name__ == "__main__":
    lint()
