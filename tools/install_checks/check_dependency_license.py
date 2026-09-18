"""T0032: dependency license check - real audit, seeded bypass cases.

Good mode: the real license audit over the repo's installed Python
requirements is clean, and seeded GOOD dist-infos pass (including an
OR-expression with one allowed branch and a free-text "MIT License" field).
Violation mode: each seeded violation case is audited through the REAL
tools/license_audit.py in a fresh subprocess (importlib.metadata caches
make in-process sys.path injection unreliable - verified empirically) and
every case must be rejected, including the historical substring bypass
"MIT AND GPL-2.0-only".
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from tools import license_audit
from tools.install_checks import CheckError

CHECK_ID = "T0032"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "license"
ROOT = Path(__file__).resolve().parent.parent.parent

# case dir -> (dist name, what it seeds)
VIOLATION_CASES = {
    "seedgpl": ("seedgpl", "plain GPL-2.0-only"),
    "seedand": ("seedand", "substring bypass: MIT AND GPL-2.0-only"),
    "seedsub": ("seedsub", "free text containing the token MIT"),
    "seedunk": ("seedunk", "UNKNOWN license"),
    "seedor": ("seedor", "OR with no allowed branch: GPL-2.0-only OR CC-BY-NC-4.0"),
    "seedtrail": ("seedtrail", "trailing operator: GPL-2.0-only OR MIT OR"),
    "seedmix1": ("seedmix1", "authoritative bad expression + permissive legacy field"),
    "seedmix2": ("seedmix2", "MIT AND GPL-2.0-only expression + MIT legacy field"),
    "seedmix3": ("seedmix3", "malformed expression + permissive classifier"),
}
GOOD_CASES = {
    "seedok": ("seedok", "OR with an allowed branch: GPL-2.0-only OR MIT"),
    "seedmit": ("seedmit", "free-text field: MIT License"),
    "seedmixok": ("seedmixok", "authoritative MIT expression overrides GPL legacy field"),
}


def _audit_in_subprocess(case_dir: Path, dist: str) -> list[str]:
    """Run the real audit on dist with case_dir first on sys.path.

    Returns the audit's problem list: empty means the dist was accepted,
    non-empty means the audit rejected it. A crashed subprocess is a
    CheckError, never a silent pass.
    """
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from tools import license_audit;"
            "print(repr(license_audit.audit_python([sys.argv[2]])))",
            str(case_dir),
            dist,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if r.returncode != 0:
        raise CheckError(f"audit subprocess crashed for {dist}: {r.stderr.strip()}")
    return ast.literal_eval(r.stdout.strip())


def run(mode: str) -> None:
    if mode == "good":
        problems = license_audit.audit_python(license_audit.python_requirements())
        if problems:
            raise CheckError(f"real audit: {problems}")
        for case, (dist, what) in sorted(GOOD_CASES.items()):
            problems = _audit_in_subprocess(FIXTURES / case, dist)
            if problems:
                raise CheckError(f"seeded good case {case} ({what}) rejected: {problems}")
        return
    uncaught = [
        f"{case} ({what})"
        for case, (dist, what) in sorted(VIOLATION_CASES.items())
        if not _audit_in_subprocess(FIXTURES / case, dist)
    ]
    if uncaught:
        return  # harness FAILS: seeded license violations escaped the real audit
    raise CheckError("all seeded license violations rejected by the real audit")
