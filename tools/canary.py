"""Post-merge canary (T0039): after every merge, the full gate must be
re-run against main HEAD and any failure must name the head SHA.

The canary workflow (.github/workflows/canary.yml) is pinned to
on.push.branches == [main] exactly and runs this tool. This module also
validates that workflow pin, so a widened trigger or a removed step is a
gate failure, not a silent drift.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.install_checks import runner  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "canary.yml"

# The merge-relevant gate set, re-run verbatim against the checked-out HEAD.
# full_gates() appends the nested install-check runner (checks that shell
# the runner are excluded, or the canary would recurse into itself).
GATES = [
    ["tools/dag.py", "verify"],
    ["tools/dag_reconcile.py"],
    ["tools/evidence_lint.py"],
    ["tools/license_audit.py"],
    ["tools/license_lint.py"],
    ["tools/governance_doc_lint.py"],
]


def static_gates() -> list[list[str]]:
    return [list(g) for g in GATES]


def full_gates() -> list[list[str]]:
    return static_gates() + [runner.nested_cmd()]


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def head_sha(root: Path = ROOT) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=_git_env(),
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def check(root: Path = ROOT, gates: list[list[str]] | None = None) -> list[str]:
    """Run every gate against root; each failure names the head SHA."""
    problems: list[str] = []
    sha = head_sha(root)
    for gate in gates if gates is not None else full_gates():
        rc = subprocess.run(
            [sys.executable, *gate], cwd=root,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
        if rc != 0:
            problems.append(f"head {sha}: gate {' '.join(gate)} exited {rc}")
    return problems


def workflow_problems(wf: Path) -> list[str]:
    """The canary workflow must be pinned to main exactly and run the canary."""
    problems: list[str] = []
    if not wf.is_file():
        return ["canary workflow missing: .github/workflows/canary.yml"]
    data = yaml.safe_load(wf.read_text())
    if not isinstance(data, dict):
        return ["canary workflow must be a mapping"]
    on = data.get("on", data.get(True))  # PyYAML 1.1 parses bare `on` as True
    branches = ((on or {}).get("push") or {}).get("branches")
    if branches != ["main"]:
        problems.append(
            f"canary trigger must be on.push.branches == ['main'] exactly, "
            f"got {branches!r}"
        )
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or "canary" not in jobs:
        problems.append("canary workflow must define a 'canary' job")
    else:
        steps = (jobs["canary"] or {}).get("steps") or []
        runs = [s.get("run", "") for s in steps if isinstance(s, dict)]
        if not any("tools/canary.py" in r for r in runs):
            problems.append("canary job has no step running tools/canary.py")
    return problems


def main() -> int:
    problems = workflow_problems(WORKFLOW) + check()
    sha = head_sha()
    for p in problems:
        print(f"CANARY FAIL head {sha[:12]}: {p}")
    if problems:
        return 1
    print(f"CANARY OK head {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
