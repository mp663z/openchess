#!/usr/bin/env python3
"""T0365: privacy cloud-off contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/cloud_off.yaml"

ROLE = {
    "kind": ("install-wide-cloud-mode-switch-gating-every-cloud-model-egress"),
    "serves": "hosted-byom-opt-in-only-and-full-loop-offline",
    "scope": ("one-install-cloud-mode-transitions-and-composed-egress-verdict"),
    "owns": ("cloud-mode-transition-semantics-and-install-level-egress-verdict"),
    "not_scope": (
        "collection-flag-state-redaction-byom-terms-payload-schema-lo"
        "cal-model-choice-delete-and-retry-idempotency"
    ),
}
RECORD = {
    "fields": [
        "install_id",
        "cloud_mode",
        "revision",
    ],
    "exact": True,
}
IDENTIFIERS = {
    "install_id": {
        "kind": "opaque-local-install-id",
        "grammar": "^ins1:[0-9a-f]{64}$",
        "source": "caller-supplied-exact-built-in-str-validated-never-trusted",
    },
    "cloud_mode": {
        "kind": "install-cloud-mode",
        "grammar": "exact-built-in-str-off-or-on",
        "source": "stored-by-the-switch-default-off",
    },
    "revision": {
        "kind": "monotonic-cloud-mode-revision",
        "grammar": "exact-built-in-int-0-through-9223372036854775807-never-bool",
        "source": "derived-by-the-switch-never-caller-chosen",
    },
    "opt_in": {
        "kind": "scoped-user-opt-in-token",
        "grammar": "^opt1:[0-9a-f]{64}$",
        "binding": "sha256-over-install-id-and-expected-revision-and-target-on",
        "preimage": {
            "fields": ["install_id", "expected_revision", "target"],
            "separator": "|",
            "trailing_separator": False,
            "encoding": "utf-8",
            "install_id": "the-validated-install-id-verbatim",
            "expected_revision": "ascii-decimal-no-sign-no-leading-zeros-zero-is-0",
            "target": "on",
            "example": "ins1:<64-lowercase-hex>|0|on",
        },
        "digest": "sha256-of-the-preimage-bytes-as-64-lowercase-hex-after-the-opt1-prefix",
        "source": "user-settings-surface-only-never-model-or-imported-content",
    },
}
SEMANTICS = {
    "default": "unknown-or-unset-install-reads-cloud-mode-off",
    "egress": (
        "cloud-destination-allowed-only-when-cloud-mode-is-exact-on-a"
        "nd-the-collection-flag-reads-exact-false-local-always-allowe"
        "d"
    ),
    "precedence": (
        "malformed-then-cloud-mode-then-collection-flag-the-switch-is-checked-before-the-flag"
    ),
    "composition": (
        "collection-flag-read-from-the-sensitive-flag-contract-never-"
        "re-owned-unknown-collection-reads-sensitive"
    ),
    "evaluation": (
        "egress-verdict-read-from-current-state-at-send-time-one-verdict-per-send-never-cached"
    ),
    "turn_off": ("on-to-off-needs-no-approval-and-applies-before-the-next-egress-verdict"),
    "turn_on": ("off-to-on-needs-an-opt-in-bound-to-this-install-and-expected-revision"),
    "same_value": "set-to-the-current-mode-is-a-no-op-revision-unchanged",
    "concurrency": "compare-and-set-on-expected-revision",
    "saturation": ("transition-at-max-revision-fails-typed-never-saturates-or-wraps"),
    "rollback": (
        "no-history-operation-a-rejected-transition-is-rolled-back-wh"
        "ole-undoing-on-is-turn-off-undoing-off-is-a-fresh-opt-in"
    ),
    "reregister": ("registering-a-known-install-is-a-no-op-never-resets-mode-or-revision"),
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "atomic": (
        "rejected-transition-or-verdict-leaves-switch-state-flag-state-and-inputs-unchanged"
    ),
    "fail_closed": "any-doubt-about-the-cloud-mode-reads-off",
    "kill_switch": (
        "turning-cloud-off-never-needs-approval-and-is-never-refused-"
        "except-malformed-unknown-stale-or-exhausted"
    ),
}
VERSIONING = {
    "base_path": "/privacy/cloud-off/v1",
    "rule": (
        "Clients pin MAJOR. MINOR is additive-only: new optional fiel"
        "ds, new enum members, new endpoints. Never change the meanin"
        "g of an existing field in place."
    ),
}
LINKS = {
    "sensitive_flag_contract": "data/contracts/sensitive_flag.yaml",
    "architecture_decision": "docs/adr/ADR-0004-asymmetric-architecture.md",
}
FAILURE_CLASSES = [
    "malformed_cloud_request",
    "unknown_install",
    "stale_revision",
    "opt_in_missing",
    "cloud_mode_off",
    "collection_sensitive",
    "revision_exhausted",
]
FAILURE_TRIGGERS = {
    "malformed_cloud_request": (
        "checked-before-every-other-class-request-grammar-or-exact-ty"
        "pe-violation-including-str-dict-list-subclasses-and-non-str-"
        "keys"
    ),
    "unknown_install": "well-formed-install-id-with-no-switch-state-on-transition",
    "stale_revision": "expected-revision-not-equal-to-current-revision",
    "opt_in_missing": "turn-on-without-a-bound-opt-in",
    "cloud_mode_off": (
        "cloud-destination-requested-while-cloud-mode-reads-off-including-an-unknown-install"
    ),
    "collection_sensitive": (
        "cloud-destination-requested-with-cloud-mode-on-while-the-collection-flag-reads-sensitive"
    ),
    "revision_exhausted": (
        "transition-at-revision-9223372036854775807-checked-after-mal"
        "formed-unknown-stale-same-value-noop-and-opt-in"
    ),
}
FAILURE_MAPPING = {
    "malformed_cloud_request": "malformed_request",
    "unknown_install": "unknown_install",
    "stale_revision": "conflict",
    "opt_in_missing": "approval_required",
    "cloud_mode_off": "cloud_off",
    "collection_sensitive": "cloud_off",
    "revision_exhausted": "revision_exhausted",
}
ERROR_ENUM = [
    "malformed_request",
    "unknown_install",
    "conflict",
    "approval_required",
    "cloud_off",
    "revision_exhausted",
    "internal",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(
        doc,
        contract_id="privacy-cloud-off",
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
    preimage = ((cc.get("identifiers") or {}).get("opt_in") or {}).get("preimage") or {}
    if type(preimage.get("trailing_separator")) is not bool:
        raise ContractError("opt_in.preimage.trailing_separator must be an exact bool")
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
    print(f"cloud-off contract lint ok: {path}")


if __name__ == "__main__":
    lint()
