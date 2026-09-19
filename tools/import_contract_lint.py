"""T0536: import capability contract lint.

Enforces the normative CONTENT of data/contracts/import.yaml by EXACT
STRUCTURED COMPARISON, never prose-marker presence: the source registry
(seven sources, each with kind and rights class; the four rights
classes), the record shape (declared fields incl. content_sha256 and
provenance), the idempotency rules (dedup key, duplicate no-op, updated
replace), the atomicity rules (per-game commit, never-visible partial,
cancel/crash semantics), the fourteen-scenario registry (exact ids,
exact chain tasks T0537-T0680, exact sources/visible_output/
persisted_state/telemetry/error_codes per entry), the failure_mapping
(no orphan classes, no undeclared error codes), the closed error enum +
exact shape, and versioning. Cross-artifact linkage is VERIFIED AGAINST
THE ACTUAL variant contract (linted first) and the rights-policy
document (fail-closed rule present).

    python tools/import_contract_lint.py [path]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.variant_contract_lint import ContractError  # noqa: E402
from tools.variant_contract_lint import lint as _variant_lint  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "import.yaml"

SOURCES = [
    {"id": "pgn-file", "kind": "user-file", "rights_class": "user-own"},
    {"id": "pgn-multi", "kind": "user-file", "rights_class": "user-own"},
    {"id": "pgn-folder", "kind": "user-file", "rights_class": "user-own"},
    {"id": "pgn-watch", "kind": "user-file", "rights_class": "user-own"},
    {"id": "lichess-public", "kind": "public-api", "rights_class": "cc0"},
    {"id": "chesscom-public", "kind": "public-api",
     "rights_class": "user-own-only"},
    {"id": "cbh-licensed", "kind": "user-file",
     "rights_class": "licensed-own"},
]
RIGHTS_CLASSES = ["user-own", "cc0", "user-own-only", "licensed-own"]
RECORD_FIELDS = ["game_id", "source_id", "provenance", "content_sha256",
                 "variant", "tags", "movetext", "imported_at"]
PROVENANCE_FIELDS = ["source_id", "rights_class", "retrieved_at",
                     "retrieval_detail"]
IDEMPOTENCY = {
    "dedup_key": "game_id",
    "duplicate": {"policy": "no-op-visible", "outcome": "already-imported"},
    "updated": {"policy": "replace-recorded", "outcome": "updated",
                "precondition": "same-game_id-different-content_sha256"},
}
ATOMICITY = {
    "commit_boundary": "per-game",
    "partial_game": "never-visible",
    "cancel": "committed-stay-in-flight-discarded",
    "crash": "restart-resumes-idempotently",
}
SCENARIOS = [
    {"id": "import-pgn", "chain_task": "T0537", "sources": ["pgn-file"],
     "visible_output": "import-summary",
     "persisted_state": ["game-records", "provenance", "index-entries"],
     "telemetry": ["import.started", "import.game_stored", "import.completed"],
     "error_codes": ["malformed_request"]},
    {"id": "import-multi-pgn", "chain_task": "T0548",
     "sources": ["pgn-multi"], "visible_output": "import-summary",
     "persisted_state": ["game-records", "provenance", "index-entries"],
     "telemetry": ["import.started", "import.game_stored", "import.completed"],
     "error_codes": ["malformed_request"]},
    {"id": "import-utf8", "chain_task": "T0559",
     "sources": ["pgn-file", "pgn-multi"], "visible_output": "import-summary",
     "persisted_state": ["game-records", "provenance", "index-entries"],
     "telemetry": ["import.started", "import.game_stored", "import.completed"],
     "error_codes": ["malformed_request"]},
    {"id": "import-bad-tags", "chain_task": "T0570",
     "sources": ["pgn-file", "pgn-multi"],
     "visible_output": "refusal-with-location",
     "persisted_state": ["rejection-record"],
     "telemetry": ["import.started", "import.game_rejected", "import.completed"],
     "error_codes": ["malformed_request"]},
    {"id": "import-truncated", "chain_task": "T0581",
     "sources": ["pgn-file", "pgn-multi"],
     "visible_output": "refusal-with-location",
     "persisted_state": ["rejection-record"],
     "telemetry": ["import.started", "import.game_rejected", "import.completed"],
     "error_codes": ["malformed_request"]},
    {"id": "import-illegal", "chain_task": "T0592",
     "sources": ["pgn-file", "pgn-multi"],
     "visible_output": "refusal-with-position",
     "persisted_state": ["rejection-record"],
     "telemetry": ["import.started", "import.game_rejected", "import.completed"],
     "error_codes": ["illegal_move"]},
    {"id": "import-duplicate", "chain_task": "T0603",
     "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
     "visible_output": "already-imported",
     "persisted_state": ["unchanged-store"],
     "telemetry": ["import.started", "import.game_duplicate", "import.completed"],
     "error_codes": []},
    {"id": "import-updated", "chain_task": "T0614",
     "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
     "visible_output": "updated-summary",
     "persisted_state": ["replaced-record", "provenance", "index-entries"],
     "telemetry": ["import.started", "import.game_updated", "import.completed"],
     "error_codes": []},
    {"id": "import-10k", "chain_task": "T0625",
     "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
     "visible_output": "progress-and-summary",
     "persisted_state": ["game-records", "provenance", "index-entries"],
     "telemetry": ["import.started", "import.progress", "import.game_stored",
                   "import.completed"],
     "error_codes": ["malformed_request"]},
    {"id": "import-folder", "chain_task": "T0636", "sources": ["pgn-folder"],
     "visible_output": "per-file-and-total-summary",
     "persisted_state": ["game-records", "provenance", "index-entries",
                         "rejection-records"],
     "telemetry": ["import.started", "import.file_completed",
                   "import.game_stored", "import.game_rejected",
                   "import.completed"],
     "error_codes": ["malformed_request"]},
    {"id": "import-cancel", "chain_task": "T0647",
     "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
     "visible_output": "cancelled-summary",
     "persisted_state": ["committed-games-only"],
     "telemetry": ["import.started", "import.cancelled"],
     "error_codes": []},
    {"id": "import-crash", "chain_task": "T0658",
     "sources": ["pgn-file", "pgn-multi", "pgn-folder"],
     "visible_output": "recovered-summary",
     "persisted_state": ["committed-games-only"],
     "telemetry": ["import.started", "import.interrupted", "import.resumed",
                   "import.completed"],
     "error_codes": []},
    {"id": "import-unknown-rights", "chain_task": "T0669", "sources": [],
     "visible_output": "refusal-with-reason",
     "persisted_state": ["rejection-record"],
     "telemetry": ["import.refused"],
     "error_codes": ["unknown_rights"]},
    {"id": "import-roundtrip", "chain_task": "T0680", "sources": ["pgn-file"],
     "visible_output": "roundtrip-proof",
     "persisted_state": ["unchanged-store"],
     "telemetry": ["export.completed", "import.completed"],
     "error_codes": []},
]
FAILURE_CLASSES = ["unknown_source", "unknown_rights", "malformed_input",
                   "illegal_movetext", "partial_visibility"]
FAILURE_MAPPING = {
    "unknown_source": {"trigger": "source-id-not-in-registry",
                       "error": "unknown_rights"},
    "unknown_rights": {"trigger": "source-rights-class-not-verified",
                       "error": "unknown_rights"},
    "malformed_input": {"trigger": "bytes-not-well-formed-for-source-kind",
                        "error": "malformed_request"},
    "illegal_movetext": {"trigger": "movetext-violates-game-rules",
                         "error": "illegal_move"},
    "partial_visibility": {"trigger": "any-partial-game-record-observable",
                           "error": "internal"},
}
ERROR_ENUM = ["malformed_request", "illegal_move", "unknown_rights",
              "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}
LINKS = {"variant_contract": "data/contracts/variant.yaml",
         "rights_policy": "docs/rights-policy.md"}
VERSIONING = {
    "base_path": "/import/v1",
    "client_pin": "MAJOR",
    "minor_policy": "additive-only",
    "minor_additions": ["new-sources", "new-optional-fields",
                        "new-scenarios"],
    "downgrade_policy": "any-earlier-minor-within-major-without-migration",
    "major_bump": "new-base-path-required",
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {"id", "sources", "record", "idempotency", "atomicity",
                    "scenarios", "failure_classes", "failure_mapping",
                    "errors", "links", "versioning"}
ALLOWED_SOURCE = {"id", "kind", "rights_class"}
ALLOWED_SCENARIO = {"id", "chain_task", "sources", "visible_output",
                    "persisted_state", "telemetry", "error_codes"}
SOURCE_KINDS = {"user-file", "public-api"}


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(f"import contract: {problem}")


def _keys(node: dict, allowed: set[str], where: str) -> None:
    _need(type(node) is dict, f"{where}: mapping required")
    unknown = set(node) - allowed
    _need(not unknown, f"{where}: unknown keys {sorted(unknown)}")


def _get(node: object, key: str, where: str) -> object:
    """Presence-checked access: a missing key is a contract violation,
    never a raw KeyError."""
    _need(type(node) is dict, f"{where}: mapping required")
    _need(key in node, f"{where}: missing key {key!r}")
    return node[key]


def _text(node: object, where: str) -> str:
    _need(type(node) is str and node.strip(), f"{where}: nonempty documentation string required")
    return node


def _strict_eq(actual: object, expected: object, where: str) -> None:
    _need(type(actual) is type(expected),
          f"{where}: type {type(actual).__name__} != {type(expected).__name__}")
    if isinstance(actual, dict):
        _need(set(actual) == set(expected),
              f"{where}: keys {sorted(actual)} != {sorted(expected)}")
        for k in actual:
            _strict_eq(actual[k], expected[k], f"{where}.{k}")
    elif isinstance(actual, list):
        _need(actual == expected, f"{where}: {actual!r} != {expected!r}")
    else:
        _need(actual == expected, f"{where}: {actual!r} != {expected!r}")


def _check_sources(sources: dict) -> None:
    _keys(sources, {"registry_rule", "entries", "rights_classes"},
          "contract.sources")
    _text(_get(sources, "registry_rule", "contract.sources"),
          "contract.sources.registry_rule")
    entries = _get(sources, "entries", "contract.sources")
    _need(type(entries) is list, "contract.sources.entries: list required")
    for i, e in enumerate(entries):
        _keys(e, ALLOWED_SOURCE, f"contract.sources.entries[{i}]")
    _strict_eq(entries, SOURCES, "contract.sources.entries")
    ids = [e["id"] for e in entries]
    _need(len(ids) == len(set(ids)), "contract.sources.entries: duplicate id")
    rc = _get(sources, "rights_classes", "contract.sources")
    _need(type(rc) is dict, "contract.sources.rights_classes: mapping required")
    _need(sorted(rc) == sorted(RIGHTS_CLASSES),
          f"contract.sources.rights_classes: keys {sorted(rc)} != {sorted(RIGHTS_CLASSES)}")
    for name, doc in rc.items():
        _text(doc, f"contract.sources.rights_classes.{name}")
    for e in entries:
        _need(e["rights_class"] in RIGHTS_CLASSES,
              f"source {e['id']}: undeclared rights class {e['rights_class']}")
        _need(e["kind"] in SOURCE_KINDS,
              f"source {e['id']}: undeclared kind {e['kind']}")


def _check_scenarios(scenarios: dict) -> None:
    _keys(scenarios, {"registry_rule", "entries"}, "contract.scenarios")
    _text(_get(scenarios, "registry_rule", "contract.scenarios"),
          "contract.scenarios.registry_rule")
    entries = _get(scenarios, "entries", "contract.scenarios")
    _need(type(entries) is list, "contract.scenarios.entries: list required")
    for i, e in enumerate(entries):
        _keys(e, ALLOWED_SCENARIO, f"contract.scenarios.entries[{i}]")
    _strict_eq(entries, SCENARIOS, "contract.scenarios.entries")
    source_ids = {s["id"] for s in SOURCES}
    for e in entries:
        for s in e["sources"]:
            _need(s in source_ids,
                  f"scenario {e['id']}: undeclared source {s}")
        for code in e["error_codes"]:
            _need(code in ERROR_ENUM,
                  f"scenario {e['id']}: undeclared error code {code}")
        _need(type(e["telemetry"]) is list and e["telemetry"],
              f"scenario {e['id']}: empty telemetry")
        _need(type(e["persisted_state"]) is list and e["persisted_state"],
              f"scenario {e['id']}: empty persisted_state")
        _need(type(e["visible_output"]) is str and e["visible_output"].strip(),
              f"scenario {e['id']}: empty visible_output")


def _check_links(links: dict, root: Path) -> None:
    _strict_eq(links, LINKS, "contract.links")
    vdoc = yaml.safe_load((root / LINKS["variant_contract"]).read_text())
    _variant_lint(vdoc)  # lint BEFORE relying on the linked artifact
    canonical = vdoc["contract"]["identity"]["canonical_fields"]
    _need("board" in canonical and "side_to_move" in canonical,
          "linked variant contract: position identity fields missing")
    policy = (root / LINKS["rights_policy"]).read_text()
    _need("Fail closed" in policy,
          "linked rights policy: fail-closed rule not found")


def lint(doc: object, root: Path = ROOT) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _need(doc.get("schema_version") == 1 and type(doc.get("schema_version")) is int,
          "schema_version: exact int 1")
    c = _get(doc, "contract", "document")
    _keys(c, ALLOWED_CONTRACT, "contract")
    for required in ALLOWED_CONTRACT:
        _get(c, required, "contract")
    _need(c["id"] == "import-capability",
          "contract.id must be import-capability")

    _check_sources(_get(c, "sources", "contract"))

    record = _get(c, "record", "contract")
    _keys(record, {"fields", "provenance_fields", "identity_rule"},
          "contract.record")
    _strict_eq(_get(record, "fields", "contract.record"),
               RECORD_FIELDS, "contract.record.fields")
    _strict_eq(_get(record, "provenance_fields", "contract.record"),
               PROVENANCE_FIELDS, "contract.record.provenance_fields")
    _text(_get(record, "identity_rule", "contract.record"),
          "contract.record.identity_rule")

    idem = _get(c, "idempotency", "contract")
    _keys(idem, {"dedup_key", "duplicate", "updated", "rule"},
          "contract.idempotency")
    _strict_eq({k: v for k, v in idem.items() if k != "rule"},
               IDEMPOTENCY, "contract.idempotency.fields")
    _text(_get(idem, "rule", "contract.idempotency"),
          "contract.idempotency.rule")

    atom = _get(c, "atomicity", "contract")
    _keys(atom, {"commit_boundary", "partial_game", "cancel", "crash",
                 "rule"}, "contract.atomicity")
    _strict_eq({k: v for k, v in atom.items() if k != "rule"},
               ATOMICITY, "contract.atomicity.fields")
    _text(_get(atom, "rule", "contract.atomicity"),
          "contract.atomicity.rule")

    _check_scenarios(_get(c, "scenarios", "contract"))

    _strict_eq(_get(c, "failure_classes", "contract"),
               FAILURE_CLASSES, "contract.failure_classes")
    mapping = _get(c, "failure_mapping", "contract")
    _strict_eq(mapping, FAILURE_MAPPING, "contract.failure_mapping")
    errors = _get(c, "errors", "contract")
    _strict_eq(errors, {"closed_enum": ERROR_ENUM, "shape": ERROR_SHAPE},
               "contract.errors")
    for cls in FAILURE_CLASSES:
        err = mapping[cls]["error"]
        _need(err in errors["closed_enum"],
              f"failure class {cls} maps to undeclared error {err}")
    versioning = _get(c, "versioning", "contract")
    _keys(versioning, {"base_path", "client_pin", "minor_policy",
                       "minor_additions", "downgrade_policy", "major_bump",
                       "rule"}, "contract.versioning")
    _strict_eq({k: v for k, v in versioning.items() if k != "rule"},
               VERSIONING, "contract.versioning.fields")
    _text(_get(versioning, "rule", "contract.versioning"),
          "contract.versioning.rule")
    _check_links(_get(c, "links", "contract"), root)


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        path = Path(argv[1]).resolve()
        root = path.parents[2] if path.parent.name == "contracts" else ROOT
    else:
        path, root = CONTRACT, ROOT
    try:
        lint(yaml.safe_load(path.read_text()), root)
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("OK import contract lint: import capability contract v1 clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
