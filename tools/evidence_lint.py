"""T0007 v2 - evidence contract lint (strict).

Rules (docs/governance/evidence-contract.md):
- A file is grandfathered ONLY when it carries an exact `pre-contract: true`
  footer line AND its path is in data/evidence-pre-contract.yaml AND the
  recorded sha256 of its bytes matches. Prose mentions mean nothing; any edit
  invalidates grandfathering and the file must meet the full contract.
- Full-contract files declare strict fields, each on its own line:
    Status: done | in-progress | pending
    Verification: <exact dag verification mode> - <verdict detail>
    Commands: `<reproducing command>` ... Environment: <environment>
    Recorded merge SHA on main: <40-hex>   (required iff Status: done)
- Cross-field checks against tasks/dag.json: a done task's evidence Status
  must be done with the SHA equal to the board's done_sha; independent-review
  / independent-verifier / human modes require a "PASS at <sha>" verdict
  naming the same SHA; a task not done on the board must not claim done.
UNVERIFIED is never done.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence"
DAG = ROOT / "tasks" / "dag.json"
ALLOWLIST = ROOT / "data" / "evidence-pre-contract.yaml"

SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
TASK_RE = re.compile(r"^T\d{4}$")
MARKER_RE = re.compile(r"^pre-contract: true( - .*)?$", re.M)
INDEPENDENT = ("independent", "human")


def _tasks(dag_path: Path) -> dict[str, dict]:
    data = json.loads(dag_path.read_text())
    items = data["tasks"] if isinstance(data, dict) and "tasks" in data else data
    return {t["id"]: t for t in items}


def _field(text: str, name: str) -> str | None:
    m = re.search(rf"^{re.escape(name)}: (.*)$", text, re.M)
    return m.group(1).strip() if m else None


def lint_file(
    path: Path,
    tasks: dict[str, dict],
    allowlist: dict[str, str],
    rel: str | None = None,
) -> list[str]:
    errors: list[str] = []
    task = path.stem
    rel = rel or f"evidence/{path.name}"
    text = path.read_text()
    if not TASK_RE.match(task):
        return [f"{rel}: filename must be TNNNN.md"]

    if MARKER_RE.search(text):
        recorded = allowlist.get(rel)
        if recorded is None:
            errors.append(f"{rel}: pre-contract marker on a non-allowlisted file")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != recorded:
            errors.append(
                f"{rel}: edited since grandfathering - must meet the full contract"
            )
        else:
            return []  # frozen grandfathered file

    first = text.splitlines()[0] if text.splitlines() else ""
    if task not in first:
        errors.append(f"{rel}: title must name {task}")

    status = _field(text, "Status")
    if status not in ("done", "in-progress", "pending"):
        errors.append(f"{rel}: missing or invalid `Status:` field (done|in-progress|pending)")

    board = tasks.get(task)
    board_done = bool(board and board.get("status") == "done")
    board_sha = (board or {}).get("done_sha") or ""
    mode = (board or {}).get("verification") if board else None

    recorded_sha = _field(text, "Recorded merge SHA on main")
    if status == "done":
        if not board_done:
            errors.append(f"{rel}: claims done but board status is not done")
        if not recorded_sha or not SHA_RE.fullmatch(recorded_sha):
            errors.append(f"{rel}: Status: done without an exact 40-hex recorded merge SHA")
        elif board_sha and recorded_sha != board_sha:
            errors.append(
                f"{rel}: recorded SHA {recorded_sha[:12]} != board done_sha {board_sha[:12]}"
            )
    elif recorded_sha and not SHA_RE.fullmatch(recorded_sha):
        errors.append(f"{rel}: recorded merge SHA is not a full 40-hex SHA")

    verification = _field(text, "Verification")
    if verification is None:
        errors.append(f"{rel}: missing `Verification:` field")
    else:
        if "UNVERIFIED" in verification:
            errors.append(f"{rel}: UNVERIFIED verdict is never done")
        if mode is not None:
            vmode, _, detail = verification.partition(" - ")
            if vmode != mode:
                errors.append(
                    f"{rel}: Verification mode {vmode!r} != board mode {mode!r}"
                )
            if status == "done" and any(k in mode for k in INDEPENDENT):
                m = re.search(r"\bPASS at ([0-9a-f]{40})\b", detail)
                if not m:
                    errors.append(
                        f"{rel}: independent/human mode requires a `PASS at <sha>` verdict"
                    )
                elif board_sha and m.group(1) != board_sha:
                    errors.append(
                        f"{rel}: verifier PASS SHA {m.group(1)[:12]} != board done_sha"
                    )

    commands = _field(text, "Commands")
    if not commands:
        errors.append(f"{rel}: missing `Commands:` field")
    else:
        if "`" not in commands:
            errors.append(f"{rel}: Commands must include a backticked command")
        if "Environment:" not in commands:
            errors.append(f"{rel}: Commands must record the environment (Environment:)")
    return errors


def lint_tree(root: Path = ROOT) -> list[str]:
    evidence = root / "evidence"
    dag = root / "tasks" / "dag.json"
    allowlist_path = root / "data" / "evidence-pre-contract.yaml"
    tasks = _tasks(dag)
    allowlist = (yaml.safe_load(allowlist_path.read_text()) or {}).get("files", {})
    errors: list[str] = []
    files = sorted(evidence.glob("T*.md"))
    if not files:
        errors.append("no evidence files found")
    for path in files:
        errors.extend(lint_file(path, tasks, allowlist))
    return errors


def main() -> int:
    errors = lint_tree()
    for e in errors:
        print(f"FAIL {e}")
    if errors:
        return 1
    print(f"OK evidence contract: {len(list(EVIDENCE.glob('T*.md')))} files lint-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
