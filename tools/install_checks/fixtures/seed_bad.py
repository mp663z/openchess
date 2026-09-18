"""Seeded violation for T0021: lint error + unpinned export."""

import os  # unused import (ruff F401)


def add(a: int, b: int) -> int:
    return a + b


class Mul:
    def __init__(self, factor: int) -> None:
        self.factor = factor

    def apply(self, x: int) -> int:
        return x * self.factor


def sneaky_extra() -> None:
    pass
