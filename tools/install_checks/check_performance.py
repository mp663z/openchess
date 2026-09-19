"""T0029: performance check - benchmarks must stay under pinned thresholds.

Good mode: every real benchmark in tools/perf_benchmarks passes (correct
result, median under threshold, no crash).
Violation mode: each planted fixture benchmark must be caught with its
intended failure class (slow / crash / wrong result) - a harness that
stops measuring, stops verifying, or swallows crashes lets them through.
"""

from __future__ import annotations

from pathlib import Path

from tools import perf_gate
from tools.install_checks import CheckError

CHECK_ID = "T0029"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "perf"
EXPECTED = {
    "slow": "slow",
    "crash": "crash",
    "wrong": "wrong result",
}


def run(mode: str) -> None:
    if mode == "good":
        failures = perf_gate.run_benchmarks(perf_gate.BENCH_DIR)
        if failures:
            raise CheckError(f"real benchmarks failed: {failures}")
        return
    failures = perf_gate.run_benchmarks(FIXTURES)
    by_class = {}
    for f in failures:
        for cls in ("slow", "crash", "wrong result"):
            if f"({cls}" in f or f": {cls}" in f:
                by_class[f.split(":")[0]] = cls
    for bench_id, cls in EXPECTED.items():
        got = by_class.get(bench_id)
        if got != cls:
            return  # harness FAILS: a seeded defect escaped its class
    if len(failures) != len(EXPECTED):
        return  # harness FAILS: unexpected extra/missing failures
    raise CheckError("all seeded benchmark defects caught by class")
