"""Install-check harness (T0021+ series).

Each check is a module in this package exposing:
  CHECK_ID:    the DAG task id it satisfies (e.g. "T0021")
  run(mode):   "good" runs the check against the seeded good fixture,
               "violation" against the seeded violation fixture;
               returns None on the expected outcome, raises CheckError
               otherwise. A check may raise CheckSkipped instead, with a
               reason naming itself, only for local-environment gaps
               outside CI (GITHUB_ACTIONS != "true"); under CI it must
               raise CheckError.

CI runs every check in both modes: the good case must pass and the seeded
violation must be caught. A check that cannot catch its own violation is
not installed. Discovery is by filename (check_*.py) - no central registry,
so parallel lanes never conflict on a shared file.
"""

from __future__ import annotations


class CheckError(RuntimeError):
    pass


class CheckSkipped(RuntimeError):
    """Local-only skip: never raised when GITHUB_ACTIONS == "true"."""


def in_ci() -> bool:
    import os
    return os.environ.get("GITHUB_ACTIONS") == "true"
