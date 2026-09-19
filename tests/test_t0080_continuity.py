"""T0080 continuity harness: the flip from red to green must not
silently reshape the fixture or the suite. Pins the EXACT bytes
(SHA-256) of the T0078 legal-moves fixture and the behavior suite, the
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
FIXTURE = ROOT / "tests" / "fixtures" / "legal_moves" / "cases.json"
SUITE = ROOT / "tests" / "test_legal_moves_behavior.py"

FIXTURE_SHA256 = "ec7b83083081d3751d463b95feb908afbb155738dd3ee5344293c1db05622a00"
SUITE_SHA256 = "ed3fe9dea4eb92351549d44bad1fe16f5d18ebc178cfbac2691ffd87b1fad00b"

EXPECTED_COUNTS = {
    "happy": 15,
    "boundary": 39,
    "terminal": 4,
    "malformed": 16,
    "rollback": 2,
}
# 15 happy + 39 boundary + 4 terminal + 16 malformed + 2 rollback
# = 76 fixture cases + 7 static structural API tests = 83 collected,
# no skips.
EXPECTED_COLLECTED = 83


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
        int(line.split()[0]) for line in out.splitlines()
        if "tests collected" in line
    )
    assert collected == EXPECTED_COLLECTED
