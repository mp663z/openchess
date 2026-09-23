#!/usr/bin/env python3
"""T0248: store export contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/export.yaml"
SECTIONS = {
    "errors",
    "failures",
    "id",
    "identifiers",
    "links",
    "oracle_boundary",
    "properties",
    "record",
    "request",
    "role",
    "semantics",
    "versioning",
}

ROLE = {
    "kind": "canonical-read-only-export-of-a-wal-logged-graph-state",
    "serves": "position-graph-store-portability",
    "scope": "single-log-current-state-export-to-a-pinned-format",
    "owns": "export-receipt-semantics-and-document-binding",
    "not_scope": "import-backup-restore-or-partial-filtered-export",
}
RECORD = {
    "fields": ["export_id", "head", "state_id", "record_count", "format", "document"],
    "exact": True,
    "field_definitions": {
        "document": {
            "kind": "canonical-rendered-export-document",
            "type": "exact-built-in-string-utf8-encodable",
            "source": "exporter-output-bound-byte-exact-to-the-local-canonical-rendering",
        }
    },
}
REQUEST = {"fields": ["format"], "exact": True, "formats": ["jsonl-v1"]}
IDENTIFIERS = {
    "export_id": {
        "kind": "content-addressed-export-receipt-id",
        "grammar": "^exp1:[0-9a-f]{64}$",
        "derivation": "sha256-over-head-state-id-record-count-format-document",
        "source": "derived-from-validated-export-never-caller-supplied",
    },
    "head": {
        "kind": "wal-chain-tip-or-pinned-genesis",
        "grammar": "^(wal0:0{64}|wal1:[0-9a-f]{64})$",
        "source": "derived-from-validated-source-log-never-caller-supplied",
    },
    "state_id": {
        "kind": "canonical-state-content-digest",
        "grammar": "^gs1:[0-9a-f]{64}$",
        "derivation": "sha256-over-canonical-serialization-of-sorted-identity-record-map",
        "source": "derived-from-replayed-state-never-caller-supplied",
    },
}
SEMANTICS = {
    "source_validation": "full-linked-wal-validation-and-replay-before-rendering",
    "format_resolution": "request-format-is-an-exact-member-of-the-closed-format-list",
    "rendering": (
        "one-line-per-live-record-sorted-by-identity-canonical-json-sorted-keys-compact-separators"
    ),
    "binding": "exporter-document-must-equal-local-canonical-rendering-byte-for-byte",
    "commit": "read-only-staged-receipt-source-log-and-request-never-mutated",
}
ORACLE_BOUNDARY = {
    "role": "document-exporter-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-export",
    "frozen_snapshots": (
        "entire-log-state-and-request-frozen-before-first-"
        "oracle-call-never-re-read-restored-bit-identical"
    ),
    "output_validation": (
        "exact-built-in-string-utf8-encodable-byte-exact-canonical-document-or-fail-closed"
    ),
}
FAILURE_CLASSES = [
    "malformed_export_request",
    "unsupported_format",
    "corrupt_source",
    "divergent_export",
]
FAILURE_TRIGGERS = {
    "malformed_export_request": "request-shape-or-type-violation-or-source-log-type-violation",
    "unsupported_format": "exact-string-format-outside-the-closed-format-list",
    "corrupt_source": "source-log-fails-linked-wal-validation-or-chain-rederivation",
    "divergent_export": (
        "exporter-raising-any-baseexception-non-exact-string-"
        "utf8-inencodable-or-canonical-divergent-document"
    ),
}
FAILURE_MAPPING = {
    "malformed_export_request": "malformed_request",
    "unsupported_format": "unsupported_format",
    "corrupt_source": "corrupt_source",
    "divergent_export": "divergent_export",
}
ERROR_ENUM = [
    "malformed_request",
    "unsupported_format",
    "corrupt_source",
    "divergent_export",
    "internal",
]
PROPERTIES = {
    "total": "hostile-requests-logs-and-exporters-fail-closed-typed-never-raw",
    "atomic": "every-export-leaves-log-and-request-bit-identical",
    "deterministic": "same-log-same-format-same-receipt",
    "rollback": "read-only-export-no-commit-to-undo",
}
VERSIONING = {
    "base_path": "/store/export/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional "
    "fields, new enum members, new endpoints. Never change the "
    "meaning of an existing field in place.",
}
LINKS = {
    "wal_contract": "data/contracts/wal.yaml",
    "migration_contract": "data/contracts/migration.yaml",
    "backup_contract": "data/contracts/backup.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="store-export", sections=SECTIONS)

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("request", REQUEST, cc.get("request"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("semantics", SEMANTICS, cc.get("semantics"))
    check("oracle_boundary", ORACLE_BOUNDARY, cc.get("oracle_boundary"))
    failures = cc["failures"]
    close_failures(failures)
    check("failures.classes", FAILURE_CLASSES, failures.get("classes"))
    check("failures.triggers", FAILURE_TRIGGERS, failures.get("triggers"))
    check("failures.mapping", FAILURE_MAPPING, failures.get("mapping"))
    if failures.get("closed") is not True:
        raise ContractError("failure model must be closed")
    if set(failures.get("mapping", {})) != set(FAILURE_CLASSES):
        raise ContractError("failures: mapping keys must equal declared classes")
    if set(failures.get("triggers", {})) != set(FAILURE_CLASSES):
        raise ContractError("failures: triggers keys must equal declared classes")
    errors = cc["errors"]
    close_errors(errors, shape_keys=("retryable_true_only_for",))
    check("errors.closed_enum", ERROR_ENUM, errors.get("closed_enum"))
    shape = errors.get("shape") or {}
    check(
        "errors.shape.retryable_true_only_for", ["internal"], shape.get("retryable_true_only_for")
    )
    if set(FAILURE_MAPPING.values()) | {"internal"} != set(ERROR_ENUM):
        raise ContractError("error enum must equal mapped codes + internal")
    check("properties", PROPERTIES, cc.get("properties"))
    check("versioning", VERSIONING, cc.get("versioning"))
    check("links", LINKS, cc.get("links"))
    for rel in cc["links"].values():
        if not (ROOT / rel).is_file():
            raise ContractError(f"linked contract missing: {rel}")
    print(f"export contract lint ok: {path}")


if __name__ == "__main__":
    lint()
