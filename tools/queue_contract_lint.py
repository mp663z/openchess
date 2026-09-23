#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0284: jobs queue contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/queue.yaml"
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
    "state",
    "versioning",
}
MAX_DEPTH = 64
MAX_INT_DIGITS = 4000

ROLE = {
    "kind": "durable-leased-priority-job-queue-state-machine",
    "serves": "background-job-execution",
    "scope": "single-queue-enqueue-claim-ack-nack-over-an-explicit-logical-clock",
    "owns": "queue-state-transitions-lease-semantics-and-receipts",
    "not_scope": "job-execution-scheduling-cron-retry-backoff-or-multi-queue-routing",
}
RECORD = {
    "fields": ["op", "job_id", "status", "attempts", "lease_expires_at", "state_id"],
    "exact": True,
}
STATE = {
    "fields": ["jobs", "next_seq"],
    "exact": True,
    "job_fields": [
        "job_id",
        "seq",
        "priority",
        "payload",
        "status",
        "attempts",
        "lease_owner",
        "lease_expires_at",
    ],
    "statuses": ["ready", "leased", "done", "dead"],
    "max_jobs": 10000,
    "next_seq": "int-0-through-2-pow-53-minus-1-a-new-job-needs-next-seq-below-2-pow-53-minus-1",
    "max_attempts": 5,
}
REQUEST = {
    "operations": [
        {"op": "enqueue", "fields": ["op", "dedupe_key", "priority", "payload"]},
        {"op": "claim", "fields": ["op", "worker", "now", "lease_ms"]},
        {"op": "ack", "fields": ["op", "job_id", "worker", "now"]},
        {"op": "nack", "fields": ["op", "job_id", "worker", "now"]},
    ],
    "exact": True,
    "closure": "no-other-operations-registered",
    "bounds": {
        "priority": "int-0-through-9-lower-is-more-urgent",
        "now": "int-0-through-2-pow-53-minus-1-milliseconds",
        "lease_ms": "int-1-through-3600000",
        "payload": "exact-canonical-json-acyclic-alias-free-depth-at-most-64-int-digits-at-most-4000",
    },
}
IDENTIFIERS = {
    "job_id": {
        "kind": "content-addressed-job-id",
        "grammar": "^job1:[0-9a-f]{64}$",
        "derivation": "sha256-over-domain-separated-dedupe-key",
        "source": "derived-from-dedupe-key-never-caller-supplied-on-enqueue",
    },
    "dedupe_key": {
        "kind": "caller-supplied-idempotency-key",
        "grammar": "^[A-Za-z0-9._:-]{1,128}$",
    },
    "worker": {"kind": "caller-supplied-lease-holder-id", "grammar": "^[A-Za-z0-9._:-]{1,64}$"},
    "state_id": {
        "kind": "canonical-queue-state-digest",
        "grammar": "^qs1:[0-9a-f]{64}$",
        "derivation": "sha256-over-canonical-json-of-the-committed-state",
        "source": "derived-from-committed-state-never-caller-supplied",
    },
}
SEMANTICS = {
    "validation": "full-state-invariant-validation-before-every-transition",
    "enqueue": "idempotent-by-dedupe-key-identical-priority-and-canonical-payload-returns-existing-job-unchanged-new-job-ready-with-next-seq",
    "dedupe": "seen-dedupe-key-with-different-priority-or-canonical-payload-fails-closed-never-overwrites",
    "ordering": "claim-picks-lowest-priority-number-then-lowest-seq-among-ready-or-lease-expired-jobs",
    "lease": "claim-sets-leased-owner-and-expiry-now-plus-lease-ms-and-increments-attempts",
    "expiry": "a-lease-with-expiry-at-or-before-now-is-reclaimable-at-least-once-delivery",
    "dead_letter": "a-claimable-job-whose-attempts-reached-max-attempts-becomes-dead-and-is-skipped",
    "empty_claim": "no-claimable-job-is-a-successful-receipt-with-null-job-id",
    "ack": "owner-with-unexpired-lease-marks-done-terminal",
    "nack": "owner-with-unexpired-lease-returns-job-to-ready-attempts-kept",
    "commit": "copy-on-write-new-state-committed-last-in-place",
}
PROPERTIES = {
    "total": "hostile-requests-and-states-fail-closed-typed-never-raw",
    "atomic": "rejected-transition-leaves-state-and-request-bit-identical",
    "deterministic": "same-state-same-request-same-receipt-and-state",
    "idempotent": "re-enqueue-of-an-identical-job-changes-nothing",
    "at_least_once": "every-non-terminal-job-is-eventually-reclaimable-until-dead",
    "rollback": "nack-returns-a-leased-job-to-ready",
}
VERSIONING = {
    "base_path": "/jobs/queue/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new endpoints. Never change the meaning of an existing field in place.",
}
LINKS = {
    "idempotency_contract": "data/contracts/idempotency.yaml",
    "crash_resume_contract": "data/contracts/crash_resume.yaml",
}
FAILURE_CLASSES = [
    "malformed_queue_request",
    "unknown_job",
    "lease_conflict",
    "dedupe_conflict",
    "capacity_exceeded",
    "corrupt_queue",
]
FAILURE_TRIGGERS = {
    "malformed_queue_request": "request-shape-type-grammar-or-bound-violation-or-inadmissible-payload",
    "unknown_job": "ack-or-nack-job-id-absent-from-the-queue",
    "lease_conflict": "ack-or-nack-by-non-owner-or-on-a-job-not-leased-or-with-an-expired-lease",
    "dedupe_conflict": "enqueue-of-a-seen-dedupe-key-whose-stored-priority-or-canonical-payload-differs",
    "capacity_exceeded": "enqueue-of-a-new-job-into-a-queue-holding-max-jobs-or-whose-next-seq-is-exhausted",
    "corrupt_queue": "state-shape-type-grammar-or-invariant-violation",
}
FAILURE_MAPPING = {
    "malformed_queue_request": "malformed_request",
    "unknown_job": "unknown_job",
    "lease_conflict": "lease_conflict",
    "dedupe_conflict": "dedupe_conflict",
    "capacity_exceeded": "capacity_exceeded",
    "corrupt_queue": "corrupt_queue",
}
ERROR_ENUM = [
    "malformed_request",
    "unknown_job",
    "lease_conflict",
    "dedupe_conflict",
    "capacity_exceeded",
    "corrupt_queue",
    "internal",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-queue", sections=SECTIONS)

    def check(name, expected, actual):
        if actual != expected:
            raise ContractError(f"{name} drifted: {actual!r}")

    for name, expected in (
        ("role", ROLE),
        ("record", RECORD),
        ("state", STATE),
        ("request", REQUEST),
        ("identifiers", IDENTIFIERS),
        ("semantics", SEMANTICS),
        ("properties", PROPERTIES),
        ("versioning", VERSIONING),
        ("links", LINKS),
    ):
        check(name, expected, cc.get(name))
    if f"depth-at-most-{MAX_DEPTH}" not in cc["request"]["bounds"]["payload"]:
        raise ContractError("payload depth bound drifted")
    if f"int-digits-at-most-{MAX_INT_DIGITS}" not in cc["request"]["bounds"]["payload"]:
        raise ContractError("payload digit bound drifted")
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
    print(f"queue contract lint ok: {path}")


if __name__ == "__main__":
    lint()
