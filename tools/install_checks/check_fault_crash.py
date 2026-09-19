"""T0030: fault/crash check - gates must fail cleanly, and the harness
must classify outcomes honestly.

Good mode: a real gate (dag verify) classifies as pass normally, and as
pass-or-reject (never crash/hang) under no-home and readonly-home faults;
a SIGTERM mid-run is classified as a crash (the kill mechanism works).
Violation mode: a hanging gate is killed at the timeout and classified
crash; a gate that swallows a fault and exits 0 is flagged when a reject
was expected; a gate dying with exit 3 is classified crash. Any
misclassification means the harness cannot tell green from broken.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tools import fault_inject as fi
from tools.install_checks import CheckError

CHECK_ID = "T0030"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fault"
REAL_GATE = ["tools/dag.py", "verify"]


def _cmd(*args: str) -> list[str]:
    return [sys.executable, *args]


def run(mode: str) -> None:
    if mode == "good":
        problems = []
        out = fi.run_under(_cmd(*REAL_GATE))
        if out.kind != "pass":
            problems.append(f"normal: {out.kind} ({out.detail})")
        for fault in ("no-home", "readonly-home"):
            out = fi.run_under(_cmd(*REAL_GATE), fault)
            if out.kind == "crash":
                problems.append(f"{fault}: crash ({out.detail})")
        out = fi.run_under(
            _cmd("-c", "import time; time.sleep(30)"), "kill", timeout=5)
        if out.kind != "crash":
            problems.append("kill: SIGTERM not classified as crash")
        if problems:
            raise CheckError("; ".join(problems))
        return
    bad = []
    out = fi.run_under(_cmd(str(FIXTURES / "hang_gate.py")), timeout=2)
    if out.kind != "crash" or "timeout" not in out.detail:
        bad.append(f"hang_gate: {out.kind}")
    out = fi.run_under(_cmd(str(FIXTURES / "swallow_gate.py")))
    if fi.expect(out, "reject") is None:
        bad.append("swallow_gate: silent accept not flagged")
    out = fi.run_under(_cmd(str(FIXTURES / "crash_gate.py")))
    if out.kind != "crash":
        bad.append(f"crash_gate: {out.kind}")
    if bad:
        return  # harness FAILS: a fault/crash escaped classification
    raise CheckError("hang killed, silent accept flagged, crash classified")
