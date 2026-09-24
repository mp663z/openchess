#!/usr/bin/env python3
"""T0374: privacy local-model contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/local_model.yaml"

ROLE = {
    "kind": ("install-wide-local-large-model-opt-in-and-local-inference-verdict"),
    "serves": ("small-local-models-by-default-and-an-explicit-local-large-model-opt-in"),
    "scope": ("one-install-large-model-mode-transitions-and-the-local-inference-verdict"),
    "owns": (
        "large-model-mode-transition-semantics-host-and-no-egress-ver"
        "dict-and-reference-claim-status"
    ),
    "not_scope": (
        "cloud-mode-collection-flags-byom-terms-redaction-model-weigh"
        "ts-download-chess-evidence-delete-and-retry-idempotency"
    ),
}
RECORD = {
    "fields": [
        "install_id",
        "large_model",
        "revision",
        "reference_claims",
    ],
    "exact": True,
}
IDENTIFIERS = {
    "install_id": {
        "kind": "opaque-local-install-id",
        "grammar": "^ins1:[0-9a-f]{64}$",
        "source": "caller-supplied-exact-built-in-str-validated-never-trusted",
    },
    "large_model": {
        "kind": "local-large-model-mode",
        "grammar": "exact-built-in-str-off-or-on",
        "source": "stored-by-the-install-default-off",
    },
    "revision": {
        "kind": "monotonic-large-model-revision",
        "grammar": "exact-built-in-int-0-through-9223372036854775807-never-bool",
        "source": "derived-by-the-install-never-caller-chosen",
    },
    "reference_claims": {
        "kind": "reference-machine-p95-claim-status",
        "grammar": "exact-built-in-str-valid-or-suspended",
        "source": ("derived-suspended-exactly-while-large-model-is-on-never-caller-set"),
    },
    "opt_in": {
        "kind": "scoped-user-large-model-opt-in-token",
        "grammar": "^lmo1:[0-9a-f]{64}$",
        "binding": ("sha256-over-install-id-and-expected-revision-and-target-large-on"),
        "preimage": {
            "fields": ["install_id", "expected_revision", "target"],
            "separator": "|",
            "trailing_separator": False,
            "encoding": "utf-8",
            "install_id": "the-validated-install-id-verbatim",
            "expected_revision": "ascii-decimal-no-sign-no-leading-zeros-zero-is-0",
            "target": "large-on",
            "example": "ins1:<64-lowercase-hex>|0|large-on",
        },
        "digest": "sha256-of-the-preimage-bytes-as-64-lowercase-hex-after-the-lmo1-prefix",
        "source": (
            "user-settings-surface-that-states-the-published-requirements"
            "-and-the-suspended-p95-claims-never-model-or-imported-conten"
            "t"
        ),
    },
    "tier": {
        "kind": "local-model-tier",
        "grammar": "exact-built-in-str-small-or-large",
        "source": "caller-supplied-validated",
    },
    "host": {
        "kind": "inference-host",
        "grammar": "exact-built-in-str-desktop-web-or-server",
        "source": "caller-supplied-validated",
    },
}
SEMANTICS = {
    "default": ("unknown-or-unset-install-reads-large-model-off-reference-claims-valid"),
    "small_tier": ("always-available-on-the-desktop-host-part-of-the-default-install-no-opt-in"),
    "large_tier": ("available-only-while-large-model-is-exact-on-and-only-on-the-desktop-host"),
    "host": (
        "model-inference-runs-only-on-desktop-web-and-server-never-run-a-model-of-either-tier"
    ),
    "egress": (
        "a-local-model-invocation-never-sends-an-outbound-payload-any"
        "-non-local-destination-is-refused-whatever-the-cloud-mode"
    ),
    "precedence": "malformed-then-egress-then-host-then-large-model-mode",
    "claims": (
        "activation-suspends-the-reference-machine-p95-claims-deactiv"
        "ation-restores-them-reported-on-every-record-and-verdict"
    ),
    "cloud_independence": (
        "the-local-model-verdict-never-reads-or-changes-the-cloud-mode-and-works-with-cloud-off"
    ),
    "evaluation": ("verdict-read-from-current-state-at-invocation-time-never-cached"),
    "turn_off": ("on-to-off-needs-no-approval-and-applies-before-the-next-verdict"),
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
    "atomic": ("rejected-transition-or-verdict-leaves-model-state-and-inputs-unchanged"),
    "fail_closed": "any-doubt-about-the-large-model-mode-reads-off",
    "no_egress": "no-verdict-ever-allows-a-non-local-destination",
}
VERSIONING = {
    "base_path": "/privacy/local-model/v1",
    "rule": (
        "Clients pin MAJOR. MINOR is additive-only: new optional fiel"
        "ds, new enum members, new endpoints. Never change the meanin"
        "g of an existing field in place."
    ),
}
LINKS = {
    "architecture_decision": "docs/adr/ADR-0004-asymmetric-architecture.md",
    "platform_ownership": "docs/adr/ADR-0005-platform-ownership.md",
}
FAILURE_CLASSES = [
    "malformed_model_request",
    "unknown_install",
    "stale_revision",
    "opt_in_missing",
    "egress_requested",
    "host_not_desktop",
    "large_model_off",
    "revision_exhausted",
]
FAILURE_TRIGGERS = {
    "malformed_model_request": (
        "checked-before-every-other-class-request-grammar-or-exact-ty"
        "pe-violation-including-str-dict-list-subclasses-and-non-str-"
        "keys"
    ),
    "unknown_install": "well-formed-install-id-with-no-model-state-on-transition",
    "stale_revision": "expected-revision-not-equal-to-current-revision",
    "opt_in_missing": "turn-on-without-a-bound-opt-in",
    "egress_requested": "well-formed-invocation-naming-a-non-local-destination",
    "host_not_desktop": "well-formed-local-invocation-on-the-web-or-server-host",
    "large_model_off": (
        "large-tier-invocation-on-desktop-while-large-model-reads-off-including-an-unknown-install"
    ),
    "revision_exhausted": (
        "transition-at-revision-9223372036854775807-checked-after-mal"
        "formed-unknown-stale-same-value-noop-and-opt-in"
    ),
}
FAILURE_MAPPING = {
    "malformed_model_request": "malformed_request",
    "unknown_install": "unknown_install",
    "stale_revision": "conflict",
    "opt_in_missing": "approval_required",
    "egress_requested": "local_only",
    "host_not_desktop": "host_refused",
    "large_model_off": "model_unavailable",
    "revision_exhausted": "revision_exhausted",
}
ERROR_ENUM = [
    "malformed_request",
    "unknown_install",
    "conflict",
    "approval_required",
    "local_only",
    "host_refused",
    "model_unavailable",
    "revision_exhausted",
    "internal",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(
        doc,
        contract_id="privacy-local-model",
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
    print(f"local-model contract lint ok: {path}")


if __name__ == "__main__":
    lint()
