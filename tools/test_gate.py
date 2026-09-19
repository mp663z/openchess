"""Quarantine-aware test gate (T0037): the ONE test command used by CI,
pre-push and the post-merge canary.

The quarantine file is validated FIRST (fail closed: a malformed, expired,
stale or fabricated entry fails the gate before any test runs), then pytest
runs with every validly-quarantined test deselected. A quarantined flake
cannot red the gate; a non-quarantined failure always does.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools import flake_quarantine  # noqa: E402

PYTEST_TIMEOUT_S = 1800  # a hung test run is a failed gate


def gate_problems(root: Path = ROOT, timeout: int = PYTEST_TIMEOUT_S) -> list[str]:
    problems: list[str] = []
    qfile = root / "data" / "flake-quarantine.yaml"
    try:
        entries = flake_quarantine.load(qfile)
    except (ValueError, FileNotFoundError) as exc:
        return [f"quarantine file unreadable: {exc}"]
    try:
        collected = flake_quarantine.collected_tests(root)
    except RuntimeError as exc:
        return [f"pytest collection failed: {exc}"]
    problems += flake_quarantine.validate(entries, collected)
    if not flake_quarantine.gate_set(collected, entries):
        problems.append("gate set is empty: every test is quarantined")
    if problems:
        return problems
    cmd = [sys.executable, "-m", "pytest", "-q"]
    for e in entries:
        cmd += ["--deselect", e["test_id"]]
    try:
        rc = subprocess.run(
            cmd, cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=timeout,
        ).returncode
    except subprocess.TimeoutExpired:
        return [f"pytest timed out after {timeout}s"]
    if rc != 0:
        problems.append(f"test gate failed: pytest exited {rc}")
    return problems


def main() -> int:
    problems = gate_problems()
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        return 1
    entries = flake_quarantine.load()
    print(
        f"OK test gate: {len(entries)} quarantined (deselected), "
        "all gate tests passed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
