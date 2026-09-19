BENCH_ID = "crash"
THRESHOLD_MS = 1000.0


def run() -> int:
    raise RuntimeError("seeded crash")
