"""T0033: data/model rights check - real rights audit, seeded violations.

Good mode: tools/rights_audit.py validates the repo's real
data/datasets/public-source-rights.yaml (per-source rules + required-source
coverage). Violation mode: seeded manifest fixtures under
fixtures/rights/, each breaking exactly one rule: allow without a
transformation grant, tampered evidence hash, unknown decision value,
missing license, fabricated license string, non-https URL, self-authored
grant (statement absent from its evidence bytes), missing local source
path. Every seeded violation must be rejected by the real validator
for its own seeded reason.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tools import rights_audit
from tools.install_checks import CheckError

CHECK_ID = "T0033"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rights"

VIOLATIONS = {
    "bad_allow_without_grant.yaml": "decision=allow requires transformation_permission",
    "bad_evidence_hash.yaml": "evidence_sha256 does not recompute",
    "bad_unknown_decision.yaml": "unknown decision",
    "bad_missing_license.yaml": "missing license",
    "bad_fake_license.yaml": "outside the policy set",
    "bad_url_scheme.yaml": "url is not an https URL",
    "bad_self_authored.yaml": "statement not contained in evidence file",
    "bad_local_missing.yaml": "local path missing",
    "bad_fetched_at.yaml": "fetched_at is not an ISO date",
    "bad_fetched_at_trailing.yaml": "fetched_at is not an ISO date",
    "bad_future_fetch.yaml": "fetched_at in the future",
    "bad_path_escape.yaml": "evidence_path escapes its base",
}

# Violations only the full audit catches (directory confinement is resolved,
# not lexical): the path resolves inside the base but outside statements/.
FULL_AUDIT_VIOLATIONS = {
    "bad_dir_escape.yaml": "evidence must live under",
}


def run(mode: str) -> None:
    if mode == "good":
        problems = rights_audit.validate(yaml.safe_load(rights_audit.RIGHTS.read_text()))
        if problems:
            raise CheckError(f"real rights manifest: {problems}")
        return
    uncaught = []
    for fname, expect in sorted(VIOLATIONS.items()):
        problems = rights_audit.validate_sources(
            yaml.safe_load((FIXTURES / fname).read_text()),
            FIXTURES,
        )
        if not any(expect in p for p in problems):
            uncaught.append(f"{fname}: expected problem containing {expect!r}, got {problems}")
    for fname, expect in sorted(FULL_AUDIT_VIOLATIONS.items()):
        problems = rights_audit.validate(
            yaml.safe_load((FIXTURES / fname).read_text()),
            FIXTURES,
        )
        if not any(expect in p for p in problems):
            uncaught.append(f"{fname}: expected problem containing {expect!r}, got {problems}")
    if uncaught:
        return  # harness FAILS: a seeded rights violation escaped the validator
    raise CheckError("all seeded rights violations rejected by the real validator")
