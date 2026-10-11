"""T0364 independent verification of the sensitive_flag instrument (T0363).

Authored separately from tests/test_t0363_*: differential runs against the
real FlagStore across happy, boundary, malformed and rollback paths, own
crash accounting and own mutation probes.
"""

from __future__ import annotations

import pytest

from desktop.privacy import sensitive_flag as policy
from desktop.privacy import sensitive_flag_instrument as production
from tests.privacy_instrument_verify_support import classify, generic_probe, verify_wrapper

C1 = "col1:" + "a" * 64
C2 = "col1:" + "b" * 64
MAX = 2**63 - 1


def tr(cid, sensitive, rev, approval=None):
    return {
        "collection_id": cid,
        "sensitive": sensitive,
        "expected_revision": rev,
        "approval": approval,
    }


def rb(cid, rev, approval=None):
    return {"collection_id": cid, "expected_revision": rev, "approval": approval}


SCRIPT = [
    ("register", (C1,)),
    ("register", (C1,)),
    ("record", (C1,)),
    ("verdict", ({"collection_id": C1, "destination": "cloud"},)),
    ("verdict", ({"collection_id": C1, "destination": "local"},)),
    ("transition", (tr(C1, False, 0),)),
    ("transition", (tr(C1, False, 0, policy.approval_for(C1, 0)),)),
    ("verdict", ({"collection_id": C1, "destination": "cloud"},)),
    ("transition", (tr(C1, True, 0),)),
    ("transition", (tr(C1, "no", 1),)),
    ("transition", ({**tr(C1, True, 1), "extra": 1},)),
    ("transition", (tr(C1, True, True),)),
    ("transition", (tr(C2, True, 0),)),
    ("transition", (tr(C1, True, 1),)),
    ("rollback", (rb(C1, 2),)),
    ("rollback", (rb(C1, 2, policy.approval_for(C1, 2)),)),
    ("rollback", (rb(C1, 3),)),
    ("rollback", (rb(C1, 4),)),
    ("rollback", ("not-a-dict",)),
    ("register", ("bad",)),
]
EXPECTED = [
    "ok",
    "ok",
    "ok",
    "egress_blocked",
    "ok",
    "approval_missing",
    "ok",
    "ok",
    "stale_revision",
    "malformed_flag_request",
    "malformed_flag_request",
    "malformed_flag_request",
    "unknown_collection",
    "ok",
    "approval_missing",
    "ok",
    "ok",
    "empty_history",
    "malformed_flag_request",
    "malformed_flag_request",
]


def test_differential_happy_malformed_stale_and_rollback_paths():
    tracer = verify_wrapper(
        production, "FlagTracer", policy.FlagError, policy.FlagStore, SCRIPT, EXPECTED, [C1, C2]
    )
    assert len(tracer.records) == len(SCRIPT)


def test_boundary_revision_saturation_is_unchanged_and_traced_as_reject():
    def engine():
        store = policy.FlagStore()
        store.register(C1)
        store._state[C1] = (True, MAX, [])
        return store

    script = [
        ("transition", (tr(C1, False, MAX, policy.approval_for(C1, MAX)),)),
        ("transition", (tr(C1, False, MAX + 1),)),
        ("transition", (tr(C1, True, -1),)),
        ("transition", (tr(C1, True, MAX),)),
        ("record", (C1,)),
    ]
    verify_wrapper(
        production,
        "FlagTracer",
        policy.FlagError,
        engine,
        script,
        ["revision_exhausted", "malformed_flag_request", "malformed_flag_request", "ok", "ok"],
        [C1],
    )


def test_unregistered_record_keyerror_is_same_object_and_traced_as_crash():
    bare = policy.FlagStore()
    with pytest.raises(KeyError) as expected:
        bare.record(C1)
    tracer = production.FlagTracer(policy.FlagStore())
    with pytest.raises(KeyError) as caught:
        tracer.record(C1)
    assert caught.value.args == expected.value.args
    assert caught.value.__cause__ is None
    assert [r["outcome"] for r in tracer.records] == ["crash"]


def test_refusal_object_is_the_policy_object_with_clean_chain():
    tracer = production.FlagTracer(policy.FlagStore())
    with pytest.raises(policy.FlagError) as caught:
        tracer.transition({})
    assert type(caught.value) is policy.FlagError
    assert caught.value.__cause__ is None and caught.value.failure_class == "malformed_flag_request"


def test_scope_limits_unlisted_methods_and_keywords_are_not_forwarded_or_traced():
    tracer = production.FlagTracer(policy.FlagStore())
    with pytest.raises(AttributeError):
        tracer.reads_sensitive(C1)
    with pytest.raises(TypeError):
        tracer.register(collection_id=C1)
    assert tracer.records == ()


def test_trace_is_per_instance_unbounded_and_records_are_fresh_copies():
    a, b = production.FlagTracer(policy.FlagStore()), production.FlagTracer(policy.FlagStore())
    for _ in range(300):
        a.register(C1)
    assert len(a.records) == 300 and len(b.records) == 0
    first, second = a.records, a.records
    assert first == second and first is not second and first[0] is not second[0]
    assert a.records[-1]["seq"] == 299


def test_junk_trace_container_recovers_without_changing_the_result():
    store = policy.FlagStore()
    tracer = production.FlagTracer(store)
    tracer._trace = "junk"
    assert tracer.register(C1)["collection_id"] == C1
    assert tracer.records == ({"seq": 0, "operation": "register", "outcome": "accept"},)


MUTANTS = [
    ("except FlagError:", "except BaseException:", "oracle_kill"),
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
    probe = generic_probe("FlagTracer", policy.FlagError, "malformed_flag_request")
    assert classify(production, old, new, probe) == expected


def real_probe(module):
    verify_wrapper(
        module, "FlagTracer", policy.FlagError, policy.FlagStore, SCRIPT, EXPECTED, [C1, C2]
    )


def test_rollback_misrouting_mutant_is_killed_by_the_real_policy_differential():
    old = 'return self._call("rollback", args)'
    new = 'return self._call("transition", args)'
    assert (
        classify(
            production,
            old,
            new,
            generic_probe("FlagTracer", policy.FlagError, "malformed_flag_request"),
        )
        == "survivor"
    )
    assert classify(production, old, new, real_probe) == "oracle_kill"
