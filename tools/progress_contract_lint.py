#!/usr/bin/env python3
# ruff: noqa: E501  (exact pinned contract literals)
"""T0320: jobs progress contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/progress.yaml"
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
    "kind": "pure-job-progress-report-function",
    "serves": "background-job-execution",
    "scope": "one-progress-report-for-one-leased-job-chained-to-its-previous-report-over-an-explicit-logical-clock",
    "owns": "progress-monotonicity-completion-percent-and-chained-progress-records",
    "not_scope": "queue-state-transitions-lease-validity-retry-idempotency-keys-cancellation-or-eta",
}
RECORD = {
    "fields": [
        "op",
        "job_id",
        "worker",
        "done",
        "total",
        "percent_bp",
        "reported_at",
        "previous_id",
        "progress_id",
    ],
    "exact": True,
}
REQUEST = {
    "operations": [
        {"op": "report", "fields": ["op", "job_id", "worker", "done", "total", "now", "previous"]}
    ],
    "exact": True,
    "closure": "no-other-operations-registered",
    "previous": "null-for-the-first-report-else-exactly-the-previous-progress-record",
    "bounds": {
        "total": "int-1-through-2-pow-53-minus-1-work-units",
        "done": "int-0-through-total",
        "now": "int-0-through-2-pow-53-minus-1-milliseconds",
    },
}
IDENTIFIERS = {
    "job_id": {
        "kind": "content-addressed-job-id",
        "grammar": "^job1:[0-9a-f]{64}$",
        "source": "the-linked-queue-contract-job-id-grammar",
    },
    "worker": {
        "kind": "caller-supplied-lease-holder-id",
        "grammar": "^[A-Za-z0-9._:-]{1,64}$",
        "source": "the-linked-queue-contract-worker-grammar",
    },
    "progress_id": {
        "kind": "canonical-chained-progress-digest",
        "grammar": "^pg1:[0-9a-f]{64}$",
        "derivation": "sha256-of-ascii-pg1-then-nul-byte-then-utf8-canonical-json-sort-keys-compact-separators-ensure-ascii-of-the-record-without-progress-id",
        "source": "derived-never-caller-supplied",
    },
}
SEMANTICS = {
    "validation": "request-shape-then-previous-record-integrity-then-chain-consistency-first-failure-wins",
    "percent": "percent-bp-is-floor-of-done-times-10000-over-total-exact-integer-arithmetic-10000-only-when-done-equals-total",
    "chain": "previous-id-is-null-on-the-first-report-else-the-previous-record-progress-id",
    "integrity": "a-previous-record-whose-shape-grammar-bounds-percent-or-progress-id-do-not-recompute-is-corrupt-never-trusted",
    "identity": "job-id-and-worker-must-equal-the-previous-record-fail-closed-reading-a-new-worker-starts-a-new-chain",
    "total": "total-is-fixed-by-the-first-report-fail-closed-reading-a-changed-total-is-a-conflict-never-rescaled",
    "monotone": "done-never-decreases-fail-closed-reading-an-equal-done-is-a-heartbeat-that-advances-reported-at",
    "completion": "no-report-follows-a-previous-record-with-done-equal-total-fail-closed-reading-completion-is-terminal",
    "clock": "now-never-precedes-the-previous-reported-at-fail-closed-reading-equal-now-is-accepted",
    "first": "the-first-report-may-carry-any-done-in-bounds-including-done-equal-total",
    "purity": "inputs-are-never-mutated-and-the-record-shares-no-container-with-them",
}
PROPERTIES = {
    "total": "hostile-requests-fail-closed-typed-never-raw",
    "deterministic": "same-request-same-record",
    "pure": "rejected-and-accepted-requests-leave-the-request-bit-identical",
    "bounded": "percent-bp-is-within-0-through-10000",
    "monotone": "along-an-accepted-chain-done-percent-and-reported-at-never-decrease",
    "terminal": "a-chain-accepts-no-report-after-completion",
}
VERSIONING = {
    "base_path": "/jobs/progress/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional fields, new enum members, new "
    "endpoints. Never change the meaning of an existing field in place.",
}
LINKS = {"queue_contract": "data/contracts/queue.yaml"}
FAILURE_CLASSES = ["malformed_progress_request", "corrupt_previous_record", "progress_conflict"]
FAILURE_TRIGGERS = {
    "malformed_progress_request": "request-shape-type-grammar-or-bound-violation-outside-the-previous-record-contents",
    "corrupt_previous_record": "previous-record-with-wrong-keys-types-grammar-bounds-percent-or-a-progress-id-that-does-not-recompute",
    "progress_conflict": "job-or-worker-mismatch-changed-total-done-regression-report-after-completion-or-clock-regression",
}
FAILURE_MAPPING = {
    "malformed_progress_request": "malformed_request",
    "corrupt_previous_record": "corrupt_record",
    "progress_conflict": "progress_conflict",
}
ERROR_ENUM = ["malformed_request", "corrupt_record", "progress_conflict", "internal"]


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-progress", sections=SECTIONS)

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
    # sibling bindings: the job id and worker grammars are the linked
    # queue contract's, never restated with a different value
    queue = yaml.safe_load((ROOT / cc["links"]["queue_contract"]).read_text())["contract"]
    for name in ("job_id", "worker"):
        if cc["identifiers"][name]["grammar"] != queue["identifiers"][name]["grammar"]:
            raise ContractError(f"{name} grammar must equal the queue contract's")
    print(f"progress contract lint ok: {path}")


if __name__ == "__main__":
    lint()
