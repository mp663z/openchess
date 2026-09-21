#!/usr/bin/env python3
"""T0176: graph diff contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/diff.yaml"

ROLE = {
    "kind": "exact-difference-record-between-two-graph-states",
    "serves": "no-silent-writes-approval-gate",
    "scope": "graph-state-differences",
    "owns": "diff-record-shape-and-computation-semantics",
    "not_scope": "scoring-or-ranking-of-changes",
}
RECORD = {
    "fields": ["base_id", "target_id", "added", "removed",
               "changed"],
    "exact": True,
}
IDENTITY = {
    "key_source": "from-linked-table-contracts-canonical-identity",
    "digest_keys": "never-keyed-by-accelerator-digests",
    "restated": "never",
}
SECTIONS = {
    "added": {
        "kind": "map-canonical-identity-to-exact-target-record",
        "meaning": "identity-present-in-target-only",
        "validation":
            "value-exact-valid-linked-record-key-equals-derived-"
            "identity",
    },
    "removed": {
        "kind": "map-canonical-identity-to-exact-base-record",
        "meaning": "identity-present-in-base-only",
        "validation":
            "value-exact-valid-linked-record-key-equals-derived-"
            "identity",
    },
    "changed": {
        "kind": "map-canonical-identity-to-witness-pair",
        "witness": "exact-base-record-and-exact-target-record",
        "meaning": "identity-in-both-with-unequal-exact-content",
        "validation":
            "both-records-valid-same-derived-key-base-not-equal-"
            "target",
    },
}
ID_GRAMMAR = "^gs1:[0-9a-f]{64}$"
_DERIVATION = ("sha256-over-canonical-serialization-of-full-"
               "identity-record-map")
IDENTIFIERS = {
    "base_id": {"kind": "canonical-state-content-digest",
                "grammar": ID_GRAMMAR, "total_length": 68,
                "prefix": "gs1:", "derivation": _DERIVATION},
    "target_id": {"kind": "canonical-state-content-digest",
                  "grammar": ID_GRAMMAR, "total_length": 68,
                  "prefix": "gs1:", "derivation": _DERIVATION},
    "distinctness":
        "equal-state-ids-imply-equal-states-and-empty-diff",
}
GUARANTEES = {
    "completeness":
        "every-difference-surfaces-empty-iff-equal-records",
    "determinism":
        "pure-function-of-base-and-target-canonical-order",
    "symmetry":
        "reverse-swaps-added-removed-and-flips-witnesses",
    "apply_exactness": "apply-diff-to-base-yields-target-exactly",
}
APPLY = {
    "semantics": "remove-removed-add-added-replace-changed",
    "base_check":
        "recomputed-base-state-id-must-equal-diff-base-id-and-"
        "added-identities-must-be-absent",
    "commit": "atomic-staged-copy",
    "target_check":
        "recomputed-staged-target-state-id-must-equal-diff-"
        "target-id-before-return",
}
FAILURE_CLASSES = ["malformed_diff_record", "conflicting_base",
                   "unknown_identity", "divergent_target"]
FAILURE_TRIGGERS = {
    "malformed_diff_record":
        "diff-or-state-shape-grammar-identity-or-record-"
        "violation",
    "conflicting_base":
        "recomputed-base-id-differs-or-added-identity-already-"
        "present",
    "unknown_identity":
        "changed-or-removed-identity-absent-from-base",
    "divergent_target":
        "recomputed-staged-target-state-id-differs-from-diff-"
        "target-id-or-empty-diff-with-unequal-ids",
}
FAILURE_MAPPING = {
    "malformed_diff_record": "malformed_request",
    "conflicting_base": "conflicting_base",
    "unknown_identity": "unknown_identity",
    "divergent_target": "malformed_request",
}
ERROR_ENUM = ["malformed_request", "conflicting_base",
              "unknown_identity", "internal"]
PROPERTIES = {
    "total_compute":
        "hostile-state-inputs-fail-closed-typed-never-raw",
    "structural_base_check":
        "whole-base-verified-via-state-digest-before-mutation",
    "no_silent_difference": "completeness-theorem-pinned",
    "rollback": "rejected-apply-leaves-base-bit-identical",
    "canonical_order": "sections-ordered-by-canonical-identity",
    "target_verification":
        "applied-staged-result-digest-must-equal-target-id-before-"
        "return",
    "id_agreement":
        "empty-diff-equal-ids-nonempty-diff-distinct-ids",
}
VERSIONING = {
    "base_path": "/graph/diff/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "collision_contract": "data/contracts/collision.yaml",
    "position_digest_contract": "data/contracts/position_digest.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="graph-diff",
        sections={"apply", "errors", "failures", "guarantees", "id", "identifiers",
            "identity", "links", "properties", "record", "role", "sections", "versioning",})

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("identity", IDENTITY, cc.get("identity"))
    check("sections", SECTIONS, cc.get("sections"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("guarantees", GUARANTEES, cc.get("guarantees"))
    check("apply", APPLY, cc.get("apply"))
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
    if set(failures["mapping"]) != set(failures["classes"]):
        raise ContractError(
            "failures: mapping keys must equal declared classes")
    if set(failures["triggers"]) != set(failures["classes"]):
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
    print(f"diff contract lint ok: {path}")


if __name__ == "__main__":
    lint()
