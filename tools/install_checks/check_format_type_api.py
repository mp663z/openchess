"""T0021: format/type/API check.

Good mode: the seeded module fixtures/seed_good.py is clean under ruff and
exports exactly its pinned public API. Violation mode: fixtures/seed_bad.py
has a lint error and an unpinned extra export - the check must catch both.
"""

from __future__ import annotations

import ast
import subprocess
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


def run(mode: str) -> None:
    target = FIXTURES / ("seed_good.py" if mode == "good" else "seed_bad.py")
    r = subprocess.run(
        ["ruff", "check", "-q", str(target)], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise CheckError(f"ruff: {r.stdout.strip() or r.stderr.strip()}")
    api = _public_api(target)
    if api != PINNED_API:
        raise CheckError(f"public API drift: {sorted(api)} != {sorted(PINNED_API)}")
