BENCH_ID = "verify-crash"
THRESHOLD_MS = 1000.0


def run() -> int:
    return 42


def verify(result: int) -> str | None:
    raise RuntimeError("seeded verifier crash")
