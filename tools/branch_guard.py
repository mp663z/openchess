"""Protected branch (T0036): the local branch-protection machinery must be
installed, pinned and biting.

Three sources are reconciled against data/branch-protection.yaml:
  1. .githooks/pre-push: the local gate - must exist, be executable, and its
     effective script (shebang + every non-comment, non-blank line) must
     equal the pinned script EXACTLY. A prepended "exit 0", a reordered or
     replaced gate, or any extra executable line fails.
  2. .github/workflows/ci.yml: the remote gate - the parsed workflow must
     equal the pinned structure EXACTLY: step names AND commands, action
     versions, the CLA gate's condition and env, everything. A step whose
     run is swapped for an echo, an added conditional or continue-on-error,
     a custom shell, a changed action version - all fail. Editing CI
     deliberately requires updating the pin in the same change.
  3. the policy file itself: strict schema, and every required_checks entry
     must name a real "<workflow> / <job>" found in the workflows.

The remote GitHub branch-protection settings cannot be verified hermetically;
evidence records a one-time `gh api` fetch for human review. No claim beyond.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "data" / "branch-protection.yaml"

# The documented fresh-clone install step; tools/setup.sh must carry it
# verbatim so any clone (CI, clean verifier, a new contributor) can install
# the machinery hermetically.
REQUIRED_SETUP_LINE = "git config core.hooksPath .githooks"

REQUIRED_HOOK_SHEBANG = "#!/bin/bash"

# Pinned pre-push script: every non-comment, non-blank line, exact order,
# exact content. Whole-file pin: nothing executable may be added, removed,
# or reordered without updating this pin in the same change.
REQUIRED_HOOK_LINES = [
    "set -e",
    'cd "$(git rev-parse --show-toplevel)"',
    '"$(dirname "$0")/tree-guard.sh"',
    "if [ -d .venv ]; then . .venv/bin/activate; fi",
    "ruff check .",
    "pytest -q",
    "python tools/dag.py verify",
    "python tools/evidence_lint.py",
    "python tools/license_audit.py",
    "python tools/license_lint.py",
    "python tools/governance_doc_lint.py",
]

# Pinned CI workflow: the parsed .github/workflows/ci.yml must equal this
# structure exactly (PyYAML 1.1 parses the bare `on` key as True).
EXPECTED_CI = {
    "name": "CI",
    True: {"push": None, "pull_request": None},
    "jobs": {
        "build-test-lint": {
            "runs-on": "ubuntu-latest",
            "steps": [
                {"uses": "actions/checkout@v4"},
                {"uses": "actions/setup-python@v5",
                 "with": {"python-version": "3.12"}},
                {"name": "Install dev dependencies",
                 "run": "pip install -r requirements-dev.txt"},
                {"name": "Release lock is current",
                 "run": "python tools/lock_release.py --check"},
                {"name": "Lint", "run": "ruff check ."},
                {"name": "Tests", "run": "pytest -q"},
                {"name": "DAG integrity", "run": "python tools/dag.py verify"},
                {"name": "Evidence contract",
                 "run": "python tools/evidence_lint.py"},
                {"name": "License audit", "run": "python tools/license_audit.py"},
                {"name": "License lint (T0008)",
                 "run": "python tools/license_lint.py"},
                {"name": "Governance docs lint",
                 "run": "python tools/governance_doc_lint.py"},
                {"name": "Install git hooks", "run": "bash tools/setup.sh"},
                {"name": "Install checks (good passes, seeded violation caught)",
                 "run": "python -m tools.install_checks.runner"},
                {"name": "CLA gate",
                 "if": "github.event_name == 'pull_request'",
                 "env": {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"},
                 "run": "python tools/cla_check.py"},
                {"name": "Docs parse",
                 "run": 'python -c "import json,yaml,pathlib; '
                        "json.load(open('tasks/dag.json')); "
                        "yaml.safe_load(open('scope/v0.1.yaml')); "
                        "print('docs ok', "
                        "pathlib.Path('README.md').stat().st_size)\""},
            ],
        },
    },
}


def first_diff(expected, actual, path: str = "") -> str | None:
    """First structural difference between two parsed documents, as a path."""
    if type(expected) is not type(actual):
        return f"{path or '<root>'}: type {type(expected).__name__} != {type(actual).__name__}"
    if isinstance(expected, dict):
        for k in expected:
            if k not in actual:
                return f"{path}.{k!r} missing"
            d = first_diff(expected[k], actual[k], f"{path}.{k!r}")
            if d:
                return d
        for k in actual:
            if k not in expected:
                return f"{path}.{k!r} unexpected"
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: length {len(expected)} != {len(actual)}"
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            d = first_diff(e, a, f"{path}[{i}]")
            if d:
                return d
        return None
    if expected != actual:
        return f"{path}: {expected!r} != {actual!r}"
    return None


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def head_sha(root: Path = ROOT) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=_git_env(),
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def load_policy(path: Path) -> list[str]:
    """Strict policy schema: exact keys, exact types."""
    problems: list[str] = []
    if not path.is_file():
        return [f"policy file missing: {path}"]
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        return ["policy must be a mapping"]
    extra = set(data) - {"protected_ref", "required_checks"}
    if extra:
        problems.append(f"policy: unknown keys {sorted(extra)}")
    ref = data.get("protected_ref")
    if not isinstance(ref, str) or not ref.strip():
        problems.append(f"policy: protected_ref must be a non-empty string, got {ref!r}")
    checks = data.get("required_checks")
    if not isinstance(checks, list) or not checks:
        problems.append("policy: required_checks must be a non-empty list")
    elif not all(isinstance(c, str) and " / " in c for c in checks):
        problems.append("policy: every required_check must be '<workflow> / <job>'")
    return problems


def verify_setup(root: Path) -> list[str]:
    problems: list[str] = []
    setup = root / "tools" / "setup.sh"
    if not setup.is_file():
        return ["tools/setup.sh missing: no documented fresh-clone install step"]
    if not (setup.stat().st_mode & stat.S_IXUSR):
        problems.append("tools/setup.sh is not executable")
    if REQUIRED_SETUP_LINE not in setup.read_text().splitlines():
        problems.append(
            f"tools/setup.sh does not contain exactly: {REQUIRED_SETUP_LINE}"
        )
    return problems


def verify_hook(root: Path, hooks_path: str | None = None) -> list[str]:
    problems: list[str] = []
    if hooks_path is None:
        out = subprocess.run(
            ["git", "config", "core.hooksPath"], cwd=root, env=_git_env(),
            capture_output=True, text=True,
        )
        hooks_path = out.stdout.strip()
        if out.returncode != 0 or not hooks_path:
            return ["core.hooksPath is not configured (pre-push gate not installed)"]
    if hooks_path != ".githooks":
        problems.append(f"core.hooksPath must be exactly '.githooks', got {hooks_path!r}")
    hook = root / hooks_path / "pre-push"
    if not hook.is_file():
        return problems + [f"pre-push hook missing at {hook}"]
    mode = hook.stat().st_mode
    if not (mode & stat.S_IXUSR):
        problems.append("pre-push hook is not executable")
    lines = hook.read_text().splitlines()
    if not lines or lines[0] != REQUIRED_HOOK_SHEBANG:
        problems.append(
            f"pre-push hook shebang must be exactly {REQUIRED_HOOK_SHEBANG!r}"
        )
    effective = [
        ln for ln in lines[1:]
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if effective != REQUIRED_HOOK_LINES:
        diff = first_diff(REQUIRED_HOOK_LINES, effective, "hook")
        problems.append(f"pre-push hook does not match the pinned script: {diff}")
    return problems


def _workflow_jobs(root: Path) -> dict[str, list[dict]]:
    """workflow name -> list of jobs (parsed); tolerates the YAML 1.1 `on`."""
    out: dict[str, list[dict]] = {}
    wf_dir = root / ".github" / "workflows"
    if not wf_dir.is_dir():
        return out
    for f in sorted(wf_dir.glob("*.yml")):
        data = yaml.safe_load(f.read_text())
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        jobs = data.get("jobs")
        if isinstance(name, str) and isinstance(jobs, dict):
            out.setdefault(name, []).extend(
                {"id": j, "spec": spec} for j, spec in jobs.items()
            )
    return out


def verify_ci(root: Path) -> list[str]:
    ci = root / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        return ["CI workflow missing: .github/workflows/ci.yml"]
    data = yaml.safe_load(ci.read_text())
    diff = first_diff(EXPECTED_CI, data, "ci")
    if diff:
        return [f"CI workflow does not match the pinned structure: {diff}"]
    return []


def verify_policy_links(root: Path) -> list[str]:
    problems: list[str] = []
    data = yaml.safe_load((root / "data" / "branch-protection.yaml").read_text())
    if not isinstance(data, dict) or not isinstance(data.get("required_checks"), list):
        return []  # schema problems already reported by load_policy
    jobs = _workflow_jobs(root)
    real = {f"{wf} / {j['id']}" for wf, specs in jobs.items() for j in specs}
    for req in data["required_checks"]:
        if isinstance(req, str) and req not in real:
            problems.append(f"policy required_check {req!r} matches no real workflow job")
    return problems


def verify(root: Path = ROOT, hooks_path: str | None = None) -> list[str]:
    problems = load_policy(root / "data" / "branch-protection.yaml")
    problems += verify_setup(root)
    problems += verify_hook(root, hooks_path)
    problems += verify_ci(root)
    problems += verify_policy_links(root)
    return problems


def main() -> int:
    problems = verify()
    sha = head_sha()
    for p in problems:
        print(f"FAIL head {sha[:12]}: {p}")
    if problems:
        return 1
    print(f"OK protected branch: local machinery pinned and installed (head {sha[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
