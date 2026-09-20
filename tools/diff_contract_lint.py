#!/usr/bin/env python3
"""T0176: graph diff contract lint - exact structured pins."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    },
    "removed": {
        "kind": "map-canonical-identity-to-exact-base-record",
        "meaning": "identity-present-in-base-only",
    },
    "changed": {
        "kind": "map-canonical-identity-to-witness-pair",
        "witness": "exact-base-record-and-exact-target-record",
        "meaning": "identity-in-both-with-unequal-exact-content",
    },
}
ID_GRAMMAR = "^[a-z0-9][a-z0-9._-]{0,63}$"
IDENTIFIERS = {
    "base_id": {"kind": "opaque-state-identifier",
                "grammar": ID_GRAMMAR},
    "target_id": {"kind": "opaque-state-identifier",
                  "grammar": ID_GRAMMAR},
    "distinctness":
        "base-id-and-target-id-may-be-equal-only-when-diff-empty",
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
    "base_check": "base-must-match-diff-base-exactly",
    "commit": "atomic-staged-copy",
}
FAILURE_CLASSES = ["malformed_diff_record", "conflicting_base",
                   "unknown_identity"]
FAILURE_TRIGGERS = {
    "malformed_diff_record":
        "diff-field-shape-grammar-or-reference-violation",
    "conflicting_base": "apply-base-differs-from-diff-base",
    "unknown_identity":
        "changed-or-removed-identity-absent-from-base",
}
FAILURE_MAPPING = {
    "malformed_diff_record": "malformed_request",
    "conflicting_base": "conflicting_base",
    "unknown_identity": "unknown_identity",
}
ERROR_ENUM = ["malformed_request", "conflicting_base",
              "unknown_identity", "internal"]
PROPERTIES = {
    "no_silent_difference": "completeness-theorem-pinned",
    "rollback": "rejected-apply-leaves-base-bit-identical",
    "canonical_order": "sections-ordered-by-canonical-identity",
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
    cc = doc.get("contract")
    if not isinstance(cc, dict):
        raise ContractError("diff contract missing")

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
    failures = cc.get("failures") or {}
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
    errors = cc.get("errors") or {}
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
