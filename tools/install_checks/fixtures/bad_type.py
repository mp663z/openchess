"""Seeded violation: type error only (ruff-clean, API pinned)."""


def add(a: int, b: int) -> int:
    return a + b


class Mul:
    def __init__(self, factor: int) -> None:
        self.factor = factor

    def apply(self, x: int) -> int:
        return x + "oops"  # int + str: mypy catches this
