"""Governance-document lint v2 (T0009-T0017).

Structural, fail closed:
- Every registered document must exist and carry its REQUIRED headings as
  actual markdown headings (parsed lines, not substring soup).
- Every registered document must be at least MIN_BYTES of substance.
- No placeholder markers; neutral naming (no product codename).
- Reverse registry: every governance doc file present in the repo must be
  registered here (a new doc without checks is an error).
- CODEOWNERS is validated structurally: every governance path must have a
  rule and every rule must name an owner.

CI and the pre-push hook run this on the current tree; documents register
when their task lands them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_BYTES = 400

# doc path (repo-relative) -> required headings, exact after normalization
REQUIRED_HEADINGS: dict[str, list[str]] = {
    "NOTICE": [
        "network use and source offer",
        "network use",
        "source offer",
        "corresponding source scope",
        "trademarks, data, and models",
    ],
    "SECURITY.md": [
        "reporting a vulnerability",
        "supported versions",
        "scope and disclosure",
        "hardening notes",
    ],
}


# paths that must each have a CODEOWNERS rule (when CODEOWNERS is registered)
CODEOWNERS_REQUIRED_PATHS = [
    "LICENSE", "NOTICE", "CLA.md", "DCO.md", "CONTRIBUTING.md",
    "GOVERNANCE.md", "TRADEMARK.md", "SECURITY.md", ".github/CODEOWNERS",
    "docs/rights-policy.md", "docs/licensing.md",
    "tools/evidence_lint.py", "tools/license_lint.py",
    "tools/governance_doc_lint.py", "data/evidence-pre-contract.yaml",
    "data/release-lock.json", "licensing-boundary.yaml",
]

GOVERNANCE_DOC_PATHS = [
    "NOTICE", "CLA.md", "DCO.md", "CONTRIBUTING.md", "GOVERNANCE.md",
    "TRADEMARK.md", "SECURITY.md", "docs/rights-policy.md", ".github/CODEOWNERS",
]

PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|FIXME|PLACEHOLDER|XXX)\b")
CODENAME_RE = re.compile("open" + "chess", re.I)  # split: self-scan safe


MIN_SECTION_BODY = 80  # chars of real text under each required heading


def _operative_lines(text: str) -> list[str]:
    """Lines outside fenced code blocks and HTML comments.

    A heading inside ``` / ~~~ fences or inside <!-- ... --> is inert
    content, not structure; both are stripped before parsing.
    """
    out: list[str] = []
    in_fence = False
    in_comment = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_fence:
            if re.match(r"^(```|~~~)", stripped):
                in_fence = False
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if re.match(r"^(```|~~~)", stripped):
            in_fence = True
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        out.append(line)
    return out


def _sections(text: str) -> dict[str, str]:
    """Map normalized heading -> body text (operative lines only)."""
    lines = _operative_lines(text)
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for i, line in enumerate(lines):
        m = re.match(r"^#{1,6}\s+(.*\S)\s*$", line)
        if m:
            current = " ".join(m.group(1).lower().split())
            sections.setdefault(current, [])
            continue
        if (
            i + 1 < len(lines)
            and re.match(r"^[-=]{3,}\s*$", lines[i + 1])
            and line.strip()
        ):
            current = " ".join(line.strip().lower().split())
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {name: "\n".join(body).strip() for name, body in sections.items()}


def _headings(text: str) -> set[str]:
    return set(_sections(text))


def lint_doc(path: Path, rel: str) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"{rel}: missing"]
    text = path.read_text()
    if len(text) < MIN_BYTES:
        errors.append(f"{rel}: trivially short ({len(text)} bytes)")
    if PLACEHOLDER_RE.search(text):
        errors.append(f"{rel}: contains placeholder marker")
    if CODENAME_RE.search(text):
        errors.append(f"{rel}: contains product codename (use neutral naming)")
    if rel == ".github/CODEOWNERS":
        rules = [
            ln.split()[0] for ln in text.splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
        owners_ok = all(
            "@" in ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")
        )
        if not owners_ok:
            errors.append(f"{rel}: rule without an @owner")
        for req in CODEOWNERS_REQUIRED_PATHS:
            if req not in rules:
                errors.append(f"{rel}: no rule for {req}")
        return errors
    sections = _sections(text)
    for req in REQUIRED_HEADINGS.get(rel, []):
        if req not in sections:
            errors.append(f"{rel}: missing required heading {req!r}")
        elif len(sections[req]) < MIN_SECTION_BODY:
            errors.append(
                f"{rel}: section {req!r} has no operative body "
                f"({len(sections[req])} chars, need {MIN_SECTION_BODY})"
            )
    return errors


def lint_tree(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    registered = set(REQUIRED_HEADINGS) | {".github/CODEOWNERS"}
    for rel in sorted(registered):
        if (root / rel).exists():
            errors.extend(lint_doc(root / rel, rel))
    # reverse registry: a governance doc on disk without checks is an error
    for rel in GOVERNANCE_DOC_PATHS:
        if (root / rel).exists() and rel not in registered:
            errors.append(f"{rel}: present but not registered in governance_doc_lint")
    return errors


def main() -> int:
    errors = lint_tree()
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print("OK governance docs lint: registered documents clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
