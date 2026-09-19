"""T0062 continuity harness: the flip from red to green must not
silently reshape the fixture or the suite. Pins the EXACT bytes
(SHA-256) of the T0060 castling fixture and the behavior suite, the
exact per-section case counts, and the exact collected node count. Any
deliberate fixture or suite update must update these pins IN THE SAME
CHANGE, which is where review sees it. Counts are literal pins, never
derived, so a same-count replacement still fails on the byte digests
and a byte-identical edit still fails on the counts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "castling" / "cases.json"
SUITE = ROOT / "tests" / "test_castling_behavior.py"

FIXTURE_SHA256 = "b8c0640864d14a0ace597c8f3a685e408faf9e3f1faca461ee9803cc9d4f4ce8"
SUITE_SHA256 = "0bfb5126fb054c2f30823efbf753f0c912a6db38a2e53760299af4b93fa1ec23"

EXPECTED_COUNTS = {
    "happy": 14,
    "boundary": 6,
    "malformed": 25,
    "rollback": 3,
}
# 14 happy + 6 boundary + 25 malformed + 3 rollback = 48 fixture cases
# + 4 static structural API tests = 52 collected, no skips.
EXPECTED_COLLECTED = 52


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fixture_bytes_pinned():
    assert _sha256(FIXTURE) == FIXTURE_SHA256


def test_suite_bytes_pinned():
    assert _sha256(SUITE) == SUITE_SHA256


def test_case_counts_pinned():
    cases = json.loads(FIXTURE.read_text())
    observed = {s: len(cases[s]) for s in EXPECTED_COUNTS}
    assert observed == EXPECTED_COUNTS


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
    assert collected == EXPECTED_COLLECTED
