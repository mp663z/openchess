"""Flake quarantine (T0037): a quarantined test is excluded from the
required gate set, but the quarantine file itself is gated strictly.

data/flake-quarantine.yaml holds entries {test_id, reason, added, expires}.
Every entry must be schema-exact, carry a non-empty reason, an unexpired
expiry, a unique test_id, and point at a test that actually exists in the
collected pytest suite - a stale or fabricated entry is a gate failure.
"""

from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUARANTINE = ROOT / "data" / "flake-quarantine.yaml"

ENTRY_KEYS = {"test_id", "reason", "added", "expires"}


def load(path: Path = QUARANTINE) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("tests"), list):
        raise ValueError("quarantine file must be a mapping with a 'tests' list")
    return data["tests"]


def validate(
    entries: list,
    collected: set[str],
    today: datetime.date | None = None,
) -> list[str]:
    """Schema + liveness validation; collected is the set of real node ids."""
    today = today or datetime.date.today()
    problems: list[str] = []
    seen: set[str] = set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            problems.append(f"entry {i}: must be a mapping, got {type(e).__name__}")
            continue
        extra = set(e) - ENTRY_KEYS
        missing = ENTRY_KEYS - set(e)
        if extra:
            problems.append(f"entry {i}: unknown keys {sorted(extra)}")
        if missing:
            problems.append(f"entry {i}: missing keys {sorted(missing)}")
            continue
        tid, reason = e["test_id"], e["reason"]
        if not isinstance(tid, str) or "::" not in tid:
            problems.append(f"entry {i}: test_id must be a pytest node id, got {tid!r}")
            continue
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"entry {i}: reason must be a non-empty string")
        dates: dict[str, datetime.date] = {}
        for field in ("added", "expires"):
            v = e[field]
            if isinstance(v, datetime.datetime):
                problems.append(
                    f"entry {i}: {field} must be an ISO date, got datetime {v!r}"
                )
            elif isinstance(v, datetime.date):
                dates[field] = v  # PyYAML parses ISO dates natively
            elif isinstance(v, str) and _is_iso(v):
                dates[field] = datetime.date.fromisoformat(v)
            else:
                problems.append(f"entry {i}: {field} must be an ISO date, got {v!r}")
        if "added" in dates and "expires" in dates:
            if dates["expires"] < dates["added"]:
                problems.append(
                    f"entry {i}: expires {dates['expires']} before added "
                    f"{dates['added']}"
                )
            if dates["added"] > today:
                problems.append(
                    f"entry {i}: added {dates['added']} is in the future"
                )
            if dates["expires"] < today:
                problems.append(
                    f"entry {i}: quarantine for {tid} expired {dates['expires']}"
                )
        if tid in seen:
            problems.append(f"entry {i}: duplicate quarantined test_id {tid}")
        seen.add(tid)
        if collected and tid not in collected:
            problems.append(f"entry {i}: quarantined test {tid} not in the suite")
    return problems


def _is_iso(s: str) -> bool:
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def collected_tests(root: Path = ROOT) -> set[str]:
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root, capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"pytest collection failed (exit {out.returncode}): "
            f"{out.stderr.strip()[-300:] or out.stdout.strip()[-300:]}"
        )
    return {
        line.strip() for line in out.stdout.splitlines()
        if "::" in line and not line.startswith(("=", "<"))
    }


def gate_set(collected: set[str], entries: list[dict]) -> set[str]:
    quarantined = {
        e["test_id"] for e in entries
        if isinstance(e, dict) and isinstance(e.get("test_id"), str)
    }
    return collected - quarantined


def main() -> int:
    entries = load()
    try:
        collected = collected_tests()
    except RuntimeError as exc:
        print(f"FAIL {exc}")
        return 1
    problems = validate(entries, collected)
    if not gate_set(collected, entries):
        problems.append("gate set is empty: every test is quarantined")
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        return 1
    print(
        f"OK flake quarantine: {len(entries)} quarantined, "
        f"{len(gate_set(collected, entries))} tests in the gate set"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
