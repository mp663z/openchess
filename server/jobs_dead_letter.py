"""Production jobs dead-letter burial for data/contracts/dead_letter.yaml
(contract id `jobs-dead-letter`).

`bury(request)` is a pure function of one request {op, job, reason, now}:
job is exactly one job of the linked queue contract. Only a job that
satisfies every queue job invariant and has status dead is buried; the entry
binds the payload through a digest (never a copy of it) and carries its own
content-addressed entry_id. A dead job carries attempts == max_attempts
whatever the reason, and the reason is caller-attested (exhausted or
permanent).

Validation order: request shape (malformed_dead_letter_request), job
integrity (corrupt_job), dead status (job_not_dead). Every check is total on
hostile input: exact types only, key types are checked before any set
comparison, payloads are walked iteratively (no recursion, no caller code),
and every error is a fresh typed DeadLetterError. Production never imports
tests.*.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from tools.dead_letter_contract_lint import CONTRACT, FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_RECORD_FIELDS = _CC["record"]["fields"]
_REASONS = tuple(_REQ["reasons"])
MAX_ATTEMPTS = _REQ["max_attempts"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
_JOB_FIELDS = _QUEUE["state"]["job_fields"]
_STATUSES = tuple(_QUEUE["state"]["statuses"])
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_QUEUE["identifiers"]["worker"]["grammar"], re.ASCII)

MAX_NOW = 2**53 - 1
MAX_SEQ = 2**53 - 2
MAX_PRIORITY = 9
MAX_DEPTH = 64
MAX_DIGITS = 4000
MAX_BITS = 13288  # ceil(4000 * log2(10)): a pre-screen only


__all__ = ["FAILURE_MAPPING", "DeadLetterError", "bury"]


class DeadLetterError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _int_in(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _grammar(value, regex):
    return type(value) is str and regex.fullmatch(value) is not None


def _exact_keys(mapping, fields):
    """Exact dict whose keys are exact strs equal to FIELDS (checked
    before any set comparison, so no user key code runs)."""
    return (
        type(mapping) is dict
        and all(type(k) is str for k in mapping)
        and set(mapping) == set(fields)
    )


def _scalar_ok(node):
    kind = type(node)
    if node is None or kind is bool:
        return True
    if kind is int:
        if node.bit_length() > MAX_BITS:
            return False
        ok = False
        try:
            ok = len(str(abs(node))) <= MAX_DIGITS
        except ValueError:
            ok = False
        return ok
    if kind is float:
        return math.isfinite(node)
    if kind is str:
        ok = True
        try:
            node.encode("utf-8")
        except UnicodeEncodeError:
            ok = False
        return ok
    return False


def _payload_ok(root):
    """Iterative: exact built-in JSON only, bounded ints, finite floats,
    UTF-8 strs, depth at most the bound, no container met twice (a cycle
    or an alias). Never recurses, never raises."""
    seen = set()
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        kind = type(node)
        if kind is list or kind is dict:
            if depth > MAX_DEPTH or id(node) in seen:
                return False
            seen.add(id(node))
            if kind is dict:
                for key, value in dict.items(node):
                    if type(key) is not str or not _scalar_ok(key):
                        return False
                    stack.append((value, depth + 1))
            else:
                for value in list.__iter__(node):
                    stack.append((value, depth + 1))
        elif not _scalar_ok(node):
            return False
    return True


def _request_ok(request):
    if not _exact_keys(request, _FIELDS):
        return False
    return (
        type(request["op"]) is str
        and request["op"] == "bury"
        and type(request["job"]) is dict
        and type(request["reason"]) is str
        and request["reason"] in _REASONS
        and _int_in(request["now"], 0, MAX_NOW)
    )


def _job_ok(job):
    if not _exact_keys(job, _JOB_FIELDS):
        return False
    status, attempts = job["status"], job["attempts"]
    owner, expires = job["lease_owner"], job["lease_expires_at"]
    if not (
        _grammar(job["job_id"], _JOB_RE)
        and _int_in(job["seq"], 0, MAX_SEQ)
        and _int_in(job["priority"], 0, MAX_PRIORITY)
        and type(status) is str
        and status in _STATUSES
        and _int_in(attempts, 0, MAX_ATTEMPTS)
    ):
        return False
    if status == "leased":
        if not (_grammar(owner, _WORKER_RE) and _int_in(expires, 1, MAX_NOW) and attempts >= 1):
            return False
    elif owner is not None or expires is not None:
        return False
    if status == "dead" and attempts != MAX_ATTEMPTS:
        return False
    if status == "done" and attempts < 1:
        return False
    return _payload_ok(job["payload"])


def _digest(prefix, value):
    return (
        prefix
        + ":"
        + hashlib.sha256(prefix.encode() + b"\x00" + _canon(value).encode()).hexdigest()
    )


def bury(request):
    """The burial. Fresh typed errors (flag pattern)."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_bury_request"
    elif not _job_ok(request["job"]):
        failed = "corrupt_job"
    elif request["job"]["status"] != "dead":
        failed = "job_not_dead"
    if failed is not None:
        raise DeadLetterError(failed)
    req = json.loads(_canon(request))  # detached exact copy
    job = req["job"]
    record = {
        "op": "bury",
        "job_id": job["job_id"],
        "seq": job["seq"],
        "priority": job["priority"],
        "payload_digest": _digest("pd1", job["payload"]),
        "attempts": job["attempts"],
        "reason": req["reason"],
        "buried_at": req["now"],
    }
    record["entry_id"] = _digest("dl1", record)
    return record
