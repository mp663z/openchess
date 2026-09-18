"""T0007 - evidence contract lint.

Every evidence/TNNNN.md must satisfy the contract in
docs/governance/evidence-contract.md unless it carries `pre-contract: true`
(grandfathered structure; an edited file must be brought to full contract).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"

SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
TASK_RE = re.compile(r"^T\d{4}$")


def lint_file(path: Path) -> list[str]:
    errors: list[str] = []
    task = path.stem
    if not TASK_RE.match(task):
        return [f"{path.name}: filename must be TNNNN.md"]
    text = path.read_text()
    if "pre-contract: true" in text:
        return []  # grandfathered structure; see the contract
    first = text.splitlines()[0] if text.splitlines() else ""
    if task not in first:
        errors.append(f"{path.name}: title must name {task}")
    if not SHA_RE.search(text) and "pending" not in text.lower():
        errors.append(f"{path.name}: no exact 40-hex SHA and no `pending` marker")
    if not re.search(r"^Verification:", text, re.M):
        errors.append(f"{path.name}: missing `Verification:` line")
    if not re.search(r"^Commands:", text, re.M):
        errors.append(f"{path.name}: missing `Commands:` line")
    if re.search(r"status:\s*done", text, re.I) and not SHA_RE.search(text):
        errors.append(f"{path.name}: done claim without an exact SHA (UNVERIFIED is not done)")
    return errors


def main() -> int:
    errors: list[str] = []
    files = sorted(EVIDENCE.glob("T*.md"))
    if not files:
        errors.append("no evidence files found")
    for path in files:
        errors.extend(lint_file(path))
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print(f"OK evidence contract: {len(files)} files lint-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
