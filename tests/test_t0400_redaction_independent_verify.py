"""T0400 independent verification of the redaction instrument (T0399).

Differential runs against the real module-level redaction policy, including
the sink path that never raises, own crash accounting and mutation probes.
"""

from __future__ import annotations

import pytest

from desktop.privacy import redaction as policy
from desktop.privacy import redaction_instrument as production
from tests.privacy_instrument_verify_support import (
    classify,
    identity_probe,
    ops_probe,
    verify_wrapper,
)

SECRET = "hunter2-s3cret"
RUN32 = "A" * 32


def rq(text, secrets=(SECRET,), **kw):
    r = {"text": text, "secrets": list(secrets)}
    r.update(kw)
    return r


def make_engine():
    return policy


SCRIPT = [
    ("redact", (rq("hello world"),)),
    ("redact", (rq("token is " + SECRET),)),
    ("redact", (rq("token is HUNTER2-S3CRET"),)),
    ("redact", (rq("a " * 512),)),
    ("redact", (rq("a " * 512 + "a"),)),
    ("redact", (rq("a\nb"),)),
    ("redact", (rq("k=" + RUN32),)),
    ("redact", (rq("k=" + "A" * 31),)),
    ("redact", ("not-a-dict",)),
    ("emit_text", ("not-a-dict",)),
    ("redact", (rq("ok", secrets=[""]),)),
    ("redact", (rq("ok", secrets=["s"] * 65),)),
    ("redact", (rq("ok", secrets=[b"x"]),)),
    ("redact", (rq("ok", extra=1),)),
    ("emit_text", (rq("ok", extra=1),)),
    ("emit_text", (rq("plain " + SECRET),)),
    ("emit_text", (rq("plain"),)),
    ("redact", ({"text": 5, "secrets": []},)),
]
EXPECTED = [
    "ok",
    "ok",
    "ok",
    "ok",
    "ok",
    "ok",
    "ok",
    "ok",
    "malformed_redaction_request",
    "ok",
    "malformed_redaction_request",
    "malformed_redaction_request",
    "malformed_redaction_request",
    "malformed_redaction_request",
    "ok",
    "ok",
    "ok",
    "malformed_redaction_request",
]
SECRETS = [SECRET, RUN32, "hello world"]


def real_probe(module):
    verify_wrapper(
        module, "RedactionTracer", policy.RedactionError, make_engine, SCRIPT, EXPECTED, SECRETS
    )


def test_differential_clean_hit_boundary_malformed_and_sink_paths():
    tracer = verify_wrapper(
        production,
        "RedactionTracer",
        policy.RedactionError,
        make_engine,
        SCRIPT,
        EXPECTED,
        SECRETS,
    )
    assert len(tracer.records) == len(SCRIPT)
    # a malformed request through the sink path is an accept, through redact a reject
    assert [r["outcome"] for r in tracer.records][8:10] == ["reject", "accept"]


def test_values_through_the_wrapper_match_the_contract_boundaries():
    t = production.RedactionTracer(policy)
    assert t.redact(rq("hello")) == "hello"
    assert t.redact(rq("a " * 512)) == "a " * 512
    assert t.redact(rq("a " * 512 + "a")) == policy.MARKER
    assert t.redact(rq("x" * 40)) == policy.MARKER
    assert t.redact(rq("A" * 31)) == "A" * 31
    assert t.redact(rq("A" * 32)) == policy.MARKER
    assert t.redact(rq("Hunter2-S3CRET here")) == policy.MARKER
    assert t.emit_text(7) == policy.MARKER
    assert t.emit_text(rq("a\tb")) == policy.MARKER
    for r in t.records:
        assert set(r) == {"seq", "operation", "outcome"}
    assert "hello" not in repr(t.records)


def test_sink_never_emits_the_secret_even_when_the_request_is_malformed():
    t = production.RedactionTracer(policy)
    bad = {"text": SECRET, "secrets": [SECRET], "extra": 1}
    assert t.emit_text(bad) == policy.MARKER
    assert SECRET not in repr(t.records)


def test_request_purity_secrets_list_is_not_mutated():
    t = production.RedactionTracer(policy)
    secrets = [SECRET, "other-secret"]
    req = {"text": "t " + SECRET, "secrets": secrets}
    t.redact(req)
    assert secrets == [SECRET, "other-secret"] and list(req) == ["text", "secrets"]


def test_foreign_crash_in_engine_is_same_object_and_traced_as_crash():
    boom = KeyError("synthetic")

    class Engine:
        @staticmethod
        def redact(_):
            raise boom

    t = production.RedactionTracer(Engine)
    with pytest.raises(KeyError) as caught:
        t.redact({})
    assert caught.value is boom
    assert [r["outcome"] for r in t.records] == ["crash"]


def test_refusal_is_the_policy_object_with_clean_chain():
    t = production.RedactionTracer(policy)
    with pytest.raises(policy.RedactionError) as caught:
        t.redact(None)
    assert type(caught.value) is policy.RedactionError and caught.value.__cause__ is None
    assert caught.value.failure_class == "malformed_redaction_request"


def test_scope_limits_unlisted_names_and_keywords_are_not_forwarded_or_traced():
    t = production.RedactionTracer(policy)
    with pytest.raises(AttributeError):
        _ = t.marker
    with pytest.raises(TypeError):
        t.redact(req=rq("a"))
    assert t.records == ()


def test_trace_is_per_instance_unbounded_fresh_copies_and_junk_recovers():
    a, b = production.RedactionTracer(policy), production.RedactionTracer(policy)
    for _ in range(300):
        a.redact(rq("a"))
    assert len(a.records) == 300 and len(b.records) == 0
    first, second = a.records, a.records
    assert first == second and first is not second and first[0] is not second[0]
    c = production.RedactionTracer(policy)
    c._trace = "junk"
    assert c.redact(rq("a")) == "a"
    assert c.records == ({"seq": 0, "operation": "redact", "outcome": "accept"},)


MUTANTS = [
    ("except RedactionError:", "except BaseException:", "oracle_kill"),
    ('self._append(operation, "crash")', 'self._append(operation, "reject")', "oracle_kill"),
    ('self._append(operation, "accept")', "pass", "oracle_kill"),
    ('"seq": list.__len__(trace)', '"seq": list.__len__(trace) + 1', "oracle_kill"),
    ('"operation": operation', '"operation": "redact"', "oracle_kill"),
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
OPS = ("redact", "emit_text")


@pytest.mark.parametrize("old,new,expected", MUTANTS)
def test_independent_mutation_probes_with_honest_accounting(old, new, expected):
    probe = ops_probe("RedactionTracer", policy.RedactionError, "malformed_redaction_request", OPS)
    assert classify(production, old, new, probe) == expected


def test_emit_text_misrouting_mutant_is_killed_by_the_real_policy_differential():
    old = 'def emit_text(self, *args):\n        return self._call("emit_text", args)'
    new = 'def emit_text(self, *args):\n        return self._call("redact", args)'
    assert classify(production, old, new, real_probe) == "oracle_kill"


def test_returned_result_object_identity_is_pinned_and_a_copying_mutant_is_killed():
    probe = identity_probe("RedactionTracer", "redact")
    assert classify(production, "return result", "return dict(result)", probe) == "oracle_kill"
    probe(production)
