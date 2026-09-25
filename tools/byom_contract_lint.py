#!/usr/bin/env python3
"""T0383: privacy BYOM contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/byom.yaml"

ROLE = {
    "kind": ("per-install-per-provider-byom-terms-acceptance-and-hosted-byom-send-verdict"),
    "serves": (
        "hosted-byom-opt-in-only-with-provider-terms-exposed-and-accepted-and-a-closed-payload"
    ),
    "scope": (
        "one-install-one-provider-terms-transitions-and-the-composed-hosted-byom-send-verdict"
    ),
    "owns": ("provider-terms-digest-acceptance-semantics-and-the-hosted-byom-payload-schema-gate"),
    "not_scope": (
        "cloud-mode-collection-flags-provider-key-storage-provider-ad"
        "apters-provenance-token-limits-redaction-local-model-delete-"
        "and-retry-idempotency"
    ),
}

RECORD = {
    "fields": [
        "install_id",
        "provider_id",
        "accepted_terms",
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
    "provider_id": {
        "kind": "provider-neutral-byom-provider-id",
        "grammar": "^prv1:[a-z0-9]{1,32}$",
        "source": "caller-supplied-exact-built-in-str-validated-never-trusted",
    },
    "terms": {
        "kind": "provider-terms-exposed-by-the-adapter",
        "fields": [
            "retention_days",
            "region",
            "trains_on_inputs",
        ],
        "retention_days": "exact-built-in-int-0-through-3650-never-bool",
        "region": "exact-built-in-str-matching-^[a-z]{2}$",
        "trains_on_inputs": "exact-built-in-bool",
        "exact": True,
        "source": ("the-provider-adapter-at-send-time-never-model-or-imported-content"),
        "region_reading": (
            "invented-two-lowercase-letter-grammar-providers-using-other-"
            "forms-such-as-us-east-1-fail-closed-as-malformed-request-and"
            "-cannot-be-accepted"
        ),
        "retention_reading": (
            "0-through-3650-days-cannot-represent-indefinite-retention-su"
            "ch-a-provider-fails-closed-as-malformed-request-and-cannot-b"
            "e-accepted"
        ),
    },
    "terms_digest": {
        "kind": "provider-terms-digest",
        "grammar": "^trm1:[0-9a-f]{64}$",
        "preimage": {
            "fields": [
                "retention_days",
                "region",
                "trains_on_inputs",
            ],
            "separator": "|",
            "trailing_separator": False,
            "encoding": "utf-8",
            "retention_days": "ascii-decimal-no-sign-no-leading-zeros-zero-is-0",
            "region": "the-validated-region-verbatim",
            "trains_on_inputs": "ascii-true-or-false-lowercase",
            "example": "30|eu|false",
        },
        "digest": ("sha256-of-the-preimage-bytes-as-64-lowercase-hex-after-the-trm1-prefix"),
    },
    "accepted_terms": {
        "kind": "accepted-provider-terms-digest",
        "grammar": "null-or-a-terms-digest",
        "source": "derived-by-the-store-default-null-never-caller-set",
    },
    "revision": {
        "kind": "monotonic-provider-terms-revision",
        "grammar": "exact-built-in-int-0-through-9223372036854775807-never-bool",
        "source": "derived-by-the-store-never-caller-chosen",
    },
    "approval": {
        "kind": "scoped-user-byom-terms-approval-token",
        "grammar": "^byt1:[0-9a-f]{64}$",
        "binding": ("sha256-over-install-id-provider-id-terms-digest-and-expected-revision"),
        "preimage": {
            "fields": [
                "install_id",
                "provider_id",
                "terms_digest",
                "expected_revision",
            ],
            "separator": "|",
            "trailing_separator": False,
            "encoding": "utf-8",
            "install_id": "the-validated-install-id-verbatim",
            "provider_id": "the-validated-provider-id-verbatim",
            "terms_digest": "the-trm1-digest-of-the-terms-being-accepted-verbatim",
            "expected_revision": "ascii-decimal-no-sign-no-leading-zeros-zero-is-0",
            "example": "ins1:<64-lowercase-hex>|prv1:<id>|trm1:<64-lowercase-hex>|0",
        },
        "digest": ("sha256-of-the-preimage-bytes-as-64-lowercase-hex-after-the-byt1-prefix"),
        "source": (
            "user-settings-surface-that-shows-these-exact-terms-never-model-or-imported-content"
        ),
    },
    "payload": {
        "kind": "hosted-byom-request-payload",
        "container": "exact-built-in-dict-with-exact-built-in-str-keys",
        "allowed_keys": [
            "fen",
            "moves",
            "task",
            "max-tokens",
        ],
        "mandatory_keys": [
            "fen",
            "task",
        ],
        "fen": ("exact-built-in-str-1-through-128-chars-each-printable-ascii-0x20-through-0x7e"),
        "moves": (
            "exact-built-in-list-0-through-512-items-each-an-exact-built-"
            "in-str-1-through-16-chars-printable-ascii-0x21-through-0x7e"
        ),
        "task": (
            "exact-built-in-str-delta-explanation-error-diagnosis-weekly-"
            "plan-or-review-conversation"
        ),
        "max-tokens": "exact-built-in-int-1-through-4096-never-bool",
        "never_receives": [
            "account-keys",
            "full-corpus",
            "sync-keys",
        ],
    },
}

SEMANTICS = {
    "default": "unknown-or-unset-provider-reads-no-accepted-terms",
    "send": (
        "a-hosted-byom-send-is-allowed-only-when-the-payload-passes-t"
        "he-closed-schema-and-the-cloud-verdict-allows-cloud-and-the-"
        "current-adapter-terms-digest-equals-the-accepted-digest"
    ),
    "precedence": (
        "malformed-then-payload-then-cloud-mode-then-collection-flag-"
        "then-terms-not-accepted-then-terms-changed"
    ),
    "composition": (
        "the-cloud-verdict-is-read-from-the-cloud-off-contract-which-"
        "reads-the-sensitive-flag-contract-never-re-owned"
    ),
    "terms_change": (
        "any-change-to-the-adapter-terms-changes-the-digest-and-lapse"
        "s-the-acceptance-until-a-fresh-approval"
    ),
    "evaluation": (
        "send-verdict-read-from-current-state-at-send-time-one-verdict-per-send-never-cached"
    ),
    "accept": (
        "accepting-terms-needs-an-approval-bound-to-this-install-prov"
        "ider-terms-digest-and-expected-revision-including-a-change-f"
        "rom-other-accepted-terms"
    ),
    "revoke": (
        "revoking-needs-no-approval-applies-before-the-next-send-verd"
        "ict-and-is-never-refused-for-revision-exhaustion"
    ),
    "same_value": (
        "accepting-the-currently-accepted-digest-or-revoking-with-non"
        "e-accepted-is-a-no-op-revision-unchanged"
    ),
    "concurrency": "compare-and-set-on-expected-revision",
    "saturation": (
        "an-accept-at-max-revision-fails-typed-never-saturates-or-wra"
        "ps-a-revoke-at-max-revision-writes-null-with-the-revision-un"
        "changed-a-terminal-state"
    ),
    "rollback": (
        "no-history-operation-a-rejected-transition-is-rolled-back-wh"
        "ole-undoing-an-accept-is-a-revoke-undoing-a-revoke-is-a-fres"
        "h-approval"
    ),
    "reregister": ("registering-a-known-provider-is-a-no-op-never-resets-terms-or-revision"),
    "keys": ("no-request-record-or-verdict-carries-a-provider-key-account-key-or-sync-key"),
}

PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "atomic": (
        "rejected-transition-or-verdict-leaves-terms-state-switch-sta"
        "te-flag-state-and-inputs-unchanged"
    ),
    "fail_closed": "any-doubt-about-acceptance-reads-not-accepted",
    "closed_payload": ("nothing-outside-the-declared-schema-fields-ever-reaches-a-provider"),
    "kill_switch": "revoking-is-never-refused-except-malformed-unknown-or-stale",
}

VERSIONING = {
    "base_path": "/privacy/byom/v1",
    "rule": (
        "Clients pin MAJOR. MINOR is additive-only: new optional fiel"
        "ds, new enum members, new endpoints. Never change the meanin"
        "g of an existing field in place."
    ),
}

LINKS = {
    "cloud_off_contract": "data/contracts/cloud_off.yaml",
    "sensitive_flag_contract": "data/contracts/sensitive_flag.yaml",
    "architecture_decision": "docs/adr/ADR-0004-asymmetric-architecture.md",
}

FAILURE_CLASSES = [
    "malformed_byom_request",
    "payload_rejected",
    "unknown_provider",
    "stale_revision",
    "approval_missing",
    "cloud_mode_off",
    "collection_sensitive",
    "terms_not_accepted",
    "terms_changed",
    "revision_exhausted",
]
FAILURE_TRIGGERS = {
    "malformed_byom_request": (
        "checked-before-every-other-class-envelope-or-terms-grammar-o"
        "r-exact-type-violation-including-str-dict-list-subclasses-an"
        "d-non-str-keys"
    ),
    "payload_rejected": (
        "well-formed-envelope-whose-payload-violates-the-closed-schem"
        "a-checked-before-any-state-is-read"
    ),
    "unknown_provider": ("well-formed-install-and-provider-with-no-terms-state-on-transition"),
    "stale_revision": "expected-revision-not-equal-to-current-revision",
    "approval_missing": "accepting-terms-without-a-bound-approval",
    "cloud_mode_off": "send-while-the-cloud-verdict-reads-cloud-mode-off",
    "collection_sensitive": "send-while-the-cloud-verdict-reads-the-collection-sensitive",
    "terms_not_accepted": (
        "send-to-a-provider-with-no-accepted-terms-including-an-unknown-provider"
    ),
    "terms_changed": (
        "send-when-the-current-adapter-terms-digest-differs-from-the-accepted-digest"
    ),
    "revision_exhausted": (
        "accept-at-revision-9223372036854775807-checked-after-malform"
        "ed-unknown-stale-same-value-noop-and-approval-a-revoke-is-ne"
        "ver-exhausted"
    ),
}
FAILURE_MAPPING = {
    "malformed_byom_request": "malformed_request",
    "payload_rejected": "payload_rejected",
    "unknown_provider": "unknown_provider",
    "stale_revision": "conflict",
    "approval_missing": "approval_required",
    "cloud_mode_off": "cloud_off",
    "collection_sensitive": "cloud_off",
    "terms_not_accepted": "terms_required",
    "terms_changed": "terms_required",
    "revision_exhausted": "revision_exhausted",
}
ERROR_ENUM = [
    "malformed_request",
    "payload_rejected",
    "unknown_provider",
    "conflict",
    "approval_required",
    "cloud_off",
    "terms_required",
    "revision_exhausted",
    "internal",
]

_EXACT_BOOLS = (
    ("record", "exact"),
    ("identifiers", "terms", "exact"),
    ("identifiers", "terms_digest", "preimage", "trailing_separator"),
    ("identifiers", "approval", "preimage", "trailing_separator"),
)


def _dig(cc, path):
    node = cc
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(
        doc,
        contract_id="privacy-byom",
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
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    for p in _EXACT_BOOLS:
        if type(_dig(cc, p)) is not bool:
            raise ContractError(f"{'.'.join(p)} must be an exact bool")
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
    for rel in LINKS.values():
        if not (ROOT / rel).is_file():
            raise ContractError(f"linked document missing: {rel}")
    print(f"byom contract lint ok: {path}")


def main(argv=None):
    try:
        lint()
    except ContractError as exc:
        print(f"byom contract lint FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
