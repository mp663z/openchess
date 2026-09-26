#!/usr/bin/env python3
"""T0428 closed event metadata contract and source operation checks."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from tools.contract_lint_closure import close_envelope
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data/contracts/events.yaml"
SOURCE = ROOT / "data/contracts/control-plane.yaml"
SECTIONS = {"id", "role", "versioning", "catalog", "envelope", "ordering", "failure", "links"}
FIELDS = [
    "version",
    "name",
    "operation_id",
    "event_id",
    "occurred_at",
    "correlation_id",
    "outcome",
    "error_code",
]
REQUIRED = FIELDS[:-1]
FAILURES = [
    "malformed_event",
    "unsupported_version",
    "unknown_name",
    "duplicate_conflict",
    "publication_failure",
]
CATALOG_V1_SHA256 = "7d856c1dc2f965d8c1f5f1db1593e7d38354e9560630eba60e41dd4448191ab9"
PROHIBITED = [
    "chess-content",
    "game-data",
    "FEN",
    "moves",
    "notes",
    "provider-key-material",
    "credentials",
    "request-body",
    "response-body",
    "raw-free-text",
    "account-id",
    "email",
    "IP",
]


def _exact(value, expected, label):
    if type(value) is not type(expected) or value != expected:
        raise ContractError(f"{label}: structured pin drift")


def lint(path: Path | None = None):
    doc = yaml.safe_load((path or CONTRACT).read_text())
    cc = close_envelope(doc, contract_id="contracts-control-plane-events", sections=SECTIONS)
    _exact(
        cc["role"],
        {
            "kind": "control-plane-operation-metadata-events",
            "scope": "replaceable-control-plane-boundary",
            "excludes": ["adr-0003-offline-log", "import-telemetry", "hosted-send", "oauth-sync"],
            "source": "data/contracts/control-plane.yaml",
            "source_schema_version": 2,
            "status": "opt-in-local-consumer-operation-emission-shipped-no-hosted-send",
        },
        "role",
    )
    _exact(
        cc["versioning"],
        {
            "envelope_version": 1,
            "major_rule": "unknown-versions-refused-before-publication",
            "additions": "new-operation-names-require-a-reviewed-contract-version",
        },
        "versioning",
    )
    _exact(
        cc["envelope"],
        {
            "closed": True,
            "fields": FIELDS,
            "required": REQUIRED,
            "error_code_rule": "required-only-on-error-forbidden-on-success",
            "version": "exact-integer-1-not-boolean",
            "name": "control_plane.<declared operation_id>.<outcome>",
            "operation_id": "exact-declared-area-dot-operation-from-source",
            "event_id": "opaque-identifier-not-a-request-id-or-secret",
            "correlation_id": "opaque-identifier-not-a-request-id-or-secret",
            "identifier_grammar": (
                "ASCII [A-Za-z0-9_-]{1,64}; generated identifiers only; "
                "no raw email, IP, credentials, key reference, user identifier, "
                "or body-derived value"
            ),
            "occurred_at": (
                "exact-integer-unix-milliseconds-from-0-through-253402300799999-not-boolean"
            ),
            "outcome": ["succeeded", "failed"],
            "error_code": "exact-declared-error-for-that-operation-on-failed",
            "prohibited": PROHIBITED,
            "value_policy": (
                "Only the listed scalar fields may be emitted; identifier generators "
                "must use independent random/opaque tokens, never encode private input. "
                "Field validation alone cannot prove identifier provenance."
            ),
        },
        "envelope",
    )
    _exact(
        cc["ordering"],
        {
            "creation": "terminal-outcome-after-effect-commit-or-terminal-error-decision",
            "batch": "validate-entire-batch-before-any-state-change-then-preserve-input-order",
            "replay": "same-event-id-and-identical-envelope-is-a-no-op-even-across-restart",
            "conflict": "same-event-id-with-different-envelope-refused-as-a-whole-batch",
            "relation": "no-global-order-guarantee-between-independent-batches",
        },
        "ordering",
    )
    _exact(
        cc["failure"],
        {
            "classes": FAILURES,
            "mapping": {
                "malformed_event": "malformed_request",
                "unsupported_version": "malformed_request",
                "unknown_name": "malformed_request",
                "duplicate_conflict": "conflict",
                "publication_failure": "internal",
            },
            "rollback": (
                "Validation, replay conflict, or transactional publish failure leaves "
                "the published sequence and dedupe ledger unchanged. Retry uses "
                "the same event IDs and envelopes; no partial batch publication."
            ),
            "publication": (
                "Transactional outbox or equivalent atomic ledger-and-event publication "
                "required; nontransactional sinks may only consume committed outbox rows. "
                "Fail closed when atomicity is unavailable."
            ),
            "no_http_mapping": True,
        },
        "failure",
    )
    _exact(
        cc["links"],
        {
            "operation_source": "data/contracts/control-plane.yaml",
            "excludes_offline": "docs/adr/ADR-0003-offline-authority.md",
            "excludes_import": "data/contracts/import.yaml",
        },
        "links",
    )
    src = yaml.safe_load(SOURCE.read_text())
    if (
        type(src) is not dict
        or type(src.get("schema_version")) is not int
        or src["schema_version"] != 2
    ):
        raise ContractError("source schema drift")
    errors = src["contract"]["transport"]["errors"]["closed_enum"]
    if type(errors) is not list or len(errors) != len(set(errors)):
        raise ContractError("source error enum drift")
    catalog = cc["catalog"]
    if type(catalog) is not dict or set(catalog) != {
        "operation_ids",
        "baseline_sha256",
        "name_rule",
        "source_addition",
    }:
        raise ContractError("catalog closure drift")
    _exact(
        catalog["name_rule"],
        "exactly-control_plane.<catalog-operation-id>.<succeeded-or-failed>",
        "name rule",
    )
    _exact(
        catalog["source_addition"],
        "reject-new-source-operation-until-catalog-and-version-reviewed",
        "addition rule",
    )
    ops = src["areas"]
    if type(ops) is not dict or not ops:
        raise ContractError("source operations drift")
    declared = catalog["operation_ids"]
    if (
        type(declared) is not list
        or any(type(name) is not str for name in declared)
        or declared != sorted(set(declared))
    ):
        raise ContractError("catalog operation IDs invalid")
    # This constant is independently pinned in code. A joint source+catalog
    # addition is not silently accepted under the unchanged envelope version.
    digest = hashlib.sha256("\n".join(declared).encode("ascii")).hexdigest()
    _exact(catalog["baseline_sha256"], CATALOG_V1_SHA256, "catalog baseline")
    if digest != CATALOG_V1_SHA256:
        raise ContractError("catalog change requires reviewed version baseline")
    source_operations = {
        f"{area}.{operation}" for area, detail in ops.items() for operation in detail["ops"]
    }
    if set(declared) != source_operations:
        raise ContractError("source operation additions/removals require reviewed catalog/version")
    for area, detail in ops.items():
        for operation, spec in detail["ops"].items():
            if (
                not isinstance(area, str)
                or not isinstance(operation, str)
                or not isinstance(spec["errors"], list)
                or not set(spec["errors"]) <= set(errors)
            ):
                raise ContractError("source operation error drift")
    for link in cc["links"].values():
        if not (ROOT / link).is_file():
            raise ContractError(f"missing link: {link}")
    return cc


if __name__ == "__main__":
    lint()
    print(f"events contract lint ok: {CONTRACT}")
