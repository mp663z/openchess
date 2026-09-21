#!/usr/bin/env python3
"""T0239: store rollback contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/rollback.yaml"

ROLE = {
    "kind":
        "verified-truncation-rollback-over-a-wal-logged-store",
    "serves": "position-graph-store-durability",
    "scope": "single-log-rollback-to-a-prior-position",
    "owns": "rollback-receipt-semantics-and-tail-archival",
    "not_scope": "state-level-restore-or-multi-log-replication",
}
RECORD = {
    "fields": ["rollback_id", "from_head", "to_head",
               "truncated_count", "archive_token"],
    "exact": True,
}
IDENTIFIERS = {
    "rollback_id": {
        "kind": "content-addressed-rollback-receipt-id",
        "grammar": "^rbk1:[0-9a-f]{64}$",
        "derivation": "sha256-over-from-head-to-head-truncated-"
                      "count-archive-token",
        "source": "derived-from-validated-rollback-never-caller-"
                  "supplied",
    },
    "head": {
        "kind": "wal-chain-tip-or-pinned-genesis",
        "grammar": "^(wal0:0{64}|wal1:[0-9a-f]{64})$",
        "source": "derived-from-validated-source-log-never-caller-"
                  "supplied",
    },
    "archive_token": {
        "kind": "untrusted-archiver-receipt-token",
        "grammar": "^arc1:[0-9a-f]{64}$",
        "source": "archiver-output-shape-validated-never-trusted-"
                  "beyond-shape",
    },
}
SEMANTICS = {
    "source_validation":
        "full-linked-wal-validation-before-any-truncation",
    "target_resolution":
        "target-sequence-is-an-exact-log-position-or-genesis-zero",
    "archival": "truncated-tail-archived-exactly-once-before-"
                "commit",
    "chaining": "surviving-prefix-chain-unchanged-to-head-"
                "preserved",
    "commit": "tail-removal-only-after-full-validation-and-"
              "archival",
}
ORACLE_BOUNDARY = {
    "role": "tail-archiver-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-rollback",
    "frozen_snapshots":
        "entire-log-and-request-frozen-before-first-oracle-call-"
        "never-re-read-restored-bit-identical",
    "output_validation":
        "exact-built-in-string-pinned-grammar-or-fail-closed",
}
FAILURE_CLASSES = ["malformed_rollback_record", "unknown_target",
                   "corrupt_source", "divergent_archive"]
FAILURE_TRIGGERS = {
    "malformed_rollback_record":
        "request-or-log-grammar-or-type-violation",
    "unknown_target":
        "target-sequence-outside-zero-through-log-length",
    "corrupt_source":
        "source-log-fails-linked-wal-validation-or-chain-"
        "rederivation",
    "divergent_archive":
        "archiver-raising-or-non-exact-string-or-bad-grammar-"
        "token",
}
FAILURE_MAPPING = {
    "malformed_rollback_record": "malformed_request",
    "unknown_target": "unknown_target",
    "corrupt_source": "corrupt_source",
    "divergent_archive": "divergent_archive",
}
ERROR_ENUM = ["malformed_request", "unknown_target",
              "corrupt_source", "divergent_archive", "internal"]
PROPERTIES = {
    "total":
        "hostile-requests-logs-and-archivers-fail-closed-typed-"
        "never-raw",
    "atomic": "rejected-rollback-leaves-inputs-bit-identical",
    "deterministic": "same-inputs-same-receipt-and-surviving-"
                     "prefix",
    "rollback": "committed-rollback-removes-exactly-the-tail",
}
VERSIONING = {
    "base_path": "/store/rollback/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "wal_contract": "data/contracts/wal.yaml",
    "backup_contract": "data/contracts/backup.yaml",
    "restore_contract": "data/contracts/restore.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="store-rollback",
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
    print(f"rollback contract lint ok: {path}")


if __name__ == "__main__":
    lint()
