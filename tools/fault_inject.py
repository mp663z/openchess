"""T0030: fault/crash harness - classify gate outcomes under faults.

Every target run classifies as exactly one of:
  pass   - exit 0
  reject - exit 1 with a named problem on stderr
  crash  - timeout kill, death by signal, exit code > 1, or a silent
           exit 1 (nothing on stderr: an undiagnosable failure is a crash)
A harness that cannot tell these apart turns real crashes into green CI.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Outcome:
    kind: str  # "pass" | "reject" | "crash"
    detail: str


def _fault_env(fault: str | None, tmp: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if fault == "no-home":
        env.pop("HOME", None)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
    elif fault == "readonly-home":
        ro = tmp / "ro-home"
        ro.mkdir(exist_ok=True)
        ro.chmod(0o555)
        env["HOME"] = str(ro)
    return env


def classify_rc(returncode: int, stderr: str) -> Outcome:
    if returncode == 0:
        return Outcome("pass", "")
    if returncode == 1 and stderr.strip():
        return Outcome("reject", stderr.strip()[-200:])
    if returncode == 1:
        return Outcome("crash", "exit 1 with empty stderr (undiagnosable)")
    if returncode < 0:
        return Outcome("crash", f"terminated by signal {-returncode}")
    return Outcome("crash", f"exit {returncode}")

def _popen_group(cmd: list[str], env: dict[str, str]) -> subprocess.Popen:
    """Spawn the target as a process-group leader (start_new_session) so
    kills reach the whole group - a gate's hung or pipe-holding children
    die with it instead of surviving the parent and stalling
    communicate() on inherited pipes. Limit: a child that calls setsid()
    itself escapes the group; that escape is documented, not hidden."""
    return subprocess.Popen(
        cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True)


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


def run_under(cmd: list[str], fault: str | None = None,
              timeout: float = 10.0) -> Outcome:
    with tempfile.TemporaryDirectory(prefix="fault-") as tmp_s:
        tmp = Path(tmp_s)
        env = _fault_env(fault, tmp)
        if fault == "kill":
            proc = _popen_group(cmd, env)
            time.sleep(0.3)
            if proc.poll() is not None:
                # Already exited before the signal: classify the ACTUAL
                # outcome - a fast pass is a pass, never a fake crash.
                _, err = proc.communicate()
                return classify_rc(proc.returncode, err or "")
            _signal_group(proc, signal.SIGTERM)
            try:
                _, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                _signal_group(proc, signal.SIGKILL)
                proc.communicate()
                return Outcome(
                    "crash",
                    "ignored SIGTERM, process group killed at timeout")
            if proc.returncode == -signal.SIGTERM:
                return Outcome("crash", "terminated by SIGTERM")
            # The harness injected termination while the process was LIVE:
            # swallowing it and exiting clean is a masked kill, never a
            # pass/reject.
            return Outcome(
                "crash",
                f"handled/swallowed SIGTERM and exited {proc.returncode}")
        proc = _popen_group(cmd, env)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGKILL)
            proc.communicate()
            return Outcome(
                "crash",
                f"timeout after {timeout}s (process group killed)")
        return classify_rc(proc.returncode, err or "")


def expect(outcome: Outcome, wanted: str) -> str | None:
    """None when the outcome matches; a problem string otherwise."""
    if outcome.kind == wanted:
        return None
    return f"expected {wanted}, got {outcome.kind} ({outcome.detail})"


def main() -> int:
    target = [sys.executable, "tools/secrets_scan.py"] \
        if (ROOT / "tools" / "secrets_scan.py").exists() \
        else [sys.executable, "tools/dag.py", "verify"]
    bad = []
    for fault in (None, "no-home", "readonly-home"):
        out = run_under(target, fault)
        if out.kind == "crash":
            bad.append(f"fault={fault}: {out.detail}")
    if bad:
        for b in bad:
            print(f"FAIL {b}")
        return 1
    print("OK fault/crash: target never crashes under environment faults")
    return 0


if __name__ == "__main__":
    sys.exit(main())
