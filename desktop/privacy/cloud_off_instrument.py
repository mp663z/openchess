"""Content-free, behavior-neutral local cloud_off instrumentation.

Trace fields are only sequence, fixed operation name and accept/reject/crash.
Arguments, results, error messages, identifiers and credentials are never read
for tracing. The wrapper returns the exact result or reraises the exact error.
No external telemetry sink exists. Trace failure cannot change policy behavior.
"""

from __future__ import annotations

from desktop.privacy.cloud_off import CloudError


class CloudTracer:
    def __init__(self, engine):
        self._engine = engine
        self._trace = []

    @property
    def records(self):
        trace = object.__getattribute__(self, "_trace")
        return tuple(dict(row) for row in trace) if type(trace) is list else ()

    def _append(self, operation, outcome):
        try:
            trace = object.__getattribute__(self, "_trace")
            if type(trace) is not list:
                trace = []
                object.__setattr__(self, "_trace", trace)
            list.append(
                trace, {"seq": list.__len__(trace), "operation": operation, "outcome": outcome}
            )
        except BaseException:
            pass  # diagnostics must never replace a policy result or exception

    def _call(self, operation, args):
        try:
            result = getattr(self._engine, operation)(*args)
        except CloudError:
            self._append(operation, "reject")
            raise
        except BaseException:
            self._append(operation, "crash")
            raise
        self._append(operation, "accept")
        return result

    def register(self, *args):
        return self._call("register", args)

    def record(self, *args):
        return self._call("record", args)

    def verdict(self, *args):
        return self._call("verdict", args)

    def transition(self, *args):
        return self._call("transition", args)
