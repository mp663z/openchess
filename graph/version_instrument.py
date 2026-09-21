"""Behavior-neutral structured tracing for graph version store operations."""

from __future__ import annotations

import json

from graph.version_store import VersionError, VersionStore


def _snapshot(value, depth=0):
    if depth > 64:
        return {"__opaque__": "max-depth-exceeded"}
    t = type(value)
    if t in (type(None), bool, int, float, str):
        return value
    if t in (list, tuple):
        return [_snapshot(v, depth + 1) for v in value]
    if t is dict:
        out = {}
        for key, item in value.items():
            if type(key) is not str:
                return {"__opaque__": "dict-non-str-key"}
            out[key] = _snapshot(item, depth + 1)
        return out
    return {"__opaque__": t.__name__}


class VersionTracer:
    """Append-only trace wrapper that preserves exact store behavior."""

    def __init__(self, store=None, insert_fn=None, merge_fn=None):
        candidate = VersionStore() if store is None else store
        if type(candidate) is not VersionStore:
            raise TypeError("store must be an exact VersionStore")
        self.store = candidate
        self._insert = self.store.insert if insert_fn is None else insert_fn
        self._merge = self.store.merge if merge_fn is None else merge_fn
        self._records = []

    @property
    def records(self):
        return tuple(_snapshot(self._records))

    def _call(self, operation, argument, fn):
        base = {
            "seq": len(self._records),
            "operation": operation,
            "argument": _snapshot(argument),
            "root_before": _snapshot(self.store.root_id),
            "versions_before": len(self.store._records),
        }
        try:
            result = fn()
        except VersionError as error:
            self._records.append(
                base
                | {
                    "outcome": "reject",
                    "failure_class": _snapshot(error.failure_class),
                    "code": _snapshot(error.code),
                    "witness": _snapshot(error.witness),
                }
            )
            raise
        except BaseException as error:
            self._records.append(base | {"outcome": "crash", "error_type": type(error).__name__})
            raise
        self._records.append(
            base
            | {
                "outcome": "accept",
                "root_after": _snapshot(self.store.root_id),
                "versions_after": len(self.store._records),
                "result": _snapshot(result if operation == "insert" else None),
            }
        )
        return result

    def insert(self, record):
        return self._call("insert", record, lambda: self._insert(record))

    def merge(self, other):
        return self._call("merge", other, lambda: self._merge(other))

    def to_jsonl(self):
        return "\n".join(json.dumps(r, sort_keys=True) for r in self._records)
