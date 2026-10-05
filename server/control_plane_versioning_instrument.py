"""Behavior-neutral structured tracing for the control-plane version comparator
(server.control_plane_versioning.compare).

Records operation metadata only: minors, base paths, the verdict or typed
failure. Snapshot content is never copied into a record.
"""

from __future__ import annotations

import json
from contextlib import suppress

from graph.diff_instrument import _attribute, _opaque, _snapshot, _type_name
from server.control_plane_versioning import VersionError, compare


def _base_path(doc):
    """The declared base path of a snapshot, read without dispatching."""
    try:
        if type(doc) is not dict:
            return _opaque(_type_name(type(doc)))
        contract = dict.get(doc, "contract")
        versioning = dict.get(contract, "versioning") if type(contract) is dict else None
        path = dict.get(versioning, "base_path") if type(versioning) is dict else None
        return path if type(path) is str else _opaque("base_path")
    except BaseException as error:
        return _opaque("base-path-" + _type_name(type(error)))


class VersionTracer:
    """Append-only trace wrapper around a compare callable that cannot change
    wrapped behavior: results and exceptions are passed through as the same
    objects, and arguments are snapshotted without dispatching."""

    def __init__(self, comparator=compare):
        self._comparator = comparator
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

    def compare(self, old, new, old_minor, new_minor):
        before = [_snapshot(a) for a in (old, new, old_minor, new_minor)]
        base = {
            "seq": self._seq(),
            "operation": "compare",
            "old_base_path": _base_path(old),
            "new_base_path": _base_path(new),
            "old_minor": before[2],
            "new_minor": before[3],
        }
        try:
            result = self._comparator(old, new, old_minor, new_minor)
        except VersionError as error:
            self._append_total(
                base
                | {
                    "outcome": "reject",
                    "failure_class": _attribute(error, "failure_class"),
                    "code": _attribute(error, "code"),
                    "arguments_unchanged": [_snapshot(a) for a in (old, new, old_minor, new_minor)]
                    == before,
                }
            )
            raise
        except BaseException as error:
            self._append_total(
                base
                | {
                    "outcome": "crash",
                    "error_type": _type_name(type(error)),
                    "arguments_unchanged": [_snapshot(a) for a in (old, new, old_minor, new_minor)]
                    == before,
                }
            )
            raise
        self._append_total(
            base | {"outcome": "accept", "compatible": result if type(result) is bool else None}
        )
        return result

    def to_jsonl(self):
        lines = []
        for record in self.records:
            try:
                lines.append(json.dumps(record, sort_keys=True))
            except BaseException:
                lines.append(json.dumps(_opaque("jsonl-record-failed"), sort_keys=True))
        return "\n".join(lines)
