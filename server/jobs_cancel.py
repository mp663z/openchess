"""Production jobs cancellation decision for data/contracts/cancel.yaml
(contract id `jobs-cancel`).

`cancel(request)` is a pure function of one request
{op, job, requester, now}: job is exactly one job of the linked queue
contract. Only a valid job whose presented status is ready or leased is
cancelled; the decision record binds the payload through its digest
(never a copy), carries no lease fields, and is content-addressed by its
own decision id. A recorded cancellation is terminal; applying it to
queue state or aborting a worker is outside this contract.

Validation order: request shape (malformed_cancel_request), then job
integrity against every queue job invariant (corrupt_job), then status
(job_not_cancellable for a valid done or dead job). Every check is total
on hostile input: exact types only, key types are checked before any set
comparison, the payload walk is iterative and bounded, no caller code
runs, and every error is a fresh typed CancelError. Production never
imports tests.*.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from tools.cancel_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
_CC = yaml.safe_load((ROOT / "data" / "contracts" / "cancel.yaml").read_text())["contract"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
_REQUEST_FIELDS = tuple(_CC["request"]["operations"][0]["fields"])
_CANCELLABLE = frozenset(_CC["record"]["statuses_before"])
MAX_ATTEMPTS = _CC["request"]["max_attempts"]
_JOB_FIELDS = tuple(_QUEUE["state"]["job_fields"])
_STATUSES = frozenset(_QUEUE["state"]["statuses"])
_JOB_ID = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_REQUESTER = re.compile(_CC["identifiers"]["requester"]["grammar"], re.ASCII)
_WORKER = re.compile(_QUEUE["identifiers"]["worker"]["grammar"], re.ASCII)

# Numeric spellings of the contract's request.bounds text.
MAX_NOW = 2**53 - 1
MAX_SEQ = 2**53 - 2
MAX_PRIORITY = 9
MAX_DEPTH = 64
MAX_INT_DIGITS = 4000
MAX_INT_BITS = 13288  # ceil(4000 * log2(10)): a pre-screen only

__all__ = ["FAILURE_MAPPING", "CancelError", "cancel"]


class CancelError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _int(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _grammar(value, regex):
    return type(value) is str and regex.fullmatch(value) is not None


def _keys_are(mapping, fields):
    if type(mapping) is not dict:
        return False
    keys = list(dict.keys(mapping))
    return all(type(k) is str for k in keys) and set(keys) == set(fields)


def _reject(failure_class):
    raise CancelError(failure_class)


def _scalar_ok(node):
    kind = type(node)
    if node is None or kind is bool:
        return True
    if kind is int:
        if node.bit_length() > MAX_INT_BITS:
            return False
        return len(str(abs(node))) <= MAX_INT_DIGITS
    if kind is float:
        return math.isfinite(node)
    if kind is str:
        try:
            node.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
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
    if not _keys_are(request, _REQUEST_FIELDS):
        return False
    return (
        type(request["op"]) is str
        and request["op"] == "cancel"
        and type(request["job"]) is dict
        and _grammar(request["requester"], _REQUESTER)
        and _int(request["now"], 0, MAX_NOW)
    )


def _job_ok(job):
    if not _keys_are(job, _JOB_FIELDS):
        return False
    status, attempts = job["status"], job["attempts"]
    owner, expires = job["lease_owner"], job["lease_expires_at"]
    if not (
        _grammar(job["job_id"], _JOB_ID)
        and _int(job["seq"], 0, MAX_SEQ)
        and _int(job["priority"], 0, MAX_PRIORITY)
        and type(status) is str
        and status in _STATUSES
        and _int(attempts, 0, MAX_ATTEMPTS)
    ):
        return False
    if status == "leased":
        if not (_grammar(owner, _WORKER) and _int(expires, 1, MAX_NOW) and attempts >= 1):
            return False
    elif owner is not None or expires is not None:
        return False
    if status == "dead" and attempts != MAX_ATTEMPTS:
        return False
    if status == "done" and attempts < 1:
        return False
    return _payload_ok(job["payload"])


def cancel(request):
    if not _request_ok(request):
        _reject("malformed_cancel_request")
    if not _job_ok(request["job"]):
        _reject("corrupt_job")
    if request["job"]["status"] not in _CANCELLABLE:
        _reject("job_not_cancellable")
    req = json.loads(_canonical(request))  # detached exact copy
    job = req["job"]
    record = {
        "op": "cancel",
        "job_id": job["job_id"],
        "seq": job["seq"],
        "priority": job["priority"],
        "payload_digest": "pd1:"
        + hashlib.sha256(b"pd1\x00" + _canonical(job["payload"]).encode()).hexdigest(),
        "status_before": job["status"],
        "attempts": job["attempts"],
        "requester": req["requester"],
        "cancelled_at": req["now"],
    }
    record["decision_id"] = (
        "cd1:" + hashlib.sha256(b"cd1\x00" + _canonical(record).encode()).hexdigest()
    )
    return record
