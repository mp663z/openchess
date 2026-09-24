#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0347: jobs restart contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/restart.yaml"
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
    "kind": "pure-worker-restart-lease-release-over-one-queue-state",
    "serves": "background-job-execution",
    "scope": "release-every-unexpired-lease-held-by-one-restarted-worker-in-one-queue-state-over-an-explicit-logical-clock",
    "owns": "restart-lease-release-and-restart-receipts",
    "not_scope": "claim-ordering-dead-marking-ack-retry-backoff-idempotency-keys-cancellation-or-multi-queue-routing",
}
RECORD = {
    "fields": [
        "op",
        "worker",
        "restarted_at",
        "released",
        "prior_state_id",
        "state_id",
        "restart_id",
    ],
    "exact": True,
    "released": "job-ids-of-the-released-jobs-in-ascending-seq-order-possibly-empty",
}
REQUEST = {
    "operations": [{"op": "restart", "fields": ["op", "worker", "now"]}],
    "exact": True,
    "closure": "no-other-operations-registered",
    "state": "exactly-one-state-of-the-linked-queue-contract-with-its-state-and-job-fields",
    "bounds": {
        "now": "int-0-through-2-pow-53-minus-1-milliseconds",
        "max_jobs": 10000,
        "max_attempts": 5,
    },
    "bounds_source": "linked-queue-contract-state-max-jobs-and-max-attempts-never-caller-supplied",
}
IDENTIFIERS = {
    "worker": {
        "kind": "caller-supplied-lease-holder-id",
        "grammar": "^[A-Za-z0-9._:-]{1,64}$",
        "source": "the-linked-queue-contract-worker-grammar",
    },
    "state_id": {
        "kind": "canonical-queue-state-digest",
        "grammar": "^qs1:[0-9a-f]{64}$",
        "derivation": "the-linked-queue-contract-state-id-sha256-of-utf8-canonical-json-sort-keys-compact-separators-non-ascii-kept-of-the-state",
        "source": "derived-never-caller-supplied",
    },
    "restart_id": {
        "kind": "canonical-restart-receipt-digest",
        "grammar": "^rst1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-rst1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-the-record-without-restart-id",
        "source": "derived-never-caller-supplied",
    },
}
SEMANTICS = {
    "validation": "request-shape-then-full-queue-state-invariants-first-failure-wins",
    "integrity": "the-state-must-satisfy-every-linked-queue-state-and-job-invariant-or-it-is-corrupt-never-repaired",
    "release": "every-job-leased-by-the-worker-with-now-strictly-before-its-lease-expiry-returns-to-ready-with-no-lease-owner-or-expiry",
    "attempts": "fail-closed-reading-attempts-are-kept-as-on-a-queue-nack-a-restart-never-refunds-or-charges-an-attempt",
    "expired": "fail-closed-reading-a-lease-at-or-past-expiry-is-left-untouched-it-is-already-reclaimable-by-the-queue-claim",
    "others": "jobs-of-other-workers-and-ready-done-or-dead-jobs-are-never-touched-and-next-seq-and-job-order-are-kept",
    "dead": "fail-closed-reading-a-released-job-at-max-attempts-returns-to-ready-and-dead-marking-stays-with-the-queue-claim",
    "empty": "a-worker-holding-no-unexpired-lease-is-a-successful-receipt-with-an-empty-released-list-and-an-unchanged-state",
    "idempotent": "a-second-restart-of-the-same-worker-at-the-same-now-releases-nothing",
    "commit": "copy-on-write-new-state-committed-last-in-place",
    "purity": "a-rejected-restart-leaves-the-state-and-request-bit-identical-and-the-receipt-shares-no-container-with-them",
}
PROPERTIES = {
    "total": "hostile-requests-and-states-fail-closed-typed-never-raw",
    "atomic": "a-rejected-restart-leaves-state-and-request-bit-identical",
    "deterministic": "same-state-same-request-same-receipt-and-state",
    "idempotent": "restarting-twice-releases-nothing-the-second-time",
    "rollback": "release-returns-each-live-lease-of-the-worker-to-ready",
    "conserving": "the-set-of-job-ids-seqs-priorities-payloads-and-attempts-is-unchanged",
}
VERSIONING = {
    "base_path": "/jobs/restart/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new "
    "endpoints. Never change the meaning of an existing field in place.",
}
LINKS = {"queue_contract": "data/contracts/queue.yaml"}
FAILURE_CLASSES = ["malformed_restart_request", "corrupt_queue"]
FAILURE_TRIGGERS = {
    "malformed_restart_request": "request-shape-type-grammar-or-bound-violation",
    "corrupt_queue": "state-shape-type-grammar-bound-or-invariant-violation-of-the-linked-queue-contract",
}
FAILURE_MAPPING = {
    "malformed_restart_request": "malformed_request",
    "corrupt_queue": "corrupt_queue",
}
ERROR_ENUM = ["malformed_request", "corrupt_queue", "internal"]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-restart", sections=SECTIONS)

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
    # sibling binding: worker grammar, state_id grammar, max_jobs and
    # max_attempts are the linked queue contract's, never restated
    # with a different value
    queue = yaml.safe_load((ROOT / cc["links"]["queue_contract"]).read_text())["contract"]
    if cc["identifiers"]["worker"]["grammar"] != queue["identifiers"]["worker"]["grammar"]:
        raise ContractError("worker grammar must equal the queue contract's")
    if cc["identifiers"]["state_id"]["grammar"] != queue["identifiers"]["state_id"]["grammar"]:
        raise ContractError("state_id grammar must equal the queue contract's")
    if cc["request"]["bounds"]["max_jobs"] != queue["state"]["max_jobs"]:
        raise ContractError("max_jobs must equal the queue contract's")
    if cc["request"]["bounds"]["max_attempts"] != queue["state"]["max_attempts"]:
        raise ContractError("max_attempts must equal the queue contract's")
    print(f"restart contract lint ok: {path}")


if __name__ == "__main__":
    lint()
