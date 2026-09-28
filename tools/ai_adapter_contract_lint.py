#!/usr/bin/env python3
"""T0464: closed AI adapter contract and cross-contract boundary checks."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract_lint_closure import close_envelope  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data/contracts/ai_adapter.yaml"
SECTIONS = {"id", "role", "interface", "modes", "cost", "failures", "versioning", "unresolved"}
SOURCES = {
    "byom": ROOT / "data/contracts/byom.yaml",
    "cloud": ROOT / "data/contracts/cloud_off.yaml",
    "sensitive": ROOT / "data/contracts/sensitive_flag.yaml",
    "local": ROOT / "data/contracts/local_model.yaml",
    "route": ROOT / "data/contracts/control-plane.yaml",
}


def _same(value, expected, label):
    if type(value) is not type(expected) or value != expected:
        raise ContractError(f"{label}: structured contract drift")


def lint(doc=None):
    if doc is None:
        doc = yaml.safe_load(CONTRACT.read_text())
    c = close_envelope(doc, contract_id="contracts-ai-adapter", sections=SECTIONS)
    _same(
        c["role"],
        {
            "kind": "provider-neutral-local-or-opted-in-hosted-inference-boundary",
            "scope": "local-orchestrator-to-selected-provider-adapter-not-a-control-plane-endpoint",
            "status": "contract-only-no-provider-invocation-shipped-by-this-task",
            "source": "T0464-ruling-2026-09-25",
        },
        "role",
    )
    _same(
        c["interface"],
        {
            "version": 1,
            "request": {
                "closed": True,
                "fields": [
                    "version",
                    "mode",
                    "capability",
                    "provider_ref",
                    "payload",
                    "max_cost_usd",
                ],
                "required": ["version", "mode", "capability", "provider_ref", "payload"],
                "version": "exact-integer-1-not-bool",
                "mode": ["local", "hosted_byom"],
                "capability": "nonempty-provider-neutral-identifier-not-a-prompt",
                "provider_ref": "opaque-local-reference-not-credential-or-key-material",
                "payload": "local-only-or-closed-byom-payload-according-to-mode",
                "max_cost_usd": (
                    "optional-finite-nonnegative-number-excluding-bool-no-implicit-unlimited-spend"
                ),
                "unknown_fields": "refuse-before-any-provider-effect",
            },
            "response": {
                "closed": True,
                "fields": ["version", "status", "output", "usage", "cost_usd", "failure"],
                "version": "exact-integer-1-not-bool",
                "status": ["succeeded", "refused", "cancelled"],
                "output": (
                    "provider-neutral-text-only-on-success-never-claim-evidence-without-task-check"
                ),
                "usage": (
                    "optional-provider-neutral-token-counts-only-"
                    "when-known-never-estimate-presented-as-measured"
                ),
                "cost_usd": (
                    "optional-finite-nonnegative-measured-amount-only-when-known-no-invented-zero"
                ),
                "failure": "typed-code-only-on-refused-or-cancelled-no-raw-provider-message",
                "no_echo": [
                    "provider-key",
                    "credential",
                    "request-payload",
                    "raw-provider-error",
                    "FEN",
                    "moves",
                ],
            },
            "capability": {
                "metadata": ["capability", "context_limit", "output_limit"],
                "limits": "optional-exact-positive-integers-only-when-provider-reports-them",
                "unknown": "capability-unavailable-not-a-guessed-fallback",
                "provider_names": "open-set-not-hardcoded-by-callers",
            },
        },
        "interface",
    )
    _same(
        c["modes"],
        {
            "local": {
                "authority": "data/contracts/local_model.yaml",
                "host": "desktop-only-for-local-model-verdict",
                "egress": "none-even-when-cloud-mode-on",
                "availability": "works-without-network-or-control-plane-route",
                "payload": "local-only-never-outbound",
            },
            "hosted_byom": {
                "authority": "data/contracts/byom.yaml",
                "payload": "exactly-the-closed-byom-payload-schema-no-extra-fields",
                "approval": "explicit-opt-in-and-current-provider-terms-acceptance",
                "checks": [
                    "closed-payload",
                    "cloud-off-current-verdict",
                    "sensitive-collection-current-verdict",
                    "terms-current-digest",
                ],
                "timing": "recheck-at-send-time-not-only-at-routing-or-request-construction",
                "credentials": (
                    "approved-local-custody-only-never-in-contract-request-response-or-diagnostics"
                ),
                "route": (
                    "control-plane-route-is-metadata-only-capability-policy-cost-no-chess-content"
                ),
                "route_source": "data/contracts/control-plane.yaml",
            },
        },
        "modes",
    )
    _same(
        c["cost"],
        {
            "policy": (
                "no-paid-hosted-send-without-explicit-cost-"
                "limit-and-verifiable-provider-bounded-preflight"
            ),
            "cap": (
                "cost_cap_exceeded-refusal-before-send-if-"
                "estimate-exceeds-limit-or-bounded-preflight-unavailable"
            ),
            "report": "measured-cost-only-when-known-never-assert-estimate-as-charge",
            "authority": "control-plane-cost-policy-does-not-by-itself-authorize-provider-charge",
            "local": "no-provider-charge",
        },
        "cost",
    )
    classes = [
        "malformed_request",
        "payload_rejected",
        "cloud_off",
        "terms_required",
        "provider_unavailable",
        "cost_cap_exceeded",
        "cancelled",
        "internal",
    ]
    _same(
        c["failures"],
        {
            "classes": classes,
            "precedence": (
                "validate-envelope-and-payload-then-fresh-"
                "privacy-checks-then-cost-preflight-then-provider-send"
            ),
            "provider": "normalize-provider-errors-without-raw-text-or-secrets",
            "cancellation": (
                "before-send-no-effect-in-flight-best-effort-no-false-guarantee-of-no-charge"
            ),
            "atomic": (
                "refusal-before-send-has-no-provider-effect-and-no-"
                "success-output-after-send-do-not-promise-provider-rollback"
            ),
            "mapping": {code: code for code in classes},
        },
        "failures",
    )
    _same(
        c["versioning"],
        {
            "rule": (
                "major-change-for-existing-field-meaning-or-payload-"
                "widening-additive-minor-fields-require-reviewed-contract"
            ),
            "rollback": (
                "old-major-clients-use-their-pinned-contract-no-silent-payload-or-egress-upgrade"
            ),
        },
        "versioning",
    )
    _same(
        c["unresolved"],
        [
            "provider-specific-capabilities-and-credential-flow-not-defined-here",
            "token-usage-and-actual-billing-verification-require-provider-specific-adapters",
            "streaming-partial-output-and-in-flight-cancel-charge-semantics-not-defined",
            "OAuth-and-hosted-sync-architecture-require-owner-decision",
        ],
        "unresolved",
    )
    rulings = list((ROOT / "docs").glob("*contract-rulings-2026-09-25.md"))
    if len(rulings) != 1 or "## T0464 AI adapter" not in rulings[0].read_text():
        raise ContractError("missing T0464 ruling")
    for path in SOURCES.values():
        if not path.is_file():
            raise ContractError(f"missing source: {path}")
    byom = yaml.safe_load(SOURCES["byom"].read_text())["contract"]
    _same(
        byom["identifiers"]["payload"]["allowed_keys"],
        ["fen", "moves", "task", "max-tokens"],
        "BYOM allowed payload",
    )
    _same(
        byom["identifiers"]["payload"]["mandatory_keys"], ["fen", "task"], "BYOM required payload"
    )
    _same(
        byom["semantics"]["evaluation"],
        "send-verdict-read-from-current-state-at-send-time-one-verdict-per-send-never-cached",
        "BYOM send timing",
    )
    cloud = yaml.safe_load(SOURCES["cloud"].read_text())["contract"]
    _same(
        cloud["semantics"]["evaluation"],
        "egress-verdict-read-from-current-state-at-send-time-one-verdict-per-send-never-cached",
        "cloud timing",
    )
    sensitive = yaml.safe_load(SOURCES["sensitive"].read_text())["contract"]
    _same(
        sensitive["semantics"]["default"],
        "unknown-or-unset-flag-reads-sensitive-true-cloud-off",
        "collection default",
    )
    local = yaml.safe_load(SOURCES["local"].read_text())["contract"]
    _same(
        local["semantics"]["egress"],
        "a-local-model-invocation-never-sends-an-outbound-payload-any-non-local-destination-is-refused-whatever-the-cloud-mode",
        "local egress",
    )
    route = yaml.safe_load(SOURCES["route"].read_text())
    _same(
        route["areas"]["provider_routing"]["ops"]["route"]["request"]["fields"]["capability"][
            "type"
        ],
        "string",
        "route capability",
    )
    _same(
        list(route["areas"]["provider_routing"]["ops"]["route"]["request"]["fields"]),
        ["capability", "policy"],
        "route request closure",
    )
    _same(
        list(route["areas"]["provider_routing"]["ops"]["route"]["response"]["fields"]),
        ["provider_kind", "key_ref", "estimated_cost_usd"],
        "route response closure",
    )
    return c


if __name__ == "__main__":
    lint()
    print(f"AI adapter contract lint ok: {CONTRACT}")
