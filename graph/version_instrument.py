"""Behavior-neutral structured tracing for graph version store operations."""

from __future__ import annotations

import json
from contextlib import suppress

from graph.version_store import VersionError, VersionStore

_OPAQUE = "__opaque__"


def _opaque(kind):
    return {_OPAQUE: kind}


def _snapshot(value, depth=0):
    """Total, non-dispatching snapshot over exact JSON-like built-ins."""
    try:
        if depth > 64:
            return _opaque("max-depth-exceeded")
        t = type(value)
        if t in (type(None), bool, int, float, str):
            return value
        if t in (list, tuple):
            return [_snapshot(v, depth + 1) for v in value]
        if t is dict:
            out = {}
            for key, item in dict.items(value):
                if type(key) is not str:
                    return _opaque("dict-non-str-key")
                out[key] = _snapshot(item, depth + 1)
            return out
        return _opaque(t.__name__)
    except BaseException as error:
        return _opaque("snapshot-" + type(error).__name__)


def _attribute(obj, name):
    try:
        return _snapshot(getattr(obj, name))
    except BaseException as error:
        return _opaque("attribute-" + type(error).__name__)


def _store_state(store):
    """Total snapshot that never dispatches through hostile store internals."""
    try:
        root = object.__getattribute__(store, "_root_id")
        root = _snapshot(root)
    except BaseException as error:
        root = _opaque("root-" + type(error).__name__)
    try:
        records = object.__getattribute__(store, "_records")
        count = (
            dict.__len__(records)
            if type(records) is dict
            else _opaque("records-" + type(records).__name__)
        )
    except BaseException as error:
        count = _opaque("records-" + type(error).__name__)
    return {"root": root, "versions": count}


def _delta(before, after):
    a, b = before["versions"], after["versions"]
    return b - a if type(a) is int and type(b) is int else _opaque("count-unavailable")


class VersionTracer:
    """Append-only trace wrapper that cannot change wrapped behavior."""

    def __init__(self, store=None, insert_fn=None, merge_fn=None):
        candidate = VersionStore() if store is None else store
        if type(candidate) is not VersionStore:
            raise TypeError("store must be an exact VersionStore")
        self.store = candidate
        self._insert = self.store.insert if insert_fn is None else insert_fn
        self._merge = self.store.merge if merge_fn is None else merge_fn
        self._trace = []

    @property
    def records(self):
        try:
            trace = object.__getattribute__(self, "_trace")
            return tuple(_snapshot(trace)) if type(trace) is list else ()
        except BaseException:
            return ()

    def _append_total(self, record):
        """Append despite a tampered/faulting trace container; never raise."""
        try:
            trace = object.__getattribute__(self, "_trace")
            if type(trace) is not list:
                trace = []
                object.__setattr__(self, "_trace", trace)
            list.append(trace, _snapshot(record))
        except BaseException:
            with suppress(BaseException):
                object.__setattr__(self, "_trace", [_opaque("trace-append-failed")])

    def _call(self, operation, argument, fn):
        before = _store_state(self.store)
        try:
            seq = list.__len__(self._trace) if type(self._trace) is list else 0
        except BaseException:
            seq = 0
        base = {
            "seq": seq,
            "operation": operation,
            "argument": _snapshot(argument),
            "root_before": before["root"],
            "versions_before": before["versions"],
        }
        try:
            result = fn()
        except VersionError as error:
            after = _store_state(self.store)
            self._append_total(
                base
                | {
                    "outcome": "reject",
                    "failure_class": _attribute(error, "failure_class"),
                    "code": _attribute(error, "code"),
                    "witness": _attribute(error, "witness"),
                    "root_after": after["root"],
                    "versions_after": after["versions"],
                    "version_delta": _delta(before, after),
                    "rollback_preserved": before == after,
                }
            )
            raise
        except BaseException as error:
            after = _store_state(self.store)
            self._append_total(
                base
                | {
                    "outcome": "crash",
                    "error_type": type(error).__name__,
                    "root_after": after["root"],
                    "versions_after": after["versions"],
                    "version_delta": _delta(before, after),
                    "rollback_preserved": before == after,
                }
            )
            raise
        after = _store_state(self.store)
        self._append_total(
            base
            | {
                "outcome": "accept",
                "root_after": after["root"],
                "versions_after": after["versions"],
                "version_delta": _delta(before, after),
                "rollback_preserved": before == after,
                "result": _snapshot(result if operation == "insert" else None),
            }
        )
        return result

    def insert(self, record):
        return self._call("insert", record, lambda: self._insert(record))

    def merge(self, other):
        return self._call("merge", other, lambda: self._merge(other))

    def to_jsonl(self):
        try:
            return "\n".join(json.dumps(r, sort_keys=True) for r in self.records)
        except BaseException:
            return json.dumps(_opaque("jsonl-failed"), sort_keys=True)
