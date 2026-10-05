"""Behavior-neutral structured tracing for the production WAL (store.wal)."""

from __future__ import annotations

import json
from contextlib import suppress

from graph.diff_instrument import _attribute, _opaque, _snapshot, _type_name
from store.wal import WalError


def _summary(operation, result):
    """A small, non-dispatching result summary per operation."""
    try:
        if type(result) is not dict:
            return _opaque(_type_name(type(result)))
        if operation == "append":
            return {
                field: _snapshot(dict.get(result, field))
                for field in ("entry_id", "sequence", "op")
            }
        state = dict.get(result, "state")
        return {
            "head": _snapshot(dict.get(result, "head")),
            "applied": _snapshot(dict.get(result, "applied")),
            "state_id": _snapshot(dict.get(result, "state_id")),
            "identities": dict.__len__(state) if type(state) is dict else _opaque("state"),
        }
    except BaseException as error:
        return _opaque("summary-" + _type_name(type(error)))


class WalTracer:
    """Append-only trace wrapper around a WalEngine that cannot change
    wrapped behavior: results and exceptions are passed through as the
    same objects, and arguments are snapshotted without dispatching."""

    def __init__(self, engine):
        self._engine = engine
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

    def _seq(self):
        try:
            trace = object.__getattribute__(self, "_trace")
            return list.__len__(trace) if type(trace) is list else 0
        except BaseException:
            return 0

    def _call(self, operation, args):
        before = [_snapshot(a) for a in args]
        base = {"seq": self._seq(), "operation": operation, "arguments": before}
        try:
            result = getattr(self._engine, operation)(*args)
        except WalError as error:
            self._append_total(
                base
                | {
                    "outcome": "reject",
                    "failure_class": _attribute(error, "failure_class"),
                    "code": _attribute(error, "code"),
                    "arguments_unchanged": [_snapshot(a) for a in args] == before,
                }
            )
            raise
        except BaseException as error:
            self._append_total(
                base
                | {
                    "outcome": "crash",
                    "error_type": _type_name(type(error)),
                    "arguments_unchanged": [_snapshot(a) for a in args] == before,
                }
            )
            raise
        self._append_total(
            base
            | {
                "outcome": "accept",
                "result": _summary(operation, result),
            }
        )
        return result

    def append(self, log, request):
        return self._call("append", (log, request))

    def replay(self, log):
        return self._call("replay", (log,))

    def to_jsonl(self):
        lines = []
        for record in self.records:
            try:
                lines.append(json.dumps(record, sort_keys=True))
            except BaseException:
                lines.append(json.dumps(_opaque("jsonl-record-failed"), sort_keys=True))
        return "\n".join(lines)
