"""Behavior-neutral structured tracing for graph.opening_context.ContextTable."""

from __future__ import annotations

import json
from contextlib import suppress

from graph.diff_instrument import _attribute, _opaque, _snapshot, _type_name
from graph.opening_context import ContextError, ContextTable


def _summary(operation, result):
    """A small, non-dispatching result summary per operation."""
    try:
        if operation == "merge":
            if type(result) is not ContextTable:
                return _opaque(_type_name(type(result)))
            records = object.__getattribute__(result, "_records")
            return {
                "records": dict.__len__(records) if type(records) is dict else _opaque("records")
            }
        if type(result) is not dict:
            return _opaque(_type_name(type(result)))
        moves = dict.get(result, "path_moves")
        return {
            "variant": _snapshot(dict.get(result, "variant")),
            "opening_code": _snapshot(dict.get(result, "opening_code")),
            "moves": list.__len__(moves) if type(moves) is list else _opaque("moves"),
        }
    except BaseException as error:
        return _opaque("summary-" + _type_name(type(error)))


class ContextTracer:
    """Append-only trace wrapper around a ContextTable that cannot change wrapped
    behavior: results and exceptions are passed through as the same objects
    and arguments are snapshotted without dispatching."""

    def __init__(self, table):
        self._table = table
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
            result = getattr(self._table, operation)(*args)
        except ContextError as error:
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

    def insert(self, variant, path):
        return self._call("insert", (variant, path))

    def merge(self, other):
        return self._call("merge", (other,))

    def to_jsonl(self):
        lines = []
        for record in self.records:
            try:
                lines.append(json.dumps(record, sort_keys=True))
            except BaseException:
                lines.append(json.dumps(_opaque("jsonl-record-failed"), sort_keys=True))
        return "\n".join(lines)
