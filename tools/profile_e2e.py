"""T0026: clean-profile E2E - gates must pass with zero user-machine state.

scrub_env() builds an environment with a fresh temporary HOME, no git
identity sources, no tokens, no GIT_* hook context - proving the gates do
not silently depend on the developer's profile. A leaked profile is a
finding, never a convenience.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DROP_PREFIXES = ("GIT_", "GH_", "GITHUB_")
# Only true process essentials survive. Dependency-affecting state
# (PYTHONPATH, VIRTUAL_ENV) and user identity (USER/LOGNAME/SHELL) are
# user-machine state: dropped, and asserted absent by leaked_vars.
KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL",
        "HOME")
FORBIDDEN_RETAINED = ("PYTHONPATH", "VIRTUAL_ENV", "USER", "LOGNAME",
                      "SHELL", "CI")


def scrub_env(base: dict[str, str], home: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in base.items():
        if any(key.startswith(p) for p in DROP_PREFIXES):
            continue
        if key in KEEP:
            env[key] = value
    env["HOME"] = str(home)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


# Isolation markers the scrubber itself installs - not leaks.
SCRUB_SET = {"GIT_CONFIG_NOSYSTEM": "1"}
SCRUB_SET_PATHS = {"GIT_CONFIG_GLOBAL"}


def leaked_vars(env: dict[str, str], real_home: str) -> list[str]:
    leaks = []
    for key in env:
        if key in FORBIDDEN_RETAINED:
            leaks.append(key)
            continue
        if not any(key.startswith(p) for p in DROP_PREFIXES):
            continue
        if key in SCRUB_SET and env[key] == SCRUB_SET[key]:
            continue
        if key in SCRUB_SET_PATHS and env[key] == os.devnull:
            continue
        leaks.append(key)
    if env.get("HOME") == real_home and real_home:
        leaks.append("HOME(unscrubbed)")
    return leaks


def run_clean_profile(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    real_home = os.environ.get("HOME", "")
    with tempfile.TemporaryDirectory(prefix="clean-profile-") as home:
        env = scrub_env(dict(os.environ), Path(home))
        leaks = leaked_vars(env, real_home)
        if leaks:
            raise RuntimeError(f"profile scrub leaked: {leaks}")
        return subprocess.run(
            cmd, cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=timeout,
        )


def main() -> int:
    cmd = [sys.executable, "tools/dag.py", "verify"]
    r = run_clean_profile(cmd)
    if r.returncode != 0:
        print(f"FAIL clean-profile E2E: {cmd} exited {r.returncode}: "
              f"{r.stderr.strip()[-300:]}")
        return 1
    print("OK clean-profile E2E: gates pass with zero user-machine state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
