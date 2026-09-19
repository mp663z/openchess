"""T0025: integration check - the governance CLI chain executed as REAL
subprocesses against a REAL copy of the repo.

Good mode: a tracked-file copy of the repo passes every CLI
(license_lint, dag verify, evidence_lint, rights_audit) with rc 0.
Violation mode: the same copy with one targeted tamper per CLI -
LICENSE text altered, dag.json duplicated task, an evidence file's bytes
flipped, a required source removed from the rights manifest - each CLI
must exit NONZERO on its own tamper. Subprocess env is stripped of GIT_*
variables (a leaked GIT_DIR once corrupted a real repo during testing).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.install_checks import CheckError

CHECK_ID = "T0025"
ROOT = Path(__file__).resolve().parent.parent.parent

CLIS = [
    "tools/license_lint.py",
    "tools/evidence_lint.py",
    "tools/rights_audit.py",
]


def _copy_repo(dst: Path) -> None:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    ).stdout.splitlines()
    for rel in tracked:
        src, out = ROOT / rel, dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONPATH"] = str(ROOT)  # tools imports; CLIs use repo-relative ROOT
    return env


def _run_cli(repo: Path, cli: str, *args: str) -> int:
    return subprocess.run(
        [sys.executable, cli, *args], cwd=repo,
        capture_output=True, text=True, env=_env(), timeout=120,
    ).returncode


def _tampers(repo: Path) -> dict[str, str]:
    """Apply each tamper to a FRESH copy; return {name: cli-that-must-fail}."""
    return {}


def _tampered_copies(base_copy: Path, tmp: Path) -> dict[str, tuple[Path, str]]:
    cases = {}

    c = tmp / "tamper_license"
    shutil.copytree(base_copy, c)
    with open(c / "LICENSE", "a") as f:
        f.write("\nNOT THE AGPL ANYMORE\n")
    cases["LICENSE altered"] = (c, "tools/license_lint.py")

    c = tmp / "tamper_dag"
    shutil.copytree(base_copy, c)
    board = json.loads((c / "tasks/dag.json").read_text())
    board["tasks"].append(dict(board["tasks"][0]))
    (c / "tasks/dag.json").write_text(json.dumps(board))
    cases["dag.json duplicated task"] = (c, "dag")

    c = tmp / "tamper_evidence"
    shutil.copytree(base_copy, c)
    ev = c / "evidence" / "T0033.md"
    text = ev.read_text()
    # corrupt the recorded merge SHA (first hex digit flips 0<->1):
    # evidence_lint cross-checks it against the board's done_sha
    import re as _re
    m = _re.search(r"Recorded merge SHA on main: ([0-9a-f]{40})", text)
    sha = m.group(1)
    forged = ("1" if sha[0] != "1" else "0") + sha[1:]
    ev.write_text(text.replace(sha, forged))
    cases["evidence merge SHA forged"] = (c, "tools/evidence_lint.py")

    c = tmp / "tamper_rights"
    shutil.copytree(base_copy, c)
    rights = c / "data/datasets/public-source-rights.yaml"
    lines = rights.read_text().splitlines()
    rights.write_text("\n".join(lines[: len(lines) // 2]))  # truncate manifest
    cases["rights manifest truncated"] = (c, "tools/rights_audit.py")

    return cases


def run(mode: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = tmp / "clean"
        _copy_repo(base)
        if mode == "good":
            bad = []
            for cli in CLIS:
                if _run_cli(base, cli) != 0:
                    bad.append(f"{cli} failed on a clean repo copy")
            if _run_cli(base, "tools/dag.py", "verify") != 0:
                bad.append("dag verify failed on a clean repo copy")
            if bad:
                raise CheckError(str(bad))
            return
        uncaught = []
        for label, (copy, cli) in _tampered_copies(base, tmp).items():
            args = ("verify",) if cli == "dag" else ()
            rc = _run_cli(copy, "tools/dag.py" if cli == "dag" else cli, *args)
            if rc == 0:
                uncaught.append(f"{label}: {cli} exited 0 on tampered repo")
        if uncaught:
            return  # harness FAILS: a tamper escaped the CLI chain
        raise CheckError("all repo tampers caught by the real CLI chain")
