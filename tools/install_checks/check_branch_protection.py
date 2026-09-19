"""T0036: protected branch check - the local protection machinery (pre-push
hook, CI workflow, policy file) must be installed, pinned and biting.

Good mode: tools/branch_guard.py verifies the real repo - hooks path pinned,
pre-push hook executable and matching the pinned script exactly (shebang +
every effective line), CI workflow matching the pinned structure exactly
(names, commands, action versions, the CLA gate's condition and env), policy
schema-exact with required_checks naming real workflow jobs.
Violation mode: seeded fixture trees - hook missing a gate, hook not
executable, gates reordered, early-exit line prepended, inert CI step
(echo), CI conditional, continue-on-error, custom shell, policy naming a
nonexistent job, wrong-type protected_ref, setup.sh missing, setup.sh
missing the pinned line - each caught for its own reason.
"""

from __future__ import annotations

import copy
import stat
import tempfile
from pathlib import Path

import yaml

from tools import branch_guard
from tools.install_checks import CheckError

CHECK_ID = "T0036"


def _hook_text(lines) -> str:
    return "\n".join(
        ["#!/bin/bash", "# Run the same gates CI runs, locally, before pushing."]
        + list(lines)
    ) + "\n"


HOOK = _hook_text(branch_guard.REQUIRED_HOOK_LINES)

SETUP = ("#!/bin/bash\nset -e\ncd \"$(git rev-parse --show-toplevel)\"\n"
         + branch_guard.REQUIRED_SETUP_LINE + "\n")

POLICY = {"protected_ref": "main",
          "required_checks": ["CI / build-test-lint"]}


def _ci(mutator=None) -> dict:
    ci = copy.deepcopy(branch_guard.EXPECTED_CI)
    if mutator:
        mutator(ci)
    return ci


def _fixture(hook=HOOK, executable=True, ci=None, policy=POLICY,
             setup=SETUP) -> Path:
    td = tempfile.mkdtemp()
    root = Path(td)
    (root / ".githooks").mkdir(parents=True)
    h = root / ".githooks" / "pre-push"
    h.write_text(hook)
    if executable:
        h.chmod(h.stat().st_mode | stat.S_IXUSR)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text(
        yaml.safe_dump(ci if ci is not None else _ci()))
    (root / "data").mkdir()
    (root / "data" / "branch-protection.yaml").write_text(yaml.safe_dump(policy))
    if setup is not None:
        (root / "tools").mkdir()
        s = root / "tools" / "setup.sh"
        s.write_text(setup)
        s.chmod(s.stat().st_mode | stat.S_IXUSR)
    return root


def _steps(ci: dict) -> list:
    return ci["jobs"]["build-test-lint"]["steps"]


def _step(ci: dict, name: str) -> dict:
    return next(s for s in _steps(ci) if s.get("name") == name)


def _reordered(lines):
    lines = list(lines)
    i, j = lines.index("ruff check ."), lines.index("pytest -q")
    lines[i], lines[j] = lines[j], lines[i]
    return lines


CASES = {
    "hook missing a gate": dict(
        hook=_hook_text([ln for ln in branch_guard.REQUIRED_HOOK_LINES
                         if ln != "pytest -q"]),
        expect="does not match the pinned script",
    ),
    "hook not executable": dict(
        executable=False,
        expect="not executable",
    ),
    "hook gates reordered": dict(
        hook=_hook_text(_reordered(branch_guard.REQUIRED_HOOK_LINES)),
        expect="does not match the pinned script",
    ),
    "early-exit prepended": dict(
        hook=_hook_text(["exit 0"] + branch_guard.REQUIRED_HOOK_LINES),
        expect="does not match the pinned script",
    ),
    "inert CI step (echo)": dict(
        ci=_ci(lambda c: _step(c, "Tests").update(run="echo tests skipped")),
        expect="does not match the pinned structure",
    ),
    "CI conditional added": dict(
        ci=_ci(lambda c: _step(c, "Tests").update(**{"if": "false"})),
        expect="does not match the pinned structure",
    ),
    "CI continue-on-error": dict(
        ci=_ci(lambda c: _step(c, "Lint").update(**{"continue-on-error": True})),
        expect="does not match the pinned structure",
    ),
    "CI custom shell": dict(
        ci=_ci(lambda c: _step(c, "Lint").update(shell="pwsh")),
        expect="does not match the pinned structure",
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
        expect="does not match the pinned script",
    ),
    "setup.sh early exit": dict(
        setup="#!/bin/bash\nexit 0\n" + SETUP.split("\n", 1)[1],
        expect="does not match the pinned script",
    ),
    "setup.sh inert echo": dict(
        setup=SETUP.replace("git config core.hooksPath .githooks",
                            "echo git config core.hooksPath .githooks"),
        expect="does not match the pinned script",
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
