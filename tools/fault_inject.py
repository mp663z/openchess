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


def run_under(cmd: list[str], fault: str | None = None,
              timeout: float = 10.0) -> Outcome:
    with tempfile.TemporaryDirectory(prefix="fault-") as tmp_s:
        tmp = Path(tmp_s)
        env = _fault_env(fault, tmp)
        if fault == "kill":
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            time.sleep(0.3)
            if proc.poll() is not None:
                # Already exited before the signal: classify the ACTUAL
                # outcome - a fast pass is a pass, never a fake crash.
                _, err = proc.communicate()
                return classify_rc(proc.returncode, err or "")
            proc.send_signal(signal.SIGTERM)
            try:
                _, err = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return Outcome("crash", "ignored SIGTERM, killed at timeout")
            if proc.returncode != -signal.SIGTERM:
                # Signal delivered but the process exited on its own
                # terms: classify what actually happened.
                return classify_rc(proc.returncode, err or "")
            return Outcome("crash", "terminated by SIGTERM")
        try:
            r = subprocess.run(
                cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                timeout=timeout)
        except subprocess.TimeoutExpired:
            return Outcome("crash", f"timeout after {timeout}s (killed)")
        return classify_rc(r.returncode, r.stderr)


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
