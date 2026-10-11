"""T0396 independent redaction scanner model, boundaries and seeded properties.

Outputs and input purity only; arbitrary binding cross-call memory retention is
not certified. Candidate exceptions are isolated before semantic assertions.
"""

from __future__ import annotations

import copy
import random
import string

import pytest

from desktop.privacy import redaction as production
from tests.privacy_probe_support import ForeignCrash, load_mutant, outcome

TOKEN = frozenset(string.ascii_letters + string.digits + "+/=_%-")


def model(text, secrets):
    if len(text) > 1024 or any(ord(c) < 32 or ord(c) > 126 for c in text):
        return "redacted"
    if any(secret.casefold() in text.casefold() for secret in secrets):
        return "redacted"
    run = 0
    for char in text:
        run = run + 1 if char in TOKEN else 0
        if run >= 32:
            return "redacted"
    return text


def probe(module, text, secrets):
    rq = {"text": text, "secrets": secrets}
    before = copy.deepcopy(rq)
    expected = model(text, secrets)
    assert outcome(module.redact, module.RedactionError, rq) == ("ok", expected)
    assert outcome(module.emit_text, module.RedactionError, rq) == ("ok", expected)
    assert rq == before
    assert outcome(
        module.redact, module.RedactionError, {"text": expected, "secrets": secrets}
    ) == ("ok", expected)


@pytest.mark.parametrize("seed", range(30))
def test_seeded_scanner_agreement_and_stability(seed):
    rng = random.Random(seed)
    for _ in range(80):
        alphabet = rng.choice(["abc .!?", "aZ09+/=_%-", "abc\n", "abcé", "SS ß"])
        text = "".join(rng.choices(alphabet, k=rng.randrange(1100)))
        secrets = rng.choice([[], ["a"], ["SS"], ["ß"], ["fixture"], ["missing", "Z"]])
        probe(production, text, secrets)


@pytest.mark.parametrize("length", [0, 1, 31, 32, 33, 1023, 1024, 1025])
@pytest.mark.parametrize("char", ["a", "~", " ", "/", "é", "\n"])
def test_length_charset_and_run_edges(length, char):
    probe(production, char * length, [])


@pytest.mark.parametrize(
    "text,secrets",
    [
        ("SS", ["ß"]),
        ("x KEY y", ["key"]),
        ("key", ["missing", "key"]),
        ("redacted", ["red"]),
        ("clean", ["x" * 4096]),
        ("clean", ["x"] * 64),
    ],
)
def test_secret_edges(text, secrets):
    probe(production, text, secrets)


def red_probe(module):
    for text, secrets in [
        ("a" * 32, []),
        ("a" * 31, []),
        ("~", []),
        ("é", []),
        ("SS", ["ß"]),
        ("key", ["key"]),
    ]:
        probe(module, text, secrets)


@pytest.mark.parametrize(
    "old,new",
    [
        ("if len(text) > _MAX_TEXT:", "if len(text) >= _MAX_TEXT:"),
        ('r"[A-Za-z0-9+/=_%-]{32}"', 'r"[A-Za-z0-9+/=_%-]{33}"'),
        ("if s.casefold() in folded:", "if s.lower() in folded:"),
        ("if s.casefold() in folded:", "if False:"),
        ('if not " " <= ch <= "~":', 'if not " " <= ch < "~":'),
    ],
)
def test_independent_red_controls(old, new):
    mutant = load_mutant(production, old, new)
    with pytest.raises(AssertionError):
        red_probe(mutant)
        probe(mutant, ("a " * 512), [])


@pytest.mark.parametrize("exception", [KeyError, AssertionError])
def test_raw_crash_is_not_a_semantic_kill(exception):
    def broken(rq):
        raise exception("candidate-only crash")

    with pytest.raises(ForeignCrash):
        outcome(broken, production.RedactionError, {})
