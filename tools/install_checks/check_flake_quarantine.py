"""T0037: flake quarantine check - the quarantine file must be schema-exact
and every entry must point at a real test in the collected suite.

Good mode: tools/flake_quarantine.py validates the real repo (quarantine
list, pytest collection, non-empty gate set).
Violation mode: seeded malformed entries against a synthetic collected set -
unknown test id, expired entry, missing reason, duplicate id, bad date,
extra key, empty gate set - each caught for its own reason.
"""

from __future__ import annotations

import datetime

from tools import flake_quarantine
from tools.install_checks import CheckError

CHECK_ID = "T0037"

COLLECTED = {"tests/test_a.py::test_one", "tests/test_b.py::test_two"}
TODAY = datetime.date(2026, 9, 19)
FUTURE = "2026-12-31"


def _entry(**kw):
    base = {"test_id": "tests/test_a.py::test_one", "reason": "flakes on CI",
            "added": "2026-09-01", "expires": FUTURE}
    base.update(kw)
    return base


CASES = {
    "unknown test id": (
        [_entry(test_id="tests/test_zzz.py::test_nope")],
        "not in the suite",
    ),
    "expired entry": (
        [_entry(expires="2026-09-01")],
        "expired",
    ),
    "missing reason": (
        [_entry(reason="  ")],
        "reason must be a non-empty string",
    ),
    "duplicate test id": (
        [_entry(), _entry()],
        "duplicate quarantined test_id",
    ),
    "bad date format": (
        [_entry(expires="next-tuesday")],
        "must be an ISO date",
    ),
    "extra key": (
        [_entry(comment="sneaky")],
        "unknown keys",
    ),
    "non-mapping entry": (
        ["tests/test_a.py::test_one"],
        "must be a mapping",
    ),
}


def run(mode: str) -> None:
    if mode == "good":
        entries = flake_quarantine.load()
        collected = flake_quarantine.collected_tests()
        problems = flake_quarantine.validate(entries, collected)
        if not flake_quarantine.gate_set(collected, entries):
            problems.append("gate set is empty")
        if problems:
            raise CheckError(f"real repo quarantine: {problems}")
        return
    uncaught = []
    for label, (entries, expect) in CASES.items():
        problems = flake_quarantine.validate(entries, COLLECTED, today=TODAY)
        if not any(expect in p for p in problems):
            uncaught.append(f"{label}: expected {expect!r}, got {problems}")
    # empty gate set: everything quarantined
    all_quarantined = [
        _entry(test_id=tid) for tid in sorted(COLLECTED)
    ]
    if flake_quarantine.gate_set(COLLECTED, all_quarantined):
        uncaught.append("empty gate set not detected")
    if uncaught:
        return  # harness FAILS: a quarantine defect escaped
    raise CheckError("all seeded quarantine defects caught")
