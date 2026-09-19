"""Data/model rights audit (T2229 discipline, wrapped by install check T0033).

Fail-closed validation of data/datasets/public-source-rights.yaml. Beyond
field presence, every claim is checked against something independent:

- URLs must be https with a real host (no javascript:/data:/file: schemes);
  url may be explicitly null only with a statement_source_url recorded.
- statement_source_url is either an https URL or a repo-relative path that
  EXISTS; for local paths the cited statement must appear verbatim in the
  referenced file.
- decision=allow requires a policy-approved license (permissive set) or the
  pinned user-owned-files exemption, an explicit transformation grant, and
  captured evidence.
- Evidence is pinned BYTES, not a claim stored beside its hash: each
  source's statement lives in data/datasets/statements/<id>.txt with
  fetched_from/fetched_at provenance; evidence_sha256 recomputes over the
  snapshot file bytes and the manifest statement must appear verbatim in
  the snapshot.
- fetched_at must be a real ISO date, not in the future; fetched_from must
  equal statement_source_url; evidence paths must stay inside the repo
  (no absolute paths, no .. escapes) and under data/datasets/statements/.
SCOPE (honest): snapshots are human-reviewed captured evidence. The gate
proves bytes, provenance shape and internal consistency; it cannot prove a
REMOTE page ever said anything - that assurance comes from snapshot diffs
landing under the CLA-protected data/ prefix (non-de-minimis review), and
from re-fetch verification at acquisition time.
- Unknown decision values, missing terms, hash drift and unknown licenses
  are violations: unproven terms exclude the source, never assume it.
"""

from __future__ import annotations

import hashlib
import re
import sys
from datetime import date
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

# Policy: licenses compatible with the fail-closed licensing directive for
# data/content acquisition (mirrors the dependency allowlist's permissive
# core; content licenses limited to CC0 + permissive code licenses).
ALLOW_DATA_LICENSES = {
    "CC0-1.0", "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
}
# The one non-published-license allow: the user's own purchased files.
USER_OWNED_ID = "user_own_cbh_files"
USER_OWNED_LICENSE = (
    "user-purchased ChessBase databases, imported locally at the user's request"
)

_HTTPS_RE = re.compile(r"^https://[A-Za-z0-9.-]+\.[A-Za-z]{2,}([/?#].*)?$")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _is_https_url(value: str) -> bool:
    return bool(_HTTPS_RE.match(value.strip()))



def _contained(base: Path, rel: str) -> bool:
    """rel must resolve inside base - no absolute paths, no .. escapes."""
    p = Path(rel)
    if p.is_absolute():
        return False
    try:
        (base / p).resolve().relative_to(base.resolve())
    except ValueError:
        return False
    return True


def validate_sources(doc: dict, base: Path = ROOT) -> list[str]:
    """Per-source rules. Returns a list of problems (empty = clean).

    base: directory that repo-relative evidence/source paths resolve
    against (the repository root in real runs; the fixture dir in seeded
    violation runs).
    """
    problems: list[str] = []
    sources = doc.get("sources") if isinstance(doc, dict) else None
    if not isinstance(sources, list) or not sources:
        return ["rights manifest: sources must be a non-empty list"]
    seen: set[str] = set()
    for s in sources:
        if not isinstance(s, dict):
            problems.append("source entry is not a mapping")
            continue
        sid_raw = s.get("id")
        if not isinstance(sid_raw, str) or not sid_raw.strip():
            problems.append(f"source entry with a non-string or empty id: {sid_raw!r}")
            continue
        sid = sid_raw
        if sid in seen:
            problems.append(f"{sid}: duplicate source id")
        seen.add(sid)

        # --- URL shape ---
        if "url" not in s:
            problems.append(f"{sid}: missing url")
        else:
            url = s["url"]
            if url is None:
                if not s.get("statement_source_url"):
                    problems.append(f"{sid}: null url without a statement_source_url")
            elif not isinstance(url, str) or not _is_https_url(url):
                problems.append(f"{sid}: url is not an https URL: {url!r}")

        license_raw = s.get("license")
        if license_raw is not None and not isinstance(license_raw, str):
            problems.append(
                f"{sid}: license must be a string, got {type(license_raw).__name__}"
            )
            license_ = ""
        else:
            license_ = (license_raw or "").strip()
        if not license_:
            problems.append(f"{sid}: missing license")

        perm = s.get("transformation_permission")
        if not isinstance(perm, bool):
            problems.append(f"{sid}: transformation_permission must be a boolean")

        decision = s.get("decision")
        if not isinstance(decision, str) or decision not in DECISIONS:
            problems.append(f"{sid}: unknown decision {decision!r} (fail closed)")
        elif decision == "allow":
            if perm is not True:
                problems.append(
                    f"{sid}: decision=allow requires transformation_permission: true"
                )
            if license_ not in ALLOW_DATA_LICENSES and not (
                sid == USER_OWNED_ID and license_ == USER_OWNED_LICENSE
            ):
                problems.append(
                    f"{sid}: decision=allow with license outside the policy set: "
                    f"{license_!r}"
                )

        # --- statement source: https URL or existing local file containing it ---
        statement = s.get("statement")
        if statement is not None and not isinstance(statement, str):
            problems.append(
                f"{sid}: statement must be a string, got {type(statement).__name__}"
            )
            statement = None
        src_raw = s.get("statement_source_url")
        if src_raw is not None and not isinstance(src_raw, str):
            problems.append(
                f"{sid}: statement_source_url must be a string, "
                f"got {type(src_raw).__name__}"
            )
            src_url = ""
        else:
            src_url = (src_raw or "").strip()
        if statement is not None and not src_url:
            problems.append(f"{sid}: statement without statement_source_url")
        if src_url:
            if _is_https_url(src_url):
                pass
            else:
                local = base / src_url
                if "://" in src_url or src_url.startswith(("javascript:", "data:", "file:")):
                    problems.append(f"{sid}: statement_source_url is not https: {src_url!r}")
                elif not _contained(base, src_url):
                    problems.append(
                        f"{sid}: statement_source_url escapes its base: {src_url!r}"
                    )
                elif not local.is_file():
                    problems.append(
                        f"{sid}: statement_source_url local path missing: {src_url}"
                    )
                elif statement is not None and statement not in local.read_text():
                    problems.append(
                        f"{sid}: statement not found verbatim in {src_url}"
                    )

        # --- evidence bytes: snapshot file pinned by hash, statement inside ---
        ev_path = s.get("evidence_path")
        if ev_path is not None and not isinstance(ev_path, str):
            problems.append(
                f"{sid}: evidence_path must be a string, got {type(ev_path).__name__}"
            )
            ev_path = None
        ev_hash = s.get("evidence_sha256")
        if ev_hash is not None and not isinstance(ev_hash, str):
            problems.append(
                f"{sid}: evidence_sha256 must be a string, got {type(ev_hash).__name__}"
            )
            ev_hash = None
        if statement is not None:
            if not ev_path or not ev_hash:
                problems.append(f"{sid}: statement without evidence_path/evidence_sha256")
            elif not _contained(base, ev_path):
                problems.append(
                    f"{sid}: evidence_path escapes its base (absolute/..): {ev_path!r}"
                )
            else:
                f = base / ev_path
                if not f.is_file():
                    problems.append(f"{sid}: evidence file missing: {ev_path}")
                else:
                    blob = f.read_bytes()
                    if _sha256_bytes(blob) != ev_hash:
                        problems.append(f"{sid}: evidence_sha256 does not recompute")
                    text = blob.decode("utf-8", errors="replace")
                    if statement not in text:
                        problems.append(
                            f"{sid}: statement not contained in evidence file {ev_path}"
                        )
                    header_from = re.search(r"^fetched_from: (.+)$", text, re.M)
                    if not header_from:
                        problems.append(f"{sid}: evidence file lacks fetched_from")
                    elif src_url and header_from.group(1).strip() != src_url:
                        problems.append(
                            f"{sid}: evidence fetched_from != statement_source_url"
                        )
                    header_at = re.search(r"^fetched_at: (.+)$", text, re.M)
                    if not header_at:
                        problems.append(f"{sid}: evidence file lacks fetched_at")
                    else:
                        try:
                            fetched = date.fromisoformat(header_at.group(1).strip())
                            if fetched > date.today():
                                problems.append(
                                    f"{sid}: evidence fetched_at in the future: "
                                    f"{header_at.group(1).strip()!r}"
                                )
                        except ValueError:
                            problems.append(
                                f"{sid}: evidence fetched_at is not an ISO date: "
                                f"{header_at.group(1).strip()!r}"
                            )
        elif ev_path or ev_hash:
            problems.append(f"{sid}: evidence fields without a statement")
    return problems


def validate(doc: dict, base: Path = ROOT) -> list[str]:
    """Full audit: per-source rules plus required-source coverage."""
    problems = validate_sources(doc, base)
    ids = {
        s.get("id")
        for s in (doc.get("sources") or [])
        if isinstance(s, dict)
    }
    missing = REQUIRED_SOURCES - ids
    if missing:
        problems.append(f"missing required sources: {sorted(missing)}")
    stmts_dir = (base / "data/datasets/statements").resolve()
    for s in (doc.get("sources") or []):
        if isinstance(s, dict) and s.get("statement") is not None:
            ev = s.get("evidence_path") or ""
            resolved = (base / ev).resolve()
            if not resolved.is_relative_to(stmts_dir):
                problems.append(
                    f"{s.get('id')}: evidence must live under "
                    f"data/datasets/statements/ (got {ev!r})"
                )
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
