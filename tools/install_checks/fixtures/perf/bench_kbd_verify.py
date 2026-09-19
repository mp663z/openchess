BENCH_ID = "kbd-verify"
THRESHOLD_MS = 1000.0


def run() -> int:
    return 42


def verify(result: int) -> str | None:
    raise KeyboardInterrupt
