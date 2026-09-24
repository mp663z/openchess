"""Behavior-neutral structured tracing for graph diff operations.

DiffTracer wraps the four public graph.diff operations (state_id,
compute, validate_diff, apply). Every call returns exactly what the
wrapped operation returns (the same object) or re-raises exactly the
error it raised (the same object), and appends one trace record:
operation, a non-dispatching snapshot of the arguments, the outcome
(accept, reject with failure class and code, or crash with the error
type), a result summary, and whether every argument was left unchanged.

Diagnostics are total and non-interfering: snapshots read only exact
JSON-like built-ins through their base-type methods (any subclass, or a
dict with a non-str key, is recorded as an opaque marker and never
dispatched through; types are compared by identity and their names are
read through the type slot, so a metaclass __eq__ or __name__ never
runs), error attributes are read defensively, and a
tampered or faulting trace container is recovered without changing the
wrapped result. A mutation inside an opaque (subclass) argument is not
visible to the snapshot, so it records arguments_unchanged as True.
"""

from __future__ import annotations

import json
from contextlib import suppress

from graph import diff as _diff

_OPAQUE = "__opaque__"
_MAX_DEPTH = 64


def _opaque(kind):
    return {_OPAQUE: kind}


_TYPE_NAME = type.__dict__["__name__"]


def _type_name(t):
    """The type's own name through the type slot: never a metaclass
    __name__ property; total."""
    try:
        name = _TYPE_NAME.__get__(t)
        return name if type(name) is str else "unnamed"
    except BaseException:
        return "unnamed"


def _snapshot(value, depth=0):
    """Total, non-dispatching snapshot over exact JSON-like built-ins."""
    try:
        if depth > _MAX_DEPTH:
            return _opaque("max-depth-exceeded")
        t = type(value)
        if t is int:
            try:
                int.__repr__(value)  # beyond the int-string limit: not serializable
            except ValueError:
                return _opaque("int-too-large")
            return value
        if t is type(None) or t is bool or t is float or t is str:
            return value
        if t is list or t is tuple:
            items = list.__iter__(value) if t is list else tuple.__iter__(value)
            return [_snapshot(v, depth + 1) for v in items]
        if t is dict:
            out = {}
            for key, item in dict.items(value):
                if type(key) is not str:
                    return _opaque("dict-non-str-key")
                out[key] = _snapshot(item, depth + 1)
            return out
        return _opaque(_type_name(t))
    except BaseException as error:
        return _opaque("snapshot-" + _type_name(type(error)))


def _attribute(obj, name):
    try:
        return _snapshot(getattr(obj, name))
    except BaseException as error:
        return _opaque("attribute-" + _type_name(type(error)))


def _summary(operation, result):
    """A small, non-dispatching result summary per operation."""
    try:
        if operation == "state_id" or operation == "validate_diff":
            return _snapshot(result)
        if type(result) is not dict:
            return _opaque(_type_name(type(result)))
        if operation == "apply":
            return {"identities": dict.__len__(result)}
        out = {}
        for field in ("base_id", "target_id"):
            out[field] = _snapshot(dict.get(result, field))
        for field in ("added", "removed", "changed"):
            section = dict.get(result, field)
            out[field] = dict.__len__(section) if type(section) is dict else _opaque("section")
        return out
    except BaseException as error:
        return _opaque("summary-" + _type_name(type(error)))


class DiffTracer:
    """Append-only trace wrapper that cannot change wrapped behavior."""

    def __init__(self, state_id_fn=None, compute_fn=None, validate_fn=None, apply_fn=None):
        self._fns = {
            "state_id": _diff.state_id if state_id_fn is None else state_id_fn,
            "compute": _diff.compute if compute_fn is None else compute_fn,
            "validate_diff": _diff.validate_diff if validate_fn is None else validate_fn,
            "apply": _diff.apply if apply_fn is None else apply_fn,
        }
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
        fn = self._fns[operation]
        try:
            result = fn(*args)
        except _diff.DiffError as error:
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
                "arguments_unchanged": [_snapshot(a) for a in args] == before,
            }
        )
        return result

    def state_id(self, state):
        return self._call("state_id", (state,))

    def compute(self, base, target):
        return self._call("compute", (base, target))

    def validate_diff(self, diff):
        return self._call("validate_diff", (diff,))

    def apply(self, diff, base):
        return self._call("apply", (diff, base))

    def to_jsonl(self):
        try:
            records = self.records
        except BaseException:
            records = ()
        lines = []
        for record in records:
            try:
                lines.append(json.dumps(record, sort_keys=True))
            except BaseException:
                lines.append(json.dumps(_opaque("jsonl-record-failed"), sort_keys=True))
        return "\n".join(lines)
