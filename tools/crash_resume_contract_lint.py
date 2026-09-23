#!/usr/bin/env python3
"""T0275: store crash-resume contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/crash_resume.yaml"
MAX_DEPTH = 64
MAX_INT_DIGITS = 4000
SECTIONS = {
    "errors",
    "failures",
    "id",
    "identifiers",
    "links",
    "oracle_boundary",
    "properties",
    "record",
    "request",
    "role",
    "semantics",
    "versioning",
}

ROLE = {
    "kind": "verified-torn-tail-recovery-of-a-wal-logged-store-after-a-crash",
    "serves": "position-graph-store-durability",
    "scope": "single-log-resume-from-a-durable-checkpoint",
    "owns": "resume-receipt-semantics-and-torn-tail-quarantine",
    "not_scope": "backup-restore-rollback-or-multi-log-replication",
}
RECORD = {
    "fields": [
        "resume_id",
        "head",
        "state_id",
        "resumed_count",
        "discarded_count",
        "quarantine_token",
    ],
    "exact": True,
}
REQUEST = {"fields": ["checkpoint_sequence"], "exact": True}
IDENTIFIERS = {
    "resume_id": {
        "kind": "content-addressed-resume-receipt-id",
        "grammar": "^rsm1:[0-9a-f]{64}$",
        "derivation": "sha256-over-head-state-id-resumed-count-discarded-count-quarantine-token",
        "source": "derived-from-validated-resume-never-caller-supplied",
    },
    "head": {
        "kind": "wal-chain-tip-or-pinned-genesis",
        "grammar": "^(wal0:0{64}|wal1:[0-9a-f]{64})$",
        "source": "derived-from-the-longest-valid-prefix-never-caller-supplied",
    },
    "state_id": {
        "kind": "canonical-state-content-digest",
        "grammar": "^gs1:[0-9a-f]{64}$",
        "derivation": "sha256-over-canonical-serialization-of-sorted-identity-record-map",
        "source": "derived-from-the-replayed-valid-prefix-never-caller-supplied",
    },
    "quarantine_token": {
        "kind": "untrusted-quarantine-sink-receipt-token",
        "grammar": "^qtn1:[0-9a-f]{64}$",
        "derivation": (
            "sha256-over-domain-separated-length-framed-canonical-json-of-every-discarded-entry"
        ),
        "source": "sink-output-shape-validated-and-bound-byte-exact-to-the-local-tail-derivation",
    },
}
SEMANTICS = {
    "prefix_resolution": "longest-prefix-passing-full-linked-wal-validation-and-replay",
    "checkpoint_resolution": (
        "checkpoint-sequence-is-a-non-negative-acknowledged-position-or-genesis-zero"
    ),
    "durability": "every-entry-at-or-before-the-checkpoint-must-lie-in-the-valid-prefix",
    "torn_tail": (
        "everything-from-the-first-invalid-entry-onward-is-"
        "discarded-even-if-later-entries-look-valid"
    ),
    "quarantine": (
        "discarded-tail-quarantined-exactly-once-before-commit-"
        "token-bound-byte-exact-to-canonical-tail-serialization"
    ),
    "admission": (
        "discarded-entries-exact-canonical-json-acyclic-alias-free-"
        "nesting-depth-at-most-64-int-decimal-digits-at-most-4000"
    ),
    "commit": "torn-tail-removal-only-after-full-validation-and-quarantine",
}
ORACLE_BOUNDARY = {
    "role": "quarantine-sink-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-resume-including-an-empty-tail",
    "frozen_snapshots": (
        "entire-log-and-request-frozen-before-first-oracle-call-"
        "never-re-read-restored-bit-identical"
    ),
    "output_validation": (
        "exact-built-in-string-pinned-grammar-byte-exact-tail-bound-token-or-fail-closed"
    ),
}
FAILURE_CLASSES = [
    "malformed_resume_record",
    "unknown_checkpoint",
    "corrupt_source",
    "divergent_quarantine",
]
FAILURE_TRIGGERS = {
    "malformed_resume_record": (
        "request-shape-or-type-violation-source-log-type-violation-or-inadmissible-discarded-entry"
    ),
    "unknown_checkpoint": "negative-checkpoint-sequence",
    "corrupt_source": (
        "an-entry-at-or-before-the-checkpoint-is-missing-or-"
        "fails-linked-wal-validation-or-chain-rederivation"
    ),
    "divergent_quarantine": (
        "sink-raising-any-baseexception-non-exact-string-bad-grammar-or-tail-divergent-token"
    ),
}
FAILURE_MAPPING = {
    "malformed_resume_record": "malformed_request",
    "unknown_checkpoint": "unknown_checkpoint",
    "corrupt_source": "corrupt_source",
    "divergent_quarantine": "divergent_quarantine",
}
ERROR_ENUM = [
    "malformed_request",
    "unknown_checkpoint",
    "corrupt_source",
    "divergent_quarantine",
    "internal",
]
PROPERTIES = {
    "total": "hostile-requests-logs-and-sinks-fail-closed-typed-never-raw",
    "atomic": "rejected-resume-leaves-inputs-bit-identical",
    "deterministic": "same-inputs-same-receipt-and-surviving-prefix",
    "idempotent": "resuming-a-resumed-log-discards-nothing-and-keeps-head-and-state",
    "rollback": "committed-resume-removes-exactly-the-torn-tail",
}
VERSIONING = {
    "base_path": "/store/crash-resume/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional "
    "fields, new enum members, new endpoints. Never change the "
    "meaning of an existing field in place.",
}
LINKS = {
    "wal_contract": "data/contracts/wal.yaml",
    "migration_contract": "data/contracts/migration.yaml",
    "rollback_contract": "data/contracts/rollback.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="store-crash-resume", sections=SECTIONS)

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("request", REQUEST, cc.get("request"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("semantics", SEMANTICS, cc.get("semantics"))
    check("oracle_boundary", ORACLE_BOUNDARY, cc.get("oracle_boundary"))
    failures = cc["failures"]
    close_failures(failures)
    check("failures.classes", FAILURE_CLASSES, failures.get("classes"))
    check("failures.triggers", FAILURE_TRIGGERS, failures.get("triggers"))
    check("failures.mapping", FAILURE_MAPPING, failures.get("mapping"))
    if failures.get("closed") is not True:
        raise ContractError("failure model must be closed")
    if set(failures.get("mapping", {})) != set(FAILURE_CLASSES):
        raise ContractError("failures: mapping keys must equal declared classes")
    if set(failures.get("triggers", {})) != set(FAILURE_CLASSES):
        raise ContractError("failures: triggers keys must equal declared classes")
    errors = cc["errors"]
    close_errors(errors, shape_keys=("retryable_true_only_for",))
    check("errors.closed_enum", ERROR_ENUM, errors.get("closed_enum"))
    shape = errors.get("shape") or {}
    check(
        "errors.shape.retryable_true_only_for", ["internal"], shape.get("retryable_true_only_for")
    )
    if set(FAILURE_MAPPING.values()) | {"internal"} != set(ERROR_ENUM):
        raise ContractError("error enum must equal mapped codes + internal")
    check("properties", PROPERTIES, cc.get("properties"))
    check("versioning", VERSIONING, cc.get("versioning"))
    check("links", LINKS, cc.get("links"))
    for rel in cc["links"].values():
        if not (ROOT / rel).is_file():
            raise ContractError(f"linked contract missing: {rel}")
    print(f"crash-resume contract lint ok: {path}")


if __name__ == "__main__":
    lint()
