"""Seeded violation: unpinned extra export only (ruff- and type-clean)."""


def add(a: int, b: int) -> int:
    return a + b


class Mul:
    def __init__(self, factor: int) -> None:
        self.factor = factor

    def apply(self, x: int) -> int:
        return x * self.factor


def sneaky_extra() -> None:
    pass
