#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0329: jobs dead-letter contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/dead_letter.yaml"
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
    "kind": "pure-job-dead-letter-entry-function",
    "serves": "background-job-execution",
    "scope": "one-dead-letter-entry-for-one-dead-queue-job-over-an-explicit-logical-clock",
    "owns": "dead-letter-admission-and-dead-letter-entry-records",
    "not_scope": "queue-state-transitions-retry-decisions-redrive-idempotency-keys-or-cancellation",
}
RECORD = {
    "fields": [
        "op",
        "job_id",
        "seq",
        "priority",
        "payload_digest",
        "attempts",
        "reason",
        "buried_at",
        "entry_id",
    ],
    "exact": True,
}
REQUEST = {
    "operations": [{"op": "bury", "fields": ["op", "job", "reason", "now"]}],
    "exact": True,
    "closure": "no-other-operations-registered",
    "job": "exactly-one-job-of-the-linked-queue-contract-with-its-job-fields",
    "reasons": ["exhausted", "permanent"],
    "bounds": {
        "now": "int-0-through-2-pow-53-minus-1-milliseconds",
        "seq": "int-0-through-2-pow-53-minus-2",
        "priority": "int-0-through-9",
        "payload": "exact-canonical-json-acyclic-alias-free-depth-at-most-64-int-digits-at-most-4000",
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
    "payload_digest": {
        "kind": "canonical-payload-digest",
        "grammar": "^pd1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-pd1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-the-payload",
        "source": "derived-never-caller-supplied",
    },
    "entry_id": {
        "kind": "canonical-dead-letter-entry-digest",
        "grammar": "^dl1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-dl1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-the-record-without-entry-id",
        "source": "derived-never-caller-supplied",
    },
}
SEMANTICS = {
    "validation": "request-shape-then-job-integrity-then-dead-status-first-failure-wins",
    "integrity": "the-job-must-satisfy-every-linked-queue-job-invariant-or-it-is-corrupt-never-repaired",
    "dead_only": "only-a-job-with-status-dead-is-buried-any-other-valid-status-is-job-not-dead",
    "attempts": "a-dead-job-carries-attempts-equal-to-max-attempts-matching-the-queue-dead-invariant",
    "reason": "fail-closed-reading-reason-is-caller-attested-exhausted-or-permanent-and-both-require-the-queue-dead-invariant",
    "lease": "a-dead-job-holds-no-lease-owner-and-no-lease-expiry",
    "payload": "fail-closed-reading-the-entry-stores-only-the-payload-digest-never-a-copy-of-the-payload",
    "clock": "buried-at-is-now",
    "provisional": "a-permanent-failure-job-with-attempts-below-max-attempts-is-not-a-valid-dead-queue-job-and-is-corrupt-job-provisional-pending-owner-ruling-on-the-retry-permanent-dead-rule-vs-the-queue-dead-invariant",
    "purity": "inputs-are-never-mutated-and-the-record-shares-no-container-with-them",
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "deterministic": "same-request-same-record",
    "pure": "rejected-and-accepted-requests-leave-the-request-bit-identical",
    "bound": "an-entry-binds-the-exact-payload-through-its-digest",
}
VERSIONING = {
    "base_path": "/jobs/dead-letter/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new "
    "endpoints. Never change the meaning of an existing field in place.",
}
LINKS = {
    "queue_contract": "data/contracts/queue.yaml",
    "retry_contract": "data/contracts/retry.yaml",
}
FAILURE_CLASSES = ["malformed_bury_request", "corrupt_job", "job_not_dead"]
FAILURE_TRIGGERS = {
    "malformed_bury_request": "request-shape-type-enum-or-bound-violation-outside-the-job-contents",
    "corrupt_job": "job-with-wrong-keys-types-grammar-bounds-inadmissible-payload-or-a-broken-queue-job-invariant",
    "job_not_dead": "a-valid-queue-job-whose-status-is-ready-leased-or-done",
}
FAILURE_MAPPING = {
    "malformed_bury_request": "malformed_request",
    "corrupt_job": "corrupt_job",
    "job_not_dead": "job_not_dead",
}
ERROR_ENUM = ["malformed_request", "corrupt_job", "job_not_dead", "internal"]
JOB_FIELDS = [
    "job_id",
    "seq",
    "priority",
    "payload",
    "status",
    "attempts",
    "lease_owner",
    "lease_expires_at",
]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-dead-letter", sections=SECTIONS)

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
    # sibling bindings: the job fields, statuses, max_attempts, job id
    # grammar and priority/payload bounds are the linked queue
    # contract's, never restated with a different value
    queue = yaml.safe_load((ROOT / cc["links"]["queue_contract"]).read_text())["contract"]
    if cc["request"]["max_attempts"] != queue["state"]["max_attempts"]:
        raise ContractError("max_attempts must equal the queue contract's")
    if cc["identifiers"]["job_id"]["grammar"] != queue["identifiers"]["job_id"]["grammar"]:
        raise ContractError("job_id grammar must equal the queue contract's")
    for name in ("priority", "payload"):
        if not queue["request"]["bounds"][name].startswith(cc["request"]["bounds"][name]):
            raise ContractError(f"{name} bound must equal the queue contract's")
    if queue["state"]["job_fields"] != JOB_FIELDS:
        raise ContractError("queue job_fields drifted from the pinned dead-letter job shape")
    if "dead" not in queue["state"]["statuses"]:
        raise ContractError("the queue contract must declare the dead status")
    print(f"dead-letter contract lint ok: {path}")


if __name__ == "__main__":
    lint()
