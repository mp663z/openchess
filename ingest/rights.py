"""Shipped import rights gate (T0536 contract + import-rights-policy-v1).

The single shipped authority for import intake authorization. It loads
the structured source registry (data/contracts/import.yaml) and the
normative rights policy (data/contracts/rights_policy.yaml) and answers
one question fail-closed: may this source's data be STORED?

A rights-class label alone is never authorization, and a loaded policy
or registry is never trusted on its own say-so: before any decision the
gate validates BOTH loaded boundaries in one pass against the closed
contract semantics.

- Policy boundary: the exact root key set (id, fail_closed, classes,
  unknown_class, rule - rule is documentation-only prose and must be a
  string), the exact class set, every class's exact structured tuple
  (ownership, persistence, third_party_storage, redistribution,
  provenance_required, each exactly typed and valued), top-level
  fail_closed exactly the boolean True, the policy id, and the
  unknown-class effect tuple. Any missing, extra, mistyped, unknown, or
  contradictory field makes the whole policy untrustworthy: every
  decision refuses closed with a stable policy_contradiction reason.
- Registry boundary: the raw entries list is validated BEFORE any
  projection to a lookup map - list type, every entry a mapping with
  the exact key set (id, kind, rights_class) and exact field types,
  no duplicate ids (no projection may launder duplicates), the exact
  source set, kind in the contract kind set, and rights_class a string
  in the closed class set. Any malformed entry makes the whole registry
  untrustworthy: every decision refuses closed with a stable
  registry_contradiction reason, never a crash.

Storage is then allowed only for the exact storage-permitting tuples,
and the session-only class (user-own-only) requires ownership_verified
to be exactly the boolean True - any other type or value is a
structured refusal, never a raise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
IMPORT_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "import.yaml").read_text())
RIGHTS_DOC = yaml.safe_load((ROOT / "data" / "contracts" / "rights_policy.yaml").read_text())
_SOURCE_ENTRIES = IMPORT_DOC["contract"]["sources"]["entries"]
_POLICY = RIGHTS_DOC["rights_policy"]

_POLICY_ID = "import-rights-policy-v1"
_ROOT_FIELDS = ("id", "fail_closed", "classes", "unknown_class", "rule")
_CLASS_FIELDS = ("ownership", "persistence", "third_party_storage",
                 "redistribution", "provenance_required")
# The closed contract semantics: the exact allowed structured tuple per
# rights class. The loaded policy MUST equal this boundary verbatim;
# any drift is a contradiction and closes the gate.
_EXPECTED_CLASSES = {
    "user-own": {
        "ownership": "importing-user-own-data",
        "persistence": "stored-for-importing-user",
        "third_party_storage": "never",
        "redistribution": "never",
        "provenance_required": True,
    },
    "cc0": {
        "ownership": "public-dedication-cc0",
        "persistence": "stored-snapshot-pinned",
        "third_party_storage": "permitted-under-cc0",
        "redistribution": "permitted-under-cc0",
        "provenance_required": True,
    },
    "user-own-only": {
        "ownership": "importing-user-own-games-only",
        "persistence": "verifying-session-only-for-third-party",
        "third_party_storage": "never",
        "redistribution": "never",
        "provenance_required": True,
    },
    "licensed-own": {
        "ownership": "importing-user-licensed-copy",
        "persistence": "local-for-importing-user",
        "third_party_storage": "never",
        "redistribution": "never",
        "provenance_required": True,
    },
}
_UNKNOWN_CLASS = {"effect": "reject-import", "error": "unknown_rights"}
_SESSION_ONLY = "verifying-session-only-for-third-party"

# The closed registry boundary: exactly these sources, each a mapping
# with exactly the keys (id, kind, rights_class), lookup key == id,
# kind in the contract kind set, rights_class in the closed class set.
_EXPECTED_SOURCES = ("pgn-file", "pgn-multi", "pgn-folder", "pgn-watch",
                     "lichess-public", "chesscom-public", "cbh-licensed")
_SOURCE_FIELDS = ("id", "kind", "rights_class")
_KINDS = {"user-file", "public-api"}


@dataclass(frozen=True)
class RightsDecision:
    source_id: str
    known: bool
    rights_class: str | None
    allowed: bool
    flags: dict = field(default_factory=dict)
    reason: str = ""


def policy_classes() -> dict:
    """The policy's structured rights classes, verbatim."""
    return dict(_POLICY["classes"])


def _field_matches(actual, expected) -> bool:
    """Exact typed equality: bool never matches int, str never matches
    list/None, and no hashing (unhashable values cannot raise)."""
    return type(actual) is type(expected) and actual == expected


def _policy_contradiction() -> str | None:
    """None iff the loaded policy is exactly the closed contract
    semantics; otherwise a stable contradiction tag naming the first
    drifted field. One pass over the whole operative boundary."""
    rp = _POLICY
    if not isinstance(rp, dict):
        return "rights_policy"
    keys = set(rp)
    if keys != set(_ROOT_FIELDS):
        for f in _ROOT_FIELDS:
            if f not in keys:
                return f
        return sorted(keys - set(_ROOT_FIELDS))[0]
    if not _field_matches(rp["id"], _POLICY_ID):
        return "id"
    if not (type(rp["fail_closed"]) is bool and rp["fail_closed"] is True):
        return "fail_closed"
    if not (isinstance(rp["rule"], str) and rp["rule"].strip()):
        return "rule"
    classes = rp["classes"]
    if not isinstance(classes, dict) or set(classes) != set(_EXPECTED_CLASSES):
        return "classes"
    for cls, expected in _EXPECTED_CLASSES.items():
        fields = classes[cls]
        if not isinstance(fields, dict):
            return cls
        fkeys = set(fields)
        if fkeys != set(_CLASS_FIELDS):
            for f in _CLASS_FIELDS:
                if f not in fkeys:
                    return f"{cls}:{f}"
            return f"{cls}:{sorted(fkeys - set(_CLASS_FIELDS))[0]}"
        for f in _CLASS_FIELDS:
            if not _field_matches(fields[f], expected[f]):
                return f"{cls}:{f}"
    unknown = rp["unknown_class"]
    if not (isinstance(unknown, dict)
            and set(unknown) == set(_UNKNOWN_CLASS)
            and all(_field_matches(unknown[k], v) for k, v in _UNKNOWN_CLASS.items())):
        return "unknown_class"
    return None


def _registry_contradiction() -> str | None:
    """None iff the loaded source registry is exactly the closed
    boundary; otherwise a stable contradiction tag naming the first
    malformed field. Validates the RAW entries list before any
    projection: duplicate ids and shape defects cannot be laundered by
    a lossy map construction. One pass, deterministic tag order."""
    entries = _SOURCE_ENTRIES
    if not isinstance(entries, list):
        return "registry"
    ids = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return f"entry[{i}]"
        keys = set(entry)
        if keys != set(_SOURCE_FIELDS):
            for f in _SOURCE_FIELDS:
                if f not in keys:
                    return f"entry[{i}]:{f}"
            return f"entry[{i}]:{sorted(keys - set(_SOURCE_FIELDS))[0]}"
        if type(entry["id"]) is not str or not entry["id"]:
            return f"entry[{i}]:id"
        sid = entry["id"]
        if not (type(entry["kind"]) is str and entry["kind"] in _KINDS):
            return f"{sid}:kind"
        if not (type(entry["rights_class"]) is str
                and entry["rights_class"] in _EXPECTED_CLASSES):
            return f"{sid}:rights_class"
        ids.append(sid)
    for sid in ids:
        if ids.count(sid) > 1:
            return f"duplicate:{sid}"
    if set(ids) != set(_EXPECTED_SOURCES):
        return "sources"
    return None


def intake_decision(source_id: str, *, ownership_verified: bool = False) -> RightsDecision:
    """Fail-closed intake decision for a source id.

    allowed=True only when source_id is a nonempty string, the loaded
    registry (raw entries, validated before projection) AND the loaded
    policy are each exactly the closed contract semantics, the source is
    in the registry, and
    the class's exact tuple permits storage - with the session-only
    class additionally requiring ownership_verified to be exactly the
    boolean True. Everything else is a structured refusal, never a
    raise."""
    if type(source_id) is not str:
        return RightsDecision(source_id=repr(source_id), known=False,
                              rights_class=None, allowed=False,
                              reason="source_id_invalid_type")
    if not source_id.strip():
        return RightsDecision(source_id=source_id, known=False, rights_class=None,
                              allowed=False, reason="source_id_empty")
    if type(ownership_verified) is not bool:
        return RightsDecision(source_id=source_id, known=False, rights_class=None,
                              allowed=False,
                              reason="ownership_verification_invalid_type")
    registry = _registry_contradiction()
    if registry is not None:
        return RightsDecision(source_id=source_id, known=False, rights_class=None,
                              allowed=False,
                              reason=f"registry_contradiction:{registry}")
    # the raw list is validated; the lookup map is built only now, so no
    # projection can launder duplicates or shape defects
    source_map = {e["id"]: e for e in _SOURCE_ENTRIES}
    source = source_map.get(source_id)
    known = source is not None
    rights_class = source["rights_class"] if known else None
    contradiction = _policy_contradiction()
    if contradiction is not None:
        return RightsDecision(source_id=source_id, known=known,
                              rights_class=rights_class, allowed=False,
                              reason=f"policy_contradiction:{contradiction}")
    if not known:
        return RightsDecision(source_id=source_id, known=False, rights_class=None,
                              allowed=False, reason="unknown_source")
    flags = dict(_POLICY["classes"][rights_class])
    if flags["persistence"] == _SESSION_ONLY and ownership_verified is not True:
        # third-party data may live only in a verifying session; storage
        # requires the importing user's OWN games, verified exactly
        return RightsDecision(source_id=source_id, known=True,
                              rights_class=rights_class, allowed=False,
                              flags=flags, reason="ownership_unverified")
    return RightsDecision(source_id=source_id, known=True, rights_class=rights_class,
                          allowed=True, flags=flags, reason="")
