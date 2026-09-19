"""T0029: performance gate - benchmarks must stay under pinned thresholds.

A benchmark module defines BENCH_ID (nonempty unique string), THRESHOLD_MS
(finite positive number) and run(); it may define verify(result) ->
str | None. Every timed run's result is verified - a benchmark correct on
a warmup call but wrong when timed is caught. Duplicate or empty IDs,
non-finite or non-positive thresholds, and non-callable run/verify are
malformed and fail closed. A crash is a failure, never a skip.
Thresholds are generous order-of-magnitude guards, not micro-benchmarks.
"""

from __future__ import annotations

import importlib.util
import math
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


def _key_of(path: Path) -> str:
    """Identity for failure reporting: the module's BENCH_ID if it parses
    as a string at all, else the filename."""
    try:
        mod = _load(path)
        bench_id = getattr(mod, "BENCH_ID", None)
        if isinstance(bench_id, str) and bench_id.strip():
            return bench_id.strip()
    except Exception:
        pass
    return path.name


def _validate(mod, path: Path) -> str | None:
    bench_id = getattr(mod, "BENCH_ID", None)
    if not isinstance(bench_id, str) or not bench_id.strip():
        return "BENCH_ID must be a nonempty string"
    threshold = getattr(mod, "THRESHOLD_MS", None)
    if (isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold) or threshold <= 0):
        return "THRESHOLD_MS must be a finite positive number"
    if not callable(getattr(mod, "run", None)):
        return "run must be callable"
    verify = getattr(mod, "verify", None)
    if verify is not None and not callable(verify):
        return "verify must be callable when present"
    return None


def run_benchmarks(bench_dir: Path) -> list[str]:
    """Failures as '<id>: <class> (<detail>)', class one of malformed,
    duplicate, crash, wrong result, slow."""
    failures: list[str] = []
    benches = sorted(bench_dir.glob("bench_*.py"))
    if not benches:
        return [f"{bench_dir.name}: malformed (no benchmarks found)"]
    loaded = []
    for path in benches:
        key = _key_of(path)
        try:
            mod = _load(path)
        except Exception as e:
            failures.append(f"{key}: malformed (load: {type(e).__name__}: {e})")
            continue
        problem = _validate(mod, path)
        if problem is not None:
            failures.append(f"{key}: malformed ({problem})")
            continue
        loaded.append((key, mod))
    seen: dict[str, str] = {}
    for key, mod in loaded:
        if key in seen:
            failures.append(
                f"{key}: duplicate (BENCH_ID shadows another benchmark)")
        else:
            seen[key] = mod.__name__
    runnable = []
    duped = {f.split(":", 1)[0] for f in failures}
    for key, mod in loaded:
        if key in duped:
            continue  # fail closed: shadowed identities never run
        runnable.append((key, mod))
    for key, mod in runnable:
        threshold = float(mod.THRESHOLD_MS)
        verify = mod.verify if callable(getattr(mod, "verify", None)) else None
        try:
            samples = []
            results = []
            for _ in range(REPEATS):
                t0 = time.perf_counter()
                results.append(mod.run())
                samples.append((time.perf_counter() - t0) * 1000.0)
            if verify is not None:
                # Verification lives INSIDE the crash boundary: a crashing
                # verifier is a crash failure, never an escaping exception.
                wrong = []
                for r in results:
                    v = verify(r)
                    if v is None:
                        continue
                    if not (isinstance(v, str) and v.strip()):
                        wrong.append(
                            "verify must return None or a nonempty string")
                    else:
                        wrong.append(v)
        except Exception as e:  # a crash is a failure, never a skip
            failures.append(f"{key}: crash ({type(e).__name__}: {e})")
            continue
        if verify is not None and wrong:
            failures.append(f"{key}: wrong result ({wrong[0]})")
            continue
        median = sorted(samples)[len(samples) // 2]
        if median > threshold:
            failures.append(f"{key}: slow ({median:.0f}ms > {threshold:.0f}ms)")
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
