"""Shared independent-verification drivers for privacy instrument wrappers.

Test-only. The expected semantics (failure classes, accept/reject/crash
mapping) are written out in each test file; nothing here decides them.
Mutation accounting separates oracle kills (AssertionError from a probe
assertion) from crash-only kills (foreign exception, never a semantic kill)
and survivors.
"""

from __future__ import annotations

import contextlib
import json

from tests.privacy_probe_support import load_mutant, outcome


def run(target, engine, error, script):
    """Run steps against target; ("!", fn) applies fn(engine) out of band."""
    got = []
    for op, args in script:
        if op == "!":
            args(engine)
            continue
        got.append(outcome(getattr(target, op), error, *args))
    return got


def labels(outs):
    return ["ok" if o[0] == "ok" else o[1] for o in outs]


def verify_wrapper(module, cls_name, error, make_engine, script, expected_labels, secrets):
    wrapped, bare = make_engine(), make_engine()
    tracer = getattr(module, cls_name)(wrapped)
    outs = run(tracer, wrapped, error, script)
    bare_outs = run(bare, bare, error, script)
    assert outs == bare_outs
    assert labels(outs) == expected_labels
    assert getattr(wrapped, "_state", None) == getattr(bare, "_state", None)
    ops = [op for op, _ in script if op != "!"]
    assert [r["seq"] for r in tracer.records] == list(range(len(ops)))
    assert [r["operation"] for r in tracer.records] == ops
    assert [r["outcome"] for r in tracer.records] == [
        "accept" if label == "ok" else "reject" for label in labels(outs)
    ]
    blob = json.dumps(tracer.records)
    for secret in secrets:
        assert secret not in blob
    return tracer


def classify(module, old, new, probe):
    mutant = load_mutant(module, old, new)
    try:
        probe(mutant)
    except AssertionError:
        return "oracle_kill"
    except BaseException:  # noqa: BLE001 - crash-only, never a semantic kill
        return "crash_only"
    return "survivor"


def generic_probe(cls_name, error, fc, key_exc=KeyError):
    """Probe over a stub engine: crash vs reject, seq, op names, junk reset."""

    class Stub:
        def __init__(self):
            self.mode = "ok"

        def _go(self):
            if self.mode == "reject":
                raise error(fc)
            if self.mode == "crash":
                raise key_exc("k")
            return {"r": 1}

        register = record = verdict = transition = rollback = lambda self, *a: self._go()

    def probe(module):
        stub = Stub()
        tracer = getattr(module, cls_name)(stub)
        tracer.register("a")
        stub.mode = "reject"
        with contextlib.suppress(error):
            tracer.verdict("a")
        stub.mode = "crash"
        with contextlib.suppress(key_exc):
            tracer.record("a")
        assert [(r["seq"], r["operation"], r["outcome"]) for r in tracer.records] == [
            (0, "register", "accept"),
            (1, "verdict", "reject"),
            (2, "record", "crash"),
        ]
        tracer._trace = "junk"
        assert tracer.records == ()
        stub.mode = "ok"
        tracer.transition("a")
        assert [(r["seq"], r["operation"], r["outcome"]) for r in tracer.records] == [
            (0, "transition", "accept")
        ]

    return probe


def ops_probe(cls_name, error, fc, ops, key_exc=KeyError):
    """Like generic_probe but for any public operation names (cycled)."""

    class Stub:
        mode = "ok"

        def __getattr__(self, name):
            def go(*_a):
                if self.mode == "reject":
                    raise error(fc)
                if self.mode == "crash":
                    raise key_exc("k")
                return {"r": 1}

            return go

    def pick(i):
        return ops[i % len(ops)]

    def probe(module):
        stub = Stub()
        tracer = getattr(module, cls_name)(stub)
        getattr(tracer, pick(0))("a")
        stub.mode = "reject"
        with contextlib.suppress(error):
            getattr(tracer, pick(1))("a")
        stub.mode = "crash"
        with contextlib.suppress(key_exc):
            getattr(tracer, pick(2))("a")
        assert [(r["seq"], r["operation"], r["outcome"]) for r in tracer.records] == [
            (0, pick(0), "accept"),
            (1, pick(1), "reject"),
            (2, pick(2), "crash"),
        ]
        tracer._trace = "junk"
        assert tracer.records == ()
        stub.mode = "ok"
        getattr(tracer, pick(3))("a")
        assert [(r["seq"], r["operation"], r["outcome"]) for r in tracer.records] == [
            (0, pick(3), "accept")
        ]

    return probe


def identity_probe(cls_name, op):
    """The wrapper returns the engine's exact result object (not a copy)."""

    class Stub:
        def __init__(self):
            self.receipt = {"r": [1]}

        def __getattr__(self, name):
            return lambda *_a: self.receipt

    def probe(module):
        stub = Stub()
        got = getattr(getattr(module, cls_name)(stub), op)("a")
        assert got is stub.receipt

    return probe
