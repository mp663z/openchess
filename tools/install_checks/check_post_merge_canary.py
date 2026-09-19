"""T0039: post-merge canary check - after every merge to main the full gate
re-runs against HEAD and any failure names the head SHA.

Good mode: tools/canary.py's workflow pin validates the real
.github/workflows/canary.yml (on.push.branches == ["main"] exactly, canary
job runs tools/canary.py) and the canary's gate set passes against the real
repo HEAD.
Violation mode: (a) trigger widened beyond main; (b) no step running the
canary; (c) no 'canary' job; (d) a failing gate against a fixture repo HEAD
must be reported naming the head SHA.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import yaml

from tools import canary
from tools.install_checks import CheckError, runner

CHECK_ID = "T0039"

WF = {
    "name": "Canary",
    "on": {"push": {"branches": ["main"]}},
    "jobs": {"canary": {"runs-on": "ubuntu-latest",
                        "steps": [{"run": "python tools/canary.py"}]}},
}


def _wf(text: str) -> Path:
    f = Path(tempfile.mkdtemp()) / "canary.yml"
    f.write_text(text)
    return f


CASES = {
    "trigger widened": (
        {**WF, "on": {"push": {"branches": ["main", "dev"]}}},
        "exactly",
    ),
    "trigger not main": (
        {**WF, "on": {"push": {"branches": ["release"]}}},
        "exactly",
    ),
    "no canary step": (
        {**WF, "jobs": {"canary": {"runs-on": "ubuntu-latest",
                                   "steps": [{"run": "echo hi"}]}}},
        "no step running tools/canary.py",
    ),
    "no canary job": (
        {**WF, "jobs": {"build": {"runs-on": "ubuntu-latest", "steps": []}}},
        "must define a 'canary' job",
    ),
}


def _fixture_repo() -> Path:
    td = tempfile.mkdtemp()
    root = Path(td)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q"], cwd=root, env=env, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root,
                   env=env, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root,
                   env=env, check=True)
    (root / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=root, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, env=env,
                   check=True)
    return root


def run(mode: str) -> None:
    if mode == "good":
        problems = canary.workflow_problems(canary.WORKFLOW)
        if problems:
            raise CheckError(f"real repo canary workflow: {problems}")
        # the enclosing runner covers the nested-runner gate; run the rest
        problems = canary.check(gates=canary.static_gates())
        if problems:
            raise CheckError(f"real repo canary gates: {problems}")
        full = canary.full_gates()
        if not any("tools.install_checks.runner" in g for g in full):
            raise CheckError("canary gates must include the nested runner")
        if "T0039" not in runner.INNER_EXCLUDE:
            raise CheckError("nested runner must exclude T0039 (recursion)")
        return
    uncaught = []
    for label, (wf, expect) in CASES.items():
        problems = canary.workflow_problems(_wf(yaml.safe_dump(wf)))
        if not any(expect in p for p in problems):
            uncaught.append(f"{label}: expected {expect!r}, got {problems}")
    # failing gate names the head SHA
    root = _fixture_repo()
    sha = canary.head_sha(root)
    problems = canary.check(
        root, gates=[["-c", "import sys; sys.exit(1)"]])
    if not any(sha in p for p in problems):
        uncaught.append(f"failing gate did not name head sha: {problems}")
    if uncaught:
        return  # harness FAILS: a canary defect escaped
    raise CheckError("all seeded canary defects caught")
