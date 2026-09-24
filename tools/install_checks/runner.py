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
from tools.install_checks import CheckError, CheckSkipped, in_ci


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


# Checks that themselves invoke the runner (clean verifier, post-merge
# canary) must be excluded from any NESTED runner invocation: without the
# exclusion, runner -> check -> runner -> check would recurse without bound.
INNER_EXCLUDE = ("T0038", "T0039")


def nested_cmd() -> list[str]:
    """Runner invocation for use INSIDE another gate. The exclusion travels
    as a flag so the nested runner discovers its OWN check set (committed
    content may differ from the in-tree one) minus the recursive checks."""
    return ["-m", "tools.install_checks.runner",
            "--exclude", ",".join(INNER_EXCLUDE)]


def run_all(only: set[str] | None = None,
            exclude: set[str] | None = None,
            skipped: list[str] | None = None) -> list[str]:
    """Failures, one per deviation. A CheckSkipped in local good mode is
    appended to SKIPPED and is not a failure; without a SKIPPED list, under
    CI, or in violation mode it is one."""
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
        if exclude and check_id in exclude:
            continue
        for mode, expect_ok in (("good", True), ("violation", False)):
            try:
                mod.run(mode)
                ok = True
            except CheckSkipped as skip:
                if in_ci() or mode != "good":
                    failures.append(f"{check_id} ({name}) mode={mode}: "
                                    f"skip not allowed here: {skip}")
                elif skipped is None:
                    # fail closed: a caller that cannot report skips gets
                    # a failure, never a silent pass
                    failures.append(f"{check_id} ({name}) mode={mode}: "
                                    f"skip not recorded (no skipped list): {skip}")
                else:
                    skipped.append(f"{check_id} ({name}): {skip}")
                continue
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
    args = sys.argv[1:]
    exclude: set[str] = set()
    if args[:1] == ["--exclude"]:
        exclude = {x for x in args[1].split(",") if x}
        args = args[2:]
    only = set(args) or None
    skipped: list[str] = []
    failures = run_all(only, exclude, skipped)
    for s in skipped:
        print(f"SKIP {s}")
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
