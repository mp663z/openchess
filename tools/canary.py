"""Post-merge canary (T0039): after every merge, the full gate must be
re-run against main HEAD and any failure must name the head SHA.

The canary workflow (.github/workflows/canary.yml) must equal the pinned
structure EXACTLY: trigger, job, step order, action versions, install and
setup commands, the exact `python tools/canary.py` invocation. An echoed
lookalike command, a disabled job (`if:`), continue-on-error, a custom
shell, an extra trigger or any other drift fails the pin. Editing the
workflow deliberately requires updating the pin in the same change.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.branch_guard import first_diff  # noqa: E402
from tools.install_checks import runner  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "canary.yml"

# The merge-relevant gate set, re-run verbatim against the checked-out HEAD.
# full_gates() (direct `python tools/canary.py`, e.g. the canary workflow)
# appends the quarantine-aware test gate and the nested install-check runner
# (checks that shell the runner are excluded, or the canary would recurse
# into itself). static_gates() omits the test gate: under the install-check
# runner the Tests step / an enclosing test_gate run already covers it, and
# including it would recurse (test_gate -> pytest -> runner -> canary).
GATES = [
    ["tools/dag.py", "verify"],
    ["tools/dag_reconcile.py"],
    ["tools/evidence_lint.py"],
    ["tools/license_audit.py"],
    ["tools/license_lint.py"],
    ["tools/governance_doc_lint.py"],
]
TEST_GATE = ["tools/test_gate.py"]


def static_gates() -> list[list[str]]:
    return [list(g) for g in GATES]


def full_gates() -> list[list[str]]:
    return [list(TEST_GATE)] + static_gates() + [runner.nested_cmd()]


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def head_sha(root: Path = ROOT) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=_git_env(),
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


# a hung gate is a failed gate. Sized from the measured test-gate time
# (ci.yml "Tests" = tools/test_gate.py). History: 7m09s and 7m46s at
# 13ed690 after the contract-parse cache (#211), when this was 900s.
# At bb3c047 (after the batch A fuzz/fault batteries) the same step took
# 16m56s and 17m04s on the GitHub runner, and the canary gate timed out
# at 900s. Aligned with test_gate's own PYTEST_TIMEOUT_S (1800s): a hang
# still fails, and the canary no longer reds before CI's own bound would.
GATE_TIMEOUT_S = 1800


def check(root: Path = ROOT, gates: list[list[str]] | None = None,
          timeout: int = GATE_TIMEOUT_S) -> list[str]:
    """Run every gate against root; each failure names the head SHA."""
    problems: list[str] = []
    sha = head_sha(root)
    for gate in gates if gates is not None else full_gates():
        try:
            rc = subprocess.run(
                [sys.executable, *gate], cwd=root, timeout=timeout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode
        except subprocess.TimeoutExpired:
            problems.append(
                f"head {sha}: gate {' '.join(gate)} timed out after {timeout}s"
            )
            continue
        if rc != 0:
            problems.append(f"head {sha}: gate {' '.join(gate)} exited {rc}")
    return problems


# Pinned canary workflow: the parsed .github/workflows/canary.yml must
# equal this structure exactly (PyYAML 1.1 parses the bare `on` key as True).
EXPECTED_WORKFLOW = {
    "name": "Canary",
    True: {"push": {"branches": ["main"]}},
    "jobs": {
        "canary": {
            "runs-on": "ubuntu-latest",
            "steps": [
                {"uses": "actions/checkout@v4",
                 "with": {"fetch-depth": 0}},
                {"uses": "actions/setup-python@v5",
                 "with": {"python-version": "3.12"}},
                {"name": "Install dev dependencies",
                 "run": "pip install -r requirements-dev.txt"},
                {"name": "Install git hooks", "run": "bash tools/setup.sh"},
                {"name": "Post-merge canary", "run": "python tools/canary.py"},
            ],
        },
    },
}


def workflow_problems(wf: Path) -> list[str]:
    """Whole-structure pin: the canary workflow must match EXPECTED_WORKFLOW."""
    if not wf.is_file():
        return ["canary workflow missing: .github/workflows/canary.yml"]
    data = yaml.safe_load(wf.read_text())
    diff = first_diff(EXPECTED_WORKFLOW, data, "canary")
    if diff:
        return [f"canary workflow does not match the pinned structure: {diff}"]
    return []


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
