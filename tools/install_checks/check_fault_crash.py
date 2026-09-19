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

import contextlib
import os
import sys
import tempfile
import time
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
        out = fi.run_under(
            _cmd(str(FIXTURES / "immediate_pass.py")), "kill", timeout=5)
        if out.kind != "pass":
            problems.append(f"kill: immediate pass classified {out.kind}")
        out = fi.run_under(
            _cmd(str(FIXTURES / "immediate_reject.py")), "kill", timeout=5)
        if out.kind != "reject":
            problems.append(f"kill: immediate reject classified {out.kind}")
        out = fi.run_under(
            _cmd(str(FIXTURES / "sigterm_swallow.py")), "kill", timeout=5)
        if out.kind != "crash" or "swallowed" not in out.detail:
            problems.append(f"kill: swallowed SIGTERM classified {out.kind}")

        # Group-kill probe: a gate whose forked child holds the pipes must
        # still die at the timeout - the kill has to reach the process
        # group, not just the direct child.
        pidfile = Path(tempfile.mkdtemp()) / "child.pid"
        os.environ["FAULT_CHILD_PIDFILE"] = str(pidfile)
        try:
            started = time.monotonic()
            out = fi.run_under(
                _cmd(str(FIXTURES / "spawn_child_ignore_term.py")),
                "kill", timeout=2)
            elapsed = time.monotonic() - started
        finally:
            os.environ.pop("FAULT_CHILD_PIDFILE", None)
        if out.kind != "crash":
            problems.append(f"group-kill: classified {out.kind}")
        if elapsed > 6:
            problems.append(
                f"group-kill: took {elapsed:.1f}s for a 2s timeout - "
                "a pipe-holding child survived")
        child_gone = False
        if pidfile.exists():
            child_pid = int(pidfile.read_text().strip())
            for _ in range(30):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    child_gone = True
                    break
                time.sleep(0.1)
        if not child_gone:
            problems.append("group-kill: forked child survived the kill")

        # Mutation probe: with group signaling reverted to direct-process
        # signaling, the probe above MUST fail - otherwise it proves
        # nothing about the group kill.
        def _direct_only(proc, sig):
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(sig)

        original = fi._signal_group
        fi._signal_group = _direct_only
        try:
            pidfile2 = Path(tempfile.mkdtemp()) / "child.pid"
            os.environ["FAULT_CHILD_PIDFILE"] = str(pidfile2)
            try:
                started = time.monotonic()
                fi.run_under(
                    _cmd(str(FIXTURES / "spawn_child_ignore_term.py")),
                    "kill", timeout=2)
                mutated_elapsed = time.monotonic() - started
            finally:
                os.environ.pop("FAULT_CHILD_PIDFILE", None)
        finally:
            fi._signal_group = original
        survived = False
        if pidfile2.exists():
            child_pid = int(pidfile2.read_text().strip())
            try:
                os.kill(child_pid, 0)
                survived = True
                os.kill(child_pid, 9)  # clean up the mutation-run survivor
            except ProcessLookupError:
                pass
        if not (survived or mutated_elapsed > 6):
            problems.append(
                "mutation: direct-only signaling was NOT detected - the "
                "group-kill probe proves nothing")
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
