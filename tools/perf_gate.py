"""T0029: performance gate - benchmarks must stay under pinned thresholds.

A benchmark module defines BENCH_ID (exact str, nonempty, unique),
THRESHOLD_MS (exact int/float, finite, positive) and run(); it may define
verify(result) -> str | None. Module-supplied attributes are untrusted:
every touch (getattr, call, conversion) happens inside a crash boundary,
because PEP 562 module __getattr__ and subclassed values can raise on
mere access. Exact builtin types only - a subclass can override the very
methods validation calls. Every timed run's result is verified - a benchmark correct on
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


def _validate(mod) -> tuple[str, str | None]:
    """(identity key, problem). Every getattr on the untrusted module is
    inside the caller's crash boundary. Identity is the exact-str BENCH_ID
    when valid, else the module's load path name."""
    bench_id = getattr(mod, "BENCH_ID", None)  # may raise via __getattr__
    if type(bench_id) is not str or not bench_id.strip():
        return ("<unknown>", "BENCH_ID must be an exact nonempty str")
    key = bench_id.strip()
    threshold = getattr(mod, "THRESHOLD_MS", None)
    # Exact builtin numeric types only: a float/int subclass can raise in
    # __float__ during normalization, escaping the crash boundary.
    if (type(threshold) not in (int, float)
            or not math.isfinite(threshold) or threshold <= 0):
        return (key, "THRESHOLD_MS must be a finite positive exact int/float")
    if not callable(getattr(mod, "run", None)):
        return (key, "run must be callable")
    verify = getattr(mod, "verify", None)
    if verify is not None and not callable(verify):
        return (key, "verify must be callable when present")
    return (key, None)


def run_benchmarks(bench_dir: Path) -> list[str]:
    """Failures as '<id>: <class> (<detail>)', class one of malformed,
    duplicate, crash, wrong result, slow."""
    failures: list[str] = []
    benches = sorted(bench_dir.glob("bench_*.py"))
    if not benches:
        return [f"{bench_dir.name}: malformed (no benchmarks found)"]
    loaded = []
    for path in benches:
        try:
            # Load AND validate inside one crash boundary: every attribute
            # touch on the untrusted module can raise.
            mod = _load(path)
            key, problem = _validate(mod)
        except BaseException as e:
            try:
                detail = f"{type(e).__name__}: {e}"
            except BaseException:
                detail = "unprintable exception"
            failures.append(f"{path.name}: malformed ({detail})")
            continue
        if key == "<unknown>":
            key = path.name
        if problem is not None:
            failures.append(f"{key}: malformed ({problem})")
            continue
        loaded.append((key, mod))
    seen: set[str] = set()
    for key, _mod in loaded:
        if key in seen:
            failures.append(
                f"{key}: duplicate (BENCH_ID shadows another benchmark)")
        else:
            seen.add(key)
    runnable = []
    duped = {f.split(":", 1)[0] for f in failures}
    for key, mod in loaded:
        if key in duped:
            continue  # fail closed: shadowed identities never run
        runnable.append((key, mod))
    for key, mod in runnable:
        try:
            verify_attr = getattr(mod, "verify", None)
            verify = verify_attr if callable(verify_attr) else None
            threshold = float(mod.THRESHOLD_MS)  # inside the crash boundary
            samples = []
            results = []
            for _ in range(REPEATS):
                t0 = time.perf_counter()
                results.append(mod.run())
                samples.append((time.perf_counter() - t0) * 1000.0)
            if verify is not None:
                # Verification lives INSIDE the crash boundary: a crashing
                # verifier is a crash failure, never an escaping exception.
                # Verifier-RETURNED strings are untrusted too: an exact
                # builtin str only, or its overridden dunders attack the
                # report interpolation later.
                wrong = []
                for r in results:
                    v = verify(r)
                    if v is None:
                        continue
                    if type(v) is not str or not v.strip():
                        wrong.append(
                            "verify must return None or a nonempty exact str")
                    else:
                        wrong.append(v)
        except BaseException as e:  # a crash is a failure, never a skip
            # Even the exception object is hostile: its __str__ may raise.
            try:
                detail = f"{type(e).__name__}: {e}"
            except BaseException:
                detail = "unprintable exception"
            failures.append(f"{key}: crash ({detail})")
            continue
        if verify is not None and wrong:
            failures.append(f"{key}: wrong result ({wrong[0]})")
            continue
        median = sorted(samples)[len(samples) // 2]
        if median > threshold:
            failures.append(f"{key}: slow ({median:.0f}ms > {threshold:.0f}ms)")
    return failures


def main() -> int:
    bench_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else BENCH_DIR
    failures = run_benchmarks(bench_dir)
    for f in failures:
        print(f"FAIL {f}")
    if failures:
        return 1
    print("OK performance gate: benchmarks under thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
