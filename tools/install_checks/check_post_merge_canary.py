"""T0039: post-merge canary check - after every merge to main the full gate
re-runs against HEAD and any failure names the head SHA.

Good mode: tools/canary.py's workflow pin validates the real
.github/workflows/canary.yml (on.push.branches == ["main"] exactly, canary
job runs tools/canary.py) and the canary's gate set passes against the real
repo HEAD.
Violation mode: whole-structure pin attacks - echoed lookalike command,
disabled job (`if:`), extra trigger, wrong action version,
continue-on-error, removed canary step - plus a failing gate against a
fixture repo HEAD, which must be reported naming the head SHA.
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

def _wf_dict(mutator=None) -> dict:
    import copy
    wf = copy.deepcopy(canary.EXPECTED_WORKFLOW)
    if mutator:
        mutator(wf)
    return wf


def _wf(text: str) -> Path:
    f = Path(tempfile.mkdtemp()) / "canary.yml"
    f.write_text(text)
    return f


CASES = {
    "echo lookalike command": (
        _wf_dict(lambda w: w["jobs"]["canary"]["steps"][4].update(
            run="echo python tools/canary.py")),
        "does not match the pinned structure",
    ),
    "disabled job": (
        _wf_dict(lambda w: w["jobs"]["canary"].update(**{"if": "${{ false }}"})),
        "does not match the pinned structure",
    ),
    "extra trigger": (
        _wf_dict(lambda w: w[True].update(pull_request=None)),
        "does not match the pinned structure",
    ),
    "wrong action version": (
        _wf_dict(lambda w: w["jobs"]["canary"]["steps"][0].update(
            uses="actions/checkout@v3")),
        "does not match the pinned structure",
    ),
    "continue-on-error": (
        _wf_dict(lambda w: w["jobs"]["canary"]["steps"][4].update(
            **{"continue-on-error": True})),
        "does not match the pinned structure",
    ),
    "canary step removed": (
        _wf_dict(lambda w: w["jobs"]["canary"]["steps"].pop()),
        "does not match the pinned structure",
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
        problems = canary.workflow_problems(_wf(yaml.safe_dump(wf, sort_keys=False)))
        if not any(expect in p for p in problems):
            uncaught.append(f"{label}: expected {expect!r}, got {problems}")
    # failing gate names the head SHA
    root = _fixture_repo()
    sha = canary.head_sha(root)
    problems = canary.check(
        root, gates=[["-c", "import sys; sys.exit(1)"]])
    if not any(sha in p for p in problems):
        uncaught.append(f"failing gate did not name head sha: {problems}")
    # hung gate times out, naming the head SHA
    problems = canary.check(
        root, gates=[["-c", "import time; time.sleep(30)"]], timeout=1)
    if not any("timed out" in p and sha in p for p in problems):
        uncaught.append(f"hung gate not reported naming head sha: {problems}")
    if uncaught:
        return  # harness FAILS: a canary defect escaped
    raise CheckError("all seeded canary defects caught")
