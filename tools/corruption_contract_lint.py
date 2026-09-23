#!/usr/bin/env python3
"""T0266: store corruption contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/corruption.yaml"

SECTIONS = {"errors", "failures", "id", "identifiers", "links",
            "oracle_boundary", "properties", "record", "role",
            "semantics", "versioning"}
ROLE = {
    "kind": "verified-corruption-scan-and-salvage-over-a-wal-"
            "logged-store",
    "serves": "position-graph-store-durability",
    "scope": "single-log-scan-to-the-longest-verified-prefix",
    "owns": "corruption-receipt-semantics-and-suffix-quarantine",
    "not_scope": "repair-of-corrupt-entries-or-target-chosen-"
                 "rollback",
}
VERDICTS = ["clean", "salvaged"]
MAX_DEPTH = 8
MAX_INT_BITS = 256
RECORD = {
    "fields": ["scan_id", "verdict", "verified_head",
               "verified_count", "quarantined_count",
               "quarantine_token"],
    "exact": True,
    "field_definitions": {
        "verdict": {
            "kind": "closed-scan-verdict",
            "values": VERDICTS,
            "source": "derived-from-the-verified-prefix-never-"
                      "caller-supplied",
        },
        "verified_count": {
            "kind": "longest-verified-prefix-length",
            "type": "exact-built-in-int-zero-through-log-length",
            "source": "derived-from-linked-wal-validation-of-every-"
                      "prefix",
        },
        "quarantined_count": {
            "kind": "corrupt-suffix-length",
            "type": "exact-built-in-int-log-length-minus-verified-"
                    "count",
            "source": "derived-never-caller-supplied",
        },
    },
}
IDENTIFIERS = {
    "scan_id": {
        "kind": "content-addressed-corruption-receipt-id",
        "grammar": "^crp1:[0-9a-f]{64}$",
        "derivation": "sha256-over-verdict-verified-head-verified-"
                      "count-quarantined-count-quarantine-token",
        "source": "derived-from-validated-scan-never-caller-"
                  "supplied",
    },
    "verified_head": {
        "kind": "wal-chain-tip-of-the-verified-prefix-or-pinned-"
                "genesis",
        "grammar": "^(wal0:0{64}|wal1:[0-9a-f]{64})$",
        "source": "derived-from-linked-wal-replay-of-the-verified-"
                  "prefix-never-caller-supplied",
    },
    "quarantine_token": {
        "kind": "untrusted-quarantine-sink-receipt-token-or-null-"
                "when-clean",
        "grammar": "^qrn1:[0-9a-f]{64}$",
        "derivation": "sha256-over-domain-separated-type-tagged-"
                      "length-framed-canonical-encoding-of-the-"
                      "entire-frozen-corrupt-suffix",
        "source": "sink-output-shape-validated-and-bound-byte-"
                  "exact-to-the-local-suffix-derivation",
    },
}
SEMANTICS = {
    "scan_domain": "exact-built-in-acyclic-alias-free-tree-of-dict-"
                   "list-str-int-bool-null-within-nesting-depth-8-ints-"
                   "within-256-bits",
    "detection": "longest-prefix-passing-full-linked-wal-"
                 "validation-and-chain-rederivation",
    "loss_bound": "quarantined-count-must-not-exceed-request-max-"
                  "loss",
    "quarantine": "nonempty-corrupt-suffix-quarantined-exactly-"
                  "once-before-commit-token-bound-byte-exact-to-"
                  "canonical-suffix-encoding",
    "clean": "empty-corrupt-suffix-never-calls-the-sink-null-token-"
             "log-untouched",
    "commit": "suffix-removal-only-after-full-scan-loss-check-and-"
              "quarantine",
}
ORACLE_BOUNDARY = {
    "role": "quarantine-sink-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-salvage-zero-when-"
                         "clean",
    "frozen_snapshots":
        "entire-log-and-request-frozen-before-first-oracle-call-"
        "never-re-read-restored-bit-identical",
    "output_validation":
        "exact-built-in-string-pinned-grammar-byte-exact-suffix-"
        "bound-token-or-fail-closed",
}
FAILURE_CLASSES = ["malformed_corruption_record", "excessive_loss",
                   "divergent_quarantine"]
FAILURE_TRIGGERS = {
    "malformed_corruption_record":
        "request-grammar-or-type-violation-or-log-outside-the-scan-"
        "domain",
    "excessive_loss": "corrupt-suffix-longer-than-request-max-loss",
    "divergent_quarantine":
        "sink-raising-any-baseexception-non-exact-string-bad-"
        "grammar-or-suffix-divergent-token",
}
FAILURE_MAPPING = {
    "malformed_corruption_record": "malformed_request",
    "excessive_loss": "excessive_loss",
    "divergent_quarantine": "divergent_quarantine",
}
ERROR_ENUM = ["malformed_request", "excessive_loss",
              "divergent_quarantine", "internal"]
PROPERTIES = {
    "total": "hostile-requests-logs-and-sinks-fail-closed-typed-"
             "never-raw",
    "atomic": "rejected-scan-leaves-inputs-bit-identical",
    "deterministic": "same-inputs-same-receipt-and-verified-prefix",
    "rollback": "committed-salvage-removes-exactly-the-corrupt-"
                "suffix",
}
VERSIONING = {
    "base_path": "/store/corruption/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "wal_contract": "data/contracts/wal.yaml",
    "rollback_contract": "data/contracts/rollback.yaml",
    "restore_contract": "data/contracts/restore.yaml",
}


def _exact(name, expected, actual):
    """Type-exact structural equality: True != 1, 1 != 1.0,
    dict/list only as exact built-ins - a coerced pin never
    passes as its intended value."""
    if type(expected) is not type(actual):
        raise ContractError(f"{name} drifted: {actual!r}")
    if type(expected) is dict:
        if set(expected) != set(actual):
            raise ContractError(f"{name} drifted: {actual!r}")
        for key in expected:
            _exact(f"{name}.{key}", expected[key], actual[key])
    elif type(expected) is list:
        if len(expected) != len(actual):
            raise ContractError(f"{name} drifted: {actual!r}")
        for i, (e, a) in enumerate(zip(expected, actual,
                                       strict=True)):
            _exact(f"{name}[{i}]", e, a)
    elif expected != actual:
        raise ContractError(f"{name} drifted: {actual!r}")


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="store-corruption",
                        sections=SECTIONS)
    _exact("role", ROLE, cc.get("role"))
    _exact("record", RECORD, cc.get("record"))
    _exact("identifiers", IDENTIFIERS, cc.get("identifiers"))
    _exact("semantics", SEMANTICS, cc.get("semantics"))
    _exact("oracle_boundary", ORACLE_BOUNDARY,
           cc.get("oracle_boundary"))
    failures = cc["failures"]
    close_failures(failures)
    _exact("failures.classes", FAILURE_CLASSES,
           failures.get("classes"))
    _exact("failures.triggers", FAILURE_TRIGGERS,
           failures.get("triggers"))
    _exact("failures.mapping", FAILURE_MAPPING,
           failures.get("mapping"))
    if failures.get("closed") is not True:
        raise ContractError("failure model must be closed")
    errors = cc["errors"]
    close_errors(errors, shape_keys=("retryable_true_only_for",))
    _exact("errors.closed_enum", ERROR_ENUM,
           errors.get("closed_enum"))
    _exact("errors.shape", {"retryable_true_only_for": ["internal"]},
           errors.get("shape"))
    if not set(FAILURE_MAPPING.values()) <= set(ERROR_ENUM):
        raise ContractError("failure mapping escapes the error enum")
    _exact("properties", PROPERTIES, cc.get("properties"))
    _exact("versioning", VERSIONING, cc.get("versioning"))
    _exact("links", LINKS, cc.get("links"))
    for rel in LINKS.values():
        if not (ROOT / rel).is_file():
            raise ContractError(f"linked contract missing: {rel}")
    print(f"corruption contract lint ok: {path}")


if __name__ == "__main__":
    lint()
