BENCH_ID = None  # replaced below with an evil str subclass


class EvilStr(str):
    def strip(self, *args):
        raise RuntimeError("seeded BENCH_ID crash")


BENCH_ID = EvilStr("evil-str")
THRESHOLD_MS = 1000.0


def run() -> int:
    return 1
