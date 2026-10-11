"""T0391 independent verification of the BYOM instrument (T0390).

Differential runs against the real ByomStore composed over a real
CloudSwitch and FlagStore, own crash accounting and own mutation probes.
"""

from __future__ import annotations

import pytest

from desktop.privacy import byom as policy
from desktop.privacy import byom_instrument as production
from desktop.privacy import cloud_off, sensitive_flag
from tests.privacy_instrument_verify_support import (
    classify,
    identity_probe,
    ops_probe,
    verify_wrapper,
)

I1 = "ins1:" + "a" * 64
P1 = "prv1:fixture"
C1 = "col1:" + "c" * 64
MAX = 2**63 - 1
TERMS = {"retention_days": 30, "region": "eu", "trains_on_inputs": False}
OTHER = {"retention_days": 30, "region": "us", "trains_on_inputs": False}
DIGEST = policy.terms_digest(TERMS)
PAYLOAD = {"fen": "8/8/8/8/8/8/8/8 w - - 0 1", "moves": ["e2e4"], "task": "weekly-plan"}


def make_engine():
    flags = sensitive_flag.FlagStore()
    flags.register(C1)
    cloud = cloud_off.CloudSwitch(flags)
    cloud.register(I1)
    return policy.ByomStore(cloud)


def cloud_on(e):
    e._cloud.transition(
        {
            "install_id": I1,
            "cloud_mode": "on",
            "expected_revision": 0,
            "opt_in": cloud_off.opt_in_for(I1, 0),
        }
    )


def unflag(e):
    e._cloud._flags.transition(
        {
            "collection_id": C1,
            "sensitive": False,
            "expected_revision": 0,
            "approval": sensitive_flag.approval_for(C1, 0),
        }
    )


def send(terms=TERMS, payload=PAYLOAD, **kw):
    rq = {
        "install_id": I1,
        "provider_id": P1,
        "collection_id": C1,
        "terms": terms,
        "payload": payload,
    }
    rq.update(kw)
    return rq


def tr(terms, rev, approval=None, **kw):
    rq = {
        "install_id": I1,
        "provider_id": P1,
        "terms": terms,
        "expected_revision": rev,
        "approval": approval,
    }
    rq.update(kw)
    return rq


def apv(rev, terms=TERMS):
    return policy.byom_approval_for(I1, P1, policy.terms_digest(terms), rev)


SCRIPT = [
    ("register", (I1, P1)),
    ("register", (I1, P1)),
    ("record", (I1, P1)),
    ("send", (send(),)),
    ("!", cloud_on),
    ("send", (send(),)),
    ("!", unflag),
    ("send", (send(),)),
    ("transition", (tr(TERMS, 0),)),
    ("transition", (tr(TERMS, 0, apv(9)),)),
    ("transition", (tr(TERMS, 0, apv(0)),)),
    ("send", (send(),)),
    ("send", (send(OTHER),)),
    ("send", (send(payload={**PAYLOAD, "extra": 1}),)),
    ("send", (send(payload={"fen": "", "task": "weekly-plan"}),)),
    ("send", (send(payload={**PAYLOAD, "max-tokens": True}),)),
    ("send", (send(payload={**PAYLOAD, "moves": "e2e4"}),)),
    ("send", (send(terms={**TERMS, "retention_days": 3651}),)),
    ("send", (send(terms={**TERMS, "trains_on_inputs": "no"}),)),
    ("send", (send(terms={**TERMS, "region": "eu-west-1"}),)),
    ("send", ({**send(), "extra": 1},)),
    ("transition", (tr(None, 1),)),
    ("send", (send(),)),
    ("transition", (tr(TERMS, 0, apv(0)),)),
    ("transition", (tr(None, 2, provider_id="prv1:other"),)),
    ("transition", (tr(None, 2, approval="zzz"),)),
    ("register", (I1, "bad")),
]
EXPECTED = [
    "ok",
    "ok",
    "ok",
    "cloud_mode_off",
    "collection_sensitive",
    "terms_not_accepted",
    "approval_missing",
    "approval_missing",
    "ok",
    "ok",
    "terms_changed",
    "payload_rejected",
    "payload_rejected",
    "payload_rejected",
    "payload_rejected",
    "malformed_byom_request",
    "malformed_byom_request",
    "malformed_byom_request",
    "malformed_byom_request",
    "ok",
    "terms_not_accepted",
    "stale_revision",
    "unknown_provider",
    "malformed_byom_request",
    "malformed_byom_request",
]
SECRETS = [I1, C1, P1, "e2e4", "8/8/8"]


def real_probe(module):
    verify_wrapper(module, "ByomTracer", policy.ByomError, make_engine, SCRIPT, EXPECTED, SECRETS)


def test_differential_happy_refusal_terms_payload_malformed_and_revoke_paths():
    tracer = verify_wrapper(
        production, "ByomTracer", policy.ByomError, make_engine, SCRIPT, EXPECTED, SECRETS
    )
    assert len(tracer.records) == 25


def test_revoke_boundary_at_max_revision_writes_null_in_place_and_accept_is_exhausted():
    def engine():
        e = make_engine()
        e.register(I1, P1)
        e._state[(I1, P1)] = (None, MAX)
        return e

    verify_wrapper(
        production,
        "ByomTracer",
        policy.ByomError,
        engine,
        [("transition", (tr(TERMS, MAX, apv(MAX)),)), ("transition", (tr(None, MAX),))],
        ["revision_exhausted", "ok"],
        [I1],
    )

    def accepted():
        e = engine()
        e._state[(I1, P1)] = (DIGEST, MAX)
        return e

    tracer = verify_wrapper(
        production,
        "ByomTracer",
        policy.ByomError,
        accepted,
        [("transition", (tr(None, MAX),)), ("record", (I1, P1))],
        ["ok", "ok"],
        [I1],
    )
    assert tracer._engine._state[(I1, P1)] == (None, MAX)


def test_unregistered_record_keyerror_is_reraised_and_traced_as_crash():
    tracer = production.ByomTracer(make_engine())
    with pytest.raises(KeyError) as caught:
        tracer.record(I1, P1)
    assert caught.value.args == ((I1, P1),) and caught.value.__cause__ is None
    assert [r["outcome"] for r in tracer.records] == ["crash"]


def test_refusal_is_the_policy_object_with_clean_chain():
    tracer = production.ByomTracer(make_engine())
    with pytest.raises(policy.ByomError) as caught:
        tracer.send({})
    assert type(caught.value) is policy.ByomError and caught.value.__cause__ is None
    assert caught.value.failure_class == "malformed_byom_request"


def test_request_purity_the_wrapper_does_not_mutate_or_retain_requests():
    e = make_engine()
    cloud_on(e)
    unflag(e)
    tracer = production.ByomTracer(e)
    tracer.register(I1, P1)
    rq = tr(TERMS, 0, apv(0))
    snap = repr(rq)
    tracer.transition(rq)
    s = send()
    snap2 = repr(s)
    assert tracer.send(s)["verdict"] == "allow"
    assert repr(rq) == snap and repr(s) == snap2
    assert "fixture" not in repr(tracer.records) and "e2e4" not in repr(tracer.records)


def test_scope_limits_unlisted_methods_and_keywords_are_not_forwarded_or_traced():
    tracer = production.ByomTracer(make_engine())
    with pytest.raises(AttributeError):
        tracer.accepted(I1, P1)
    with pytest.raises(TypeError):
        tracer.register(install_id=I1, provider_id=P1)
    assert tracer.records == ()


def test_trace_is_per_instance_unbounded_fresh_copies_and_junk_recovers():
    a, b = production.ByomTracer(make_engine()), production.ByomTracer(make_engine())
    for _ in range(300):
        a.register(I1, P1)
    assert len(a.records) == 300 and len(b.records) == 0
    first, second = a.records, a.records
    assert first == second and first is not second and first[0] is not second[0]
    c = production.ByomTracer(make_engine())
    c._trace = "junk"
    assert c.register(I1, P1)["install_id"] == I1
    assert c.records == ({"seq": 0, "operation": "register", "outcome": "accept"},)


MUTANTS = [
    ("except ByomError:", "except BaseException:", "oracle_kill"),
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
OPS = ("register", "send", "record", "transition")


@pytest.mark.parametrize("old,new,expected", MUTANTS)
def test_independent_mutation_probes_with_honest_accounting(old, new, expected):
    probe = ops_probe("ByomTracer", policy.ByomError, "malformed_byom_request", OPS)
    assert classify(production, old, new, probe) == expected


def test_send_misrouting_mutant_is_killed_by_the_real_policy_differential():
    old = 'def send(self, *args):\n        return self._call("send", args)'
    new = 'def send(self, *args):\n        return self._call("transition", args)'
    assert classify(production, old, new, real_probe) == "oracle_kill"


def test_returned_result_object_identity_is_pinned_and_a_copying_mutant_is_killed():
    probe = identity_probe("ByomTracer", "send")
    assert classify(production, "return result", "return dict(result)", probe) == "oracle_kill"
    probe(production)
