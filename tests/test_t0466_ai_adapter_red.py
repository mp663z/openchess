"""T0466: executable red-test battery for the synthetic AI adapter boundary.

Binds the closed T0465 corpus (and extra hostile probes) to ONE rebinding point,
BINDING. The T0465 reference is the green baseline. Each mutant below is a
one-edit change to the reference source and must turn the battery red, so a
future provider adapter bound here cannot silently skip a gate.
No provider adapter, credential flow, network or payment exists in this file.
"""

from __future__ import annotations

import copy
import inspect

import pytest

from tests import test_t0465_ai_adapter_fixture as fx

# Single rebinding point: (adapter callable, its refusal error type).
BINDING = (fx.reference, fx.Refusal)
ROWS = [(s, r) for s in fx.SECTIONS for r in fx.CASES[s]]
IDS = [r["name"] for _s, r in ROWS]


class _SubDict(dict):
    pass


class _SubStr(str):
    pass


def _hosted():
    row = next(r for r in fx.CASES["happy"] if r["name"] == "hosted-opted-in")
    return fx.materialize(row)


def _expect(adapter, error, request, state, code, effects_expected):
    effects = []
    before = copy.deepcopy((request, state))
    try:
        adapter(request, state, effects)
    except error as exc:
        assert exc.code == code, (exc.code, code)
        assert "SECRET-FIXTURE" not in str(exc)
    else:
        assert code == "succeeded"
    assert effects == effects_expected
    assert (request, state) == before


class Crash(Exception):
    """A foreign exception: never a semantic kill, never an AssertionError."""


def _guarded(adapter, error):
    """An exception outside the closed refusal type is a failed battery."""

    def run(request, state, effects):
        try:
            return adapter(request, state, effects)
        except error:
            raise
        except Exception as exc:  # noqa: BLE001
            raise Crash(f"crash {type(exc).__name__}") from exc

    return run


def battery(adapter, error):
    """Green iff the adapter passes the whole corpus and every probe."""
    adapter = _guarded(adapter, error)
    # cost-value rows first: each is judged by outcome before any hostile type can crash a mutant
    for cap, code in ((float("nan"), "malformed_request"), (-1, "malformed_request"),
                      (float("inf"), "malformed_request")):  # fmt: skip
        request, state = _hosted()
        request["max_cost_usd"] = cap
        _expect(adapter, error, request, state, code, [])
    for bounded in (float("nan"), -1, float("inf")):
        request, state = _hosted()
        state["bounded_cost"] = bounded
        _expect(adapter, error, request, state, "cost_cap_exceeded", [])
    # envelope rows first, so a mutant is judged by outcome before any hostile type can crash it
    request, state = _hosted()
    _expect(adapter, error, _SubDict(request), state, "malformed_request", [])
    request, state = _hosted()
    request = {(_SubStr(k) if k == "mode" else k): v for k, v in request.items()}
    _expect(adapter, error, request, state, "malformed_request", [])
    local = next(r for r in fx.CASES["happy"] if r["name"] == "local-offline")
    request, state = fx.materialize(local)
    del request["payload"]  # required key absent; the local path never reads the payload
    _expect(adapter, error, request, state, "malformed_request", [])
    request, state = fx.materialize(local)
    state["sensitive"] = True  # sensitive gates the hosted path only
    _expect(adapter, error, request, state, "succeeded", ["local"])
    # str-subclass values that equal valid ones are still outside the closed domain
    for mode_row, field, value in (
        ("hosted-opted-in", "mode", _SubStr("hosted_byom")),
        ("local-offline", "mode", _SubStr("local")),
        ("hosted-opted-in", "capability", _SubStr("summarize")),
        ("hosted-opted-in", "provider_ref", _SubStr("byok:test")),
    ):
        base = next(r for r in fx.CASES["happy"] if r["name"] == mode_row)
        request, state = fx.materialize(base)
        if field != "mode":
            value = _SubStr(request[field])
        request[field] = value
        _expect(adapter, error, request, state, "malformed_request", [])
    for _section, row in ROWS:
        request, state = fx.materialize(row)
        before = copy.deepcopy((request, state))
        effects = []
        try:
            result = adapter(request, state, effects)
        except error as exc:
            assert exc.code == row["expect"], row["name"]
            assert "SECRET-FIXTURE" not in str(exc), row["name"]
        else:
            assert row["expect"] == "succeeded", row["name"]
            assert result == {"version": 1, "status": "succeeded", "output": "synthetic text"}
        assert effects == row["effects"], row["name"]
        assert (request, state) == before, row["name"]

    # precedence: payload before privacy before terms before cost
    request, state = _hosted()
    request["payload"]["full-corpus"] = "SECRET-FIXTURE"
    state.update(cloud=False, accepted_terms="old", bounded_cost=None)
    _expect(adapter, error, request, state, "payload_rejected", [])
    del request["payload"]["full-corpus"]
    _expect(adapter, error, request, state, "cloud_off", [])
    state["cloud"] = True
    _expect(adapter, error, request, state, "terms_required", [])
    state["accepted_terms"] = "current"
    _expect(adapter, error, request, state, "cost_cap_exceeded", [])

    # sensitive wins over an otherwise open cloud
    request, state = _hosted()
    state["sensitive"] = True
    _expect(adapter, error, request, state, "cloud_off", [])

    # cancellation before dispatch: no effect; provider error after dispatch: one effect
    request, state = _hosted()
    state["cancelled"] = True
    _expect(adapter, error, request, state, "cancelled", [])
    request, state = _hosted()
    state["provider_error"] = True
    _expect(adapter, error, request, state, "provider_unavailable", ["hosted"])

    # a fresh gate on every call: state change after one success is honored
    request, state = _hosted()
    _expect(adapter, error, request, state, "succeeded", ["hosted"])
    state["cloud"] = False
    _expect(adapter, error, request, state, "cloud_off", [])

    # hostile envelopes
    for bad in (None, [], {"version": 1}, {1: "secret"}, {"version": True}):
        _expect(adapter, error, bad, {}, "malformed_request", [])
    for field, value in (("mode", ["local"]), ("capability", ["x"]), ("provider_ref", 5)):
        request, state = _hosted()
        request[field] = value
        _expect(adapter, error, request, state, "malformed_request", [])
    request, state = _hosted()
    request["version"] = 1.0
    _expect(adapter, error, request, state, "malformed_request", [])
    request, state = _hosted()
    request["max_cost_usd"] = float("inf")
    _expect(adapter, error, request, state, "malformed_request", [])
    request, state = _hosted()
    state["bounded_cost"] = float("nan")
    _expect(adapter, error, request, state, "cost_cap_exceeded", [])
    request, state = _hosted()
    state["bounded_cost"] = -1
    _expect(adapter, error, request, state, "cost_cap_exceeded", [])

    # one violation per row: a missing gate input refuses, it is never defaulted
    request, state = _hosted()
    assert state["bounded_cost"] == 0 or state["bounded_cost"] <= request["max_cost_usd"]
    del state["cloud"]  # cloud absent, nothing else wrong
    _expect(adapter, error, request, state, "cloud_off", [])
    request, state = _hosted()
    del request["max_cost_usd"]  # cap absent, bounded cost present
    state["bounded_cost"] = 0
    _expect(adapter, error, request, state, "cost_cap_exceeded", [])
    request, state = _hosted()
    request["max_cost_usd"] = 0  # cap present and zero: absent differs from zero
    state["bounded_cost"] = 0
    _expect(adapter, error, request, state, "succeeded", ["hosted"])
    request, state = _hosted()
    del state["bounded_cost"]  # estimate absent, cap present
    _expect(adapter, error, request, state, "cost_cap_exceeded", [])
    request, state = _hosted()
    request["max_cost_usd"] = 0
    state["bounded_cost"] = 1  # one over a zero cap
    _expect(adapter, error, request, state, "cost_cap_exceeded", [])

    # a bool is not a cost estimate (isolated: everything else on the hosted row is valid)
    for flag in (False, True):
        request, state = _hosted()
        state["bounded_cost"] = flag
        _expect(adapter, error, request, state, "cost_cap_exceeded", [])
    # cancellation before dispatch applies to the local mode too
    local = next(r for r in fx.CASES["happy"] if r["name"] == "local-offline")
    request, state = fx.materialize(local)
    state["cancelled"] = True
    _expect(adapter, error, request, state, "cancelled", [])


def test_green_binding_passes_the_whole_battery():
    battery(*BINDING)


@pytest.mark.parametrize("section,row", ROWS, ids=IDS)
def test_each_corpus_row_is_bound(section, row):
    assert section in fx.SECTIONS
    fx.run_row(row, adapter=BINDING[0])


def _mutant(old, new, fn="reference"):
    source = inspect.getsource(getattr(fx, fn))
    assert source.count(old) == 1, old
    namespace = dict(vars(fx))
    exec(compile(source.replace(old, new), "mutant", "exec"), namespace)  # noqa: S102
    return namespace


MUTANTS = [
    ("reference", 'if not state.get("cloud", False) or state.get("sensitive", False):',
     'if not state.get("cloud", False):'),
    ("reference", 'if not state.get("cloud", False) or state.get("sensitive", False):',
     'if state.get("sensitive", False):'),
    ("reference", 'if state.get("accepted_terms") != "current":', "if False:"),
    ("reference", "if bounded < 0 or bounded > cap:", "if bounded < 0 or bounded >= cap:"),
    ("reference", "if bounded < 0 or bounded > cap:", "if bounded > cap:"),
    ("reference", " or not math.isfinite(bounded):", ":"),
    ("reference", 'if state.get("cancelled", False):', "if False:"),
    ("reference", 'effects.append("hosted" if mode == "hosted_byom" else "local")\n',
     'effects.append("hosted" if mode == "hosted_byom" else "local")\n    state["touched"] = 1\n'),
    ("reference", "        _refuse(exc.failure_class)", "        _refuse('internal')"),
    ("reference", 'if state.get("provider_error", False):', "if False:"),
    ("reference", 'not state.get("cloud", False) or', 'not state.get("cloud", True) or'),
    ("reference", 'cap = request.get("max_cost_usd")', 'cap = request.get("max_cost_usd", 0)'),
    ("reference", 'bounded = state.get("bounded_cost")', 'bounded = state.get("bounded_cost", 0)'),
    (
        "reference",
        "type(bounded) not in (int, float)",
        "not isinstance(bounded, (int, float))",
    ),
    (
        "reference",
        'if state.get("cancelled", False):',
        'if mode == "hosted_byom" and state.get("cancelled", False):',
    ),
    (
        "_envelope",
        'type(request["mode"]) is not str or',
        'not isinstance(request["mode"], str) or',
    ),
    (
        "_envelope",
        'if type(request[field]) is not str or not request[field]:',
        'if not isinstance(request[field], str) or not request[field]:',
    ),
    ("_envelope", "or cap < 0:", ":"),
    ("reference", "if bounded < 0 or bounded > cap:", "if bounded < 0:"),
    ("_envelope", "if type(request) is not dict or any(", "if type(request) is not dict and any("),
    (
        "_envelope",
        "if not request.keys() >= REQUIRED or not request.keys() <= ALLOWED:",
        "if not request.keys() <= ALLOWED:",
    ),
    (
        "reference",
        'effects.append("hosted" if mode == "hosted_byom" else "local")\n',
        'effects.append("hosted" if mode == "hosted_byom" else "local")\n'
        '    if mode == "local" and state.get("sensitive", False):\n'
        '        effects.append("local")\n',
    ),
    ("_envelope", 'if type(request["version"]) is not int or request["version"] != 1:',
     'if request["version"] != 1:'),
    ("_envelope", 'if type(cap) not in (float, int) or not math.isfinite(cap) or cap < 0:',
     'if type(cap) not in (float, int) or cap < 0:'),
    ("_envelope", "if type(request[field]) is not str or not request[field]:",
     "if not request[field]:"),
    ("_envelope", "if not request.keys() >= REQUIRED or not request.keys() <= ALLOWED:",
     "if not request.keys() >= REQUIRED:"),
]  # fmt: skip


@pytest.mark.parametrize("fn,old,new", MUTANTS, ids=[f"m{i}" for i in range(len(MUTANTS))])
def test_every_mutant_turns_the_battery_red(fn, old, new):
    namespace = _mutant(old, new, fn)
    adapter = namespace["reference"]
    if fn == "_envelope":
        # the mutated _envelope is what reference calls, via its module globals
        fx_globals = fx.reference.__globals__
        original = fx_globals["_envelope"]
        fx_globals["_envelope"] = namespace["_envelope"]
        try:
            with pytest.raises(AssertionError):
                battery(fx.reference, fx.Refusal)
        finally:
            fx_globals["_envelope"] = original
        battery(fx.reference, fx.Refusal)  # restored and green again
    else:
        with pytest.raises(AssertionError):
            battery(adapter, fx.Refusal)
