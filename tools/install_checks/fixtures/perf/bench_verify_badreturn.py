BENCH_ID = "verify-badreturn"
THRESHOLD_MS = 1000.0


def run() -> int:
    return 42


def verify(result: int):
    return 5  # must be None or a nonempty string
