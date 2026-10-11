"""Production jobs retry decision for data/contracts/retry.yaml (contract
id `jobs-retry`).

`decide(request)` is a pure function of one request
{op, job_id, attempts, failure, now, policy}. A permanent failure or an
exhausted job (attempts == max_attempts) is dead; any other failure
(transient or timeout) is retried after
min(max_delay_ms, base_delay_ms * multiplier ** (attempts - 1)) ms, at
now + delay. There is no jitter. A retry_at past 2**53 - 1 is
clock_overflow: never clamped, never turned into dead.

Validation order: request shape (malformed_retry_request), then policy
(invalid_retry_policy), then clock (clock_overflow). Every check is total
on hostile input: exact types only, key types are checked before any set
comparison, no caller code runs, and every error is a fresh typed
RetryError. Production never imports tests.*.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from tools.retry_contract_lint import FAILURE_MAPPING

ROOT = Path(__file__).resolve().parents[1]
_CC = yaml.safe_load((ROOT / "data" / "contracts" / "retry.yaml").read_text())["contract"]
_REQUEST_FIELDS = tuple(_CC["request"]["operations"][0]["fields"])
_POLICY_FIELDS = tuple(_CC["request"]["policy_fields"])
_FAILURES = frozenset(_CC["request"]["failures"])
MAX_ATTEMPTS = _CC["request"]["max_attempts"]
MAX_NOW = 2**53 - 1
MAX_BASE_DELAY_MS = 3_600_000
MAX_MULTIPLIER = 10
MAX_DELAY_MS = 86_400_000
_JOB_ID = re.compile(_CC["identifiers"]["job_id"]["grammar"], re.ASCII)

__all__ = ["FAILURE_MAPPING", "RetryError", "decide"]


class RetryError(Exception):
    def __init__(self, failure_class):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = FAILURE_MAPPING[failure_class]
        self.retryable = False


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _int(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _keys_are(mapping, fields):
    if type(mapping) is not dict:
        return False
    keys = list(dict.keys(mapping))
    return all(type(k) is str for k in keys) and set(keys) == set(fields)


def _request_ok(request):
    if not _keys_are(request, _REQUEST_FIELDS):
        return False
    op, job_id, failure = request["op"], request["job_id"], request["failure"]
    return (
        type(op) is str
        and op == "decide"
        and type(job_id) is str
        and _JOB_ID.fullmatch(job_id) is not None
        and _int(request["attempts"], 1, MAX_ATTEMPTS)
        and type(failure) is str
        and failure in _FAILURES
        and _int(request["now"], 0, MAX_NOW)
        and type(request["policy"]) is dict
    )


def _policy_ok(policy):
    if not _keys_are(policy, _POLICY_FIELDS):
        return False
    base = policy["base_delay_ms"]
    return (
        _int(base, 1, MAX_BASE_DELAY_MS)
        and _int(policy["multiplier"], 1, MAX_MULTIPLIER)
        and _int(policy["max_delay_ms"], base, MAX_DELAY_MS)
    )


def _reject(failure_class):
    raise RetryError(failure_class)


def decide(request):
    if not _request_ok(request):
        _reject("malformed_retry_request")
    if not _policy_ok(request["policy"]):
        _reject("invalid_retry_policy")
    req = json.loads(_canonical(request))  # detached exact copy
    policy = req["policy"]
    dead = req["failure"] == "permanent" or req["attempts"] >= MAX_ATTEMPTS
    if dead:
        delay = retry_at = None
    else:
        delay = min(
            policy["max_delay_ms"],
            policy["base_delay_ms"] * policy["multiplier"] ** (req["attempts"] - 1),
        )
        retry_at = req["now"] + delay
        if retry_at > MAX_NOW:
            _reject("clock_overflow")
    record = {
        "op": "decide",
        "job_id": req["job_id"],
        "decision": "dead" if dead else "retry",
        "attempts": req["attempts"],
        "delay_ms": delay,
        "retry_at": retry_at,
    }
    digest = hashlib.sha256(
        b"rd1\x00" + _canonical({"request": req, "record": record}).encode()
    ).hexdigest()
    record["decision_id"] = "rd1:" + digest
    return record
