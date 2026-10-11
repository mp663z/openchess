"""T0397 malformed redaction request fuzz and sink fail-closed boundary faults.

No arbitrary binding memory-retention or external log persistence guarantee.
"""

from __future__ import annotations

import copy
import random

import pytest

from desktop.privacy import redaction as production
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome


class Bomb(dict):
    calls = []

    def keys(self):
        self.calls.append("keys")
        raise AssertionError("caller hook")


def defects():
    return [
        None,
        [],
        {"text": "safe"},
        {"text": "safe", "secrets": [], "extra": 1},
        {"text": 1, "secrets": []},
        {"text": None, "secrets": []},
        {"text": "safe", "secrets": "key"},
        {"text": "safe", "secrets": [""]},
        {"text": "safe", "secrets": ["x" * 4097]},
        {"text": "safe", "secrets": ["x"] * 65},
        {"text": "safe", "secrets": [True]},
        {"text": "x" * 1100, "secrets": None},
    ]


def probe(module, rq):
    before = copy.deepcopy(rq)
    assert outcome(module.redact, module.RedactionError, rq) == (
        "failure",
        "malformed_redaction_request",
        "malformed_request",
    )
    assert outcome(module.emit_text, module.RedactionError, rq) == ("ok", "redacted")
    assert rq == before
    assert outcome(module.redact, module.RedactionError, {"text": "clean", "secrets": []}) == (
        "ok",
        "clean",
    )


@pytest.mark.parametrize("seed", range(30))
def test_seeded_malformed_fuzz_and_sink_followup(seed):
    rows = defects()
    random.Random(seed).shuffle(rows)
    for rq in rows:
        probe(production, rq)


def test_hostile_dictionary_sink_never_calls_hook():
    rq = Bomb(text="synthetic sensitive text", secrets=[])
    Bomb.calls.clear()
    assert outcome(production.redact, production.RedactionError, rq) == (
        "failure",
        "malformed_redaction_request",
        "malformed_request",
    )
    assert outcome(production.emit_text, production.RedactionError, rq) == ("ok", "redacted")
    assert Bomb.calls == []


@pytest.mark.parametrize(
    "old,new",
    [
        ("len(secrets) > _MAX_SECRETS", "len(secrets) > _MAX_SECRETS+1"),
        ("not 1 <= len(s) <= _MAX_SECRET", "not 0 <= len(s) <= _MAX_SECRET"),
        ("return MARKER\n", 'return "unsafe raw sink"\n'),
    ],
)
def test_independent_red_fallback_and_bounds(old, new):
    module = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        for rq in defects():
            probe(module, rq)


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_foreign_implementation_crash_not_semantic(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.RedactionError, {})
