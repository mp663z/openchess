#!/usr/bin/env python3
"""T0185: graph conflict contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/conflict.yaml"

ROLE = {
    "kind":
        "three-way-edit-incompatibility-detection-over-graph-"
        "states",
    "serves": "no-silent-writes-approval-gate",
    "scope": "base-plus-two-derived-states",
    "owns": "conflict-detection-and-witness-shape",
    "not_scope": "automatic-conflict-resolution",
}
RECORD = {
    "fields": ["base_id", "left_id", "right_id", "conflicts"],
    "exact": True,
}
IDENTITY = {
    "key_source": "from-linked-table-contracts-canonical-identity",
    "digest_keys": "never-keyed-by-accelerator-digests",
    "change_derivation":
        "identity-keyed-symmetric-difference-vs-base",
}
CONFLICT_KINDS = ["both_changed_differently", "changed_vs_removed",
                  "added_differently"]
CONFLICTS_SECTION = {
    "kind": "map-canonical-identity-to-witness",
    "witness": {
        "fields": ["kind", "left", "right"],
        "absent_sentinel": "null-means-side-did-not-touch-identity",
    },
    "kinds": CONFLICT_KINDS,
}
COMPATIBILITY = {
    "disjoint_identities": "always-compatible",
    "identical_outcomes":
        "byte-identical-add-remove-or-change-compatible",
    "everything_else": "incompatible-and-reported",
}
IDENTIFIERS = {
    "state_id": {
        "kind": "canonical-state-content-digest",
        "grammar": "^gs1:[0-9a-f]{64}$",
        "derivation":
            "sha256-over-canonical-serialization-of-sorted-"
            "identity-record-map",
        "source":
            "derived-from-validated-state-never-caller-supplied",
    },
}
BASE_CHECK = {
    "distinct_ids":
        "derived-base-id-distinct-from-each-derived-side-id",
    "violation": "divergent_base-fail-closed-on-derived-id-"
                 "collision",
}
GUARANTEES = {
    "determinism": "pure-function-canonical-identity-order",
    "symmetry": "left-right-swap-flips-witnesses",
    "completeness":
        "every-incompatible-overlap-surfaces-empty-iff-compatible",
    "no_silent_resolution": "conflicts-reported-never-auto-merged",
}
FAILURE_CLASSES = ["malformed_conflict_record", "divergent_base"]
FAILURE_TRIGGERS = {
    "malformed_conflict_record":
        "state-validation-field-set-grammar-witness-shape-or-"
        "kind-violation",
    "divergent_base": "base-derived-id-equals-a-side-derived-id",
}
FAILURE_MAPPING = {
    "malformed_conflict_record": "malformed_request",
    "divergent_base": "divergent_base",
}
ERROR_ENUM = ["malformed_request", "divergent_base", "internal"]
PROPERTIES = {
    "witnesses_exact": "both-side-exact-change-payloads",
    "atomic": "detection-never-mutates-inputs",
    "canonical_order": "conflicts-ordered-by-canonical-identity",
    "state_validation":
        "every-state-validated-through-linked-machinery-before-"
        "change-derivation",
}
VERSIONING = {
    "base_path": "/graph/conflict/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "collision_contract": "data/contracts/collision.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="graph-conflict",
        sections={"base_check", "compatibility", "conflicts_section", "errors", "failures",
            "guarantees", "id", "identifiers", "identity", "links", "properties", "record",
            "role", "versioning",})

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("identity", IDENTITY, cc.get("identity"))
    check("conflicts_section", CONFLICTS_SECTION,
          cc.get("conflicts_section"))
    check("compatibility", COMPATIBILITY, cc.get("compatibility"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("base_check", BASE_CHECK, cc.get("base_check"))
    check("guarantees", GUARANTEES, cc.get("guarantees"))
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
    print(f"conflict contract lint ok: {path}")


if __name__ == "__main__":
    lint()
