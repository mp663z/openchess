#!/usr/bin/env python3
"""T0293: jobs idempotency contract lint - exact structured pins."""

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

CONTRACT = ROOT / "data/contracts/jobs_idempotency.yaml"
QUEUE_CONTRACT = ROOT / "data/contracts/queue.yaml"

SECTIONS = {"errors", "failures", "id", "identifiers", "links",
            "request",
            "oracle_boundary", "outcomes", "properties", "record",
            "role", "semantics", "versioning"}
ROLE = {
    "kind": "keyed-exactly-once-apply-over-one-queue-state",
    "serves": "background-job-execution",
    "scope": "single-state-single-ledger-idempotent-apply",
    "owns": "idempotency-key-receipt-semantics-and-request-"
            "fingerprinting",
    "not_scope": "key-expiry-cross-state-deduplication-distributed-"
                 "locking-or-the-queue-own-enqueue-dedupe-key-"
                 "mechanics",
}
RECORD = {
    "fields": ["receipt_id", "idempotency_key",
               "request_fingerprint", "prior_state_id", "state_id",
               "sequence"],
    "exact": True,
}
REQUEST = {
    "fields": ["idempotency_key", "op", "payload"],
    "exact": True,
}
IDENTIFIERS = {
    "receipt_id": {
        "kind": "content-addressed-idempotency-receipt-id",
        "grammar": "^jir1:[0-9a-f]{64}$",
        "derivation": "sha256-over-idempotency-key-request-"
                      "fingerprint-prior-state-id-state-id-sequence",
        "source": "derived-from-validated-apply-never-caller-"
                  "supplied",
    },
    "idempotency_key": {
        "kind": "caller-supplied-opaque-idempotency-key",
        "grammar": "^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        "source": "caller-supplied-exact-built-in-string-grammar-"
                  "validated",
    },
    "request_fingerprint": {
        "kind": "untrusted-fingerprinter-request-token",
        "grammar": "^jif1:[0-9a-f]{64}$",
        "derivation": "sha256-over-domain-separated-length-framed-"
                      "canonical-serialization-of-every-exact-"
                      "frozen-request-field-except-the-key",
        "source": "fingerprinter-output-shape-validated-and-bound-"
                  "byte-exact-to-the-local-request-derivation",
    },
    "state_id": {
        "kind": "queue-state-id-of-the-post-apply-state",
        "grammar": "^qs1:[0-9a-f]{64}$",
        "source": "derived-from-the-linked-queue-transition-never-"
                  "caller-supplied",
    },
    "prior_state_id": {
        "kind": "queue-state-id-of-the-pre-apply-state",
        "grammar": "^qs1:[0-9a-f]{64}$",
        "source": "derived-from-the-linked-queue-state-never-caller-"
                  "supplied",
    },
}
OUTCOMES = {
    "values": ["applied", "replayed"],
    "closed": True,
    "applied": "unseen-key-applies-exactly-one-queue-transition-and-"
               "commits-one-receipt",
    "replayed": "seen-key-with-identical-fingerprint-returns-the-"
                "stored-receipt-no-transition",
}
SEMANTICS = {
    "request_validation": "exact-key-op-payload-shape-op-and-"
                          "payload-through-linked-queue-validation",
    "source_validation": "full-linked-queue-state-validation-and-"
                         "full-ledger-validation-before-any-oracle-"
                         "call",
    "ledger_binding": "every-receipt-rederives-sequences-strictly-"
                      "increasing-from-zero-keys-unique-state-ids-"
                      "chained-last-receipt-state-id-names-the-live-"
                      "state",
    "deduplication": "key-lookup-then-byte-exact-fingerprint-"
                     "comparison-never-key-only",
    "conflict": "seen-key-with-different-fingerprint-fails-closed-"
                "never-overwrites",
    "commit": "queue-transition-and-receipt-committed-together-last-"
              "after-full-validation-and-fingerprinting",
    "queue_rejection": "a-request-the-linked-queue-engine-rejects-on-"
                       "its-own-domain-rules-propagates-that-typed-"
                       "error-and-commits-no-receipt",
}
ORACLE_BOUNDARY = {
    "role": "request-fingerprinter-is-untrusted-input",
    "single_evaluation": "exactly-one-call-per-apply",
    "frozen_snapshots": "request-state-and-ledger-frozen-before-"
                        "first-oracle-call-never-re-read-restored-"
                        "bit-identical",
    "output_validation": "exact-built-in-string-pinned-grammar-"
                         "byte-exact-request-bound-token-or-fail-"
                         "closed",
}
FAILURE_CLASSES = ["malformed_idempotency_request", "corrupt_queue",
                   "corrupt_ledger", "key_conflict",
                   "divergent_fingerprint"]
FAILURE_TRIGGERS = {
    "malformed_idempotency_request":
        "request-key-op-payload-grammar-or-type-violation",
    "corrupt_queue":
        "queue-state-fails-linked-queue-state-validation-or-state-"
        "id-rederivation",
    "corrupt_ledger":
        "ledger-shape-grammar-rederivation-uniqueness-chain-or-"
        "live-state-binding-violation",
    "key_conflict": "seen-key-with-a-different-request-fingerprint",
    "divergent_fingerprint":
        "fingerprinter-raising-any-baseexception-non-exact-string-"
        "bad-grammar-or-request-divergent-token",
}
FAILURE_MAPPING = {
    "malformed_idempotency_request": "malformed_request",
    "corrupt_queue": "corrupt_queue",
    "corrupt_ledger": "corrupt_ledger",
    "key_conflict": "key_conflict",
    "divergent_fingerprint": "divergent_fingerprint",
}
ERROR_ENUM = ["malformed_request", "corrupt_queue", "corrupt_ledger",
              "key_conflict", "divergent_fingerprint", "internal"]
PROPERTIES = {
    "total": "hostile-requests-states-ledgers-and-fingerprinters-"
             "fail-closed-typed-never-raw",
    "atomic": "rejected-apply-leaves-state-ledger-and-request-bit-"
              "identical",
    "deterministic": "same-inputs-same-outcome-receipt-state-and-"
                     "ledger",
    "idempotent": "repeated-identical-keyed-request-returns-the-"
                  "identical-receipt-and-applies-nothing",
    "rollback": "durable-state-rolled-back-past-a-receipt-makes-the-"
                "stale-ledger-fail-closed-never-replayed",
}
VERSIONING = {
    "base_path": "/jobs/idempotency/v1",
    "rule": "Clients pin MAJOR. MINOR is additive-only: new optional"
            " fields, new enum members, new endpoints. Never change"
            " the meaning of an existing field in place.",
}
LINKS = {
    "queue_contract": "data/contracts/queue.yaml",
}


def lint(path=None):
    path = path or CONTRACT
    doc = yaml.safe_load(Path(path).read_text())
    cc = close_envelope(doc, contract_id="jobs-idempotency",
                        sections=SECTIONS)

    def check(name, expected, actual):
        # exact equality AND exact built-in types: a bool/int
        # confusion (True == 1) or a str subclass is drift
        if actual != expected or not _same_types(expected, actual):
            raise ContractError(f"{name} drifted: {actual!r}")

    check("role", ROLE, cc.get("role"))
    check("record", RECORD, cc.get("record"))
    check("request", REQUEST, cc.get("request"))
    check("identifiers", IDENTIFIERS, cc.get("identifiers"))
    check("outcomes", OUTCOMES, cc.get("outcomes"))
    check("semantics", SEMANTICS, cc.get("semantics"))
    check("oracle_boundary", ORACLE_BOUNDARY,
          cc.get("oracle_boundary"))
    failures = cc["failures"]
    close_failures(failures)
    check("failures.classes", FAILURE_CLASSES,
          failures.get("classes"))
    check("failures.triggers", FAILURE_TRIGGERS,
          failures.get("triggers"))
    check("failures.mapping", FAILURE_MAPPING,
          failures.get("mapping"))
    check("failures.closed", True, failures.get("closed"))
    errors = cc["errors"]
    close_errors(errors, shape_keys=("retryable_true_only_for",))
    check("errors.closed_enum", ERROR_ENUM,
          errors.get("closed_enum"))
    check("errors.shape.retryable_true_only_for", ["internal"],
          errors["shape"].get("retryable_true_only_for"))
    if set(FAILURE_MAPPING.values()) != set(ERROR_ENUM) - {"internal"}:
        raise ContractError("failure mapping must cover the enum")
    check("properties", PROPERTIES, cc.get("properties"))
    check("versioning", VERSIONING, cc.get("versioning"))
    check("links", LINKS, cc.get("links"))
    for name, rel in LINKS.items():
        if not (ROOT / rel).is_file():
            raise ContractError(f"link {name} missing: {rel}")
    # sibling binding: the state-id grammars are the linked queue
    # contract's, never restated with a different value
    queue = yaml.safe_load(QUEUE_CONTRACT.read_text())["contract"]
    queue_state_id = queue["identifiers"]["state_id"]["grammar"]
    for name in ("state_id", "prior_state_id"):
        if cc["identifiers"][name]["grammar"] != queue_state_id:
            raise ContractError(
                f"{name} grammar must equal the queue contract's")
    print(f"jobs idempotency contract lint ok: {path}")


def _same_types(expected, actual):
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict):
        return all(_same_types(v, actual[k])
                   for k, v in expected.items())
    if isinstance(expected, list):
        return all(_same_types(e, a)
                   for e, a in zip(expected, actual, strict=True))
    return True


if __name__ == "__main__":
    lint()
