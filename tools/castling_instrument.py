"""T0066: castling instrumentation - a diagnostic trace layer over
the T0062 castling runtime (O2 trusted diagnosis).

CastlingTracer wraps an apply function (default castling_runtime.apply)
and records ONE structured trace record per call:

- accept: {"seq", "outcome": "accept", "move", "state_before",
  "state_after"}
- reject: {"seq", "outcome": "reject", "move", "state_before",
  "failure_class", "code", "retryable", "message"} -
  the exact CastlingError fields, class-to-code mapping included.
- crash: {"seq", "outcome": "crash", "move", "state_before",
  "error_type"} - any non-CastlingError BaseException
  (ordinary errors AND control-flow exits like KeyboardInterrupt /
  SystemExit); the exact exception object propagates.

NEUTRALITY CONTRACT (strict): the tracer NEVER changes runtime
behavior. The wrapped function is always called exactly once with the
ORIGINAL argument objects (no pre-call copying at all); accept
returns the EXACT object it returned; reject/crash re-raise the EXACT
exception object. The tracer never mutates caller state.

Snapshots are taken with a NON-DISPATCHING structural copier over the
turn runtime's closed JSON-like domain (None/bool/int/float/str, and
lists/tuples/dicts-with-str-keys thereof, exact types only). It never
invokes user copy hooks (__deepcopy__/__copy__/__reduce__), so a hook
that raises OR a hook that succeeds while mutating caller state can
neither change the arguments the runtime observes nor touch caller
state. Values outside the closed domain (custom objects, dict
subclasses, non-str keys, exotic scalars) are recorded as
{"__opaque__": <type name>} WITHOUT calling any user method. If
record FIELD extraction itself raises, the record degrades to
{"outcome": "record_degraded", ...}; exactly one record is still
appended and the original result/exception is preserved. History
reads (records, to_jsonl) snapshot through the same copier, so
reading the trace is as side-effect-free as writing it.

Records are append-only: the records view is an immutable structural
copy, seq is strictly increasing from 0, and every call writes
exactly one record. to_jsonl() is deterministic for a fixed session
so a trace can be byte-pinned.
"""

from __future__ import annotations

import json

from tools import castling_runtime as rt

_LEAF_TYPES = (type(None), bool, int, float, str)
_MAX_DEPTH = 64


def _safe_snapshot(value, _depth: int = 0):
    """Non-dispatching structural snapshot over the closed JSON-like
    domain. Executes NO user-defined code: no copy hooks, no repr, no
    coercion. Out-of-domain values degrade to a type marker."""
    if _depth > _MAX_DEPTH:
        return {"__opaque__": "max-depth-exceeded"}
    t = type(value)
    if t in _LEAF_TYPES:
        return value  # immutable scalars are safe to share by reference
    if t is list or t is tuple:
        return [_safe_snapshot(v, _depth + 1) for v in value]
    if t is dict:
        out = {}
        for k, v in value.items():
            if type(k) is not str:
                return {"__opaque__": "dict-non-str-key"}
            out[k] = _safe_snapshot(v, _depth + 1)
        return out
    return {"__opaque__": t.__name__}


class CastlingTracer:
    """Append-only diagnostic tracer over a turn apply function."""

    def __init__(self, apply_fn=rt.apply):
        self._apply = apply_fn
        self._records: list[dict] = []

    @property
    def records(self) -> tuple:
        return tuple(_safe_snapshot(self._records))

    def apply(self, state, move):
        base = {
            "seq": len(self._records),
            "move": _safe_snapshot(move),
            "state_before": _safe_snapshot(state),
        }
        try:
            out = self._apply(state, move)
        except rt.CastlingError as err:
            self._append(base, lambda err=err: {
                "outcome": "reject",
                "failure_class": _safe_snapshot(err.failure_class),
                "code": _safe_snapshot(err.code),
                "retryable": _safe_snapshot(err.retryable),
                "message": _safe_snapshot(err.args[0]),
            })
            raise
        except BaseException as exc:  # incl. control-flow exits
            self._append(base, lambda exc=exc: {
                "outcome": "crash",
                "error_type": type(exc).__name__,
            })
            raise
        self._append(base, lambda: {
            "outcome": "accept",
            "state_after": _safe_snapshot(out),
        })
        return out

    def _append(self, base: dict, fields_fn) -> None:
        """Append exactly one record; field extraction can never
        replace the wrapped result/exception."""
        try:
            fields = fields_fn()
        except BaseException as exc:
            fields = {"outcome": "record_degraded",
                      "record_error": type(exc).__name__}
        self._records.append(base | fields)

    def to_jsonl(self) -> str:
        return "\n".join(
            json.dumps(r, sort_keys=True) for r in self._records)
