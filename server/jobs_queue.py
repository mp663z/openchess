"""Production jobs queue for data/contracts/queue.yaml (contract id
`jobs-queue`).

One durable, priority-ordered, at-least-once job queue held in an exact
JSON state `{jobs, next_seq}`. `QueueEngine().apply(state, request)`
validates the request first (malformed_queue_request), then the state
(corrupt_queue), works on a detached canonical copy, and commits the new
state in place LAST, so a rejected call leaves state and request
untouched. Operations:

- enqueue: idempotent by dedupe key (the job id is derived from it); an
  identical re-enqueue returns the existing job, a different priority or
  payload is dedupe_conflict; a new job is capacity_exceeded past
  max_jobs or when the seq space is spent.
- claim: the claimable job (ready, or leased with an expired lease) with
  the lowest (priority, seq) is leased to the worker; a claimable job
  already at max_attempts is marked dead instead, and the claim moves on
  (the dead-marking commits even when no job is leased).
- ack / nack: only the lease holder, strictly before expiry
  (lease_conflict otherwise; unknown_job for an absent id); ack -> done,
  nack -> ready, attempts kept.

Every check is total on hostile input: exact types only, key types are
checked before any set or hash comparison, payloads are walked
iteratively (exact built-in JSON, UTF-8 strs, ints of at most
MAX_INT_DIGITS digits, finite floats, depth at most MAX_DEPTH, no
container met twice across all payloads of a state). Production never
imports tests.*.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from tools.queue_contract_lint import FAILURE_MAPPING, MAX_DEPTH, MAX_INT_DIGITS

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "queue.yaml"
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_STATE_FIELDS = tuple(_CC["state"]["fields"])
_JOB_FIELDS = tuple(_CC["state"]["job_fields"])
_STATUSES = frozenset(_CC["state"]["statuses"])
MAX_JOBS = _CC["state"]["max_jobs"]
MAX_ATTEMPTS = _CC["state"]["max_attempts"]
_OP_FIELDS = {spec["op"]: frozenset(spec["fields"]) for spec in _CC["request"]["operations"]}
_ID = _CC["identifiers"]
_JOB_ID = re.compile(_ID["job_id"]["grammar"], re.ASCII)
_DEDUPE_KEY = re.compile(_ID["dedupe_key"]["grammar"], re.ASCII)
_WORKER = re.compile(_ID["worker"]["grammar"], re.ASCII)
MAX_NOW = 2**53 - 1
MAX_LEASE_MS = 3_600_000
MAX_PRIORITY = 9
# every int below 2**_INT_BITS has at most MAX_INT_DIGITS + 1 decimal
# digits, far under the interpreter's int/str conversion limit; the exact
# decimal count then decides
_INT_BITS = math.ceil(MAX_INT_DIGITS * math.log2(10))

__all__ = ["FAILURE_MAPPING", "QueueEngine", "QueueError", "job_id_for", "state_id_for"]


class QueueError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _reject(failure_class):
    raise QueueError(failure_class, FAILURE_MAPPING[failure_class])


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def job_id_for(dedupe_key):
    return "job1:" + hashlib.sha256(f"job1|{dedupe_key}".encode()).hexdigest()


def state_id_for(state):
    return "qs1:" + hashlib.sha256(_canonical(state).encode()).hexdigest()


def _scalar(node):
    kind = type(node)
    if node is None or kind is bool:
        return True
    if kind is int:
        if node.bit_length() > _INT_BITS:
            return False
        try:
            return len(str(abs(node))) <= MAX_INT_DIGITS
        except ValueError:
            return False
    if kind is float:
        return math.isfinite(node)
    if kind is str:
        try:
            node.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    return False


def _payloads_ok(roots, visited):
    """Iterative walk over ROOTS sharing VISITED (container ids): never
    recurses, never raises, never calls user code."""
    pending = [(root, 1) for root in roots]
    while pending:
        node, depth = pending.pop()
        kind = type(node)
        if kind is dict or kind is list:
            if depth > MAX_DEPTH or id(node) in visited:
                return False
            visited.add(id(node))
            if kind is list:
                pending.extend((item, depth + 1) for item in list.__iter__(node))
                continue
            for key, value in dict.items(node):
                if type(key) is not str or not _scalar(key):
                    return False
                pending.append((value, depth + 1))
        elif not _scalar(node):
            return False
    return True


def _keys_are(mapping, fields):
    keys = list(dict.keys(mapping))
    return all(type(k) is str for k in keys) and set(keys) == set(fields)


def _int(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _matches(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _job_ok(job, lowest_seq, next_seq, visited):
    if type(job) is not dict or not _keys_are(job, _JOB_FIELDS):
        return False
    if not _int(job["seq"], lowest_seq, next_seq - 1):
        return False
    if not _int(job["priority"], 0, MAX_PRIORITY):
        return False
    status, attempts = job["status"], job["attempts"]
    if type(status) is not str or status not in _STATUSES:
        return False
    if not _int(attempts, 0, MAX_ATTEMPTS):
        return False
    owner, expires = job["lease_owner"], job["lease_expires_at"]
    if status == "leased":
        if not (_matches(owner, _WORKER) and _int(expires, 1, MAX_NOW) and attempts >= 1):
            return False
    elif owner is not None or expires is not None:
        return False
    if status == "dead" and attempts != MAX_ATTEMPTS:
        return False
    if status == "done" and attempts < 1:
        return False
    return _payloads_ok([job["payload"]], visited)


def _check_state(state):
    if type(state) is not dict or not _keys_are(state, _STATE_FIELDS):
        _reject("corrupt_queue")
    jobs, next_seq = state["jobs"], state["next_seq"]
    if type(jobs) is not list or not _int(next_seq, 0, MAX_NOW) or len(jobs) > MAX_JOBS:
        _reject("corrupt_queue")
    ids, visited, lowest_seq = set(), set(), 0
    for job in list.__iter__(jobs):
        # the id is checked (grammar, then uniqueness) before the rest of
        # the job, exactly as the contract's integrity order reads
        if type(job) is not dict or not _keys_are(job, _JOB_FIELDS):
            _reject("corrupt_queue")
        jid = job["job_id"]
        if not _matches(jid, _JOB_ID) or jid in ids:
            _reject("corrupt_queue")
        ids.add(jid)
        if not _job_ok(job, lowest_seq, next_seq, visited):
            _reject("corrupt_queue")
        lowest_seq = job["seq"] + 1


def _check_request(request, clock_limit):
    """Returns a detached exact copy of the request's values."""
    if type(request) is not dict:
        _reject("malformed_queue_request")
    keys = list(dict.keys(request))
    if not all(type(k) is str for k in keys) or "op" not in keys:
        _reject("malformed_queue_request")
    op = request["op"]
    if type(op) is not str or op not in _OP_FIELDS or set(keys) != _OP_FIELDS[op]:
        _reject("malformed_queue_request")
    if op == "enqueue":
        key, priority, payload = request["dedupe_key"], request["priority"], request["payload"]
        if not (
            _matches(key, _DEDUPE_KEY)
            and _int(priority, 0, MAX_PRIORITY)
            and _payloads_ok([payload], set())
        ):
            _reject("malformed_queue_request")
        return {
            "op": op,
            "dedupe_key": key,
            "priority": priority,
            "payload": json.loads(_canonical(payload)),
        }
    worker, now = request["worker"], request["now"]
    if not (_matches(worker, _WORKER) and _int(now, 0, MAX_NOW)):
        _reject("malformed_queue_request")
    if op == "claim":
        lease_ms = request["lease_ms"]
        if not _int(lease_ms, 1, MAX_LEASE_MS) or now + lease_ms > clock_limit:
            _reject("malformed_queue_request")
        return {"op": op, "worker": worker, "now": now, "lease_ms": lease_ms}
    jid = request["job_id"]
    if not _matches(jid, _JOB_ID):
        _reject("malformed_queue_request")
    return {"op": op, "worker": worker, "now": now, "job_id": jid}


def _by_id(jobs, jid):
    return next((job for job in jobs if job["job_id"] == jid), None)


class QueueEngine:
    """The production queue transitions."""

    SEQ_LIMIT = MAX_NOW  # a new job needs next_seq below this
    CLOCK_LIMIT = MAX_NOW  # now + lease_ms may not pass this

    def apply(self, state, request):
        req = _check_request(request, self.CLOCK_LIMIT)
        _check_state(state)
        work = json.loads(_canonical(state))  # detached working copy
        job = getattr(self, "_" + req["op"])(work, req)
        receipt = {
            "op": req["op"],
            "job_id": None if job is None else job["job_id"],
            "status": None if job is None else job["status"],
            "attempts": None if job is None else job["attempts"],
            "lease_expires_at": None if job is None else job["lease_expires_at"],
            "state_id": state_id_for(work),
        }
        state.clear()  # commit last, in place
        state.update(work)
        return receipt

    def _enqueue(self, work, req):
        jid = job_id_for(req["dedupe_key"])
        existing = _by_id(work["jobs"], jid)
        if existing is not None:
            same = existing["priority"] == req["priority"] and _canonical(
                existing["payload"]
            ) == _canonical(req["payload"])
            if not same:
                _reject("dedupe_conflict")
            return existing
        if len(work["jobs"]) >= MAX_JOBS or work["next_seq"] >= self.SEQ_LIMIT:
            _reject("capacity_exceeded")
        job = {
            "job_id": jid,
            "seq": work["next_seq"],
            "priority": req["priority"],
            "payload": req["payload"],
            "status": "ready",
            "attempts": 0,
            "lease_owner": None,
            "lease_expires_at": None,
        }
        work["jobs"].append(job)
        work["next_seq"] += 1
        return job

    def _claim(self, work, req):
        now = req["now"]
        claimable = [
            job
            for job in work["jobs"]
            if job["status"] == "ready"
            or (job["status"] == "leased" and job["lease_expires_at"] <= now)
        ]
        claimable.sort(key=lambda job: (job["priority"], job["seq"]))
        for job in claimable:
            if job["attempts"] >= MAX_ATTEMPTS:
                job.update(status="dead", lease_owner=None, lease_expires_at=None)
                continue
            job.update(
                status="leased",
                attempts=job["attempts"] + 1,
                lease_owner=req["worker"],
                lease_expires_at=now + req["lease_ms"],
            )
            return job
        return None

    def _settle(self, work, req, status):
        job = _by_id(work["jobs"], req["job_id"])
        if job is None:
            _reject("unknown_job")
        held = (
            job["status"] == "leased"
            and job["lease_owner"] == req["worker"]
            and req["now"] < job["lease_expires_at"]
        )
        if not held:
            _reject("lease_conflict")
        job.update(status=status, lease_owner=None, lease_expires_at=None)
        return job

    def _ack(self, work, req):
        return self._settle(work, req, "done")

    def _nack(self, work, req):
        return self._settle(work, req, "ready")
