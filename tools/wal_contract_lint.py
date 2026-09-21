#!/usr/bin/env python3
"""T0212: store WAL contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/wal.yaml"

ROLE = {
    "kind":
        "write-ahead-log-over-content-addressed-graph-state-"
        "mutations",
    "serves": "position-graph-store-durability",
    "scope": "single-log-append-and-replay",
    "owns": "wal-entry-semantics-and-receipt-shape",
    "not_scope": "snapshots-compaction-or-multi-log-replication",
}
RECORD = {
    "fields": ["entry_id", "sequence", "op", "payload",
               "prior_entry_id"],
    "exact": True,
}
IDENTIFIERS = {
    "entry_id": {
        "kind": "content-addressed-wal-entry-id",
        "grammar": "^wal1:[0-9a-f]{64}$",
        "derivation": "sha256-over-sequence-op-canonical-payload-"
                      "prior-entry-id",
        "source": "derived-from-validated-entry-never-caller-"
                  "supplied",
    },
    "prior_entry_id": {
        "kind": "wal-chain-link-or-pinned-genesis",
        "grammar": "^(wal0:0{64}|wal1:[0-9a-f]{64})$",
        "source": "derived-from-log-tip-never-caller-supplied",
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
REGISTRY = {
    "operations": [
        {"op": "put",
         "payload_fields": ["identity", "record"],
         "effect": "upsert-identity-to-exact-node-record"},
        {"op": "delete",
         "payload_fields": ["identity", "record"],
         "effect": "remove-identity-of-exact-record-if-present"},
    ],
    "closure": "no-other-operations-registered",
}
SEMANTICS = {
    "sequencing":
        "entry-sequence-is-exactly-its-1-based-log-position",
    "chaining":
        "every-entry-pins-the-prior-tip-genesis-pins-wal0-zero",
    "append":
        "request-then-full-log-validation-staged-commit-last",
    "replay":
        "fold-registered-ops-in-sequence-order-over-empty-state",
    "head_verification":
        "replay-head-is-the-tip-entry-id-genesis-on-empty",
    "payload_canonicalization":
        "canonical-payload-string-feeds-entry-id-derivation",
    "canonical_encoding":
        "canonical-payload-utf8-encodable-for-entry-id-"
        "derivation",
}
ORACLE_BOUNDARY = {
    "role": "payload-canonicalizer-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-entry-per-operation",
    "frozen_snapshots":
        "entire-log-frozen-before-first-oracle-call-never-re-read",
    "request_freeze":
        "validated-request-frozen-before-first-oracle-call-never-"
        "re-read-restored-bit-identical",
    "output_validation": "exact-built-in-string-or-fail-closed",
}
FAILURE_CLASSES = ["malformed_wal_entry", "unknown_operation",
                   "sequence_conflict", "corrupt_chain",
                   "divergent_canonicalization"]
FAILURE_TRIGGERS = {
    "malformed_wal_entry":
        "request-entry-payload-or-record-grammar-violation",
    "unknown_operation": "op-not-in-the-pinned-registry",
    "sequence_conflict":
        "sequence-not-the-exact-1-based-log-position",
    "corrupt_chain":
        "prior-link-or-recomputed-entry-id-divergence",
    "divergent_canonicalization":
        "canonicalizer-raising-or-non-exact-string-output",
}
FAILURE_MAPPING = {
    "malformed_wal_entry": "malformed_request",
    "unknown_operation": "unknown_operation",
    "sequence_conflict": "sequence_conflict",
    "corrupt_chain": "corrupt_chain",
    "divergent_canonicalization": "divergent_canonicalization",
}
ERROR_ENUM = ["malformed_request", "unknown_operation",
              "sequence_conflict", "corrupt_chain",
              "divergent_canonicalization", "internal"]
PROPERTIES = {
    "total":
        "hostile-requests-logs-and-oracles-fail-closed-typed-"
        "never-raw",
    "atomic": "rejected-append-or-replay-leaves-inputs-bit-"
              "identical",
    "deterministic": "same-inputs-same-entry-and-replayed-state",
    "rollback": "staged-entry-only-commit-after-full-validation",
}
VERSIONING = {
    "base_path": "/store/wal/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "diff_contract": "data/contracts/diff.yaml",
    "migration_contract": "data/contracts/migration.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="store-wal",
        sections={"errors", "failures", "id", "identifiers", "links", "oracle_boundary",
            "properties", "record", "registry", "role", "semantics", "versioning",})

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
    print(f"wal contract lint ok: {path}")


if __name__ == "__main__":
    lint()
