"""T0031: secrets check - the tracked tree must never contain credentials.

Good mode: the real tracked tree scans clean (fixtures excluded - they
hold the planted samples).
Violation mode: each planted fixture must be caught, and every detector
class in secrets_scan.PATTERNS must fire at least once across the
fixtures - a neutered detector lets its violation class through and the
harness fails.
"""

from __future__ import annotations

from pathlib import Path

from tools import secrets_scan
from tools.install_checks import CheckError

CHECK_ID = "T0031"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "secrets"
CLEAN_FIXTURE = "clean.txt"


def run(mode: str) -> None:
    if mode == "good":
        findings = secrets_scan.scan_tree()
        if findings:
            raise CheckError(f"tracked tree has secrets: {findings}")
        return
    fixtures = sorted(FIXTURES.glob("*.txt"))
    if not fixtures:
        return  # harness FAILS: no planted fixtures, detectors unverifiable
    uncaught = []
    fired: set[str] = set()
    for path in fixtures:
        findings = secrets_scan.scan_paths([path])
        classes = {f.split(": ")[-1] for f in findings}
        fired |= classes
        if path.name == CLEAN_FIXTURE:
            if findings:
                return  # harness FAILS: false positive on the clean fixture
        elif not findings:
            uncaught.append(path.name)
    if uncaught or fired != set(secrets_scan.PATTERNS):
        return  # harness FAILS: a violation escaped or a detector is dead
    raise CheckError("all seeded secrets caught, all detectors fired")
