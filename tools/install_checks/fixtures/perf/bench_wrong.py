BENCH_ID = "wrong"
THRESHOLD_MS = 1000.0


def run() -> int:
    return 41


def verify(result: int) -> str | None:
    if result != 42:
        return f"got {result}, expected 42"
    return None
