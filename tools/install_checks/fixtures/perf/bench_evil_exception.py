BENCH_ID = "evil-exception"
THRESHOLD_MS = 1000.0


class EvilException(Exception):
    def __str__(self) -> str:
        raise RuntimeError("seeded unprintable-exception crash")


def run() -> int:
    raise EvilException("you cannot print me")
