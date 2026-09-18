"""Data/model rights audit (T2229 discipline, wrapped by install check T0033).

Fail-closed validation of data/datasets/public-source-rights.yaml: every
source needs url + license + boolean transformation_permission + a known
decision; decision=allow additionally requires an explicit transformation
grant and a captured, hash-pinned permission statement that recomputes.
Unknown decision values, missing terms and hash drift are all violations -
a source with unproven terms is excluded, never assumed.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RIGHTS = ROOT / "data/datasets/public-source-rights.yaml"

REQUIRED_SOURCES = {
    "lichess_standard_games",
    "lichess_puzzles",
    "lichess_evals",
    "twic",
    "lichess_broadcasts",
    "lichess_chess_openings",
    "lichess_public_studies",
    "user_own_cbh_files",
}
DECISIONS = {"allow", "fail_closed"}


def validate_sources(doc: dict) -> list[str]:
    """Per-source rules. Returns a list of problems (empty = clean)."""
    problems: list[str] = []
    sources = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(sources, list) or not sources:
        return ["rights manifest: sources must be a non-empty list"]
    seen: set[str] = set()
    for s in sources:
        if not isinstance(s, dict) or not s.get("id"):
            problems.append("source entry without an id")
            continue
        sid = s["id"]
        if sid in seen:
            problems.append(f"{sid}: duplicate source id")
        seen.add(sid)
        if "url" not in s:
            problems.append(f"{sid}: missing url")
        elif not s["url"] and not s.get("statement_source_url"):
            # url may be explicitly null (e.g. the user's own local files)
            # only when provenance is recorded via statement_source_url
            problems.append(f"{sid}: null url without a statement_source_url")
        if not s.get("license"):
            problems.append(f"{sid}: missing license")
        perm = s.get("transformation_permission")
        if not isinstance(perm, bool):
            problems.append(f"{sid}: transformation_permission must be a boolean")
        decision = s.get("decision")
        if decision not in DECISIONS:
            problems.append(f"{sid}: unknown decision {decision!r} (fail closed)")
        elif decision == "allow":
            if perm is not True:
                problems.append(
                    f"{sid}: decision=allow requires transformation_permission: true"
                )
            for field in ("statement", "statement_source_url", "statement_sha256"):
                if not s.get(field):
                    problems.append(f"{sid}: decision=allow requires {field}")
        statement = s.get("statement")
        recorded = s.get("statement_sha256")
        if statement is not None:
            if not recorded:
                problems.append(f"{sid}: statement without statement_sha256")
            elif hashlib.sha256(statement.encode()).hexdigest() != recorded:
                problems.append(f"{sid}: statement_sha256 does not recompute")
        elif recorded:
            problems.append(f"{sid}: statement_sha256 without a statement")
    return problems


def validate(doc: dict) -> list[str]:
    """Full audit: per-source rules plus required-source coverage."""
    problems = validate_sources(doc)
    ids = {
        s.get("id")
        for s in (doc.get("sources") or [])
        if isinstance(s, dict)
    }
    missing = REQUIRED_SOURCES - ids
    if missing:
        problems.append(f"missing required sources: {sorted(missing)}")
    return problems


def main() -> int:
    problems = validate(yaml.safe_load(RIGHTS.read_text()))
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        return 1
    print(f"OK rights audit passed ({len(REQUIRED_SOURCES)} required sources covered)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
