#!/usr/bin/env python3
"""T0167: version contract lint - exact structured pins.

The graph-content version contract must pin its record shape,
content-addressed identity, lineage invariants, failure model and
merge semantics as exact structured values, never prose alone.
"""

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

CONTRACT = ROOT / "data/contracts/version.yaml"

ROLE = {
    "kind": "immutable-versioned-snapshot-reference-over-graph-content",
    "serves": "trusted-diagnosis-reproducibility",
    "scope": "graph-content-versions",
    "owns": "version-record-identity-and-lineage",
    "versions": "graph-content-never-contract-schemas",
}
RECORD = {
    "fields": ["version_id", "parent_ids", "graph_digest",
               "created_at", "label"],
    "exact": True,
    "immutability": "never-in-place-only-new-versions",
}
IDENTITY = {
    "version_id": {
        "addressing": "content-addressed-over-parent-ids-and-graph-digest",
        "grammar": "^gv1:[0-9a-f]{64}$",
        "total_length": 68,
        "prefix": "gv1:",
    },
    "label": "metadata-never-identity",
    "graph_digest": "content-input-never-record-identity",
}
CONTENT_ADDRESSING = {
    "algorithm": "sha256-over-canonical-encoding",
    "canonical_encoding":
        "gv1-content\\n+sorted-parent-ids-each-followed-by-\\n"
        "+graph-digest+\\n",
    "dedup": "equal-parents-and-graph-digest-yield-equal-version-id",
    "collision_policy":
        "equal-id-unequal-content-fails-closed-never-overwrites",
}
FIELDS = {
    "parent_ids": {
        "kind": "list-of-version-ids",
        "item_grammar": "from-identity-version_id-grammar",
        "root_only_empty": True,
        "order": "insignificant-canonicalized-sorted",
    },
    "graph_digest": {
        "kind": "graph-state-content-hash",
        "grammar": "^gdv1:[0-9a-f]{64}$",
        "total_length": 68,
        "prefix": "gdv1:",
    },
    "created_at": {
        "kind": "rfc3339-utc-instant",
        "grammar": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}Z$",
        "calendar": "gregorian-real-date-with-leap-year-rules",
        "leap_seconds": "rejected-second-field-must-be-00-59",
        "monotonicity": "child-created-at-not-before-every-parent",
    },
    "label": {
        "kind": "human-readable-metadata",
        "grammar": "^[ -~]{1,120}$",
    },
}
LINEAGE = {
    "shape": "directed-acyclic-graph-of-version-records",
    "root": "exactly-one-root-version-with-empty-parent-ids",
    "non_root": "nonempty-parent-ids-each-existing-in-store",
    "parent_existence": "fail-closed-on-unknown-parent",
}
MERGE = {
    "semantics": "insert-or-return-existing",
    "commit": "atomic-staged-copy",
    "idempotent": True,
    "commutative": True,
    "associative": True,
}
FAILURE_CLASSES = ["malformed_version_record", "unknown_parent",
                   "conflicting_version", "root_violation",
                   "nonmonotonic_version"]
FAILURE_TRIGGERS = {
    "malformed_version_record":
        "field-set-shape-grammar-or-content-address-violation",
    "unknown_parent": "parent-id-absent-from-store",
    "conflicting_version":
        "equal-id-unequal-content-or-divergent-metadata-"
        "fail-closed",
    "root_violation": "second-root-claim",
    "nonmonotonic_version": "child-older-than-a-parent",
}
FAILURE_MAPPING = {
    "malformed_version_record": "malformed_request",
    "unknown_parent": "unknown_parent",
    "conflicting_version": "conflicting_version",
    "root_violation": "root_violation",
    "nonmonotonic_version": "nonmonotonic_version",
}
ERROR_ENUM = ["malformed_request", "unknown_parent",
              "conflicting_version", "root_violation",
              "nonmonotonic_version", "internal"]
PROPERTIES = {
    "metadata_policy":
        "equal-metadata-required-for-existing-id-divergence-"
        "rejected",
    "dedup": "insert-or-return-existing-never-duplicates",
    "rollback": "rejected-insert-leaves-store-bit-identical",
    "determinism": "canonical-view-sorted-by-version-id",
    "trust_boundary":
        "injected-digest-collision-surfaced-never-absorbed",
}
VERSIONING = {
    "base_path": "/graph/version/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {"variant_contract": "data/contracts/variant.yaml"}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="graph-version",
        sections={"content_addressing", "errors", "failures", "fields", "id", "identity",
            "lineage", "links", "merge", "properties", "record", "role", "versioning",})

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("identity", IDENTITY, cc.get("identity"))
    check("content_addressing", CONTENT_ADDRESSING,
          cc.get("content_addressing"))
    check("fields", FIELDS, cc.get("fields"))
    check("lineage", LINEAGE, cc.get("lineage"))
    check("merge", MERGE, cc.get("merge"))
    failures = cc["failures"]
    close_failures(failures)
    check("failures.classes", FAILURE_CLASSES,
          failures.get("classes"))
    check("failures.triggers", FAILURE_TRIGGERS,
          failures.get("triggers"))
    if set(failures.get("triggers", {})) != \
            set(FAILURE_CLASSES):
        raise ContractError(
            "failures: triggers keys must equal declared classes")
    check("failures.mapping", FAILURE_MAPPING,
          failures.get("mapping"))
    if failures.get("closed") is not True:
        raise ContractError("failure model must be closed")
    errors = cc["errors"]
    close_errors(
        errors,
        shape_keys=(
            "collision_witness_included_on_conflicting_"
            "version",
            "retryable_true_only_for"))
    check("errors.closed_enum", ERROR_ENUM,
          errors.get("closed_enum"))
    shape = errors.get("shape") or {}
    check("errors.shape.retryable_true_only_for", ["internal"],
          shape.get("retryable_true_only_for"))
    if shape.get(
            "collision_witness_included_on_conflicting_version"
            ) is not True:
        raise ContractError(
            "conflicting_version must include a collision witness")
    check("properties", PROPERTIES, cc.get("properties"))
    check("versioning", VERSIONING, cc.get("versioning"))
    check("links", LINKS, cc.get("links"))

    # version_id grammar must match its own pinned shape
    grammar = IDENTITY["version_id"]["grammar"]
    import re
    if re.fullmatch(grammar.replace("^", "").replace("$", ""),
                    "") is not None:
        raise ContractError("version_id grammar must not match"
                            " empty")
    print(f"version contract lint ok: {path}")


if __name__ == "__main__":
    lint()
