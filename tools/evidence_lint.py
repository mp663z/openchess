"""T0007 v3 - evidence contract lint (strict, frozen grandfathering).

v3 hardenings (verifier findings on v2):
- The grandfather allowlist is pinned: ALLOWLIST_SHA256 is the sha256 of the
  allowlist file's exact bytes, hardcoded in this enforcement module. A commit
  that edits data/evidence-pre-contract.yaml (replace an entry, keep the
  count, retarget a digest) fails closed: every marker file then errors as
  non-allowlisted. Changing the allowlist requires changing trusted code.
- The pre-contract marker must close the file (only whitespace after it).
- An evidence file for a task missing from tasks/dag.json is an error.
- Commands must contain a nonempty backticked command and a nonempty
  Environment value.

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

# sha256 of data/evidence-pre-contract.yaml bytes - the frozen grandfather list
ALLOWLIST_SHA256 = "1475352ad9092f9e676096948476c2eb1b650a0d513f4021aa0a53fa17fd5a42"
SHA_RE = re.compile(r"\b[0-9a-f]{40}\b")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWLIST_KEY_RE = re.compile(r"^evidence/T\d{4}\.md$")
CMD_RE = re.compile(r"`[^`\s][^`]*`")
ENV_RE = re.compile(r"Environment:\s*\S")
TASK_RE = re.compile(r"^T\d{4}$")
MARKER_RE = re.compile(r"pre-contract: true( - [^\n]*)?\s*\Z")
INDEPENDENT = ("independent", "human")

# Judgment-substituted human checkpoints (owner-delegated, 2026-09-19):
# a HUMAN gate (never an independent-verifier task) may complete when the
# owner has delegated the decision, recorded as a provisional substitute
# with re-verify hooks. The substitution is only as strong as its record:
# the record file must exist, name the task, carry the owner wamid, and
# name at least one real re-verify hook task.
SUBST_RE = re.compile(
    r"\Ajudgment-substituted per owner (wamid\.[A-Za-z0-9+/=]+) "
    r"\((\d{4}-\d{2}-\d{2})\); provisional decision: "
    r"(evidence/substitutions/(T\d{4})\.md); "
    r"re-verify hooks: (T\d{4}(?:, T\d{4})*)\Z")


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
    if board is None:
        errors.append(f"{rel}: task {task} missing from tasks/dag.json")
        return errors
    board_done = bool(board.get("status") == "done")
    board_sha = board.get("done_sha") or ""
    mode = board.get("verification")
    if not mode:
        errors.append(f"{rel}: board task {task} has no verification mode")

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
                subst = SUBST_RE.match(detail.strip())
                if m:
                    if board_sha and m.group(1) != board_sha:
                        errors.append(
                            f"{rel}: verifier PASS SHA {m.group(1)[:12]} != board done_sha"
                        )
                elif subst and "human" in mode and "independent" not in mode:
                    wamid, _date, record, record_task, hooks = subst.groups()
                    if record_task != task:
                        errors.append(
                            f"{rel}: substitution record {record} is for "
                            f"{record_task}, not {task}")
                    record_path = ROOT / record
                    if not record_path.is_file():
                        errors.append(
                            f"{rel}: substitution record {record} missing")
                    else:
                        body = record_path.read_text()
                        if task not in body.splitlines()[0]:
                            errors.append(
                                f"{rel}: substitution record {record} title "
                                f"must name {task}")
                        if wamid not in body:
                            errors.append(
                                f"{rel}: substitution record {record} does "
                                "not carry the owner wamid it cites")
                        if "Re-verify hook" not in body:
                            errors.append(
                                f"{rel}: substitution record {record} lacks "
                                "a Re-verify hook section")
                    for hook in hooks.split(", "):
                        if hook not in tasks:
                            errors.append(
                                f"{rel}: re-verify hook {hook} is not a "
                                "dag task")
                elif "human" in mode and "independent" not in mode:
                    errors.append(
                        f"{rel}: human mode requires a `PASS at <sha>` "
                        "verdict or a judgment-substituted verdict naming "
                        "owner wamid, substitution record and re-verify "
                        "hooks")
                else:
                    errors.append(
                        f"{rel}: independent/human mode requires a `PASS at <sha>` verdict"
                    )

    commands = _field(text, "Commands")
    if not commands:
        errors.append(f"{rel}: missing `Commands:` field")
    else:
        if not CMD_RE.search(commands):
            errors.append(f"{rel}: Commands must include a nonempty backticked command")
        if not ENV_RE.search(commands):
            errors.append(
                f"{rel}: Commands must record a nonempty environment (Environment: <value>)"
            )
    return errors


def load_allowlist(path: Path) -> tuple[dict[str, str], list[str]]:
    """Load the grandfather allowlist, failing closed on any tampering.

    The file's bytes must hash to ALLOWLIST_SHA256 (pinned above in trusted
    code); on mismatch nothing is grandfathered. Shape is validated too.
    """
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ALLOWLIST_SHA256:
        return {}, [
            "data/evidence-pre-contract.yaml: digest != ALLOWLIST_SHA256 pinned "
            "in tools/evidence_lint.py - allowlist tampered; grandfathering "
            "refused (fail closed)"
        ]
    errors: list[str] = []
    mapping = (yaml.safe_load(raw.decode()) or {}).get("files", {})
    for key, value in mapping.items():
        if not ALLOWLIST_KEY_RE.match(key):
            errors.append(f"allowlist key {key!r} is not evidence/TNNNN.md")
        if not isinstance(value, str) or not DIGEST_RE.match(value):
            errors.append(f"allowlist digest for {key!r} is not 64-hex sha256")
    return mapping, errors


def lint_tree(root: Path = ROOT) -> list[str]:
    evidence = root / "evidence"
    dag = root / "tasks" / "dag.json"
    allowlist_path = root / "data" / "evidence-pre-contract.yaml"
    tasks = _tasks(dag)
    allowlist, errors = load_allowlist(allowlist_path)
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
