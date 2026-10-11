"""T0409 independent verification of the delete instrument (T0408).

Differential runs against the real Deleter with in-memory stores, including
a faulting store, own crash accounting and own mutation probes.
"""

from __future__ import annotations

import pytest

from desktop.privacy import delete as policy
from desktop.privacy import delete_instrument as production
from tests.privacy_instrument_verify_support import (
    classify,
    identity_probe,
    ops_probe,
    verify_wrapper,
)

C1 = "col1:" + "a" * 64
C2 = "col1:" + "b" * 64
C3 = "col1:" + "c" * 64
MAX = 2**63 - 1
NAMES = ("content", "index", "analysis", "training", "ciphertext")


class Store:
    def __init__(self, data, fail=0):
        self.data, self.fail = set(data), fail

    def erase(self, cid):
        if self.fail:
            self.fail -= 1
            raise OSError("synthetic erase fault")
        self.data.discard(cid)


def make_engine():
    stores = {n: Store({C1, C2}, int(n == "index")) for n in NAMES}
    d = policy.Deleter(stores)
    d.stores_view = stores
    return d


def dl(cid, rev, decision=None, **kw):
    rq = {"collection_id": cid, "expected_revision": rev, "decision": decision}
    rq.update(kw)
    return rq


SCRIPT = [
    ("register", (C1,)),
    ("register", (C2,)),
    ("register", (C1,)),
    ("record", (C1,)),
    ("access", ({"collection_id": C1},)),
    ("access", ({"collection_id": C3},)),
    ("delete", (dl(C1, 0),)),
    ("delete", (dl(C1, 5, policy.decision_for(C1, 5)),)),
    ("delete", (dl(C1, 0, policy.decision_for(C1, 9)),)),
    ("delete", (dl(C1, 0, policy.decision_for(C1, 0)),)),
    ("access", ({"collection_id": C1},)),
    ("register", (C1,)),
    ("delete", (dl(C1, 0),)),
    ("delete", (dl(C1, 1),)),
    ("delete", (dl(C1, 1),)),
    ("delete", (dl(C3, 0, policy.decision_for(C3, 0)),)),
    ("delete", (dl(C2, "0"),)),
    ("delete", (dl(C2, True),)),
    ("delete", (dl(C2, 0, "zzz"),)),
    ("delete", ({**dl(C2, 0), "x": 1},)),
    ("access", ({"collection_id": C2, "x": 1},)),
    ("access", ("nope",)),
    ("register", ("bad",)),
]
EXPECTED = [
    "ok",
    "ok",
    "ok",
    "ok",
    "ok",
    "unknown_collection",
    "decision_missing",
    "stale_revision",
    "decision_missing",
    "ok",
    "collection_deleted",
    "collection_deleted",
    "stale_revision",
    "ok",
    "ok",
    "unknown_collection",
    "malformed_delete_request",
    "malformed_delete_request",
    "malformed_delete_request",
    "malformed_delete_request",
    "malformed_delete_request",
    "malformed_delete_request",
    "malformed_delete_request",
]
SECRETS = [C1, C2, C3]


def real_probe(module):
    verify_wrapper(
        module, "DeleteTracer", policy.DeleteError, make_engine, SCRIPT, EXPECTED, SECRETS
    )


def test_differential_happy_refusal_pending_retry_and_malformed_paths():
    tracer = verify_wrapper(
        production, "DeleteTracer", policy.DeleteError, make_engine, SCRIPT, EXPECTED, SECRETS
    )
    eng = tracer._engine
    assert eng._state[C1] == ("deleted", 1, ())
    assert all(C1 not in s.data for s in eng.stores_view.values())
    assert all(C2 in s.data for s in eng.stores_view.values())


def test_faulting_store_stays_pending_then_retry_clears_it_through_the_wrapper():
    tracer = production.DeleteTracer(make_engine())
    tracer.register(C1)
    first = tracer.delete(dl(C1, 0, policy.decision_for(C1, 0)))
    assert first["state"] == "deleted" and first["pending"] == ["index"]
    assert C1 in tracer._engine.stores_view["index"].data
    second = tracer.delete(dl(C1, 1))
    assert second["pending"] == [] and C1 not in tracer._engine.stores_view["index"].data
    assert [r["outcome"] for r in tracer.records] == ["accept"] * 3


def test_boundary_revision_at_max_deletes_without_exhaustion_and_rejects_overflow():
    def engine():
        e = make_engine()
        e.register(C1)
        e._state[C1] = ("live", MAX, ())
        return e

    verify_wrapper(
        production,
        "DeleteTracer",
        policy.DeleteError,
        engine,
        [
            ("delete", (dl(C1, MAX + 1, policy.decision_for(C1, 1)),)),
            ("delete", (dl(C1, -1),)),
            ("delete", (dl(C1, MAX, policy.decision_for(C1, MAX)),)),
        ],
        ["malformed_delete_request", "malformed_delete_request", "ok"],
        [C1],
    )


def test_unregistered_record_keyerror_is_reraised_and_traced_as_crash():
    tracer = production.DeleteTracer(make_engine())
    with pytest.raises(KeyError) as caught:
        tracer.record(C1)
    assert caught.value.args == (C1,) and caught.value.__cause__ is None
    assert [r["outcome"] for r in tracer.records] == ["crash"]


def test_refusal_is_the_policy_object_with_clean_chain():
    tracer = production.DeleteTracer(make_engine())
    with pytest.raises(policy.DeleteError) as caught:
        tracer.delete({})
    assert type(caught.value) is policy.DeleteError and caught.value.__cause__ is None
    assert caught.value.failure_class == "malformed_delete_request"


def test_scope_limits_unlisted_methods_and_keywords_are_not_forwarded_or_traced():
    tracer = production.DeleteTracer(make_engine())
    with pytest.raises(AttributeError):
        tracer.stores_view.get  # noqa: B018 - attribute on engine is not proxied
    with pytest.raises(AttributeError):
        tracer.rollback({})
    with pytest.raises(TypeError):
        tracer.register(collection_id=C1)
    assert tracer.records == ()


def test_trace_is_per_instance_unbounded_fresh_copies_and_junk_recovers():
    a, b = production.DeleteTracer(make_engine()), production.DeleteTracer(make_engine())
    for _ in range(300):
        a.register(C1)
    assert len(a.records) == 300 and len(b.records) == 0
    first, second = a.records, a.records
    assert first == second and first is not second and first[0] is not second[0]
    c = production.DeleteTracer(make_engine())
    c._trace = "junk"
    assert c.register(C1)["collection_id"] == C1
    assert c.records == ({"seq": 0, "operation": "register", "outcome": "accept"},)


MUTANTS = [
    ("except DeleteError:", "except BaseException:", "oracle_kill"),
    ('self._append(operation, "crash")', 'self._append(operation, "reject")', "oracle_kill"),
    ('self._append(operation, "accept")', "pass", "oracle_kill"),
    ('"seq": list.__len__(trace)', '"seq": list.__len__(trace) + 1', "oracle_kill"),
    ('"operation": operation', '"operation": "delete"', "oracle_kill"),
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
OPS = ("register", "delete", "record", "access")


@pytest.mark.parametrize("old,new,expected", MUTANTS)
def test_independent_mutation_probes_with_honest_accounting(old, new, expected):
    probe = ops_probe("DeleteTracer", policy.DeleteError, "malformed_delete_request", OPS)
    assert classify(production, old, new, probe) == expected


def test_delete_misrouting_mutant_is_killed_by_the_real_policy_differential():
    old = 'def delete(self, *args):\n        return self._call("delete", args)'
    new = 'def delete(self, *args):\n        return self._call("access", args)'
    assert classify(production, old, new, real_probe) == "oracle_kill"


def test_returned_result_object_identity_is_pinned_and_a_copying_mutant_is_killed():
    probe = identity_probe("DeleteTracer", "delete")
    assert classify(production, "return result", "return dict(result)", probe) == "oracle_kill"
    probe(production)
