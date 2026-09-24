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
# --max-worker-restart 0: a crashed worker fails the run at once instead of
# hanging it when -n auto resolves to a single worker
XDIST_ARGS = ("-n", "auto", "--dist", "loadfile", "--max-worker-restart", "0")


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
    # Collection and quarantine validation above ran once, in this process,
    # before any worker starts. The run itself is parallel (pytest-xdist):
    # one module per worker at a time, so module-level caches stay whole.
    # A crashed worker fails its test and the run exits non-zero.
    cmd = [sys.executable, "-m", "pytest", "-q", *XDIST_ARGS]
    for e in entries:
        cmd += ["--deselect", e["test_id"]]
    try:
        out = subprocess.run(
            cmd, cwd=root,
            capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [f"pytest timed out after {timeout}s"]
    if out.returncode != 0:
        tail = "\n".join((out.stdout + out.stderr).splitlines()[-15:])
        problems.append(
            f"test gate failed: pytest exited {out.returncode}\n{tail}"
        )
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
