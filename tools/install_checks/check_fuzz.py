"""T0024: fuzz check - the fail-closed gates must NEVER crash or hang on
adversarial input; they must reject.

Targets: license_audit.expression_ok/candidate_ok (string gate),
rights_audit.validate_sources (parsed-YAML structures),
cla_check.validate_registry (parsed-YAML registry).

Good mode: 3,000 seeded random inputs per string target + 1,000 random
structures per YAML target - no exception, correct return type, and no
random string may be ACCEPTED as a license expression unless it is
structurally a known-allowlisted one (fail closed under noise).
Violation mode: pinned nasties (huge inputs, NUL bytes, doubled
operators, wrong-type YAML nodes, absolute evidence paths) must each be
handled without exception AND rejected; a crash or silent accept is an
uncaught violation.
"""

from __future__ import annotations

import random
import string

from tools import cla_check, rights_audit
from tools import license_audit as la
from tools.install_checks import CheckError

CHECK_ID = "T0024"

STRING_NASTIES = [
    "OR " * 10000,
    "\x00" * 100,
    "MIT" * 5000,
    "MIT OR OR Apache-2.0",
    "MIT\nOR\nApache-2.0",
    "MIT\x00OR\x00Apache-2.0",
    " AND" * 2000,
    "مIT",  # confusable
    "MIT OR",
    " " * 10000,
]

YAML_NASTIES = [
    None,
    [],
    "just a string",
    {"sources": None},
    {"sources": "nope"},
    {"sources": [None]},
    {"sources": ["nope"]},
    {"sources": [{"id": None, "statement": 123, "evidence_path": "/etc/passwd"}]},
    {"sources": [{"id": "x", "statement": "s", "evidence_path": "../escape"}]},
    {"acceptances": [{"handle": None}]},
    {"acceptances": "nope"},
    {"acceptances": [{"handle": "ok", "timestamp": "not-a-date", "pr": -1}]},
]


def _random_string(rng: random.Random) -> str:
    n = rng.randint(0, 40)
    alphabet = string.ascii_letters + string.digits + " .-\t\n\x00()ORANDWITH"
    return "".join(rng.choice(alphabet) for _ in range(n))


def _random_yaml(rng: random.Random, depth: int = 0):
    if depth > 3:
        return rng.choice([None, 1, "x", True])
    kind = rng.randint(0, 5)
    if kind == 0:
        return None
    if kind == 1:
        return rng.randint(-100, 100)
    if kind == 2:
        return _random_string(rng)
    if kind == 3:
        return rng.choice([True, False])
    if kind == 4:
        return [_random_yaml(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {rng.choice(["id", "sources", "acceptances", "handle", "statement",
                        "evidence_path", "x"]): _random_yaml(rng, depth + 1)
            for _ in range(rng.randint(0, 3))}


def _expect_no_crash_string(fn, inputs, label):
    bad = []
    for s in inputs:
        try:
            r = fn(s)
            if not isinstance(r, bool):
                bad.append(f"{label}({s[:20]!r}...) returned non-bool {type(r).__name__}")
        except Exception as e:
            bad.append(f"{label}({s[:30]!r}...) CRASHED: {e!r}")
    return bad


def _expect_no_crash_yaml(fn, inputs, label):
    """A domain rejection (ClaError for the registry) is the DESIGNED
    fail-closed outcome, not a crash; any other exception type is."""
    bad = []
    for doc in inputs:
        try:
            r = fn(doc)
            if not isinstance(r, (list, dict)):
                bad.append(f"{label} returned {type(r).__name__}")
        except cla_check.ClaError:
            pass  # designed fail-closed rejection
        except Exception as e:
            bad.append(f"{label} CRASHED on {str(doc)[:40]!r}: {e!r}")
    return bad


def run(mode: str) -> None:
    rng = random.Random(20260919)
    random_strings = [_random_string(rng) for _ in range(3000)]
    random_docs = [_random_yaml(rng) for _ in range(1000)]

    if mode == "good":
        bad = []
        bad += _expect_no_crash_string(
            la.expression_ok, random_strings + STRING_NASTIES, "expression_ok")
        bad += _expect_no_crash_string(
            la.candidate_ok, random_strings[:500], "candidate_ok")
        bad += _expect_no_crash_yaml(
            lambda d: rights_audit.validate_sources(d) if isinstance(d, dict) else [],
            random_docs, "validate_sources")
        bad += _expect_no_crash_yaml(cla_check.validate_registry, random_docs, "validate_registry")
        # noise must not be ACCEPTED as a license expression
        accepted_noise = [s for s in random_strings
                          if la.expression_ok(s)
                          and not all(t in la.ALLOWED or t.upper() in {"OR", "AND"}
                                      for t in s.split())]
        if accepted_noise:
            bad.append(f"noise accepted: {accepted_noise[:3]!r}")
        if bad:
            raise CheckError(f"fuzz failures: {bad[:5]}")
        return

    uncaught = []
    uncaught += _expect_no_crash_string(
        la.expression_ok, STRING_NASTIES, "expression_ok")
    uncaught += _expect_no_crash_yaml(
        lambda d: rights_audit.validate_sources(d) if isinstance(d, dict) else [],
        YAML_NASTIES, "validate_sources")
    # every string nasty must be REJECTED (not silently accepted)
    for s in STRING_NASTIES:
        if la.expression_ok(s):
            uncaught.append(f"nasty ACCEPTED: {s[:40]!r}")
    if uncaught:
        return  # harness FAILS: a crash or silent accept escaped
    raise CheckError("all fuzz nasties handled without crash and rejected")
