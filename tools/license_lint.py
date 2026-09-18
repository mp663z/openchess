"""T0008 - LICENSE AGPL-3.0-or-later lint.

Asserts:
1. LICENSE is the verbatim FSF AGPL-3.0 text (whitespace-normalized sha256 of
   the canonical text from gnu.org, verified 2026-09-19).
2. Every repo license declaration (pyproject, README, docs/licensing.md)
   states SPDX AGPL-3.0-or-later, per the v5 product decision (report v5
   line 378: "the complete chess product is AGPL-3.0-or-later").
3. No declaration says AGPL-3.0-only.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# whitespace-normalized sha256 of the canonical FSF AGPL-3.0 text
# (https://www.gnu.org/licenses/agpl-3.0.txt, fetched and compared 2026-09-19)
AGPL3_NORMALIZED_SHA256 = "2cd0fb7883a3b3553dedbb0bad171646d46f51e2642951aae7c59cb5cec86c46"

DECLARATIONS = ("pyproject.toml", "README.md", "docs/licensing.md", "licensing-boundary.yaml")


def _normalized_sha256(path: Path) -> str:
    text = re.sub(r"\s+", " ", path.read_text()).strip()
    return hashlib.sha256(text.encode()).hexdigest()


def lint() -> list[str]:
    errors: list[str] = []
    license_path = ROOT / "LICENSE"
    if not license_path.exists():
        errors.append("LICENSE file missing")
    elif _normalized_sha256(license_path) != AGPL3_NORMALIZED_SHA256:
        errors.append("LICENSE is not the verbatim FSF AGPL-3.0 text")
    for rel in DECLARATIONS:
        path = ROOT / rel
        if not path.exists():
            errors.append(f"{rel} missing")
            continue
        text = path.read_text()
        if "AGPL-3.0-or-later" not in text:
            errors.append(f"{rel}: does not declare AGPL-3.0-or-later")
        if "AGPL-3.0-only" in text:
            errors.append(f"{rel}: declares AGPL-3.0-only (product is AGPL-3.0-or-later)")
    return errors


def main() -> int:
    errors = lint()
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print("OK license lint: LICENSE is verbatim AGPL-3.0; declarations are AGPL-3.0-or-later")
    return 0


if __name__ == "__main__":
    sys.exit(main())
