#!/usr/bin/env python3
"""T0401: privacy delete contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/delete.yaml"

ROLE = {
    "kind": ("per-collection-user-decided-irreversible-local-delete-with-tombstone"),
    "serves": ("deletions-never-automated-and-a-deleted-collection-never-readable-again"),
    "scope": ("one-collection-delete-transition-erase-across-local-stores-and-access-verdict"),
    "owns": ("delete-semantics-the-tombstone-and-the-collection-access-verdict"),
    "not_scope": (
        "account-delete-on-the-control-plane-account-keys-sync-keys-b"
        "ackups-cloud-mode-collection-flags-byom-terms-redaction-loca"
        "l-model-and-retry-idempotency"
    ),
}
RECORD = {
    "fields": [
        "collection_id",
        "state",
        "revision",
        "pending",
    ],
    "exact": True,
}
IDENTIFIERS = {
    "collection_id": {
        "kind": "opaque-local-collection-id",
        "grammar": "^col1:[0-9a-f]{64}$",
        "source": "caller-supplied-exact-built-in-str-validated-never-trusted",
    },
    "state": {
        "kind": "collection-lifecycle-state",
        "grammar": "exact-built-in-str-live-or-deleted",
        "source": "stored-by-the-deleter-registered-live",
    },
    "revision": {
        "kind": "monotonic-delete-revision",
        "grammar": "exact-built-in-int-0-through-9223372036854775807-never-bool",
        "source": "derived-by-the-deleter-never-caller-chosen",
    },
    "pending": {
        "kind": "stores-not-yet-erased",
        "grammar": (
            "exact-built-in-list-of-store-names-in-declared-store-order-e"
            "mpty-when-live-or-fully-erased"
        ),
        "source": "derived-by-the-deleter-from-erase-outcomes",
    },
    "stores": {
        "kind": "local-stores-holding-collection-data",
        "names": [
            "content",
            "index",
            "analysis",
            "training",
            "ciphertext",
        ],
        "reading": (
            "invented-closed-list-from-adr-0004-desktop-content-and-ciphe"
            "rtext-plus-the-derived-index-engine-analysis-and-training-st"
            "ate"
        ),
    },
    "approval": {
        "kind": "scoped-user-delete-decision-token",
        "grammar": "^del1:[0-9a-f]{64}$",
        "binding": ("sha256-over-collection-id-and-expected-revision-and-target-delete"),
        "preimage": {
            "fields": [
                "collection_id",
                "expected_revision",
                "target",
            ],
            "separator": "|",
            "trailing_separator": False,
            "encoding": "utf-8",
            "collection_id": "the-validated-collection-id-verbatim",
            "expected_revision": "ascii-decimal-no-sign-no-leading-zeros-zero-is-0",
            "target": "delete",
            "example": "col1:<64-lowercase-hex>|0|delete",
        },
        "digest": ("sha256-of-the-preimage-bytes-as-64-lowercase-hex-after-the-del1-prefix"),
        "source": ("user-decision-surface-only-never-model-imported-content-or-automation"),
    },
}
SEMANTICS = {
    "default": ("registering-a-new-collection-reads-live-revision-0-nothing-pending"),
    "decision": (
        "live-to-deleted-needs-a-delete-decision-bound-to-this-collec"
        "tion-and-expected-revision-never-automated"
    ),
    "order": (
        "the-tombstone-is-written-before-any-store-is-erased-then-eve"
        "ry-store-is-erased-in-declared-order"
    ),
    "erase_failure": (
        "a-store-that-fails-to-erase-stays-in-pending-the-collection-"
        "stays-deleted-and-never-reads-live-again"
    ),
    "retry": (
        "a-delete-of-a-deleted-collection-needs-no-decision-keeps-the"
        "-revision-and-re-erases-only-the-pending-stores"
    ),
    "access": (
        "a-live-collection-is-allowed-a-deleted-collection-is-refused"
        "-even-with-nothing-pending-an-unregistered-id-is-unknown"
    ),
    "tombstone": ("registering-a-deleted-collection-id-is-refused-an-id-is-never-reused"),
    "reregister": ("registering-a-live-collection-is-a-no-op-never-resets-state-or-revision"),
    "concurrency": "compare-and-set-on-expected-revision",
    "saturation": (
        "a-delete-at-max-revision-is-never-refused-it-writes-deleted-with-the-revision-unchanged"
    ),
    "rollback": (
        "a-rejected-delete-is-rolled-back-whole-and-touches-no-store-"
        "an-accepted-delete-is-irreversible-there-is-no-undelete"
    ),
    "readings": {
        "decision": (
            "spec-says-deletions-are-never-automated-and-need-an-explicit"
            "-decision-so-a-delete-without-a-bound-decision-is-refused"
        ),
        "irreversible": (
            "spec-silent-an-undelete-would-resurrect-data-the-user-chose-to-erase-so-none-exists"
        ),
        "never_exhausted": (
            "privacy-fail-closed-a-delete-is-never-refused-for-revision-e"
            "xhaustion-as-with-the-byom-revoke-ruling"
        ),
        "stores": (
            "the-five-store-names-are-invented-a-store-outside-the-list-i"
            "s-not-covered-by-this-contract"
        ),
    },
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "atomic": ("a-rejected-delete-or-access-leaves-state-every-store-and-inputs-unchanged"),
    "fail_closed": ("any-doubt-reads-deleted-a-partly-erased-collection-is-never-readable"),
    "never_exhausted": (
        "a-delete-is-never-refused-except-malformed-unknown-stale-or-decision-missing"
    ),
}
VERSIONING = {
    "base_path": "/privacy/delete/v1",
    "rule": (
        "Clients pin MAJOR. MINOR is additive-only: new optional fiel"
        "ds, new enum members, new endpoints. Never change the meanin"
        "g of an existing field in place."
    ),
}
LINKS = {
    "sensitive_flag_contract": "data/contracts/sensitive_flag.yaml",
    "control_plane_contract": "data/contracts/control-plane.yaml",
    "architecture_decision": "docs/adr/ADR-0004-asymmetric-architecture.md",
    "product_report": "docs/plan/product-report-v5.md",
}
FAILURE_CLASSES = [
    "malformed_delete_request",
    "unknown_collection",
    "stale_revision",
    "decision_missing",
    "collection_deleted",
]
FAILURE_TRIGGERS = {
    "malformed_delete_request": (
        "checked-before-every-other-class-request-grammar-or-exact-ty"
        "pe-violation-including-str-dict-list-subclasses-and-non-str-"
        "keys"
    ),
    "unknown_collection": (
        "well-formed-collection-id-that-was-never-registered-on-delete-or-access"
    ),
    "stale_revision": "expected-revision-not-equal-to-current-revision",
    "decision_missing": "delete-of-a-live-collection-without-a-bound-delete-decision",
    "collection_deleted": "access-to-or-registering-of-a-deleted-collection",
}
FAILURE_MAPPING = {
    "malformed_delete_request": "malformed_request",
    "unknown_collection": "unknown_collection",
    "stale_revision": "conflict",
    "decision_missing": "approval_required",
    "collection_deleted": "collection_deleted",
}
ERROR_ENUM = [
    "malformed_request",
    "unknown_collection",
    "conflict",
    "approval_required",
    "collection_deleted",
    "internal",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(
        doc,
        contract_id="privacy-delete",
        sections={
            "errors",
            "failures",
            "id",
            "identifiers",
            "links",
            "properties",
            "record",
            "role",
            "semantics",
            "versioning",
        },
    )

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    if type((cc.get("record") or {}).get("exact")) is not bool:
        raise ContractError("record.exact must be an exact bool")
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    preimage = ((cc.get("identifiers") or {}).get("approval") or {}).get("preimage") or {}
    if type(preimage.get("trailing_separator")) is not bool:
        raise ContractError("approval.preimage.trailing_separator must be an exact bool")
    check("semantics", SEMANTICS, cc.get("semantics"))
    failures = cc["failures"]
    close_failures(failures)
    check("failures.classes", FAILURE_CLASSES, failures.get("classes"))
    check("failures.triggers", FAILURE_TRIGGERS, failures.get("triggers"))
    check("failures.mapping", FAILURE_MAPPING, failures.get("mapping"))
    if failures.get("closed") is not True:
        raise ContractError("failure model must be closed")
    errors = cc["errors"]
    close_errors(errors, shape_keys=("retryable_true_only_for",))
    check("errors.closed_enum", ERROR_ENUM, errors.get("closed_enum"))
    shape = errors.get("shape") or {}
    check(
        "errors.shape.retryable_true_only_for",
        ["internal"],
        shape.get("retryable_true_only_for"),
    )
    check("properties", PROPERTIES, cc.get("properties"))
    check("versioning", VERSIONING, cc.get("versioning"))
    check("links", LINKS, cc.get("links"))
    print(f"delete contract lint ok: {path}")


if __name__ == "__main__":
    lint()
