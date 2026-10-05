"""Behavior-neutral structured tracing for the variant runtime (tools.variant_runtime)."""

from __future__ import annotations

import json
from contextlib import suppress

from graph.diff_instrument import _attribute, _opaque, _snapshot, _type_name
from tools import variant_runtime
from tools.variant_runtime import Position, VariantError

_POSITION_FIELDS = ("variant", "board", "side_to_move", "castling_rights", "en_passant")


def _summary(operation, result):
    """A small, non-dispatching result summary per operation."""
    try:
        if type(result) is Position:
            return {
                field: _snapshot(object.__getattribute__(result, field))
                for field in _POSITION_FIELDS
            }
        if type(result) is dict:
            return _snapshot(result)
        return _opaque(_type_name(type(result)))
    except BaseException as error:
        return _opaque("summary-" + _type_name(type(error)))


class VariantTracer:
    """Append-only trace wrapper around the variant runtime that cannot
    change wrapped behavior: results and exceptions are passed through as
    the same objects and arguments are snapshotted without dispatching."""

    def __init__(self, runtime=variant_runtime):
        self._runtime = runtime
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
            result = getattr(self._runtime, operation)(*args)
        except VariantError as error:
            self._append_total(
                base
                | {
                    "outcome": "reject",
                    "failure_class": _attribute(error, "failure_class"),
                    "code": _attribute(error, "code"),
                    "retryable": _attribute(error, "retryable"),
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

    def parse_position(self, variant, fen):
        return self._call("parse_position", (variant, fen))

    def identity(self, position):
        return self._call("identity", (position,))

    def project_additive(self, record):
        return self._call("project_additive", (record,))

    def to_jsonl(self):
        lines = []
        for record in self.records:
            try:
                lines.append(json.dumps(record, sort_keys=True))
            except BaseException:
                lines.append(json.dumps(_opaque("jsonl-record-failed"), sort_keys=True))
        return "\n".join(lines)
