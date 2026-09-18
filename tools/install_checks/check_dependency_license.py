"""T0032: dependency license check.

Good mode: the real license audit over the repo's installed Python
requirements reports no problems. Violation mode: a seeded distribution
with License GPL-2.0-only (fixtures/seedgpl-1.0.dist-info) is placed on the
discovery path - the audit must reject it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tools import license_audit
from tools.install_checks import CheckError

CHECK_ID = "T0032"
FIXTURES = str(Path(__file__).resolve().parent / "fixtures")
ROOT = Path(__file__).resolve().parent.parent.parent


def run(mode: str) -> None:
    if mode == "good":
        problems = license_audit.audit_python(license_audit.python_requirements())
        if problems:
            raise CheckError(f"real audit: {problems}")
        return
    # fresh interpreter: importlib.metadata discovery caches make an
    # in-process sys.path edit unreliable
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from tools import license_audit;"
            "p = license_audit.audit_python(['seedgpl']);"
            "print(p); sys.exit(0 if p else 1)",
            FIXTURES,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if r.returncode == 0:
        raise CheckError("seeded GPL-2.0-only dependency was NOT rejected")
