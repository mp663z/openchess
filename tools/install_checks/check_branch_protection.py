"""T0036: protected branch check - the local protection machinery (pre-push
hook, CI workflow, policy file) must be installed, pinned and biting.

Good mode: tools/branch_guard.py verifies the real repo - hooks path pinned,
pre-push hook executable with the pinned gate commands in exact order, CI
workflow carrying the pinned steps in exact order, policy schema-exact with
required_checks naming real workflow jobs.
Violation mode: eight seeded fixture trees - hook missing a gate, hook not
executable, gates reordered, CI workflow missing a pinned step, policy
required_check naming a nonexistent job, policy wrong-type ref, setup.sh
missing, setup.sh missing the pinned install line - each caught for its own
reason.
"""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path

import yaml

from tools import branch_guard
from tools.install_checks import CheckError

CHECK_ID = "T0036"

HOOK = """#!/bin/bash
set -e
cd "$(git rev-parse --show-toplevel)"
"$(dirname "$0")/tree-guard.sh"
if [ -d .venv ]; then . .venv/bin/activate; fi
ruff check .
pytest -q
python tools/dag.py verify
python tools/evidence_lint.py
python tools/license_audit.py
python tools/license_lint.py
python tools/governance_doc_lint.py
"""

CI = {
    "name": "CI",
    "on": {"push": None, "pull_request": None},
    "jobs": {
        "build-test-lint": {
            "runs-on": "ubuntu-latest",
            "steps": [{"name": n, "run": "true"} for n in
                      branch_guard.REQUIRED_CI_STEPS],
        },
    },
}

POLICY = {"protected_ref": "main",
          "required_checks": ["CI / build-test-lint"]}


SETUP = ("#!/bin/bash\nset -e\ncd \"$(git rev-parse --show-toplevel)\"\n"
         + branch_guard.REQUIRED_SETUP_LINE + "\n")


def _fixture(hook=HOOK, executable=True, ci=CI, policy=POLICY,
             setup=SETUP) -> Path:
    td = tempfile.mkdtemp()
    root = Path(td)
    (root / ".githooks").mkdir(parents=True)
    h = root / ".githooks" / "pre-push"
    h.write_text(hook)
    if executable:
        h.chmod(h.stat().st_mode | stat.S_IXUSR)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(yaml.safe_dump(ci))
    (root / "data").mkdir()
    (root / "data" / "branch-protection.yaml").write_text(yaml.safe_dump(policy))
    if setup is not None:
        (root / "tools").mkdir()
        s = root / "tools" / "setup.sh"
        s.write_text(setup)
        s.chmod(s.stat().st_mode | stat.S_IXUSR)
    return root


CASES = {
    "hook missing a gate": dict(
        hook=HOOK.replace("pytest -q\n", ""),
        expect="pinned gate commands",
    ),
    "hook not executable": dict(
        executable=False,
        expect="not executable",
    ),
    "hook gates reordered": dict(
        hook=HOOK.replace("ruff check .\npytest -q", "pytest -q\nruff check ."),
        expect="pinned gate commands",
    ),
    "ci missing a pinned step": dict(
        ci={**CI, "jobs": {"build-test-lint": {"runs-on": "ubuntu-latest",
            "steps": [{"name": n, "run": "true"} for n in
                      branch_guard.REQUIRED_CI_STEPS if n != "Tests"]}}},
        expect="pinned steps",
    ),
    "policy names a nonexistent job": dict(
        policy={**POLICY, "required_checks": ["CI / no-such-job"]},
        expect="matches no real workflow job",
    ),
    "policy wrong protected_ref type": dict(
        policy={**POLICY, "protected_ref": 42},
        expect="protected_ref must be a non-empty string",
    ),
    "setup.sh missing": dict(
        setup=None,
        expect="no documented fresh-clone install step",
    ),
    "setup.sh missing the pinned line": dict(
        setup="#!/bin/bash\ngit config core.hooksPath hooks\n",
        expect="does not contain exactly",
    ),
}


def run(mode: str) -> None:
    if mode == "good":
        problems = branch_guard.verify()
        if problems:
            raise CheckError(f"real repo branch guard: {problems}")
        return
    uncaught = []
    for label, spec in CASES.items():
        expect = spec.pop("expect")
        root = _fixture(**spec)
        problems = branch_guard.verify(root, hooks_path=".githooks")
        if not any(expect in p for p in problems):
            uncaught.append(f"{label}: expected {expect!r}, got {problems}")
    if uncaught:
        return  # harness FAILS: a protection defect escaped
    raise CheckError("all seeded protection defects caught")
