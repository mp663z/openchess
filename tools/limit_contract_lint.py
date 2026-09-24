#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0338: jobs limit contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/limit.yaml"
SECTIONS = {
    "errors",
    "failures",
    "id",
    "identifiers",
    "links",
    "properties",
    "record",
    "request",
    "role",
    "semantics",
    "versioning",
}
QUEUE_CONTRACT = ROOT / "data/contracts/queue.yaml"

ROLE = {
    "kind": "pure-job-token-bucket-rate-limit-decision-function",
    "serves": "background-job-execution",
    "scope": "one-admit-or-deny-decision-for-one-keyed-token-bucket-chained-to-its-previous-bucket-record-over-an-explicit-logical-clock",
    "owns": "token-refill-admission-retry-after-and-chained-bucket-records",
    "not_scope": "queue-state-transitions-leases-retry-backoff-idempotency-keys-cancellation-or-fairness-across-keys",
}
RECORD = {
    "fields": [
        "op",
        "key",
        "capacity",
        "refill_ms",
        "cost",
        "decision",
        "tokens",
        "updated_at",
        "retry_after_ms",
        "previous_id",
        "limit_id",
    ],
    "exact": True,
    "decisions": ["admit", "deny"],
}
REQUEST = {
    "operations": [{"op": "admit", "fields": ["op", "key", "cost", "now", "policy", "previous"]}],
    "exact": True,
    "closure": "no-other-operations-registered",
    "policy_fields": ["capacity", "refill_ms"],
    "policy_exact": True,
    "previous": "null-for-a-fresh-full-bucket-else-exactly-the-previous-bucket-record",
    "bounds": {
        "capacity": "int-1-through-1000000",
        "refill_ms": "int-1-through-86400000",
        "cost": "int-1-through-capacity",
        "now": "int-0-through-2-pow-53-minus-1-milliseconds",
    },
}
IDENTIFIERS = {
    "key": {
        "kind": "caller-supplied-limit-key",
        "grammar": "^[A-Za-z0-9._:-]{1,64}$",
        "source": "the-linked-queue-contract-worker-grammar",
    },
    "limit_id": {
        "kind": "canonical-chained-bucket-digest",
        "grammar": "^lm1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-lm1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-the-record-without-limit-id",
        "source": "derived-never-caller-supplied",
    },
}
SEMANTICS = {
    "validation": "request-shape-then-policy-then-cost-within-capacity-then-previous-record-integrity-then-chain-consistency-then-clock-first-failure-wins",
    "fresh": "a-null-previous-is-a-full-bucket-with-tokens-equal-capacity-at-now",
    "refill": "gained-is-floor-of-now-minus-previous-updated-at-over-refill-ms-exact-integer-arithmetic",
    "cap": "fail-closed-reading-when-previous-tokens-plus-gained-reach-capacity-tokens-are-capacity-and-updated-at-is-now-no-partial-refill-is-banked",
    "carry": "below-capacity-updated-at-advances-by-gained-times-refill-ms-so-a-partial-refill-is-kept",
    "admit": "admit-when-refilled-tokens-are-at-least-cost-then-tokens-are-refilled-minus-cost-and-retry-after-ms-is-null",
    "deny": "deny-when-refilled-tokens-are-below-cost-then-tokens-are-refilled-and-retry-after-ms-is-cost-minus-refilled-times-refill-ms-minus-now-minus-updated-at",
    "cost": "fail-closed-reading-a-cost-above-capacity-can-never-be-admitted-and-is-a-malformed-request-never-a-deny",
    "chain": "previous-id-is-null-on-a-fresh-bucket-else-the-previous-record-limit-id",
    "integrity": "a-previous-record-whose-shape-grammar-bounds-decision-invariants-or-limit-id-do-not-recompute-is-corrupt-never-trusted",
    "identity": "fail-closed-reading-key-capacity-and-refill-ms-must-equal-the-previous-record-a-policy-change-starts-a-fresh-bucket",
    "clock": "now-never-precedes-the-previous-updated-at-fail-closed-reading-equal-now-is-accepted",
    "overflow": "a-deny-whose-now-plus-retry-after-ms-would-exceed-2-pow-53-minus-1-fails-closed-as-clock-overflow-never-clamped",
    "purity": "inputs-are-never-mutated-and-the-record-shares-no-container-with-them",
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "deterministic": "same-request-same-record",
    "pure": "rejected-and-accepted-requests-leave-the-request-bit-identical",
    "bounded": "tokens-stay-within-0-through-capacity",
    "conserving": "along-a-chain-admitted-cost-never-exceeds-capacity-plus-gained-tokens",
}
VERSIONING = {
    "base_path": "/jobs/limit/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new "
    "endpoints. Never change the meaning of an existing field in place.",
}
LINKS = {"queue_contract": "data/contracts/queue.yaml"}
FAILURE_CLASSES = [
    "malformed_limit_request",
    "invalid_limit_policy",
    "corrupt_previous_bucket",
    "limit_conflict",
    "clock_overflow",
]
FAILURE_TRIGGERS = {
    "malformed_limit_request": "request-shape-type-grammar-or-bound-violation-including-a-cost-above-capacity",
    "invalid_limit_policy": "policy-mapping-with-wrong-keys-types-or-bounds",
    "corrupt_previous_bucket": "previous-record-with-wrong-keys-types-grammar-bounds-decision-invariants-or-a-limit-id-that-does-not-recompute",
    "limit_conflict": "key-or-policy-mismatch-with-the-previous-record-or-clock-regression",
    "clock_overflow": "a-deny-whose-now-plus-retry-after-ms-would-exceed-2-pow-53-minus-1",
}
FAILURE_MAPPING = {
    "malformed_limit_request": "malformed_request",
    "invalid_limit_policy": "invalid_policy",
    "corrupt_previous_bucket": "corrupt_record",
    "limit_conflict": "limit_conflict",
    "clock_overflow": "clock_overflow",
}
ERROR_ENUM = [
    "malformed_request",
    "invalid_policy",
    "corrupt_record",
    "limit_conflict",
    "clock_overflow",
    "internal",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-limit", sections=SECTIONS)

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    for name, expected in (
        ("role", ROLE),
        ("record", RECORD),
        ("request", REQUEST),
        ("identifiers", IDENTIFIERS),
        ("semantics", SEMANTICS),
        ("properties", PROPERTIES),
        ("versioning", VERSIONING),
        ("links", LINKS),
    ):
        check(name, expected, cc.get(name))
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
    ops = [spec["op"] for spec in cc["request"]["operations"]]
    if len(ops) != len(set(ops)):
        raise ContractError("operations must be unique")
    for spec in cc["request"]["operations"]:
        if spec["fields"][0] != "op":
            raise ContractError("every operation's first field must be op")
    for rel in cc["links"].values():
        if not (ROOT / rel).is_file():
            raise ContractError(f"linked contract missing: {rel}")
    # sibling binding: the limit key grammar is the linked queue
    # contract's worker grammar, never restated with a different value
    queue = yaml.safe_load((ROOT / cc["links"]["queue_contract"]).read_text())["contract"]
    if cc["identifiers"]["key"]["grammar"] != queue["identifiers"]["worker"]["grammar"]:
        raise ContractError("key grammar must equal the queue contract's worker grammar")
    print(f"limit contract lint ok: {path}")


if __name__ == "__main__":
    lint()
