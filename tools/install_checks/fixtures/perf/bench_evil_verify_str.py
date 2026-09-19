BENCH_ID = "evil-verify-str"
THRESHOLD_MS = 1000.0


class EvilStr(str):
    def __format__(self, spec: str) -> str:
        raise RuntimeError("seeded report-interpolation crash")


def run() -> int:
    return 1


def verify(result: int) -> str | None:
    return EvilStr("plausible-looking error string")
