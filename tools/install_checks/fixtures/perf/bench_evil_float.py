BENCH_ID = "evil-float"


class EvilFloat(float):
    def __float__(self) -> float:
        raise RuntimeError("seeded conversion crash")


THRESHOLD_MS = EvilFloat(50.0)


def run() -> int:
    return 1
