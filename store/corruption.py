"""Production store corruption scan for data/contracts/corruption.yaml.

Scan a WAL-logged store for corruption and salvage it to the longest
verified prefix. Every prefix is judged by the linked production WAL
(store.wal, public surface only). The log must lie inside the closed
scan domain: an exact built-in, acyclic, alias-free tree of
dict/list/str/int/bool/None, nesting at most MAX_DEPTH, ints of at most
MAX_INT_BITS bits, UTF-8 encodable strs, exact-str dict keys. Otherwise
the scan is malformed. The quarantine sink is UNTRUSTED input behind a
BaseException boundary: one call per salvage (none when clean) on a
detached copy of the frozen suffix, exact built-in str output in the
pinned grammar, bound byte-for-byte to the local canonical suffix
encoding. The commit removes exactly the corrupt suffix, last. A
rejected scan leaves log and request bit- and reference-identical.
"""

from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

import yaml

from store import wal as _wal
from tools.corruption_contract_lint import (
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_BITS,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "corruption.yaml"

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_TOKEN_RE = re.compile(_CC["identifiers"]["quarantine_token"]["grammar"])

__all__ = ["FAILURE_MAPPING", "CorruptionEngine", "CorruptionError",
           "canonical_encoding", "derive_scan_id", "quarantine_suffix"]


class CorruptionError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(failure_class):
    raise CorruptionError(failure_class, FAILURE_MAPPING[failure_class])


class _OutOfDomain(Exception):
    pass


def _encode(value, depth, seen, out):
    """Type-tagged, length-framed canonical encoding of ONE value of the
    closed scan domain; returns a DETACHED copy. Only exact built-in
    types are inspected, so no caller-defined code runs here."""
    if depth > MAX_DEPTH:
        raise _OutOfDomain("depth")
    kind = type(value)
    if value is None:
        out.append(b"n")
        return None
    if kind is bool:
        out.append(b"t" if value else b"f")
        return value
    if kind is int:
        # range checked BEFORE str(): an unbounded int would hit the
        # interpreter's int->str limit and escape as a raw ValueError
        if value.bit_length() > MAX_INT_BITS:
            raise _OutOfDomain("int-range")
        text = str(value).encode()
        out.append(b"i%d:" % len(text) + text)
        return value
    if kind is str:
        try:
            raw = value.encode("utf-8")
        except UnicodeEncodeError:
            raise _OutOfDomain("surrogate") from None
        out.append(b"s%d:" % len(raw) + raw)
        return value
    if kind is not list and kind is not dict:
        raise _OutOfDomain(kind.__name__)
    # one seen set closes both cycles and aliases
    if id(value) in seen:
        raise _OutOfDomain("cycle-or-alias")
    seen.add(id(value))
    if kind is list:
        items = list.__iter__(value)
        values = list(items)
        out.append(b"l%d:" % len(values))
        return [_encode(item, depth + 1, seen, out) for item in values]
    pairs = list(dict.items(value))
    for key, _ in pairs:
        if type(key) is not str:
            raise _OutOfDomain("key")
    pairs.sort(key=lambda kv: kv[0])
    out.append(b"d%d:" % len(pairs))
    copied = {}
    for key, item in pairs:
        _encode(key, depth + 1, seen, out)
        copied[key] = _encode(item, depth + 1, seen, out)
    return copied


def canonical_encoding(value):
    """(detached copy, canonical bytes); raises _OutOfDomain."""
    out = []
    copied = _encode(value, 1, set(), out)
    return copied, b"".join(out)


def quarantine_suffix(suffix):
    """The pinned canonical quarantine token (and the honest sink):
    sha256 over the domain-separated canonical encoding of the entire
    frozen corrupt suffix."""
    _, raw = canonical_encoding(list(suffix))
    return "qrn1:" + hashlib.sha256(b"qrn1|" + raw).hexdigest()


def derive_scan_id(verdict, head, count, lost, token):
    return "crp1:" + hashlib.sha256(
        f"{verdict}\n{head}\n{count}\n{lost}\n"
        f"{token if token is not None else '-'}".encode()).hexdigest()


def _snapshot(value, saved):
    """Reference-preserving snapshot of every container of a validated
    (alias-free) tree."""
    if type(value) is list:
        saved.append((value, list(value)))
        for item in value:
            _snapshot(item, saved)
    elif type(value) is dict:
        saved.append((value, dict(value)))
        for item in value.values():
            _snapshot(item, saved)
    return saved


def _restore(saved):
    for obj, contents in saved:
        if type(obj) is list:
            obj[:] = contents
        else:
            obj.clear()
            obj.update(contents)


class CorruptionEngine:
    """Closed scan domain, prefix-wise linked WAL detection, loss bound,
    frozen log and request, exactly one untrusted sink call on a
    detached suffix copy (none when clean), suffix removal last."""

    def __init__(self, quarantine_sink):
        self.sink = quarantine_sink  # UNTRUSTED
        self._wal = _wal.WalEngine(_wal.canonical_payload)  # trusted

    def _prefix_head(self, frozen, count):
        """Head of frozen[:count] under the linked WAL, or None."""
        if count == 0:
            return _wal.GENESIS
        try:
            return self._wal.replay(copy.deepcopy(frozen[:count]))["head"]
        except _wal.WalError:
            return None

    def _verified_prefix(self, frozen):
        """Longest prefix passing full linked WAL validation. WAL
        validation is prefix-monotone (entry k is judged against entries
        before it only), so a binary search finds it. (count, head)."""
        head = self._prefix_head(frozen, len(frozen))
        if head is not None:
            return len(frozen), head
        good, bad = 0, len(frozen)
        while bad - good > 1:
            mid = (good + bad) // 2
            if self._prefix_head(frozen, mid) is None:
                bad = mid
            else:
                good = mid
        return good, self._prefix_head(frozen, good)

    def _quarantine(self, frozen_suffix):
        """The sink boundary: any BaseException, non-exact-str,
        wrong-grammar or UTF-8-inencodable output fails closed."""
        try:
            out = self.sink(copy.deepcopy(frozen_suffix))
        except BaseException:
            _fail("divergent_quarantine")
        if type(out) is not str or _TOKEN_RE.fullmatch(out) is None:
            _fail("divergent_quarantine")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_quarantine")
        return out

    @staticmethod
    def _receipt(verdict, head, count, lost, token):
        return {"scan_id": derive_scan_id(verdict, head, count, lost,
                                          token),
                "verdict": verdict,
                "verified_head": head,
                "verified_count": count,
                "quarantined_count": lost,
                "quarantine_token": token}

    def scan(self, log, request):
        # exact dict with exactly one exact-str key, key types checked
        # before any hashing comparison
        if type(request) is not dict or len(request) != 1 or \
                any(type(key) is not str for key in dict.keys(request)) \
                or "max_loss" not in request:
            _fail("malformed_corruption_record")
        max_loss = request["max_loss"]
        if type(max_loss) is not int or max_loss < 0:
            _fail("malformed_corruption_record")
        if type(log) is not list:
            _fail("malformed_corruption_record")
        try:
            frozen, _ = canonical_encoding(log)
        except _OutOfDomain:
            _fail("malformed_corruption_record")
        count, head = self._verified_prefix(frozen)
        lost = len(frozen) - count
        if lost == 0:
            return self._receipt("clean", head, count, 0, None)
        if lost > max_loss:
            _fail("excessive_loss")
        saved = _snapshot(log, [])
        saved_request = dict(request)
        try:
            frozen_suffix = frozen[count:]
            token = self._quarantine(frozen_suffix)
            # the untrusted token must equal the local canonical suffix
            # encoding byte-for-byte, or nothing commits
            if token != quarantine_suffix(frozen_suffix):
                _fail("divergent_quarantine")
        finally:
            _restore(saved)
            request.clear()
            request.update(saved_request)
        del log[count:]
        return self._receipt("salvaged", head, count, lost, token)
