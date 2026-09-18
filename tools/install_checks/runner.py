"""Run install checks in good/violation mode (T0021+ series).

Usage: python -m tools.install_checks.runner [CHECK_ID ...]
Every discovered check runs in both modes: good must pass, violation must
be caught. Exit 1 naming the check and mode on any deviation.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

from tools import install_checks
from tools.install_checks import CheckError


def discover() -> list[str]:
    return sorted(
        m.name
        for m in pkgutil.iter_modules(install_checks.__path__)
        if m.name.startswith("check_")
    )


def check_ids() -> dict[str, str]:
    ids: dict[str, str] = {}
    for name in discover():
        mod = importlib.import_module(f"tools.install_checks.{name}")
        if mod.CHECK_ID in ids:
            raise CheckError(
                f"duplicate CHECK_ID {mod.CHECK_ID}: {ids[mod.CHECK_ID]} and {name}"
            )
        ids[mod.CHECK_ID] = name
    return ids


def run_all(only: set[str] | None = None) -> list[str]:
    failures: list[str] = []
    known = check_ids()
    if only:
        unknown = sorted(only - set(known))
        if unknown:
            return [f"unknown check id(s): {unknown} (known: {sorted(known)})"]
    for name in discover():
        mod = importlib.import_module(f"tools.install_checks.{name}")
        check_id = mod.CHECK_ID
        if only and check_id not in only:
            continue
        for mode, expect_ok in (("good", True), ("violation", False)):
            try:
                mod.run(mode)
                ok = True
            except CheckError:
                ok = False
            if ok != expect_ok:
                outcome = "passed" if ok else "was caught"
                failures.append(
                    f"{check_id} ({name}) mode={mode}: {outcome}, "
                    f"expected {'pass' if expect_ok else 'catch'}"
                )
    return failures


def main() -> int:
    only = set(sys.argv[1:]) or None
    failures = run_all(only)
    for f in failures:
        print(f"FAIL {f}")
    if failures:
        return 1
    found = discover()
    if not found:
        print("FAIL no install checks discovered")
        return 1
    print(f"OK install checks: {len(found)} checks, good passes + violations caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
