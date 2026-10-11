"""Production jobs progress report for data/contracts/progress.yaml
(contract id `jobs-progress`).

`report(request)` is a pure function of one request
{op, job_id, worker, done, total, now, previous}: previous is null on the
first report and otherwise exactly the previous progress record. The record
carries percent_bp = floor(done * 10000 / total), the report time and a
chain link previous_id to the previous record's progress_id. total is fixed
by the first report, done never decreases (equal is a heartbeat), completion
(done == total) is terminal, now never precedes the previous reported_at,
and a previous record whose shape, bounds, percent or progress_id do not
recompute is corrupt and never trusted.

Validation order: request shape (malformed_progress_request), previous
integrity (corrupt_previous_record), chain (progress_conflict). Every check
is total on hostile input: exact types only, key types are checked before
any set comparison, no caller code runs, and every error is a fresh typed
ProgressError. Production never imports tests.*.
"""

from __future__ import annotations

import hashlib
import json
import re

import yaml

from tools.progress_contract_lint import CONTRACT, FAILURE_MAPPING

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_RECORD_FIELDS = _CC["record"]["fields"]
_JOB_RE = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)
_WORKER_RE = re.compile(_CC["identifiers"]["worker"]["grammar"], re.ASCII)
_PROGRESS_RE = re.compile(_CC["identifiers"]["progress_id"]["grammar"], re.ASCII)

MAX_TOTAL = 2**53 - 1
MAX_NOW = 2**53 - 1
BP = 10000


__all__ = ["FAILURE_MAPPING", "ProgressError", "report"]


class ProgressError(Exception):
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


def _request_ok(request):
    if not _exact_keys(request, _FIELDS):
        return False
    return (
        type(request["op"]) is str
        and request["op"] == "report"
        and _grammar(request["job_id"], _JOB_RE)
        and _grammar(request["worker"], _WORKER_RE)
        and _int_in(request["total"], 1, MAX_TOTAL)
        and _int_in(request["done"], 0, request["total"])
        and _int_in(request["now"], 0, MAX_NOW)
        and (request["previous"] is None or type(request["previous"]) is dict)
    )


def _progress_id(record_without_id):
    body = _canon(record_without_id).encode()
    return "pg1:" + hashlib.sha256(b"pg1\x00" + body).hexdigest()


def _previous_ok(prev):
    if not _exact_keys(prev, _RECORD_FIELDS):
        return False
    shape = (
        type(prev["op"]) is str
        and prev["op"] == "report"
        and _grammar(prev["job_id"], _JOB_RE)
        and _grammar(prev["worker"], _WORKER_RE)
        and _int_in(prev["total"], 1, MAX_TOTAL)
        and _int_in(prev["done"], 0, prev["total"])
        and _int_in(prev["reported_at"], 0, MAX_NOW)
        and type(prev["percent_bp"]) is int
        and prev["percent_bp"] == prev["done"] * BP // prev["total"]
        and (prev["previous_id"] is None or _grammar(prev["previous_id"], _PROGRESS_RE))
        and _grammar(prev["progress_id"], _PROGRESS_RE)
    )
    if not shape:
        return False
    return prev["progress_id"] == _progress_id({k: prev[k] for k in _RECORD_FIELDS[:-1]})


def _chain_ok(request):
    prev = request["previous"]
    return (
        request["job_id"] == prev["job_id"]
        and request["worker"] == prev["worker"]
        and request["total"] == prev["total"]
        and prev["done"] < prev["total"]
        and request["done"] >= prev["done"]
        and request["now"] >= prev["reported_at"]
    )


def report(request):
    """The report. Fresh typed errors (flag pattern)."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_progress_request"
    elif request["previous"] is not None and not _previous_ok(request["previous"]):
        failed = "corrupt_previous_record"
    elif request["previous"] is not None and not _chain_ok(request):
        failed = "progress_conflict"
    if failed is not None:
        raise ProgressError(failed)
    req = json.loads(_canon(request))  # detached exact copy
    prev = req["previous"]
    record = {
        "op": "report",
        "job_id": req["job_id"],
        "worker": req["worker"],
        "done": req["done"],
        "total": req["total"],
        "percent_bp": req["done"] * BP // req["total"],
        "reported_at": req["now"],
        "previous_id": None if prev is None else prev["progress_id"],
    }
    record["progress_id"] = _progress_id(record)
    return record
