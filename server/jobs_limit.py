"""Production jobs limit decision for data/contracts/limit.yaml (contract
id `jobs-limit`).

`admit(request)` is a pure function of one request
{op, key, cost, now, policy, previous}: a keyed token bucket of `capacity`
tokens gains one token every `refill_ms` milliseconds. previous is null for
a fresh full bucket and otherwise exactly the previous bucket record. The
request is admitted when the refilled tokens cover the cost; otherwise it is
denied with the exact retry_after_ms. At capacity no partial refill is
banked, a cost above capacity is malformed (never a deny), a deny whose
now + retry_after_ms would pass 2**53 - 1 is clock_overflow (never clamped),
and a previous record whose shape, bounds, decision invariants or limit_id
do not recompute is corrupt and never trusted.

Validation order: request, policy, cost within capacity, previous integrity,
chain, clock. Every check is total on hostile input: exact types only, key
types are checked before any set comparison, no caller code runs, and every
error is a fresh typed LimitError. Production never imports tests.*.
"""

from __future__ import annotations

import hashlib
import json
import re

import yaml

from tools.limit_contract_lint import CONTRACT, FAILURE_MAPPING

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQ = _CC["request"]
_FIELDS = _REQ["operations"][0]["fields"]
_POLICY_FIELDS = _REQ["policy_fields"]
_RECORD_FIELDS = _CC["record"]["fields"]
_DECISIONS = tuple(_CC["record"]["decisions"])
_KEY_RE = re.compile(_CC["identifiers"]["key"]["grammar"], re.ASCII)
_LIMIT_RE = re.compile(_CC["identifiers"]["limit_id"]["grammar"], re.ASCII)

MAX_CAPACITY = 1_000_000
MAX_REFILL = 86_400_000
MAX_NOW = 2**53 - 1


__all__ = ["FAILURE_MAPPING", "LimitError", "admit"]


class LimitError(Exception):
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
        and request["op"] == "admit"
        and _grammar(request["key"], _KEY_RE)
        and _int_in(request["cost"], 1, MAX_CAPACITY)
        and _int_in(request["now"], 0, MAX_NOW)
        and type(request["policy"]) is dict
        and (request["previous"] is None or type(request["previous"]) is dict)
    )


def _policy_ok(policy):
    return (
        _exact_keys(policy, _POLICY_FIELDS)
        and _int_in(policy["capacity"], 1, MAX_CAPACITY)
        and _int_in(policy["refill_ms"], 1, MAX_REFILL)
    )


def _limit_id(record_without_id):
    body = _canon(record_without_id).encode()
    return "lm1:" + hashlib.sha256(b"lm1\x00" + body).hexdigest()


def _previous_ok(prev):
    if not _exact_keys(prev, _RECORD_FIELDS):
        return False
    shape = (
        type(prev["op"]) is str
        and prev["op"] == "admit"
        and _grammar(prev["key"], _KEY_RE)
        and _int_in(prev["capacity"], 1, MAX_CAPACITY)
        and _int_in(prev["refill_ms"], 1, MAX_REFILL)
        and _int_in(prev["cost"], 1, prev["capacity"])
        and type(prev["decision"]) is str
        and prev["decision"] in _DECISIONS
        and _int_in(prev["tokens"], 0, prev["capacity"])
        and _int_in(prev["updated_at"], 0, MAX_NOW)
        and (prev["previous_id"] is None or _grammar(prev["previous_id"], _LIMIT_RE))
        and _grammar(prev["limit_id"], _LIMIT_RE)
    )
    if not shape:
        return False
    if prev["decision"] == "admit":
        if prev["retry_after_ms"] is not None or prev["tokens"] > prev["capacity"] - prev["cost"]:
            return False
    elif not (
        _int_in(prev["retry_after_ms"], 1, MAX_NOW)
        and prev["tokens"] < prev["cost"]
        and (prev["cost"] - prev["tokens"] - 1) * prev["refill_ms"] < prev["retry_after_ms"]
        and prev["retry_after_ms"] <= (prev["cost"] - prev["tokens"]) * prev["refill_ms"]
        and prev["updated_at"] + prev["retry_after_ms"] <= MAX_NOW
    ):
        return False
    return prev["limit_id"] == _limit_id({k: prev[k] for k in _RECORD_FIELDS[:-1]})


def _chain_ok(request):
    prev, policy = request["previous"], request["policy"]
    return (
        request["key"] == prev["key"]
        and policy["capacity"] == prev["capacity"]
        and policy["refill_ms"] == prev["refill_ms"]
        and request["now"] >= prev["updated_at"]
    )


def admit(request):
    """The decision. Fresh typed errors (flag pattern)."""
    failed = None
    if not _request_ok(request):
        failed = "malformed_limit_request"
    elif not _policy_ok(request["policy"]):
        failed = "invalid_limit_policy"
    elif request["cost"] > request["policy"]["capacity"]:
        failed = "malformed_limit_request"
    elif request["previous"] is not None and not _previous_ok(request["previous"]):
        failed = "corrupt_previous_bucket"
    elif request["previous"] is not None and not _chain_ok(request):
        failed = "limit_conflict"
    if failed is None:
        req = json.loads(_canon(request))  # detached exact copy
        capacity, refill = req["policy"]["capacity"], req["policy"]["refill_ms"]
        now, prev = req["now"], req["previous"]
        if prev is None:
            refilled, base = capacity, now
        else:
            gained = (now - prev["updated_at"]) // refill
            if prev["tokens"] + gained >= capacity:
                refilled, base = capacity, now
            else:
                refilled, base = prev["tokens"] + gained, prev["updated_at"] + gained * refill
        if refilled >= req["cost"]:
            decision, tokens, retry = "admit", refilled - req["cost"], None
        else:
            decision, tokens = "deny", refilled
            retry = (req["cost"] - refilled) * refill - (now - base)
            if now + retry > MAX_NOW:
                failed = "clock_overflow"
    if failed is not None:
        raise LimitError(failed)
    record = {
        "op": "admit",
        "key": req["key"],
        "capacity": capacity,
        "refill_ms": refill,
        "cost": req["cost"],
        "decision": decision,
        "tokens": tokens,
        "updated_at": base,
        "retry_after_ms": retry,
        "previous_id": None if prev is None else prev["limit_id"],
    }
    record["limit_id"] = _limit_id(record)
    return record
