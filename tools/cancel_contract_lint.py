#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0302: jobs cancel contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/cancel.yaml"
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
    "kind": "pure-job-cancellation-decision-function",
    "serves": "background-job-execution",
    "scope": "one-cancellation-decision-for-one-queue-job-over-an-explicit-logical-clock",
    "owns": "cancellation-eligibility-and-cancellation-decision-records",
    "not_scope": "queue-state-transitions-leases-retry-dead-letter-redrive-idempotency-keys-or-worker-abort",
}
RECORD = {
    "fields": [
        "op",
        "job_id",
        "seq",
        "priority",
        "payload_digest",
        "status_before",
        "attempts",
        "requester",
        "cancelled_at",
        "decision_id",
    ],
    "exact": True,
    "statuses_before": ["ready", "leased"],
}
REQUEST = {
    "operations": [{"op": "cancel", "fields": ["op", "job", "requester", "now"]}],
    "exact": True,
    "closure": "no-other-operations-registered",
    "job": "exactly-one-job-of-the-linked-queue-contract-with-its-job-fields",
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
    "requester": {
        "kind": "caller-supplied-cancellation-requester-id",
        "grammar": "^[A-Za-z0-9._:-]{1,64}$",
        "source": "caller-supplied-never-derived",
    },
    "payload_digest": {
        "kind": "canonical-payload-digest",
        "grammar": "^pd1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-pd1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-the-payload",
        "source": "derived-never-caller-supplied",
    },
    "decision_id": {
        "kind": "canonical-cancellation-decision-digest",
        "grammar": "^cd1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-cd1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-the-record-without-decision-id",
        "source": "derived-never-caller-supplied",
    },
}
SEMANTICS = {
    "validation": "request-shape-then-job-integrity-then-cancellable-status-first-failure-wins",
    "integrity": "the-job-must-satisfy-every-linked-queue-job-invariant-or-it-is-corrupt-never-repaired",
    "cancellable": "only-a-ready-or-leased-job-is-cancelled-a-done-or-dead-job-is-job-not-cancellable",
    "ready": "a-ready-job-has-not-started-cancellation-withdraws-it-before-any-claim",
    "leased": "a-leased-job-is-claimed-the-decision-is-recorded-against-the-live-lease-applying-it-to-the-queue-or-aborting-the-worker-is-outside-this-contract",
    "lease_clock": "the-presented-status-governs-lease-expiry-and-reclaim-are-the-queue-contracts-concern-never-recomputed-here",
    "terminal": "a-recorded-cancellation-is-terminal-this-contract-never-reverses-it",
    "payload": "fail-closed-reading-the-record-stores-only-the-payload-digest-never-a-copy-of-the-payload",
    "clock": "cancelled-at-is-now",
    "purity": "inputs-are-never-mutated-and-the-record-shares-no-container-with-them",
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "deterministic": "same-request-same-record",
    "pure": "rejected-and-accepted-requests-leave-the-request-bit-identical",
    "bound": "a-record-binds-the-exact-payload-through-its-digest",
    "terminal": "a-cancellation-record-is-never-reversed",
}
VERSIONING = {
    "base_path": "/jobs/cancel/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new "
    "endpoints. Never change the meaning of an existing field in place.",
}
LINKS = {
    "queue_contract": "data/contracts/queue.yaml",
    "retry_contract": "data/contracts/retry.yaml",
}
FAILURE_CLASSES = ["malformed_cancel_request", "corrupt_job", "job_not_cancellable"]
FAILURE_TRIGGERS = {
    "malformed_cancel_request": "request-shape-type-grammar-or-bound-violation-outside-the-job-contents",
    "corrupt_job": "job-with-wrong-keys-types-grammar-bounds-inadmissible-payload-or-a-broken-queue-job-invariant",
    "job_not_cancellable": "a-valid-queue-job-whose-status-is-done-or-dead",
}
FAILURE_MAPPING = {
    "malformed_cancel_request": "malformed_request",
    "corrupt_job": "corrupt_job",
    "job_not_cancellable": "job_not_cancellable",
}
ERROR_ENUM = ["malformed_request", "corrupt_job", "job_not_cancellable", "internal"]
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
STATUSES = ["ready", "leased", "done", "dead"]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-cancel", sections=SECTIONS)

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
    # grammar, worker (requester) grammar and priority/payload bounds are
    # the linked queue contract's, never restated with a different value
    queue = yaml.safe_load((ROOT / cc["links"]["queue_contract"]).read_text())["contract"]
    if cc["request"]["max_attempts"] != queue["state"]["max_attempts"]:
        raise ContractError("max_attempts must equal the queue contract's")
    if cc["identifiers"]["job_id"]["grammar"] != queue["identifiers"]["job_id"]["grammar"]:
        raise ContractError("job_id grammar must equal the queue contract's")
    if cc["identifiers"]["requester"]["grammar"] != queue["identifiers"]["worker"]["grammar"]:
        raise ContractError("requester grammar must equal the queue contract's worker grammar")
    for name in ("priority", "payload"):
        if not queue["request"]["bounds"][name].startswith(cc["request"]["bounds"][name]):
            raise ContractError(f"{name} bound must equal the queue contract's")
    if queue["state"]["job_fields"] != JOB_FIELDS:
        raise ContractError("queue job_fields drifted from the pinned cancel job shape")
    if queue["state"]["statuses"] != STATUSES:
        raise ContractError("queue statuses drifted from the pinned cancel status set")
    print(f"cancel contract lint ok: {path}")


if __name__ == "__main__":
    lint()
