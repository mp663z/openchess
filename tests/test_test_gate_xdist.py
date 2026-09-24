"""tools/test_gate.py under pytest-xdist: collection and quarantine checks
run once before any worker, quarantine deselection is unchanged, and a
non-quarantined failure or a crashed worker reds the gate."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools import test_gate

Q = {"test_id": "tests/test_flaky.py::test_bad", "reason": "seeded flake",
     "added": "2026-09-01", "expires": "2099-01-01"}

# every fixture test proves it ran on an xdist worker
_ON_WORKER = "import os\n\n\ndef _worker():\n    assert os.environ.get('PYTEST_XDIST_WORKER')\n\n\n"


def _tree(root: Path, files: dict[str, str], quarantine: list) -> Path:
    (root / "tests").mkdir()
    for name, body in files.items():
        (root / "tests" / name).write_text(_ON_WORKER + body)
    (root / "data").mkdir()
    (root / "data" / "flake-quarantine.yaml").write_text(
        yaml.safe_dump({"tests": quarantine}))
    return root


@pytest.fixture(autouse=True)
def _not_on_a_worker(monkeypatch):
    # when this file itself runs under xdist, the fixture subprocesses must
    # not inherit the outer worker's markers, or the worker proof is vacuous
    for key in ("PYTEST_XDIST_WORKER", "PYTEST_XDIST_WORKER_COUNT",
                "PYTEST_XDIST_TESTRUNUID"):
        monkeypatch.delenv(key, raising=False)


OK = {"test_ok.py": "def test_ok():\n    _worker()\n"}
BAD = {"test_flaky.py": "def test_bad():\n    _worker()\n    assert False\n"}
CRASH = {"test_crash.py": "def test_crash():\n    _worker()\n    os._exit(3)\n"}


def test_gate_runs_parallel_with_loadfile():
    assert test_gate.XDIST_ARGS == ("-n", "auto", "--dist", "loadfile",
                                    "--max-worker-restart", "0")


def test_collection_runs_once_before_the_parallel_run(monkeypatch, tmp_path):
    calls = []
    real = subprocess.run

    def spy(cmd, *a, **k):
        calls.append(list(cmd))
        return real(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    root = _tree(tmp_path, {**OK, **BAD}, [Q])
    assert test_gate.gate_problems(root) == []
    assert len(calls) == 2
    collect, run = calls
    assert "--collect-only" in collect and "-n" not in collect
    assert "--collect-only" not in run
    i = run.index("-n")
    assert run[i:i + 6] == ["-n", "auto", "--dist", "loadfile",
                            "--max-worker-restart", "0"]
    assert run[run.index("--deselect") + 1] == Q["test_id"]


def test_quarantined_failure_is_deselected_under_xdist(tmp_path):
    assert test_gate.gate_problems(_tree(tmp_path, {**OK, **BAD}, [Q])) == []


def test_non_quarantined_failure_reds_the_gate_under_xdist(tmp_path):
    problems = test_gate.gate_problems(_tree(tmp_path, {**OK, **BAD}, []))
    assert problems and "test gate failed" in problems[0]


def test_worker_proof_is_live(tmp_path):
    # a fixture test outside xdist fails its worker assertion, so the pass
    # above really ran on workers
    root = _tree(tmp_path, OK, [])
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:xdist"],
                         cwd=root, capture_output=True, text=True)
    assert out.returncode != 0


def test_worker_crash_reds_the_gate(tmp_path):
    problems = test_gate.gate_problems(_tree(tmp_path, {**OK, **CRASH}, []))
    assert problems and "test gate failed" in problems[0]


def test_worker_crash_fails_fast_with_a_single_worker(tmp_path, monkeypatch):
    # -n auto resolves to 1 on a 1-vCPU host; without --max-worker-restart 0
    # a crashed lone worker hangs the run until the gate timeout
    args = list(test_gate.XDIST_ARGS)
    args[args.index("auto")] = "1"
    monkeypatch.setattr(test_gate, "XDIST_ARGS", tuple(args))
    problems = test_gate.gate_problems(_tree(tmp_path, {**OK, **CRASH}, []),
                                       timeout=60)
    assert problems and "test gate failed" in problems[0], problems
