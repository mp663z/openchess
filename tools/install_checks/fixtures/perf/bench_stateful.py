BENCH_ID = "stateful"
THRESHOLD_MS = 1000.0

_calls = {"n": 0}


def run() -> int:
    _calls["n"] += 1
    return 42 if _calls["n"] == 1 else 41  # correct once, wrong when timed


def verify(result: int) -> str | None:
    return None if result == 42 else f"got {result}, expected 42"
