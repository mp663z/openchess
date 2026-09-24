#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0311: jobs retry contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/retry.yaml"
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
    "kind": "pure-job-retry-backoff-decision-function",
    "serves": "background-job-execution",
    "scope": "one-retry-decision-for-one-failed-attempt-over-an-explicit-logical-clock",
    "owns": "retry-eligibility-backoff-delay-and-decision-records",
    "not_scope": "queue-state-transitions-leases-idempotency-keys-cancellation-or-jitter",
}
RECORD = {
    "fields": ["op", "job_id", "decision", "attempts", "delay_ms", "retry_at", "decision_id"],
    "exact": True,
    "decisions": ["retry", "dead"],
}
REQUEST = {
    "operations": [
        {"op": "decide", "fields": ["op", "job_id", "attempts", "failure", "now", "policy"]}
    ],
    "exact": True,
    "closure": "no-other-operations-registered",
    "policy_fields": ["base_delay_ms", "multiplier", "max_delay_ms"],
    "policy_exact": True,
    "failures": ["transient", "timeout", "permanent"],
    "bounds": {
        "attempts": "int-1-through-max-attempts-the-attempts-already-consumed-including-the-failed-one",
        "now": "int-0-through-2-pow-53-minus-1-milliseconds",
        "base_delay_ms": "int-1-through-3600000",
        "multiplier": "int-1-through-10",
        "max_delay_ms": "int-base-delay-ms-through-86400000",
    },
    "max_attempts": 5,
    "max_attempts_source": "linked-queue-contract-state-max-attempts-never-caller-supplied",
}
IDENTIFIERS = {
    "job_id": {
        "kind": "content-addressed-job-id",
        "grammar": "^job1:[0-9a-f]{64}$",
        "source": "the-linked-queue-contract-job-id-grammar",
    },
    "decision_id": {
        "kind": "canonical-retry-decision-digest",
        "grammar": "^rd1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-rd1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-object-with-members-request-and-record-where-record-omits-decision-id",
        "source": "derived-never-caller-supplied",
    },
}
SEMANTICS = {
    "validation": "request-shape-then-policy-then-clock-first-failure-wins",
    "permanent": "a-permanent-failure-is-dead-at-any-attempt-count",
    "exhausted": "attempts-equal-to-max-attempts-is-dead-matching-the-queue-dead-letter-bound",
    "timeout": "a-timeout-is-retryable-exactly-like-transient-fail-closed-reading-no-separate-budget",
    "delay": "min-of-max-delay-ms-and-base-delay-ms-times-multiplier-pow-attempts-minus-1-exact-integer-arithmetic",
    "retry_at": "now-plus-delay-ms",
    "clock": "a-retry-at-beyond-2-pow-53-minus-1-fails-closed-as-clock-overflow-never-clamped-never-turned-dead",
    "dead_record": "dead-decisions-carry-null-delay-ms-and-null-retry-at",
    "jitter": "none-fail-closed-reading-decisions-are-deterministic-and-replayable",
    "purity": "inputs-are-never-mutated-and-the-record-shares-no-container-with-them",
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "deterministic": "same-request-same-record",
    "pure": "rejected-and-accepted-requests-leave-the-request-bit-identical",
    "bounded": "every-retry-delay-is-at-most-max-delay-ms",
    "monotone": "retry-delay-never-decreases-as-attempts-grow-under-one-policy",
    "terminal": "every-job-reaches-dead-by-max-attempts",
}
VERSIONING = {
    "base_path": "/jobs/retry/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new "
    "endpoints. Never change the meaning of an existing field in place.",
}
LINKS = {"queue_contract": "data/contracts/queue.yaml"}
FAILURE_CLASSES = ["malformed_retry_request", "invalid_retry_policy", "clock_overflow"]
FAILURE_TRIGGERS = {
    "malformed_retry_request": "request-shape-type-grammar-enum-or-bound-violation-outside-the-policy-contents",
    "invalid_retry_policy": "policy-mapping-with-wrong-keys-types-or-bounds-or-max-below-base",
    "clock_overflow": "a-retry-decision-whose-retry-at-would-exceed-2-pow-53-minus-1",
}
FAILURE_MAPPING = {
    "malformed_retry_request": "malformed_request",
    "invalid_retry_policy": "invalid_policy",
    "clock_overflow": "clock_overflow",
}
ERROR_ENUM = ["malformed_request", "invalid_policy", "clock_overflow", "internal"]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-retry", sections=SECTIONS)

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
    # sibling bindings: the dead-letter bound and the job id grammar are
    # the linked queue contract's, never restated with a different value
    queue = yaml.safe_load((ROOT / cc["links"]["queue_contract"]).read_text())["contract"]
    if cc["request"]["max_attempts"] != queue["state"]["max_attempts"]:
        raise ContractError("max_attempts must equal the queue contract's")
    if cc["identifiers"]["job_id"]["grammar"] != queue["identifiers"]["job_id"]["grammar"]:
        raise ContractError("job_id grammar must equal the queue contract's")
    print(f"retry contract lint ok: {path}")


if __name__ == "__main__":
    lint()
