#!/usr/bin/env python3
"""T0221: store backup contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/backup.yaml"

ROLE = {
    "kind":
        "content-addressed-backup-over-wal-logged-graph-states",
    "serves": "position-graph-store-durability",
    "scope": "snapshot-and-integrity-verification-of-a-single-log",
    "owns": "backup-receipt-semantics-and-verification",
    "not_scope": "restore-incremental-or-offsite-replication",
}
RECORD = {
    "fields": ["backup_id", "head", "state_id", "entry_count",
               "bundle"],
    "exact": True,
    "field_definitions": {
        "bundle": {
            "kind": "canonical-serialized-snapshot",
            "type": "exact-built-in-string-utf8-encodable",
            "source": "serializer-output-validated-at-the-"
                      "oracle-boundary",
        },
    },
}
IDENTIFIERS = {
    "backup_id": {
        "kind": "content-addressed-backup-receipt-id",
        "grammar": "^bck1:[0-9a-f]{64}$",
        "derivation": "sha256-over-head-state-id-entry-count-"
                      "canonical-bundle",
        "source": "derived-from-validated-snapshot-never-caller-"
                  "supplied",
    },
    "head": {
        "kind": "wal-chain-tip-or-pinned-genesis",
        "grammar": "^(wal0:0{64}|wal1:[0-9a-f]{64})$",
        "source": "derived-from-validated-source-log-never-caller-"
                  "supplied",
    },
    "state_id": {
        "kind": "canonical-state-content-digest",
        "grammar": "^gs1:[0-9a-f]{64}$",
        "derivation": "sha256-over-canonical-serialization-of-"
                      "sorted-identity-record-map",
        "source": "derived-from-replayed-state-never-caller-"
                  "supplied",
    },
}
SEMANTICS = {
    "source_validation":
        "full-linked-wal-validation-and-replay-before-snapshot",
    "snapshot":
        "replayed-state-frozen-detached-before-serialization",
    "integrity":
        "recomputed-backup-id-must-equal-stored-backup-id",
    "head_count_consistency":
        "empty-log-backup-pins-genesis-head-and-empty-state",
    "commit": "staged-receipt-only-source-log-never-mutated",
}
ORACLE_BOUNDARY = {
    "role": "bundle-serializer-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-backup",
    "frozen_snapshots":
        "entire-log-and-state-frozen-before-first-oracle-call-"
        "never-re-read",
    "output_validation": "exact-built-in-string-or-fail-closed",
}
FAILURE_CLASSES = ["malformed_backup_record", "corrupt_source",
                   "divergent_snapshot", "divergent_backup"]
FAILURE_TRIGGERS = {
    "malformed_backup_record":
        "receipt-grammar-type-or-utf8-violation-or-source-log-"
        "type-violation",
    "corrupt_source":
        "source-log-fails-linked-wal-validation-or-chain-"
        "rederivation",
    "divergent_snapshot":
        "serializer-raising-any-baseexception-or-non-exact-string-output",
    "divergent_backup":
        "recomputed-backup-id-divergence-or-head-count-"
        "inconsistency",
}
FAILURE_MAPPING = {
    "malformed_backup_record": "malformed_request",
    "corrupt_source": "corrupt_source",
    "divergent_snapshot": "divergent_snapshot",
    "divergent_backup": "divergent_backup",
}
ERROR_ENUM = ["malformed_request", "corrupt_source",
              "divergent_snapshot", "divergent_backup", "internal"]
PROPERTIES = {
    "total":
        "hostile-logs-receipts-and-serializers-fail-closed-typed-"
        "never-raw",
    "atomic":
        "rejected-backup-or-verify-leaves-inputs-bit-identical",
    "deterministic": "same-log-same-backup-receipt",
    "rollback": "staged-receipt-only-commit-after-full-validation",
}
VERSIONING = {
    "base_path": "/store/backup/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "wal_contract": "data/contracts/wal.yaml",
    "migration_contract": "data/contracts/migration.yaml",
    "diff_contract": "data/contracts/diff.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="store-backup",
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
    print(f"backup contract lint ok: {path}")


if __name__ == "__main__":
    lint()
