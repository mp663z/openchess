"""T0029: performance gate - benchmarks must stay under pinned thresholds.

A benchmark module defines BENCH_ID, THRESHOLD_MS and run(); it may define
verify(result) -> str | None for a deterministic correctness check. The
gate runs each benchmark 3 times and fails on: wrong result, median over
threshold, or a crash (exception). Thresholds are generous (order-of-
magnitude guards), not micro-benchmarks - they catch real regressions
without flaking CI.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH_DIR = ROOT / "tools" / "perf_benchmarks"
REPEATS = 3


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"bench_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load benchmark {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_benchmarks(bench_dir: Path) -> list[str]:
    failures: list[str] = []
    benches = sorted(bench_dir.glob("bench_*.py"))
    if not benches:
        return [f"no benchmarks found in {bench_dir}"]
    for path in benches:
        try:
            mod = _load(path)
            bench_id = str(mod.BENCH_ID)
            threshold = float(mod.THRESHOLD_MS)
            run = mod.run
            verify = getattr(mod, "verify", None)
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            failures.append(f"{path.name}: malformed benchmark ({e})")
            continue
        try:
            samples = []
            result = run()  # warmup + result under test
            for _ in range(REPEATS):
                t0 = time.perf_counter()
                run()
                samples.append((time.perf_counter() - t0) * 1000.0)
        except Exception as e:  # a crash is a failure, never a skip
            failures.append(f"{bench_id}: crash ({type(e).__name__}: {e})")
            continue
        if verify is not None:
            problem = verify(result)
            if problem is not None:
                failures.append(f"{bench_id}: wrong result ({problem})")
                continue
        median = sorted(samples)[len(samples) // 2]
        if median > threshold:
            failures.append(
                f"{bench_id}: slow ({median:.0f}ms > {threshold:.0f}ms)"
            )
    return failures


def main() -> int:
    failures = run_benchmarks(BENCH_DIR)
    for f in failures:
        print(f"FAIL {f}")
    if failures:
        return 1
    print("OK performance gate: benchmarks under thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
