import time

BENCH_ID = "slow"
THRESHOLD_MS = 50.0


def run() -> int:
    time.sleep(0.3)
    return 1
