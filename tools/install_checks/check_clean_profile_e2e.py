"""T0026: clean-profile E2E check - gates must pass with zero
user-machine state, and the scrubber must actually isolate.

Good mode: the scrubbed environment provably drops tokens/git context and
moves HOME, and a real gate (dag verify) passes inside it.
Violation mode: leak probes - gates that PASS only by reading leaked
profile state (GH_TOKEN/GIT_* env, the real global git config) must FAIL
inside the clean profile. If the scrubber leaks, a probe passes and the
harness fails.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from tools import profile_e2e
from tools.install_checks import CheckError

CHECK_ID = "T0026"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "profile"
PROBES = ("leaky_env_gate.py", "leaky_gitconfig_gate.py")


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
    leaked = []
    for probe in PROBES:
        r = profile_e2e.run_clean_profile(
            [sys.executable, str(FIXTURES / probe)], timeout=30)
        if r.returncode == 0:
            leaked.append(probe)  # probe passed => the profile leaked
    if leaked:
        return  # harness FAILS: profile leak let a probe pass
    raise CheckError("all leak probes failed inside the clean profile")
