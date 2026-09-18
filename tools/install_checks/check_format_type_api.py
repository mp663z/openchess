"""T0021: format/type/API check - three independent detectors.

Detectors (each must bite on its own seeded violation):
  format: ruff check
  type:   mypy --strict (real static type checker)
  api:    pinned public API via AST (exact set match)

Good mode: fixtures/seed_good.py passes all three.
Violation mode: fixtures/bad_format.py, bad_type.py, bad_api.py each trip
EXACTLY their intended detector - a missing or neutered detector lets a
violation through and the harness fails.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from tools.install_checks import CheckError

CHECK_ID = "T0021"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PINNED_API = {"add", "Mul"}  # the public API of the seeded good module


def _public_api(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_")
    }


def _format(target: Path) -> str | None:
    r = subprocess.run(
        ["ruff", "check", "-q", str(target)], capture_output=True, text=True
    )
    if r.returncode != 0:
        return f"format: {r.stdout.strip() or r.stderr.strip()}"
    return None


def _types(target: Path) -> str | None:
    r = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "--no-incremental",
         "--no-error-summary", str(target)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return f"type: {r.stdout.strip() or r.stderr.strip()}"
    return None


def _api(target: Path) -> str | None:
    api = _public_api(target)
    if api != PINNED_API:
        return f"api: {sorted(api)} != pinned {sorted(PINNED_API)}"
    return None


DETECTORS = (("format", _format), ("type", _types), ("api", _api))

# violation fixture -> the one detector intended to catch it
VIOLATIONS = {
    "bad_format.py": "format",
    "bad_type.py": "type",
    "bad_api.py": "api",
}


def _failures(target: Path) -> set[str]:
    return {name for name, det in DETECTORS if det(target) is not None}


def run(mode: str) -> None:
    if mode == "good":
        failed = _failures(FIXTURES / "seed_good.py")
        if failed:
            raise CheckError(f"seed_good.py failed detectors: {sorted(failed)}")
        return
    uncaught = []
    for fname, expect in VIOLATIONS.items():
        failed = _failures(FIXTURES / fname)
        if failed != {expect}:
            uncaught.append(
                f"{fname}: detectors fired {sorted(failed) or 'NONE'}, expected ['{expect}']"
            )
    if uncaught:
        return  # harness FAILS: a seeded violation escaped its intended detector
    raise CheckError("all seeded violations caught by their intended detectors")
