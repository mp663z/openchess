"""Clean verifier (T0038): a gate verdict that depends on leaked environment
or in-tree state is not a verdict. HEAD is cloned into a fresh directory
(committed content only - untracked and ignored files never cross) and the
gate is re-run there with a scrubbed environment; the clean-copy verdict
must match the in-tree verdict. The clone keeps history so git-dependent
gates (DAG reconcile's history anchor) run identically.

Environment scrubbing is allowlist-based: only the variables a subprocess
needs to run at all survive. GIT_*, credentials, CI flags, venv vars and
PYTHONPATH never reach the clean copy.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.install_checks import runner  # noqa: E402

ALLOWED_ENV = {
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE",
    "SYSTEMROOT", "TEMP", "TMP", "TMPDIR",
}




def scrub_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k in ALLOWED_ENV}


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}



def fresh_copy(root: Path, dest: Path) -> None:
    """Clone root and detach at its exact HEAD sha: committed content only,
    full history (git-dependent gates need it), no untracked state.

    origin/main inside the clone is PINNED to the source's origin/main sha
    read before cloning: the source's main could otherwise move between
    clone and verification, drifting the history anchor mid-run. If the
    pinned sha is not present in the clone the update fails closed."""
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, env=_git_env(),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    main_probe = subprocess.run(
        ["git", "rev-parse", "--verify", "origin/main"], cwd=root,
        env=_git_env(), capture_output=True, text=True,
    )
    main_sha = main_probe.stdout.strip() if main_probe.returncode == 0 else None
    subprocess.run(
        ["git", "clone", "--quiet", str(root), str(dest)], env=_git_env(),
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", sha], cwd=dest, env=_git_env(),
        check=True,
    )
    if main_sha:
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/main", main_sha],
            cwd=dest, env=_git_env(), check=True,
        )
        pinned = subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=dest, env=_git_env(),
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if pinned != main_sha:
            raise RuntimeError(
                f"origin/main pin drifted: wanted {main_sha[:12]}, got {pinned[:12]}"
            )


TIMEOUT_S = 600  # a hung gate is a failed gate


def _run(cmd: list[str], cwd: Path, env: dict[str, str],
         timeout: int = TIMEOUT_S) -> int:
    try:
        return subprocess.run(
            [sys.executable, *cmd], cwd=cwd, env=env, timeout=timeout,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
    except subprocess.TimeoutExpired:
        return 124  # timeout is a failure, in-tree or clean copy


def verify(
    root: Path = ROOT,
    cmd: list[str] | None = None,
    env: dict[str, str] | None = None,
    rc_tree: int | None = None,
    timeout: int = TIMEOUT_S,
) -> list[str]:
    """Run cmd in-tree (current env) and in a clean copy (scrubbed env).

    A non-zero clean-copy run, a failed in-tree run, or any divergence
    between the two verdicts is a problem. The default cmd is the nested
    install-check runner (checks that shell the runner are excluded, or
    the verification would recurse). rc_tree may be supplied by a caller
    that already knows the in-tree verdict (the enclosing runner does).
    """
    cmd = cmd if cmd is not None else runner.nested_cmd()
    env = dict(os.environ) if env is None else dict(env)
    problems: list[str] = []
    if rc_tree is None:
        rc_tree = _run(cmd, root, env, timeout)
    if rc_tree != 0:
        problems.append(f"in-tree gate run failed (exit {rc_tree})")
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "clean"
        try:
            fresh_copy(root, dest)
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            return problems + [f"clean-copy preparation failed: {exc}"]
        setup_rc = subprocess.run(
            ["bash", "tools/setup.sh"], cwd=dest,
            env=scrub_env(env),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
        if setup_rc != 0:
            problems.append("documented setup (tools/setup.sh) failed in the clean copy")
        rc_clean = _run(cmd, dest, scrub_env(env), timeout)
    if rc_clean != 0:
        problems.append(
            f"clean-copy gate run failed (exit {rc_clean}): the in-tree pass "
            "depends on leaked environment or untracked state"
        )
    if (rc_tree == 0) != (rc_clean == 0):
        problems.append(
            f"verdict diverges: in-tree exit {rc_tree}, clean-copy exit {rc_clean}"
        )
    return problems


def main() -> int:
    problems = verify()
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        return 1
    print("OK clean verifier: clean-copy verdict matches in-tree verdict")
    return 0


if __name__ == "__main__":
    sys.exit(main())
