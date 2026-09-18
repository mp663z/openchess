"""T0035: CLA/DCO check - real cla_check functions, seeded bypass attempts.

Good mode: the real data/cla-acceptances.yaml validates and a small
docs-only modification qualifies as de minimis (DCO sign-off path).
Violation mode: seeded registries that must be rejected by
cla_check.validate_registry (duplicate handle, naive timestamp,
foreign-repo PR reference, invalid handle) and seeded change sets that
must NOT qualify as de minimis (protected path, added file, over the
line budget). Every seeded violation must be caught.
"""

from __future__ import annotations

from tools import cla_check
from tools.install_checks import CheckError

CHECK_ID = "T0035"
REPO = cla_check.DEFAULT_REPO

_ENTRY = {
    "github_handle": "contributor1",
    "accepted_at": "2026-09-18T12:00:00+00:00",
    "reference": f"{cla_check.DEFAULT_REPO}#1",
}


def _registry(**mutations):
    e = dict(_ENTRY)
    e.update(mutations)
    return {"schema_version": 1, "acceptances": [e]}


# name -> (callable that must RAISE ClaError)
REGISTRY_VIOLATIONS = {
    "duplicate handle": lambda: cla_check.validate_registry(
        {"schema_version": 1, "acceptances": [dict(_ENTRY), dict(_ENTRY)]}, REPO
    ),
    "naive timestamp": lambda: cla_check.validate_registry(
        _registry(accepted_at="2026-09-18"), REPO
    ),
    "foreign-repo reference": lambda: cla_check.validate_registry(
        _registry(reference="someoneelse/otherrepo#1"), REPO
    ),
    "invalid handle": lambda: cla_check.validate_registry(
        _registry(github_handle="Bad Handle!"), REPO
    ),
    "bad schema_version": lambda: cla_check.validate_registry(
        {"schema_version": 2, "acceptances": []}, REPO
    ),
}

# name -> (changed_lines, files) that must NOT be de minimis
DEMINIMIS_VIOLATIONS = {
    "protected path": (2, [("M", "tools/dag.py")]),
    "protected file": (1, [("M", "LICENSE")]),
    "added file": (3, [("A", "docs/new.md")]),
    "over line budget": (25, [("M", "docs/faq.md")]),
}


def run(mode: str) -> None:
    if mode == "good":
        cla_check.load_registry()  # real registry must validate (empty is valid)
        if not cla_check.is_de_minimis(5, [("M", "docs/faq.md")]):
            raise CheckError("small docs-only change wrongly denied de minimis")
        return
    uncaught = []
    for name, fn in sorted(REGISTRY_VIOLATIONS.items()):
        try:
            fn()
            uncaught.append(f"registry violation accepted: {name}")
        except cla_check.ClaError:
            pass
    for name, (lines, files) in sorted(DEMINIMIS_VIOLATIONS.items()):
        if cla_check.is_de_minimis(lines, files):
            uncaught.append(f"de minimis wrongly granted: {name}")
    if uncaught:
        return  # harness FAILS: a CLA/DCO bypass escaped
    raise CheckError("all seeded CLA/DCO violations caught")
