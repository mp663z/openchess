"""T2236 - Calibration discipline.

Threshold/weights are calibrated by rating and line frequency on the train
set only. Held-out repertoires stay untouched until the final evaluation;
the split is by repertoire, never by row, and seeded for reproducibility.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    train: frozenset[str]
    held_out: frozenset[str]
    seed: int

    def check_no_leak(self, calibration_repertoires: set[str]) -> None:
        """Fail loud if any held-out repertoire touched calibration."""
        leak = self.held_out & calibration_repertoires
        if leak:
            raise CalibrationLeakError(f"held-out repertoires used in calibration: {sorted(leak)}")


class CalibrationLeakError(Exception):
    pass


def repertoire_split(
    repertoire_ids: list[str], *, seed: int, held_out_fraction: float = 0.2
) -> Split:
    """Deterministic split by repertoire id (stable across runs for a seed)."""
    if not 0.0 < held_out_fraction < 1.0:
        raise ValueError("held_out_fraction must be in (0, 1)")
    keyed = sorted(
        repertoire_ids,
        key=lambda rid: hashlib.sha256(f"{seed}:{rid}".encode()).hexdigest(),
    )
    n_held = max(1, round(len(keyed) * held_out_fraction)) if keyed else 0
    held = frozenset(keyed[:n_held])
    train = frozenset(keyed[n_held:])
    return Split(train=train, held_out=held, seed=seed)
