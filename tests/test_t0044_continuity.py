"""T0044 continuity harness: the flip from red to green must not
silently reshape the fixture or the suite. Pins the EXACT bytes
(SHA-256) of the T0042 fixture and the behavior suite, the exact
parametrized case counts per section, the exact deferred-skip count
and its re-verify hook. Any deliberate fixture or suite update must
update these pins IN THE SAME CHANGE, which is where review sees it.
Counts are literal pins, never derived, so a same-count replacement
still fails on the byte digests and a byte-identical edit still fails
on the counts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "variant" / "cases.json"
SUITE = ROOT / "tests" / "test_variant_behavior.py"

FIXTURE_SHA256 = "395933e4d1fe06da57f97b92363212663b4c80bf472dc085b9a2d462014219f0"
SUITE_SHA256 = "a4e5e9aaf47c03f5261ebb3f2ba109c819c9ae2c80804bcda3d1085ccd3641c0"

EXPECTED_COUNTS = {
    "happy": 3,
    "boundary": 6,
    "boundary_deferred": 1,
    "malformed": 14,
    "rollback": 2,
}
DEFERRED_HOOKS = {"contract.variants.entries"}
# 3 happy + 5 active boundary + 14 malformed + 1 unknown-variant
# + 1 additive + 4 static error-shape tests = 28 collected + 1 skip.
EXPECTED_COLLECTED = 28


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixture_bytes_pinned():
    assert _sha256(FIXTURE) == FIXTURE_SHA256


def test_suite_bytes_pinned():
    assert _sha256(SUITE) == SUITE_SHA256


def test_case_counts_pinned():
    cases = json.loads(FIXTURE.read_text())
    deferred = [c for c in cases["boundary"] if "deferred" in c]
    observed = {
        "happy": len(cases["happy"]),
        "boundary": len(cases["boundary"]),
        "boundary_deferred": len(deferred),
        "malformed": len(cases["malformed"]),
        "rollback": len(cases["rollback"]),
    }
    assert observed == EXPECTED_COUNTS
    assert {c["deferred"]["hook"] for c in deferred} == DEFERRED_HOOKS


def test_collected_node_count_pinned():
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "pytest", str(SUITE), "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    ).stdout
    collected = next(
        int(line.split()[0]) for line in out.splitlines() if "tests collected" in line
    )
    assert collected == EXPECTED_COLLECTED + EXPECTED_COUNTS["boundary_deferred"]
