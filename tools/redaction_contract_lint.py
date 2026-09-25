#!/usr/bin/env python3
"""T0392: privacy redaction contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/redaction.yaml"

ROLE = {
    "kind": ("whole-message-redaction-of-free-text-bound-for-a-log-or-error-sink"),
    "serves": (
        "server-never-content-or-keys-and-hosted-byom-never-receives-keys-on-every-diagnostic-path"
    ),
    "scope": ("one-text-one-secret-set-redaction-verdict-and-the-sink-fallback"),
    "owns": "the-redaction-rules-and-the-redaction-marker",
    "not_scope": (
        "hosted-byom-payload-schema-provider-terms-cloud-mode-collect"
        "ion-flags-provider-key-storage-telemetry-collection-policy-l"
        "ocal-model-delete-and-retry-idempotency"
    ),
}
REQUEST = {
    "fields": [
        "text",
        "secrets",
    ],
    "exact": True,
}
IDENTIFIERS = {
    "text": {
        "kind": "free-text-bound-for-a-log-line-or-error-message",
        "grammar": ("exact-built-in-str-of-any-length-the-content-rules-decide-redaction"),
        "source": "caller-supplied-never-trusted",
    },
    "secrets": {
        "kind": "secret-values-the-caller-holds",
        "grammar": (
            "exact-built-in-list-of-0-through-64-items-each-an-exact-buil"
            "t-in-str-of-1-through-4096-characters"
        ),
        "source": ("caller-supplied-from-key-storage-never-stored-never-returned-never-logged"),
    },
    "marker": {
        "kind": "fixed-redaction-marker",
        "value": "redacted",
        "source": "a-constant-never-derived-from-the-input",
    },
}
SEMANTICS = {
    "outcome": (
        "whole-message-the-output-is-the-text-verbatim-or-exactly-the-marker-never-a-partial-mask"
    ),
    "length": "a-text-longer-than-1024-characters-is-redacted-1024-is-kept",
    "charset": ("a-text-with-any-character-outside-0x20-through-0x7e-is-redacted"),
    "secret": ("a-text-whose-casefold-contains-the-casefold-of-any-secret-is-redacted"),
    "token_run": (
        "a-text-with-a-run-of-32-or-more-characters-each-in-[A-Za-z0-"
        "9+/=_%-]-is-redacted-31-is-kept"
    ),
    "precedence": (
        "malformed-then-content-rules-the-content-rules-are-order-free-any-one-hit-redacts"
    ),
    "evaluation": ("stateless-one-verdict-per-call-never-cached-secrets-never-stored"),
    "disclosure": ("the-output-never-says-which-rule-fired-or-which-secret-matched"),
    "sink_fallback": ("a-malformed-request-at-a-sink-emits-the-marker-never-the-raw-text"),
    "rollback": (
        "no-state-to-roll-back-a-malformed-call-raises-typed-with-inp"
        "uts-unchanged-and-the-sink-emits-only-the-marker"
    ),
    "readings": {
        "whole_message": (
            "spec-silent-a-partial-mask-leaks-length-position-and-neighbo"
            "urs-so-the-whole-text-is-replaced"
        ),
        "charset": (
            "spec-silent-non-ascii-text-including-legitimate-unicode-name"
            "s-and-line-breaks-is-redacted-whole"
        ),
        "secret_case": ("spec-silent-case-insensitive-casefold-matching-over-redacts-by-design"),
        "token_run": (
            "spec-silent-unregistered-keys-base64-hex-digests-and-install-ids-are-redacted-too"
        ),
        "encoded_secret": (
            "a-short-secret-split-by-other-characters-or-encoded-outside-"
            "the-run-charset-is-not-detected-callers-must-pass-every-secr"
            "et-they-hold"
        ),
        "marker": (
            "the-marker-is-a-public-constant-it-is-emitted-even-when-it-c"
            "ontains-a-short-secret-and-leaks-nothing"
        ),
    },
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "atomic": ("a-malformed-call-leaves-the-inputs-unchanged-and-calls-no-caller-method"),
    "fail_closed": "any-doubt-redacts",
    "stable": ("redacting-an-output-again-with-the-same-secrets-returns-it-unchanged"),
    "never_raw": "no-path-returns-or-emits-text-that-hit-a-rule",
}
VERSIONING = {
    "base_path": "/privacy/redaction/v1",
    "rule": (
        "Clients pin MAJOR. MINOR is additive-only: new optional fiel"
        "ds, new enum members, new endpoints. Never change the meanin"
        "g of an existing field in place."
    ),
}
LINKS = {
    "architecture_decision": "docs/adr/ADR-0004-asymmetric-architecture.md",
    "control_plane_precedent": "requirements/tasks/T0476.md",
}
FAILURE_CLASSES = [
    "malformed_redaction_request",
]
FAILURE_TRIGGERS = {
    "malformed_redaction_request": (
        "checked-before-every-content-rule-request-shape-or-exact-typ"
        "e-violation-including-str-dict-list-subclasses-non-str-keys-"
        "and-out-of-range-secret-counts-or-lengths"
    ),
}
FAILURE_MAPPING = {
    "malformed_redaction_request": "malformed_request",
}
ERROR_ENUM = [
    "malformed_request",
    "internal",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(
        doc,
        contract_id="privacy-redaction",
        sections={
            "errors",
            "failures",
            "id",
            "identifiers",
            "links",
            "properties",
            "request",
            "role",
            "semantics",
            "versioning",
        },
    )

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("request", REQUEST, cc.get("request"))
    if type((cc.get("request") or {}).get("exact")) is not bool:
        raise ContractError("request.exact must be an exact bool")
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    marker = ((cc.get("identifiers") or {}).get("marker") or {}).get("value")
    if type(marker) is not str:
        raise ContractError("identifiers.marker.value must be an exact str")
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
    print(f"redaction contract lint ok: {path}")


if __name__ == "__main__":
    lint()
