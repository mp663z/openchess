"""T0149: graph provenance contract lint.

Enforces the normative CONTENT of data/contracts/provenance.yaml by
EXACT STRUCTURED COMPARISON, never prose-marker presence: the
origin-attribution role with its additive-only and fail-closed
rights semantics, the CLOSED target-kind enum with each kind's
identity sourced from (never restating) the owning sibling
contract, the exact three-field source entry (registry source id,
import game_id, pinned RFC3339-UTC timestamp) with set semantics,
the exact three-field provenance record (nonempty sources, no
stored derived fields), the union merge algebra
(insert-or-union-sources, atomic, idempotent, commutative,
associative, NO same-key conflict class, exact stored records
validated before staging, rejected insert changes nothing), the
four-class failure model with its exact mapping (no orphan classes,
no undeclared codes), the error surface shape, the behavioral
properties, the link set, versioning, and linkage VERIFIED AGAINST
THE ACTUAL sibling artifacts (import sources registry + dedup key,
transposition-node identity, route-edge identity, opening-context
identity). Anything less passes silently and the retrieval and
rights attribution surfaces rot.

    python tools/provenance_contract_lint.py [path]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "provenance.yaml"
IMPORT = ROOT / "data" / "contracts" / "import.yaml"
NODE = ROOT / "data" / "contracts" / "transposition_node.yaml"
EDGE = ROOT / "data" / "contracts" / "route_edge.yaml"
CONTEXT = ROOT / "data" / "contracts" / "opening_context.yaml"

LINKS = {
    "import_contract": "data/contracts/import.yaml",
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "route_edge_contract": "data/contracts/route_edge.yaml",
    "opening_context_contract": "data/contracts/opening_context.yaml",
}
ROLE = {
    "kind": "origin-attribution-for-graph-records",
    "attribution": "by-target-record-identity-never-by-path-or-digest",
    "completeness": "source-set-is-the-complete-origin-evidence",
    "additive_only": "merged-source-entries-never-removed",
    "rights_surface": "attribution-carried-as-provenance-fail-closed",
    "links": {
        "import_contract": "data/contracts/import.yaml",
        "transposition_node": "data/contracts/transposition_node.yaml",
        "route_edge": "data/contracts/route_edge.yaml",
        "opening_context": "data/contracts/opening_context.yaml",
    },
}
TARGETS = {
    "kinds": ["transposition_node", "route_edge", "opening_context"],
    "closed": True,
    "identity_source":
        "from-linked-sibling-contracts-read-never-restated",
    "transposition_node_identity":
        "from-linked-transposition-node-contract-identity",
    "route_edge_identity": "from-linked-route-edge-contract-identity",
    "opening_context_identity":
        "from-linked-opening-context-contract-identity",
    "links": {
        "transposition_node": "data/contracts/transposition_node.yaml",
        "route_edge": "data/contracts/route_edge.yaml",
        "opening_context": "data/contracts/opening_context.yaml",
    },
}
SOURCE_ENTRY = {
    "fields": ["source_id", "game_id", "first_observed_at"],
    "source_id_form": "from-linked-import-contract-sources-registry",
    "game_id_form":
        "import-record-game_id-nonempty-delimiter-free-printable-ascii",
    "game_id_grammar":
        "from-linked-import-contract-dedup-identity-never-recomputed",
    "timestamp_grammar":
        "rfc3339-utc-seconds-Z-suffix-ascii-no-leap-second",
    "set_semantics": "order-free-duplicates-collapsed",
    "links": {
        "import_contract": "data/contracts/import.yaml",
    },
}
RECORD = {
    "fields": ["target_kind", "target", "sources"],
    "target_form": "target-identity-fields-verbatim-per-kind",
    "sources_nonempty": True,
    "derived_fields_stored": "none",
    "links": {
        "transposition_node": "data/contracts/transposition_node.yaml",
        "route_edge": "data/contracts/route_edge.yaml",
        "opening_context": "data/contracts/opening_context.yaml",
    },
}
MERGE = {
    "insert": "insert-or-union-sources",
    "atomic": True,
    "idempotent": True,
    "commutative": True,
    "associative": True,
    "same_target_never_two_records": True,
    "same_key_conflict": "none-union-is-total",
    "conflict_in_batch": "whole-merge-rejected-nothing-committed",
    "source_validation":
        "exact-stored-records-validated-before-staging",
    "rejected_insert_changes_nothing": True,
}
FAILURE_CLASSES = ["malformed_provenance_record",
                   "unknown_target_kind", "malformed_target_identity",
                   "unknown_source"]
FAILURE_MAPPING = {
    "malformed_provenance_record": "malformed_request",
    "unknown_target_kind": "unknown_target_kind",
    "malformed_target_identity": "malformed_request",
    "unknown_source": "unknown_source",
}
ERROR_ENUM = ["malformed_request", "unknown_target_kind",
              "unknown_source", "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        },
        "additional_fields": "forbidden",
    },
    "retryable_true_only_for": ["internal"],
}
PROPERTIES = {
    "key_uniqueness": "one-target-exactly-one-record",
    "source_completeness": "every-producing-observation-in-the-set",
    "monotone_growth": "merge-only-adds-source-entries",
    "merge_idempotence": "reinsert-same-record-no-op",
    "merge_commutativity": "insertion-order-never-changes-the-table",
    "union_total": "same-key-pair-always-merges-never-conflicts",
    "rollback": "rejected-insert-leaves-table-bit-identical",
}
VERSIONING = {"base_path": "/graph/provenance/v1"}
PROSE_KEYS = {"rule"}


def _load(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ContractError(f"{path.name}: unreadable: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ContractError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"{path.name}: top level must be a mapping")
    return data


def _check_exact(section: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise ContractError(
            f"{section}: content drift\n  expected: {expected!r}\n"
            f"  actual:   {actual!r}")


def _check_closure(section: str, mapping: dict, allowed: set) -> None:
    extra = sorted(set(mapping) - allowed)
    if extra:
        raise ContractError(f"{section}: undeclared keys {extra}")


def _strip_prose(section: dict) -> dict:
    return {k: v for k, v in section.items() if k not in PROSE_KEYS}


def lint(path: Path = CONTRACT, *, import_path: Path = IMPORT,
         node_path: Path = NODE, edge_path: Path = EDGE,
         context_path: Path = CONTEXT) -> None:
    data = _load(path)
    if data.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    _check_closure("top level", data, {"schema_version", "contract"})
    contract = data["contract"]
    if not isinstance(contract, dict):
        raise ContractError("contract must be a mapping")
    if contract.get("id") != "graph-provenance":
        raise ContractError(
            f"contract.id drift: {contract.get('id')!r}")
    _check_closure(
        "contract", contract,
        {"id", "role", "targets", "source_entry", "record", "merge",
         "failures", "errors", "properties", "links", "versioning"})

    for section in ("role", "targets", "source_entry", "record",
                    "merge", "failures", "errors", "properties",
                    "links", "versioning"):
        if section not in contract:
            raise ContractError(f"contract.{section}: missing section")

    role = contract["role"]
    _check_closure("role", role, set(ROLE) | {"rule"})
    _check_exact("role", _strip_prose(role), ROLE)

    targets = contract["targets"]
    _check_closure("targets", targets, set(TARGETS) | {"rule"})
    _check_exact("targets", _strip_prose(targets), TARGETS)

    source_entry = contract["source_entry"]
    _check_closure("source_entry", source_entry,
                   set(SOURCE_ENTRY) | {"rule"})
    _check_exact("source_entry", _strip_prose(source_entry),
                 SOURCE_ENTRY)

    record = contract["record"]
    _check_closure("record", record, set(RECORD) | {"rule"})
    _check_exact("record", _strip_prose(record), RECORD)

    merge = contract["merge"]
    _check_closure("merge", merge, set(MERGE) | {"rule"})
    _check_exact("merge", _strip_prose(merge), MERGE)

    failures = contract["failures"]
    _check_closure("failures", failures,
                   {"classes", "mapping", "closed", "rule"})
    _check_exact("failures.classes", failures.get("classes"),
                 FAILURE_CLASSES)
    _check_exact("failures.mapping", failures.get("mapping"),
                 FAILURE_MAPPING)
    if failures.get("closed") is not True:
        raise ContractError("failures.closed must be true")
    if set(failures["mapping"]) != set(failures["classes"]):
        raise ContractError(
            "failures: mapping keys must equal declared classes")
    undeclared = set(failures["mapping"].values()) - set(ERROR_ENUM)
    if undeclared:
        raise ContractError(
            f"failures: undeclared error codes {sorted(undeclared)}")

    errors = contract["errors"]
    _check_closure("errors", errors, {"closed_enum", "shape", "rule"})
    _check_exact("errors.closed_enum", errors.get("closed_enum"),
                 ERROR_ENUM)
    _check_exact("errors.shape", errors.get("shape"), ERROR_SHAPE)

    properties = contract["properties"]
    _check_closure("properties", properties, set(PROPERTIES) | {"rule"})
    _check_exact("properties", _strip_prose(properties), PROPERTIES)

    links = contract["links"]
    _check_closure("links", links, set(LINKS) | {"rule"})
    _check_exact("links", _strip_prose(links), LINKS)

    versioning = contract["versioning"]
    _check_closure("versioning", versioning,
                   set(VERSIONING) | {"rule"})
    _check_exact("versioning", _strip_prose(versioning), VERSIONING)

    # Linkage: verify against the ACTUAL sibling artifacts.
    imp = _load(import_path)["contract"]
    sources = imp.get("sources", {})
    entries = sources.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ContractError(
            "linkage: import sources registry missing or empty")
    entry_ids = [e.get("id") for e in entries]
    if sorted(entry_ids) != [
            "cbh-licensed", "chesscom-public", "lichess-public",
            "pgn-file", "pgn-folder", "pgn-multi", "pgn-watch"]:
        raise ContractError(
            f"linkage: import sources registry drift: {entry_ids!r}")
    for entry in entries:
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ContractError(
                "linkage: import source entry without id")
        sid = entry["id"]
        if re.fullmatch(r"[a-z][a-z0-9-]*", sid) is None or \
                not sid.isascii():
            raise ContractError(
                f"linkage: import source id {sid!r} is not "
                "delimiter-free printable ASCII - unsafe for the "
                "provenance source entry")
        if entry.get("rights_class") not in set(
                sources.get("rights_classes", [])):
            raise ContractError(
                f"linkage: import source {sid!r} carries an "
                "undeclared rights class")
    if imp.get("idempotency", {}).get("dedup_key") != "game_id":
        raise ContractError(
            "linkage: import dedup key drift - provenance game_id "
            "must be the import contract's dedup identity")

    node = _load(node_path)["contract"]
    node_identity = node.get("identity", {})
    if node_identity.get("equality") != "canonical-field-comparison-only":
        raise ContractError(
            "linkage: node identity equality drift - provenance "
            "targets inherit canonical field comparison")
    if node_identity.get("excluded") != [
            "halfmove_clock", "fullmove_number", "move_order_path",
            "repertoire_context"]:
        raise ContractError(
            "linkage: node identity exclusions drift - the provenance "
            "target identity must exclude exactly the same fields")

    edge = _load(edge_path)["contract"]
    edge_identity = edge.get("identity", {})
    _check_exact("linkage edge identity", {
        k: v for k, v in edge_identity.items()
        if k in ("components", "equality")}, {
        "components": ["variant", "move", "from_node", "to_node"],
        "equality": "canonical-field-comparison-only",
    })

    context = _load(context_path)["contract"]
    context_identity = context.get("identity", {})
    _check_exact("linkage context identity", {
        k: v for k, v in context_identity.items()
        if k in ("key", "equality")}, {
        "key": ["variant", "path_moves"],
        "equality": "exact-path-sequence-comparison-only",
    })


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        lint(path)
    except ContractError as exc:
        print(f"provenance contract lint FAILED: {exc}")
        return 1
    print(f"provenance contract lint ok: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
