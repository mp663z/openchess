"""T0053 continuity harness: the flip from red to green must not
silently reshape the fixture or the suite. Pins the EXACT bytes
(SHA-256) of the T0051 turn fixture and the behavior suite, the exact
per-section case counts, and the exact collected node count. Any
deliberate fixture or suite update must update these pins IN THE SAME
CHANGE, which is where review sees it. Counts are literal pins, never
derived, so a same-count replacement still fails on the byte digests
and a byte-identical edit still fails on the counts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "turn" / "cases.json"
SUITE = ROOT / "tests" / "test_turn_behavior.py"

FIXTURE_SHA256 = "43a25258d29253941a8c618a0778193cb8b3c5db8ec90f05f04b99ae0a19eadf"
SUITE_SHA256 = "2ac3035d77e67927510e89c9462cc0b8b6130cb0f4c19bdd6a040cbbb8d3186a"

EXPECTED_COUNTS = {
    "happy": 4,
    "boundary": 5,
    "malformed": 8,
    "rollback": 2,
}
# 4 happy + 5 boundary + 8 malformed + 2 rollback
# + 3 static error-shape tests = 22 collected, no skips.
EXPECTED_COLLECTED = 22


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
