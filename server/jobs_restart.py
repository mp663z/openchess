"""Production jobs restart for data/contracts/restart.yaml (contract id
`jobs-restart`).

`restart(state, request)` is a transition of one queue state (the linked
queue contract's state) for one request {op, worker, now}: every job leased
by that worker whose lease is still live (now strictly before its expiry)
returns to ready with no lease owner or expiry. Attempts are kept, a lease
at or past its expiry is left alone (the queue claim reclaims it), and a
released job at max_attempts returns to ready. The receipt names the
released job ids in seq order, binds the state before and after through the
queue's own state_id, and carries its own content-addressed restart_id. The
new state is committed last, in place.

Validation order: request shape (malformed_restart_request), then the state
against every queue state and job invariant (corrupt_queue). Every check is
total on hostile input: exact types only, key types are checked before any
set comparison, payloads are walked iteratively (no recursion, no caller
code), and every error is a fresh typed RestartError. A rejected call
leaves state and request unchanged. Production never imports tests.*.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from tools.restart_contract_lint import CONTRACT, FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_RECORD_FIELDS = _CC["record"]["fields"]
_QUEUE = yaml.safe_load((ROOT / _CC["links"]["queue_contract"]).read_text())["contract"]
_STATE_FIELDS = _QUEUE["state"]["fields"]
_JOB_FIELDS = _QUEUE["state"]["job_fields"]
_STATUSES = tuple(_QUEUE["state"]["statuses"])
MAX_ATTEMPTS = _REQ["bounds"]["max_attempts"]
_JOB_RE = re.compile(_QUEUE["identifiers"]["job_id"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_CC["identifiers"]["worker"]["grammar"], re.ASCII)
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"], re.ASCII)
_RESTART_RE = re.compile(_CC["identifiers"]["restart_id"]["grammar"], re.ASCII)

MAX_NOW = 2**53 - 1
MAX_JOBS = 10000
MAX_PRIORITY = 9
MAX_DEPTH = 64
MAX_DIGITS = 4000
MAX_BITS = 13288  # ceil(4000 * log2(10)): a pre-screen only


__all__ = ["FAILURE_MAPPING", "RestartError", "restart"]


class RestartError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canon_queue(obj):
    """The linked queue contract's state canon: non-ASCII kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canon_receipt(obj):
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


def _payload_ok(root, seen):
    """Iterative: exact built-in JSON only, bounded ints, finite floats,
    UTF-8 strs, depth at most the bound, no container met twice (a cycle
    or an alias, also across the payloads of one state: SEEN is shared).
    Never recurses, never raises."""
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
        and request["op"] == "restart"
        and _grammar(request["worker"], _WORKER_RE)
        and _int_in(request["now"], 0, MAX_NOW)
    )


def _job_ok(job, prior_seq, next_seq, containers):
    if not _exact_keys(job, _JOB_FIELDS):
        return False
    status, attempts = job["status"], job["attempts"]
    owner, expires = job["lease_owner"], job["lease_expires_at"]
    if not (
        _grammar(job["job_id"], _JOB_RE)
        and _int_in(job["seq"], prior_seq + 1, next_seq - 1)
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
    return _payload_ok(job["payload"], containers)


def _state_ok(state):
    if not _exact_keys(state, _STATE_FIELDS):
        return False
    jobs, next_seq = state["jobs"], state["next_seq"]
    if type(jobs) is not list or not _int_in(next_seq, 0, MAX_NOW):
        return False
    if len(jobs) > MAX_JOBS:
        return False
    seen_ids, prior_seq, containers = set(), -1, set()
    for job in list.__iter__(jobs):
        if not _job_ok(job, prior_seq, next_seq, containers):
            return False
        if job["job_id"] in seen_ids:
            return False
        seen_ids.add(job["job_id"])
        prior_seq = job["seq"]
    return True


def _state_id(state):
    return "qs1:" + hashlib.sha256(_canon_queue(state).encode("utf-8")).hexdigest()


def _restart_id(record_without_id):
    body = _canon_receipt(record_without_id).encode("utf-8")
    return "rst1:" + hashlib.sha256(b"rst1\x00" + body).hexdigest()


def restart(state, request):
    """The restart. Fresh typed errors (flag pattern); the new
    state is committed last, in place."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_restart_request"
    elif not _state_ok(state):
        failed = "corrupt_queue"
    if failed is not None:
        raise RestartError(failed)
    worker, now = request["worker"], request["now"]
    work = json.loads(_canon_queue(state))  # detached copy-on-write
    prior = _state_id(work)
    released = []
    for job in work["jobs"]:
        if (
            job["status"] == "leased"
            and job["lease_owner"] == worker
            and now < job["lease_expires_at"]
        ):
            job.update(status="ready", lease_owner=None, lease_expires_at=None)
            released.append(job["job_id"])
    record = {
        "op": "restart",
        "worker": worker,
        "restarted_at": now,
        "released": released,
        "prior_state_id": prior,
        "state_id": _state_id(work),
    }
    record["restart_id"] = _restart_id(record)
    # COMMIT LAST, in place
    state.clear()
    state.update(work)
    return record
