"""T0026: clean-profile E2E check - gates must pass with zero
user-machine state, and the scrubber must actually isolate.

Good mode: the scrubbed environment provably drops tokens, git context,
dependency-affecting vars and user identity, moves HOME, and a real gate
(dag verify) passes inside it.
Violation mode: canary-seeded. Each probe is FIRST proven to fire against
a seeded hostile profile (canary GH_TOKEN, GIT_* var, HOME carrying a
.gitconfig with an identity) - a probe that cannot fire raw is broken and
fails the harness. Then the same seeded profile is scrubbed and each
probe must be blocked. A no-op scrubber passes the raw probes through and
fails here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tools import profile_e2e
from tools.install_checks import CheckError

CHECK_ID = "T0026"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "profile"
PROBES = ("leaky_env_gate.py", "leaky_gitconfig_gate.py")
ROOT = Path(__file__).resolve().parents[2]


def _seeded_profile(home: Path) -> dict[str, str]:
    """A hostile profile with canary leaks every probe must detect."""
    (home / ".gitconfig").write_text("[user]\n\tname = Canary Leak\n")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GH_TOKEN": "canary-token",
        "GIT_DIR": "/canary/gitdir",
        "USER": "canary-user",
        "PYTHONPATH": "/canary/pypath",
    }
    return env


def _run_raw(probe: str, env: dict[str, str]) -> int:
    r = subprocess.run(
        [sys.executable, str(FIXTURES / probe)], cwd=ROOT, env=env,
        capture_output=True, timeout=30)
    return r.returncode


def _run_scrubbed(probe: str, seeded: dict[str, str]) -> tuple[list[str], int]:
    """Scrub the seeded profile (not the process env) and run the probe.
    A leak is reported, never raised: in violation mode a raised
    CheckError reads as 'caught', which would reward a leaking scrubber."""
    home = Path(seeded["HOME"])
    env = profile_e2e.scrub_env(seeded, home / "scrubbed-home")
    env["HOME"] = str(home / "scrubbed-home")
    (home / "scrubbed-home").mkdir(exist_ok=True)
    leaks = profile_e2e.leaked_vars(env, str(home))
    if leaks:
        return leaks, -1
    r = subprocess.run(
        [sys.executable, str(FIXTURES / probe)], cwd=ROOT, env=env,
        capture_output=True, timeout=30)
    return [], r.returncode


def run(mode: str) -> None:
    if mode == "good":
        real_home = os.environ.get("HOME", "")
        with tempfile.TemporaryDirectory() as home:
            env = profile_e2e.scrub_env(dict(os.environ), Path(home))
            leaks = profile_e2e.leaked_vars(env, real_home)
            if leaks:
                raise CheckError(f"scrubber leaked: {leaks}")
            if env.get("HOME") != home:
                raise CheckError("scrubber did not move HOME")
        r = profile_e2e.run_clean_profile(
            [sys.executable, "tools/dag.py", "verify"])
        if r.returncode != 0:
            raise CheckError(
                f"dag verify failed in a clean profile: {r.stderr[-200:]}")
        return
    problems = []
    with tempfile.TemporaryDirectory() as canary_home:
        seeded = _seeded_profile(Path(canary_home))
        for probe in PROBES:
            if _run_raw(probe, seeded) != 0:
                # probe cannot detect the leak even raw: non-discriminating
                problems.append(f"{probe}: does not fire on a seeded leak")
                continue
            leaks, rc = _run_scrubbed(probe, seeded)
            if leaks:
                problems.append(f"{probe}: scrubber leaked {leaks}")
            elif rc == 0:
                problems.append(f"{probe}: leak survived the scrubber")
    if problems:
        return  # harness FAILS: probe broken or scrubber leaky
    raise CheckError("canary leaks fire raw and are blocked after scrubbing")
