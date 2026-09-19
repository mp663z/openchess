"""T0040: DAG reconcile check - board, evidence and git history must agree.

Good mode: tools/dag_reconcile.py reconciles the REAL repo (tasks/dag.json
x evidence/ x origin/main history): board-done <=> evidence-done with the
same SHA, every done_sha reachable from main, no done claims either way
without the other. Pre-contract grandfathered evidence (allowlist + pinned
bytes + closing marker) is exempt from the done-format, never from the
history anchor.
Violation mode: five synthetic mismatches against a fixture evidence dir
and fake history - board done/evidence not done, SHA mismatch, orphan SHA
(fabricated provenance), evidence done/board not done, evidence for an
absent task - each must be rejected for its own reason.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools import dag_reconcile
from tools.install_checks import CheckError

CHECK_ID = "T0040"

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_ORPHAN = "c" * 40
SV = "5.2-alpha-casual-autoimport"


def _task(tid, status="done", sha=SHA_A):
    return {"id": tid, "title": "t", "acceptance": "a", "verification": "v",
            "status": status, "done_sha": sha, "evidence_manifest": f"evidence/{tid}.md",
            "dependencies": [], "milestone": "m", "phase": "p",
            "roadmap_layer": "r", "spine_outcome": "s", "track": "x", "week": "1"}


def _evidence(tid, status="done", sha=SHA_A):
    lines = [f"# {tid} x", "", "Acceptance: a.", "", "Commands: `true`. Environment: python.",
             f"Status: {status}"]
    if status == "done":
        lines.append(f"Recorded merge SHA on main: {sha}")
        lines.append(f"Verification: auto - PASS at {sha}")
    else:
        lines.append("Verification: auto - pending")
    return "\n".join(lines) + "\n"


CASES = {
    "board done, evidence in-progress": (
        {"schema_version": SV, "tasks": [_task("T0001")]},
        {"T0001": _evidence("T0001", status="in-progress")},
        {SHA_A},
        "board done but evidence is not done",
    ),
    "SHA mismatch board vs evidence": (
        {"schema_version": SV, "tasks": [_task("T0001", sha=SHA_A)]},
        {"T0001": _evidence("T0001", sha=SHA_B)},
        {SHA_A, SHA_B},
        "!= board done_sha",
    ),
    "orphan SHA (fabricated provenance)": (
        {"schema_version": SV, "tasks": [_task("T0001", sha=SHA_ORPHAN)]},
        {"T0001": _evidence("T0001", sha=SHA_ORPHAN)},
        {SHA_A},
        "not found in main history",
    ),
    "evidence done, board not done": (
        {"schema_version": SV, "tasks": [_task("T0001", status="in_progress")]},
        {"T0001": _evidence("T0001")},
        {SHA_A},
        "evidence claims done",
    ),
    "evidence for absent task": (
        {"schema_version": SV, "tasks": [_task("T0001")]},
        {"T0001": _evidence("T0001"), "T0999": _evidence("T0999", sha=SHA_B)},
        {SHA_A, SHA_B},
        "absent from the board",
    ),
}


def run(mode: str) -> None:
    if mode == "good":
        problems = dag_reconcile.reconcile(
            json.loads((dag_reconcile.ROOT / "tasks/dag.json").read_text()),
            dag_reconcile.ROOT / "evidence",
            dag_reconcile.main_history(),
        )
        if problems:
            raise CheckError(f"real repo reconcile: {problems}")
        return
    uncaught = []
    for label, (board, ev_files, history, expect) in CASES.items():
        with tempfile.TemporaryDirectory() as td:
            evdir = Path(td) / "evidence"
            evdir.mkdir()
            for tid, text in ev_files.items():
                (evdir / f"{tid}.md").write_text(text)
            problems = dag_reconcile.reconcile(board, evdir, history,
                                               grandfathered=set())
        if not any(expect in p for p in problems):
            uncaught.append(f"{label}: expected {expect!r}, got {problems}")
    if uncaught:
        return  # harness FAILS: a reconcile mismatch escaped
    raise CheckError("all seeded board/evidence/history mismatches caught")
