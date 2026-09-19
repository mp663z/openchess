"""T0029: performance check - benchmarks must stay under pinned thresholds.

Good mode: every real benchmark in tools/perf_benchmarks passes (every
timed result verified, median under threshold, no crash).
Violation mode: each planted fixture benchmark must be caught with its
intended failure class: slow, crash, wrong result, stateful (correct
warmup, wrong timed), malformed (NaN threshold), duplicate (shadowed
BENCH_ID). A harness that stops measuring, verifies only a warmup, accepts
NaN thresholds, or lets duplicate IDs shadow each other fails here.
"""

from __future__ import annotations

from pathlib import Path

from tools import perf_gate
from tools.install_checks import CheckError

CHECK_ID = "T0029"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "perf"
CLASSES = ("wrong result", "malformed", "duplicate", "crash", "slow")
EXPECTED = {
    "slow": "slow",
    "crash": "crash",
    "wrong": "wrong result",
    "stateful": "wrong result",
    "nan": "malformed",
    "dup": "duplicate",
    "verify-crash": "crash",
    "verify-badreturn": "wrong result",
    "evil-float": "malformed",
    "bench_evil_str.py": "malformed",
    "bench_evil_getattr.py": "malformed",
    "evil-verify-str": "wrong result",
    "evil-exception": "crash",
    "exit-zero": "crash",
    "kbd-verify": "crash",
}


def _by_class(failures: list[str]) -> dict[str, str]:
    out = {}
    for f in failures:
        key, _, rest = f.partition(": ")
        cls = next((c for c in CLASSES if rest.startswith(c)), None)
        out[key.strip()] = cls
    return out


def run(mode: str) -> None:
    if mode == "good":
        failures = perf_gate.run_benchmarks(perf_gate.BENCH_DIR)
        if failures:
            raise CheckError(f"real benchmarks failed: {failures}")
        return
    by_class = _by_class(perf_gate.run_benchmarks(FIXTURES))
    for bench_id, cls in EXPECTED.items():
        if by_class.get(bench_id) != cls:
            return  # harness FAILS: a seeded defect escaped its class
    if set(by_class) != set(EXPECTED):
        return  # harness FAILS: unexpected extra or missing failures
    raise CheckError("all seeded benchmark defects caught by class")
