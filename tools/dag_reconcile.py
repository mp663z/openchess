"""DAG reconcile (T0040): board, evidence and git history must agree.

Three sources of truth about "is this task done and by which merge":
  1. tasks/dag.json: status=done + done_sha + evidence_manifest
  2. evidence/T*.md: Status: done + "Recorded merge SHA on main: <sha>"
  3. git history: the recorded merge SHA must actually be on main

Reconcile checks BOTH directions and the provenance anchor:
  - board-done  => evidence done with the same SHA, and SHA in history
  - evidence-done => board done with the same SHA
  - board-not-done => evidence must not claim done
  - a recorded SHA absent from git history is fabricated provenance
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SHA_RE = re.compile(r"[0-9a-f]{40}")
STATUS_RE = re.compile(r"^Status: (\S+)", re.M)
RECORDED_RE = re.compile(r"^Recorded merge SHA on main: ([0-9a-f]{40})\s*$", re.M)


def main_history(root: Path = ROOT) -> set[str]:
    """All commit SHAs reachable from the repo's current origin/main ref."""
    env = {k: v for k, v in __import__("os").environ.items()
           if not k.startswith("GIT_")}
    out = subprocess.run(
        ["git", "rev-list", "origin/main"], cwd=root, env=env,
        capture_output=True, text=True, check=True,
    )
    return set(out.stdout.split())


def _evidence_status(text: str) -> str | None:
    m = STATUS_RE.search(text)
    return m.group(1) if m else None


sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

ALLOWLIST = ROOT / "data" / "evidence-pre-contract.yaml"
# single source for the marker shape: the evidence contract linter
from tools.evidence_lint import MARKER_RE  # noqa: E402


def _grandfathered(evidence_dir: Path) -> set[str]:
    """Task ids whose evidence file is exempt from the full contract:
    allowlist entry + exact pinned bytes + closing pre-contract marker."""
    if not ALLOWLIST.is_file():
        return set()
    files = yaml.safe_load(ALLOWLIST.read_text()).get("files") or {}
    out = set()
    for rel, want in files.items():
        f = evidence_dir.parent / rel
        if not f.is_file():
            continue
        blob = f.read_bytes()
        if hashlib.sha256(blob).hexdigest() == want and MARKER_RE.search(
            blob.decode("utf-8", "replace")
        ):
            out.add(f.stem)
    return out


def reconcile(board: dict, evidence_dir: Path, history: set[str],
              grandfathered: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    if not isinstance(board, dict):
        return ["board must be a mapping"]
    tasks = board.get("tasks")
    if not isinstance(tasks, list):
        return ["board: tasks must be a list"]

    if grandfathered is None:
        grandfathered = _grandfathered(evidence_dir)
    board_by_id = {}
    for t in tasks:
        if not isinstance(t, dict) or not isinstance(t.get("id"), str):
            problems.append(f"board task entry is malformed: {str(t)[:40]!r}")
            continue
        if t["id"] in board_by_id:
            problems.append(f"duplicate board task id {t['id']}")
            continue
        board_by_id[t["id"]] = t

    evidence_done: dict[str, str] = {}  # task id -> recorded sha
    evidence_other: set[str] = set()
    if evidence_dir.is_dir():
        for f in sorted(evidence_dir.glob("T*.md")):
            tid = f.stem
            text = f.read_text()
            status = _evidence_status(text)
            recorded = RECORDED_RE.search(text)
            if status == "done":
                if recorded:
                    evidence_done[tid] = recorded.group(1)
                else:
                    problems.append(f"{tid}: evidence done without a recorded merge SHA")
            else:
                evidence_other.add(tid)

    for tid, t in board_by_id.items():
        status = t.get("status")
        sha = t.get("done_sha")
        if status == "done":
            # evidence-format comparison (grandfathering exempts ONLY this)
            if tid not in grandfathered:
                if tid in evidence_other:
                    problems.append(f"{tid}: board done but evidence is not done")
                elif tid not in evidence_done:
                    problems.append(f"{tid}: board done but evidence file missing")
                elif isinstance(sha, str) and evidence_done[tid] != sha:
                    problems.append(
                        f"{tid}: evidence recorded SHA {evidence_done[tid][:12]} "
                        f"!= board done_sha {sha[:12]}"
                    )
            # done_sha validity + reachability: ALWAYS, grandfathered or not
            if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
                problems.append(
                    f"{tid}: board done_sha missing or not a full 40-hex string"
                )
            elif sha not in history:
                problems.append(
                    f"{tid}: board done_sha {sha[:12]} not found in main "
                    "history - fabricated provenance"
                )
        else:
            if tid in evidence_done:
                problems.append(
                    f"{tid}: evidence claims done (SHA {evidence_done[tid][:12]}) "
                    f"but board status is {status!r}"
                )
    for tid in evidence_done:
        if tid not in board_by_id:
            problems.append(f"{tid}: evidence done for a task absent from the board")
    return problems


def main() -> int:
    import json
    board = json.loads((ROOT / "tasks/dag.json").read_text())
    problems = reconcile(board, ROOT / "evidence", main_history())
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        return 1
    done = sum(1 for t in board["tasks"] if t.get("status") == "done")
    print(f"OK DAG reconcile: {done} done tasks agree across board, evidence, history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
