#!/usr/bin/env python3
"""T0356: privacy sensitive-flag contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/sensitive_flag.yaml"

ROLE = {
    "kind": "per-collection-sensitive-flag-gating-cloud-model-egress",
    "serves": "collection-scoped-cloud-model-access",
    "scope": "one-collection-flag-state-transitions-and-egress-verdict",
    "owns": "flag-state-transition-semantics-and-cloud-egress-verdict",
    "not_scope": "redaction-byom-terms-local-model-choice-delete-and-retry-idempotency",
}
RECORD = {"fields": ["collection_id", "sensitive", "revision", "prior"], "exact": True}
IDENTIFIERS = {
    "collection_id": {
        "kind": "opaque-local-collection-id",
        "grammar": "^col1:[0-9a-f]{64}$",
        "source": "caller-supplied-exact-built-in-str-validated-never-trusted",
    },
    "revision": {
        "kind": "monotonic-flag-revision",
        "grammar": "exact-built-in-int-0-through-9223372036854775807-never-bool",
        "source": "derived-by-the-store-never-caller-chosen",
    },
    "approval": {
        "kind": "scoped-user-approval-token",
        "grammar": "^apv1:[0-9a-f]{64}$",
        "binding": "sha256-over-collection-id-and-expected-revision-and-target-false",
        "preimage": {
            "fields": ["collection_id", "expected_revision", "target"],
            "separator": "|",
            "trailing_separator": False,
            "encoding": "utf-8",
            "collection_id": "the-validated-collection-id-verbatim",
            "expected_revision": "ascii-decimal-no-sign-no-leading-zeros-zero-is-0",
            "target": "false",
            "example": "col1:<64-lowercase-hex>|0|false",
        },
        "digest": "sha256-of-the-preimage-bytes-as-64-lowercase-hex-after-the-apv1-prefix",
        "source": "user-approval-surface-only-never-model-or-imported-content",
    },
}
SEMANTICS = {
    "default": "unknown-or-unset-flag-reads-sensitive-true-cloud-off",
    "egress": "cloud-destination-allowed-only-when-flag-is-exact-false-local-always-allowed",
    "evaluation": "egress-verdict-read-from-current-state-at-send-time-never-cached",
    "raise": "false-to-true-needs-no-approval-and-applies-before-the-next-egress-verdict",
    "lower": "true-to-false-needs-an-approval-bound-to-this-collection-and-expected-revision",
    "same_value": "set-to-the-current-value-is-a-no-op-revision-unchanged",
    "concurrency": "compare-and-set-on-expected-revision",
    "saturation": ("transition-or-rollback-at-max-revision-fails-typed-never-saturates-or-wraps"),
    "history": "every-applied-transition-pushes-its-prior-value",
    "rollback": (
        "pops-one-history-entry-and-restores-it-one-step-back-per-cal"
        "l-never-pushed-empty-history-fails"
    ),
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "atomic": "rejected-transition-leaves-flag-state-and-inputs-unchanged",
    "fail_closed": "any-doubt-about-the-flag-reads-sensitive-true",
    "rollback": "rollback-walks-back-one-step-under-the-same-transition-rules",
}
VERSIONING = {
    "base_path": "/privacy/sensitive-flag/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional "
    "fields, new enum members, new endpoints. Never change the "
    "meaning of an existing field in place.",
}
LINKS = {
    "control_plane_contract": "data/contracts/control-plane.yaml",
    "rights_policy_contract": "data/contracts/rights_policy.yaml",
}
FAILURE_CLASSES = [
    "malformed_flag_request",
    "unknown_collection",
    "stale_revision",
    "approval_missing",
    "egress_blocked",
    "empty_history",
    "revision_exhausted",
]
FAILURE_TRIGGERS = {
    "malformed_flag_request": (
        "checked-before-every-other-class-request-grammar-or-exact-ty"
        "pe-violation-including-str-dict-list-subclasses-and-int-for-"
        "bool"
    ),
    "unknown_collection": "well-formed-collection-id-with-no-flag-state-on-transition-or-rollback",
    "stale_revision": "expected-revision-not-equal-to-current-revision",
    "approval_missing": "lowering-transition-or-rollback-to-false-without-a-bound-approval",
    "egress_blocked": "cloud-destination-requested-while-flag-reads-true",
    "empty_history": "rollback-with-no-history-entry-left-to-pop",
    "revision_exhausted": (
        "transition-or-rollback-on-a-collection-at-revision-"
        "9223372036854775807-checked-after-malformed-unknown-stale-"
        "same-value-noop-empty-history-and-approval"
    ),
}
FAILURE_MAPPING = {
    "malformed_flag_request": "malformed_request",
    "unknown_collection": "unknown_collection",
    "stale_revision": "conflict",
    "approval_missing": "approval_required",
    "egress_blocked": "cloud_off",
    "empty_history": "nothing_to_roll_back",
    "revision_exhausted": "revision_exhausted",
}
ERROR_ENUM = [
    "malformed_request",
    "unknown_collection",
    "conflict",
    "approval_required",
    "cloud_off",
    "nothing_to_roll_back",
    "revision_exhausted",
    "internal",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(
        doc,
        contract_id="privacy-sensitive-flag",
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
    print(f"sensitive flag contract lint ok: {path}")


if __name__ == "__main__":
    lint()
