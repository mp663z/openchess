"""Production store crash resume for data/contracts/crash_resume.yaml.

Verified torn-tail recovery of a WAL-logged store after a crash. The
surviving log is the LONGEST prefix that passes the linked production
WAL (store.wal, public surface only) validation and replay, after every
entry is pre-screened by a total, iterative admission walk (exact
built-in JSON values, exact-str keys, alias-free, depth at most
MAX_DEPTH, ints of at most MAX_INT_DIGITS digits). Every entry at or
before the durable checkpoint must lie in that prefix, otherwise the
crash destroyed acknowledged data (corrupt_source). Everything from the
first invalid entry onward is the torn tail. The tail goes EXACTLY ONCE
to the UNTRUSTED quarantine sink behind a BaseException boundary
(detached frozen copy, frozen log and request); its token must equal
the local canonical tail serialization byte-for-byte. Only then is the
torn tail removed, last. A rejected resume leaves log and request bit-
and reference-identical. Production never imports tests.*.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

import yaml

from store import wal as _wal
from tools.crash_resume_contract_lint import (
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_DIGITS,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "crash_resume.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_REQUEST_FIELDS = _CC["request"]["fields"]
_TOKEN_RE = re.compile(_CC["identifiers"]["quarantine_token"]["grammar"])

__all__ = ["FAILURE_MAPPING", "ResumeEngine", "ResumeError",
           "quarantine_tail"]


class ResumeError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise ResumeError(cls, FAILURE_MAPPING[cls])


# every int below 2**_MAX_INT_BITS has at most MAX_INT_DIGITS + 1 decimal
# digits (13288 bits -> <= 4001 digits), safely under the interpreter's
# str() limit; the exact digit count below then decides inclusively
_MAX_INT_BITS = math.ceil(MAX_INT_DIGITS * math.log2(10))


def _scalar_ok(obj):
    kind = type(obj)
    if obj is None or kind is bool:
        return True
    if kind is int:
        # bounded decimal form: never hits the interpreter's int/str
        # conversion limit (a raw ValueError) - bit_length bound first
        # a pre-screen only: the exact decimal count decides
        if obj.bit_length() > _MAX_INT_BITS:
            return False
        try:
            return len(str(abs(obj))) <= MAX_INT_DIGITS
        except ValueError:
            return False
    if kind is float:
        return math.isfinite(obj)
    if kind is str:
        try:
            obj.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    return False


def _is_canonical_json(obj):
    """TOTAL admission of one discarded entry, walked ITERATIVELY
    with an explicit stack: exact built-in JSON values only (str
    UTF-8 encodable, bounded int, finite float, bool, None, list,
    dict with exact-str keys); any container met twice (a cycle or
    an alias) or nesting deeper than MAX_DEPTH is rejected.
    Never recurses, never raises."""
    seen = set()
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        kind = type(node)
        if kind is list or kind is dict:
            # ALIAS-FREE: any container met twice anywhere (a cycle or
            # a shared sub-tree) is inadmissible - keeps the walk and
            # the canonical encoding linear in the object count
            if depth > MAX_DEPTH or id(node) in seen:
                return False
            seen.add(id(node))
            if kind is dict:
                for key, value in node.items():
                    if type(key) is not str or not _scalar_ok(key):
                        return False
                    stack.append((value, depth + 1))
            else:
                for value in node:
                    stack.append((value, depth + 1))
        elif not _scalar_ok(node):
            return False
    return True


def _canon(entry):
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def quarantine_tail(tail):
    """THE pinned canonical quarantine token: sha256 over the
    DOMAIN-SEPARATED, LENGTH-FRAMED canonical JSON of every discarded
    entry, in order - deterministic in the tail and only the tail."""
    parts = ["qtn1"]
    for entry in tail:
        text = _canon(entry)
        parts.append(f"{len(text)}:{text}")
    return "qtn1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _snapshot(obj, acc, seen):
    """Reference-preserving snapshot of every nested container."""
    if id(obj) in seen:
        return
    if type(obj) is dict:
        seen.add(id(obj))
        acc.append((obj, dict(obj)))
        for value in list(obj.values()):
            _snapshot(value, acc, seen)
    elif type(obj) is list:
        seen.add(id(obj))
        acc.append((obj, list(obj)))
        for value in list(obj):
            _snapshot(value, acc, seen)


def _restore(acc):
    for obj, saved in acc:
        if type(obj) is dict:
            obj.clear()
            obj.update(saved)
        else:
            obj[:] = saved


def _valid_prefix_length(wal_engine, log):
    """Length of the longest prefix passing linked WAL replay. Every
    entry is first PRE-SCREENED by the total admission walk (exact-str
    keys, alias-free, bounded): the prefix is capped at the first
    inadmissible entry, so the linked WAL never sees a hostile object."""
    cap = len(log)
    for index, entry in enumerate(log):
        if not _is_canonical_json(entry):
            cap = index
            break
    for k in range(cap, -1, -1):
        try:
            wal_engine.replay(log[:k])
        except _wal.WalError:
            continue
        return k
    raise AssertionError("the empty prefix always replays")


class ResumeEngine:
    """The contract's pinned crash resume."""

    def __init__(self, quarantine_sink):
        self.sink = quarantine_sink  # UNTRUSTED
        self._wal = _wal.WalEngine(_wal.canonical_payload)  # trusted

    def _quarantine(self, frozen_tail):
        """THE sink boundary: raising ANY BaseException, non-exact-str
        or wrong-grammar output fails closed as divergent_quarantine."""
        try:
            out = self.sink(copy.deepcopy(frozen_tail))
        except BaseException:
            # fail closed against the FULL BaseException surface
            _fail("divergent_quarantine")
        if type(out) is not str or _TOKEN_RE.fullmatch(out) is None:
            _fail("divergent_quarantine")
        return out

    @staticmethod
    def _derive_resume_id(head, sid, resumed, discarded, token):
        return (
            "rsm1:"
            + hashlib.sha256(f"{head}\n{sid}\n{resumed}\n{discarded}\n{token}".encode()).hexdigest()
        )

    def resume(self, log, request):
        if type(log) is not list:
            _fail("malformed_resume_record")
        if type(request) is not dict:
            _fail("malformed_resume_record")
        # KEY-TYPE GUARD before any set/hash comparison: a hostile key
        # whose __hash__ collides with a field name and whose __eq__
        # raises must fail closed typed, never escape raw
        if not all(type(key) is str for key in dict.keys(request)):
            _fail("malformed_resume_record")
        if set(request.keys()) != set(_REQUEST_FIELDS):
            _fail("malformed_resume_record")
        checkpoint = request["checkpoint_sequence"]
        if type(checkpoint) is not int:
            _fail("malformed_resume_record")
        if checkpoint < 0:
            _fail("unknown_checkpoint")
        if checkpoint > len(log):
            # acknowledged entries are MISSING: the crash truncated
            # durable data - never a resumable torn tail
            _fail("corrupt_source")
        k = _valid_prefix_length(self._wal, log)
        if k < checkpoint:
            # the crash damaged an acknowledged (durable) entry
            _fail("corrupt_source")
        tail = log[k:]
        if not all(_is_canonical_json(entry) for entry in tail):
            _fail("malformed_resume_record")
        replayed = self._wal.replay(log[:k])
        # FREEZE (detached canonical copies) BEFORE the snapshot and
        # the single sink call; any canonicalization failure is typed.
        try:
            frozen_tail = [json.loads(_canon(entry)) for entry in tail]
        except (ValueError, TypeError, RecursionError):
            _fail("malformed_resume_record")
        acc = []
        _snapshot(log, acc, set())
        _snapshot(request, acc, set())
        try:
            token = self._quarantine(frozen_tail)
            # TAIL-BOUND QUARANTINE: byte-exact to the local derivation
            if token != quarantine_tail(frozen_tail):
                _fail("divergent_quarantine")
        finally:
            _restore(acc)
        discarded = len(frozen_tail)
        # COMMIT LAST: remove exactly the torn tail
        del log[k:]
        return {
            "resume_id": self._derive_resume_id(
                replayed["head"], replayed["state_id"], k, discarded, token
            ),
            "head": replayed["head"],
            "state_id": replayed["state_id"],
            "resumed_count": k,
            "discarded_count": discarded,
            "quarantine_token": token,
        }
