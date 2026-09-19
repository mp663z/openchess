BENCH_ID = "evil-getattr"
THRESHOLD_MS = 1000.0


def run() -> int:
    return 1


def __getattr__(name):  # PEP 562: attribute access itself is hostile
    raise RuntimeError(f"seeded __getattr__ crash on {name}")
