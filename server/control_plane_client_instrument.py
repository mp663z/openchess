"""Behavior-neutral structured tracing for the replaceable control-plane
consumer client (server.control_plane_client.ControlPlaneClient).

Records operation metadata only: the operation name, its declared contract
shape (auth, mutating), the outcome and the typed error code. Request and
response content, tokens and identifiers are never copied into a record.
"""

from __future__ import annotations

import json
from contextlib import suppress

from graph.diff_instrument import _attribute, _opaque, _snapshot, _type_name
from server.control_plane_client import OPS, ControlPlaneError


def _shape(name):
    """The contract-declared auth and mutating flags for an operation name."""
    try:
        op = OPS.get(name) if type(name) is str else None
        if op is None:
            return {"declared": False}
        return {"declared": True, "auth": op["auth"], "mutating": op["mutating"]}
    except BaseException as error:
        return _opaque("shape-" + _type_name(type(error)))


def _summary(result):
    """A non-dispatching result summary: the field count only."""
    try:
        if type(result) is not dict:
            return _opaque(_type_name(type(result)))
        return {"fields": dict.__len__(result)}
    except BaseException as error:
        return _opaque("summary-" + _type_name(type(error)))


class ClientTracer:
    """Append-only trace wrapper around a ControlPlaneClient that cannot
    change wrapped behavior: results and exceptions are passed through as
    the same objects, and the request body is snapshotted without
    dispatching."""

    def __init__(self, client):
        self._client = client
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

    def _run(self, operation, shape_name, fn, body):
        before = _snapshot(body)
        base = {"seq": self._seq(), "operation": operation, "shape": _shape(shape_name)}
        try:
            result = fn()
        except ControlPlaneError as error:
            self._append_total(
                base
                | {
                    "outcome": "reject",
                    "code": _attribute(error, "code"),
                    "retryable": _attribute(error, "retryable"),
                    "request_unchanged": _snapshot(body) == before,
                }
            )
            raise
        except BaseException as error:
            self._append_total(
                base
                | {
                    "outcome": "crash",
                    "error_type": _type_name(type(error)),
                    "request_unchanged": _snapshot(body) == before,
                }
            )
            raise
        self._append_total(base | {"outcome": "accept", "result": _summary(result)})
        return result

    def call(self, name, body=None):
        return self._run(name, name, lambda: self._client.call(name, body), body)

    def entitlements(self):
        return self._run("entitlements", "entitlements.get", self._client.entitlements, None)

    def to_jsonl(self):
        lines = []
        for record in self.records:
            try:
                lines.append(json.dumps(record, sort_keys=True))
            except BaseException:
                lines.append(json.dumps(_opaque("jsonl-record-failed"), sort_keys=True))
        return "\n".join(lines)
