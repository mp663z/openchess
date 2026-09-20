"""T0122: chess transposition-node contract lint.

Enforces the normative CONTENT of data/contracts/
transposition_node.yaml by EXACT STRUCTURED COMPARISON, never
prose-marker presence: the graph-node role with its transposition
semantics, the identity tuple sourced from (never restating) the
variant contract's canonical_fields with the exact exclusion list
and the accelerator-only digest role, the exact three-field node
record (registry-id variant, derived pdv1 digest, canonical
six-field FEN snapshot with halfmove-0/fullmove-1 clock
normalization), the merge semantics (insert-or-return-existing,
idempotent, commutative, associative, collision separated by field
comparison, rejected insert changes nothing), the three-class
failure model with its exact mapping (no orphan classes, no
undeclared codes), the error surface shape, the behavioral
properties, the link set, versioning, and linkage VERIFIED AGAINST
THE ACTUAL sibling artifacts (variant canonical_fields / registry /
id_grammar, digest format, FEN geometry and serializations,
en-passant identity value). Anything less passes silently and every
repertoire, delta and diagnosis join rots.

    python tools/transposition_node_contract_lint.py [path]
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

CONTRACT = ROOT / "data" / "contracts" / "transposition_node.yaml"
VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
DIGEST = ROOT / "data" / "contracts" / "position_digest.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"
EN_PASSANT = ROOT / "data" / "contracts" / "en_passant.yaml"

LINKS = {
    "variant_contract": "data/contracts/variant.yaml",
    "position_digest_contract": "data/contracts/position_digest.yaml",
    "fen_contract": "data/contracts/fen.yaml",
    "en_passant_contract": "data/contracts/en_passant.yaml",
}
ROLE = {
    "kind": "graph-node-over-position-identity",
    "transposition": "all-paths-to-one-identity-share-one-node",
    "annotations_attach": "by-node-identity-never-by-path",
    "links": {
        "variant": "data/contracts/variant.yaml",
        "position_digest": "data/contracts/position_digest.yaml",
    },
}
IDENTITY = {
    "source": "from-linked-variant-contract-identity-canonical_fields",
    "accelerator": "from-linked-position-digest-contract-digest",
    "equality": "canonical-field-comparison-only",
    "en_passant_value": "from-linked-position-digest-contract-identity",
    "excluded": ["halfmove_clock", "fullmove_number",
                 "move_order_path", "repertoire_context"],
    "links": {
        "variant": "data/contracts/variant.yaml",
        "position_digest": "data/contracts/position_digest.yaml",
    },
}
RECORD = {
    "fields": ["variant", "digest", "snapshot_fen"],
    "variant_form": "registry-id-verbatim",
    "variant_id_grammar":
        "from-linked-variant-contract-variants-id_grammar",
    "digest_form": "from-linked-position-digest-contract-format",
    "snapshot_form": "canonical-six-field-fen",
    "snapshot_ep_value":
        "from-linked-position-digest-contract-identity",
    "snapshot_clock_normalization": "halfmove-0-fullmove-1",
    "derived_fields_regenerable": ["digest"],
    "links": {
        "fen": "data/contracts/fen.yaml",
        "variant": "data/contracts/variant.yaml",
        "position_digest": "data/contracts/position_digest.yaml",
    },
}
MERGE = {
    "insert": "insert-or-return-existing",
    "idempotent": True,
    "commutative": True,
    "associative": True,
    "same_identity_never_two_nodes": True,
    "digest_collision": "canonical-field-comparison-separates",
    "rejected_insert_changes_nothing": True,
}
FAILURE_CLASSES = ["malformed_node_record", "malformed_position",
                   "unknown_variant"]
FAILURE_MAPPING = {
    "malformed_node_record": "malformed_request",
    "malformed_position": "malformed_request",
    "unknown_variant": "unknown_variant",
}
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
ERROR_ENUM = ["malformed_request", "unknown_variant", "internal"]
PROPERTIES = {
    "path_invariance": "move-order-path-never-changes-the-node",
    "merge_idempotence": "reinsert-same-identity-no-op",
    "merge_commutativity": "insertion-order-never-changes-the-table",
    "snapshot_determinism":
        "normalized-clocks-merge-order-independent",
    "digest_consistency": "record-digest-always-matches-identity",
    "accelerator_only": "digest-never-decides-equality",
    "rollback": "rejected-insert-leaves-table-bit-identical",
}
VERSIONING = {"base_path": "/graph/transposition-node/v1"}
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


def _check_closure(section: str, mapping: dict, allowed: set[str]) -> None:
    extra = sorted(set(mapping) - allowed)
    if extra:
        raise ContractError(f"{section}: undeclared keys {extra}")


def _strip_prose(section: dict) -> dict:
    return {k: v for k, v in section.items() if k not in PROSE_KEYS}


def lint(path: Path = CONTRACT, *, variant_path: Path = VARIANT,
         digest_path: Path = DIGEST, fen_path: Path = FEN,
         en_passant_path: Path = EN_PASSANT) -> None:
    data = _load(path)
    if data.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    _check_closure("top level", data, {"schema_version", "contract"})
    contract = data["contract"]
    if not isinstance(contract, dict):
        raise ContractError("contract must be a mapping")
    if contract.get("id") != "chess-transposition-node":
        raise ContractError(
            f"contract.id drift: {contract.get('id')!r}")
    _check_closure(
        "contract", contract,
        {"id", "role", "identity", "record", "merge", "failures",
         "errors", "properties", "links", "versioning"})

    for section in ("role", "identity", "record", "merge",
                    "failures", "errors", "properties", "links",
                    "versioning"):
        if section not in contract:
            raise ContractError(f"contract.{section}: missing section")

    role = contract["role"]
    _check_closure("role", role, set(ROLE) | {"rule"})
    _check_exact("role", _strip_prose(role), ROLE)

    identity = contract["identity"]
    _check_closure("identity", identity, set(IDENTITY) | {"rule"})
    _check_exact("identity", _strip_prose(identity), IDENTITY)

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
    variant = _load(variant_path)["contract"]
    fields = variant.get("identity", {}).get("canonical_fields")
    if not isinstance(fields, list) or len(fields) != 5:
        raise ContractError(
            "linkage: variant contract identity.canonical_fields "
            "missing or not five fields")
    if fields != ["variant", "board", "side_to_move",
                  "castling_rights", "en_passant"]:
        raise ContractError(
            f"linkage: variant canonical_fields drift: {fields!r}")
    id_grammar = variant.get("variants", {}).get("id_grammar", {})
    _check_exact("linkage variant id_grammar", {
        k: v for k, v in id_grammar.items() if k != "rule"}, {
        "pattern": "^[a-z][a-z0-9_-]*$",
        "alphabet": "printable-ascii-only",
        "whitespace": "forbidden",
        "control_bytes": "forbidden",
    })
    id_pattern = re.compile(id_grammar["pattern"])
    registry = variant.get("variants", {}).get("entries")
    if not isinstance(registry, list) or not registry:
        raise ContractError("linkage: variant registry missing or empty")
    for entry in registry:
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ContractError(
                "linkage: variant registry entry without id")
        vid = entry["id"]
        if id_pattern.fullmatch(vid) is None or not vid.isascii():
            raise ContractError(
                f"linkage: variant id {vid!r} violates the linked "
                "id_grammar - not safe for the node record")

    digest = _load(digest_path)["contract"]
    if digest.get("role", {}).get("kind") != "lookup-accelerator-only":
        raise ContractError(
            "linkage: digest contract role kind drift - the node "
            "contract inherits the accelerator-only role")
    _check_exact("linkage digest format",
                 digest.get("digest", {}).get("format"), {
                     "prefix": "pdv1:",
                     "total_length": 69,
                     "regex": "^pdv1:[0-9a-f]{64}$",
                 })
    if digest.get("identity", {}).get("excluded") != [
            "halfmove_clock", "fullmove_number", "move_order_path",
            "repertoire_context"]:
        raise ContractError(
            "linkage: digest identity exclusions drift - node "
            "identity must exclude exactly the same fields")

    fen = _load(fen_path)["contract"]
    fields_order = fen.get("fields", {}).get("order")
    if fields_order != ["placement", "active_color", "castling",
                        "en_passant", "halfmove_clock",
                        "fullmove_number"]:
        raise ContractError(
            f"linkage: FEN field order drift: {fields_order!r}")
    placement = fen.get("placement", {})
    if placement.get("rank_count") != 8 or placement.get("rank_sum") != 8:
        raise ContractError("linkage: FEN placement geometry drift")
    if fen.get("active_color", {}).get("values") != ["w", "b"]:
        raise ContractError("linkage: FEN active_color values drift")
    castling = fen.get("castling", {})
    if castling.get("order") != "KQkq" or \
            castling.get("none_sentinel") != "-":
        raise ContractError("linkage: FEN castling serialization drift")

    ep = _load(en_passant_path)["contract"]
    svi = ep.get("target", {}).get("storage_vs_identity", {})
    _check_exact("linkage en_passant identity_value",
                 svi.get("identity_value"),
                 "target-when-legal-capture-else-none")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        lint(path)
    except ContractError as exc:
        print(f"transposition-node contract lint FAILED: {exc}")
        return 1
    print(f"transposition-node contract lint ok: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
