"""T0038: clean verifier check - the gate must reach the same verdict in a
fresh clone with a scrubbed environment as it does in-tree.

Good mode: tools/clean_verify.py clones HEAD into a temp dir (committed
content only, full history) and re-runs the install-check runner there with
an allowlist-scrubbed environment; divergence or a clean-copy failure is a
gate failure.
Violation mode: (a) a poisoned environment (GIT_DIR, GH_TOKEN, PYTHONPATH,
CI, VIRTUAL_ENV, arbitrary) must be stripped to the allowlist by scrub_env;
(b) a fixture repo whose gate passes only when a leaked env var is present
must be reported as a clean-copy failure/divergence, never passed.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from tools import clean_verify
from tools.install_checks import CheckError, runner

CHECK_ID = "T0038"


def _fixture_repo(script: str) -> Path:
    td = tempfile.mkdtemp()
    root = Path(td)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "init", "-q"], cwd=root, env=env, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root,
                   env=env, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root,
                   env=env, check=True)
    (root / "ok.py").write_text(script)
    subprocess.run(["git", "add", "ok.py"], cwd=root, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, env=env,
                   check=True)
    return root


def run(mode: str) -> None:
    if mode == "good":
        # the enclosing runner IS the in-tree verdict (rc 0 when we run);
        # re-run only the clean-copy side
        problems = clean_verify.verify(rc_tree=0)
        if problems:
            raise CheckError(f"real repo clean verify: {problems}")
        if "T0038" not in runner.INNER_EXCLUDE:
            raise CheckError("nested runner must exclude T0038 (recursion)")
        return
    uncaught = []
    # (a) poisoned env scrubbed to the allowlist
    poisoned = {
        "PATH": os.environ.get("PATH", ""),
        "GIT_DIR": "/tmp/evil",
        "GIT_WORK_TREE": "/tmp/evil",
        "GH_TOKEN": "secret",
        "PYTHONPATH": "/tmp/evil",
        "CI": "true",
        "VIRTUAL_ENV": "/tmp/venv",
        "ANYTHING_ELSE": "x",
    }
    scrubbed = clean_verify.scrub_env(poisoned)
    if scrubbed != {"PATH": poisoned["PATH"]}:
        uncaught.append(f"scrub_env leaked: {sorted(scrubbed)}")
    # (b) a hung gate is a failed gate (bounded timeout, both sides)
    root = _fixture_repo("import time\ntime.sleep(30)\n")
    problems = clean_verify.verify(root, cmd=["ok.py"], env=poisoned, timeout=1)
    if not any("exit 124" in p for p in problems):
        uncaught.append(f"timeout not treated as failure: {problems}")
    # (c) $HOME-dependent pass must diverge under the fresh clean-copy HOME
    root = _fixture_repo(
        "import os, sys, pathlib\n"
        "sys.exit(0 if (pathlib.Path(os.environ['HOME']) / 'marker').exists()"
        " else 1)\n"
    )
    fake_home = Path(tempfile.mkdtemp())
    (fake_home / "marker").write_text("x")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(fake_home)}
    problems = clean_verify.verify(root, cmd=["ok.py"], env=env)
    if not any("clean-copy" in p or "diverges" in p for p in problems):
        uncaught.append(f"HOME-dependent pass not caught: {problems}")
    # (d) leaked-env-dependent pass must be caught in the clean copy
    root = _fixture_repo(
        'import os, sys\nsys.exit(0 if os.environ.get("LEAK_OK") == "1" else 1)\n'
    )
    env = {"PATH": os.environ.get("PATH", ""), "LEAK_OK": "1",
           "GIT_DIR": "/tmp/poison"}
    problems = clean_verify.verify(root, cmd=["ok.py"], env=env)
    if not any("clean-copy" in p or "diverges" in p for p in problems):
        uncaught.append(f"leaked-env pass not caught: {problems}")
    if uncaught:
        return  # harness FAILS: a clean-verifier defect escaped
    raise CheckError("all seeded clean-verifier defects caught")
