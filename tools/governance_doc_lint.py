"""Governance-document lint (T0009-T0017).

Each governance document must exist, be non-trivial, carry its required
sections, and contain no placeholder markers. Fail closed: any missing
section is an error. Usage: python tools/governance_doc_lint.py [DOC ...]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# doc path (repo-relative) -> required section headings (case-insensitive)
REQUIRED: dict[str, list[str]] = {
    "NOTICE": [
        "network use", "source offer", "corresponding source", "agpl",
    ],
    "CLA.md": [
        "contributor license agreement", "grant of copyright license",
        "grant of patent license", "representations", "agpl",
    ],
    "DCO.md": [
        "developer certificate of origin", "sign-off", "de minimis",
    ],
    "CONTRIBUTING.md": [
        "contributing", "pull request", "code of conduct", "license",
    ],
    "GOVERNANCE.md": [
        "governance", "maintainers", "decision", "amend",
    ],
    "TRADEMARK.md": [
        "trademark", "nominative", "not endorsed", "logo",
    ],
    "SECURITY.md": [
        "security policy", "reporting", "supported versions", "disclosure",
    ],
    "docs/rights-policy.md": [
        "rights policy", "fail closed", "dataset", "model",
    ],
    ".github/CODEOWNERS": [],
}

PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|PLACEHOLDER|XXX)\b")
CODENAME_RE = re.compile("open" + "chess", re.I)  # split: self-scan safe


def lint_doc(path: Path, rel: str) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"{rel}: missing"]
    text = path.read_text()
    if len(text) < 400:
        errors.append(f"{rel}: trivially short ({len(text)} bytes)")
    lowered = text.lower()
    for section in REQUIRED.get(rel, []):
        if section.lower() not in lowered:
            errors.append(f"{rel}: missing required section {section!r}")
    if PLACEHOLDER_RE.search(text):
        errors.append(f"{rel}: contains placeholder marker")
    if CODENAME_RE.search(text):
        errors.append(f"{rel}: contains product codename (use neutral naming)")
    return errors


def lint_tree(root: Path = ROOT, only: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    for rel in REQUIRED:
        if only and rel not in only:
            continue
        errors.extend(lint_doc(root / rel, rel))
    return errors


def main() -> int:
    only = sys.argv[1:] or None
    errors = lint_tree(only=only)
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print(f"OK governance docs: {len(only or REQUIRED)} documents lint-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
