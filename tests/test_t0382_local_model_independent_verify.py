"""T0382 independent verification of the local_model instrument (T0381).

Authored separately from tests/test_t0381_*: differential runs against the
real LocalModels across happy, boundary, malformed
and refusal paths, own crash accounting and own mutation probes.
"""

from __future__ import annotations

import pytest

from desktop.privacy import local_model as policy
from desktop.privacy import local_model_instrument as production
from tests.privacy_instrument_verify_support import classify, generic_probe, verify_wrapper

I1 = "ins1:" + "a" * 64
I2 = "ins1:" + "b" * 64
MAX = 2**63 - 1


def tr(iid, mode, rev, opt_in=None):
    return {"install_id": iid, "large_model": mode, "expected_revision": rev, "opt_in": opt_in}


def vd(iid, tier, host, dest):
    return {"install_id": iid, "tier": tier, "host": host, "destination": dest}


def make_engine():
    return policy.LocalModels()


SCRIPT = [
    ("register", (I1,)),
    ("register", (I1,)),
    ("record", (I1,)),
    ("verdict", (vd(I1, "small", "desktop", "local"),)),
    ("verdict", (vd(I1, "large", "desktop", "local"),)),
    ("verdict", (vd(I1, "small", "desktop", "cloud"),)),
    ("verdict", (vd(I1, "small", "web", "local"),)),
    ("verdict", (vd(I1, "huge", "desktop", "local"),)),
    ("transition", (tr(I1, "on", 0),)),
    ("transition", (tr(I1, "on", 0, policy.opt_in_for(I1, 5)),)),
    ("transition", (tr(I1, "on", 0, policy.opt_in_for(I1, 0)),)),
    ("record", (I1,)),
    ("verdict", (vd(I1, "large", "desktop", "local"),)),
    ("transition", (tr(I1, "on", 1),)),
    ("transition", (tr(I1, "off", 0),)),
    ("transition", (tr(I1, "maybe", 1),)),
    ("transition", ({**tr(I1, "off", 1), "x": 1},)),
    ("transition", (tr(I1, "off", False),)),
    ("transition", (tr(I2, "off", 0),)),
    ("transition", (tr(I1, "off", 1),)),
    ("verdict", (vd(I1, "large", "desktop", "local"),)),
    ("register", ("bad",)),
]
EXPECTED = [
    "ok",
    "ok",
    "ok",
    "ok",
    "large_model_off",
    "egress_requested",
    "host_not_desktop",
    "malformed_model_request",
    "opt_in_missing",
    "opt_in_missing",
    "ok",
    "ok",
    "ok",
    "ok",
    "stale_revision",
    "malformed_model_request",
    "malformed_model_request",
    "malformed_model_request",
    "unknown_install",
    "ok",
    "large_model_off",
    "malformed_model_request",
]


def test_differential_happy_malformed_stale_and_flag_interaction_paths():
    verify_wrapper(
        production, "ModelTracer", policy.ModelError, make_engine, SCRIPT, EXPECTED, [I1, I2]
    )


def test_boundary_revision_saturation_is_unchanged_and_traced_as_reject():
    def engine():
        e = make_engine()
        e.register(I1)
        e._state[I1] = ("off", MAX)
        return e

    script = [
        ("transition", (tr(I1, "on", MAX, policy.opt_in_for(I1, MAX)),)),
        ("transition", (tr(I1, "on", MAX + 1),)),
        ("transition", (tr(I1, "off", -1),)),
        ("transition", (tr(I1, "off", MAX),)),
    ]
    verify_wrapper(
        production,
        "ModelTracer",
        policy.ModelError,
        engine,
        script,
        ["revision_exhausted", "malformed_model_request", "malformed_model_request", "ok"],
        [I1],
    )


def test_unregistered_record_keyerror_is_reraised_and_traced_as_crash():
    tracer = production.ModelTracer(make_engine())
    with pytest.raises(KeyError) as caught:
        tracer.record(I1)
    assert caught.value.args == (I1,) and caught.value.__cause__ is None
    assert [r["outcome"] for r in tracer.records] == ["crash"]


def test_refusal_object_is_the_policy_object_with_clean_chain():
    tracer = production.ModelTracer(make_engine())
    with pytest.raises(policy.ModelError) as caught:
        tracer.transition({})
    assert type(caught.value) is policy.ModelError
    assert (
        caught.value.__cause__ is None and caught.value.failure_class == "malformed_model_request"
    )


def test_scope_limits_unlisted_methods_and_keywords_are_not_forwarded_or_traced():
    tracer = production.ModelTracer(make_engine())
    with pytest.raises(AttributeError):
        tracer.claims(I1)
    with pytest.raises(AttributeError):
        tracer.rollback({})
    with pytest.raises(TypeError):
        tracer.register(install_id=I1)
    assert tracer.records == ()


def test_trace_is_per_instance_unbounded_and_records_are_fresh_copies():
    a, b = production.ModelTracer(make_engine()), production.ModelTracer(make_engine())
    for _ in range(300):
        a.register(I1)
    assert len(a.records) == 300 and len(b.records) == 0
    first, second = a.records, a.records
    assert first == second and first is not second and first[0] is not second[0]
    assert a.records[-1]["seq"] == 299


def test_junk_trace_container_recovers_without_changing_the_result():
    tracer = production.ModelTracer(make_engine())
    tracer._trace = "junk"
    assert tracer.register(I1)["install_id"] == I1
    assert tracer.records == ({"seq": 0, "operation": "register", "outcome": "accept"},)


MUTANTS = [
    ("except ModelError:", "except BaseException:", "oracle_kill"),
    ('self._append(operation, "crash")', 'self._append(operation, "reject")', "oracle_kill"),
    ('self._append(operation, "accept")', "pass", "oracle_kill"),
    ('"seq": list.__len__(trace)', '"seq": list.__len__(trace) + 1', "oracle_kill"),
    ('"operation": operation', '"operation": "transition"', "oracle_kill"),
    (
        '                trace = []\n                object.__setattr__(self, "_trace", trace)\n',
        "                pass\n",
        "oracle_kill",
    ),
    (
        "return tuple(dict(row) for row in trace) if type(trace) is list else ()",
        "return tuple(dict(row) for row in trace)",
        "crash_only",
    ),
]


@pytest.mark.parametrize("old,new,expected", MUTANTS)
def test_independent_mutation_probes_with_honest_accounting(old, new, expected):
    probe = generic_probe("ModelTracer", policy.ModelError, "malformed_model_request")
    assert classify(production, old, new, probe) == expected


def real_probe(module):
    verify_wrapper(
        module, "ModelTracer", policy.ModelError, make_engine, SCRIPT, EXPECTED, [I1, I2]
    )


def test_verdict_misrouting_mutant_is_only_a_crash_kill_not_a_semantic_kill():
    old = 'def verdict(self, *args):\n        return self._call("verdict", args)'
    new = 'def verdict(self, *args):\n        return self._call("record", args)'
    assert classify(production, old, new, real_probe) == "crash_only"
