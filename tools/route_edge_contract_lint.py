"""T0131: chess route-edge contract lint.

Enforces the normative CONTENT of data/contracts/route_edge.yaml by
EXACT STRUCTURED COMPARISON, never prose-marker presence: the
graph-edge role with its route semantics, the canonical four-tuple
edge identity sourced from (never restating) the transposition-node
contract's identity with the exact exclusion list and the
accelerator-only bucket role, the move form read from the linked
legal-moves move_model with application DELEGATED and determinism
PINNED, the exact four-field edge record (registry-id variant,
long-algebraic move, two canonical snapshots, no stored derived
fields), the merge semantics (insert-or-return-existing,
idempotent, commutative, associative, determinism enforced at
insert, rejected insert changes nothing), the four-class failure
model with its exact mapping (no orphan classes, no undeclared
codes), the error surface shape, the behavioral properties, the
link set, versioning, and linkage VERIFIED AGAINST THE ACTUAL
sibling artifacts (variant registry / id_grammar, FEN geometry and
serializations, transposition-node record snapshot pins,
legal-moves move_model, digest accelerator role). Anything less
passes silently and every repertoire, delta and diagnosis join
rots.

    python tools/route_edge_contract_lint.py [path]
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

CONTRACT = ROOT / "data" / "contracts" / "route_edge.yaml"
VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"
DIGEST = ROOT / "data" / "contracts" / "position_digest.yaml"
NODE = ROOT / "data" / "contracts" / "transposition_node.yaml"
LEGAL_MOVES = ROOT / "data" / "contracts" / "legal_moves.yaml"

LINKS = {
    "variant_contract": "data/contracts/variant.yaml",
    "fen_contract": "data/contracts/fen.yaml",
    "position_digest_contract": "data/contracts/position_digest.yaml",
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "legal_moves_contract": "data/contracts/legal_moves.yaml",
}
ROLE = {
    "kind": "graph-edge-between-nodes-over-one-move",
    "direction": "from-node-via-one-move-to-node",
    "annotations_attach": "by-edge-identity-never-by-path",
    "links": {
        "transposition_node": "data/contracts/transposition_node.yaml",
        "legal_moves": "data/contracts/legal_moves.yaml",
    },
}
IDENTITY = {
    "components": ["variant", "move", "from_node", "to_node"],
    "source": "from-linked-transposition-node-contract-identity",
    "equality": "canonical-field-comparison-only",
    "accelerator": "from-digest-plus-move-bucket-only-never-equality",
    "excluded": ["move_order_path", "repertoire_context",
                 "halfmove_clock", "fullmove_number"],
    "links": {
        "transposition_node": "data/contracts/transposition_node.yaml",
        "variant": "data/contracts/variant.yaml",
    },
}
MOVE = {
    "form": "from-linked-legal-moves-contract-move_model-long-algebraic",
    "shape": "from-square-then-to-square-then-optional-promotion-letter",
    "from_to_distinct": True,
    "promotion_enum": "from-linked-legal-moves-contract-move_model",
    "application": "delegated-to-legal-moves-position-runtime-layer",
    "determinism": "same-from-plus-move-never-two-target-identities",
    "links": {
        "legal_moves": "data/contracts/legal_moves.yaml",
    },
}
RECORD = {
    "fields": ["variant", "move", "from_snapshot_fen",
               "to_snapshot_fen"],
    "variant_form": "registry-id-verbatim",
    "variant_id_grammar":
        "from-linked-variant-contract-variants-id_grammar",
    "move_form":
        "from-linked-legal-moves-contract-move_model-long-algebraic",
    "snapshot_form":
        "from-linked-transposition-node-contract-record-snapshot",
    "derived_fields_stored": "none",
    "links": {
        "variant": "data/contracts/variant.yaml",
        "legal_moves": "data/contracts/legal_moves.yaml",
        "transposition_node": "data/contracts/transposition_node.yaml",
        "fen": "data/contracts/fen.yaml",
    },
}
MERGE = {
    "insert": "insert-or-return-existing",
    "atomic": True,
    "idempotent": True,
    "commutative": True,
    "associative": True,
    "same_edge_never_two_records": True,
    "conflicting_target": "rejected-as-conflicting_edge",
    "conflict_in_batch": "whole-merge-rejected-nothing-committed",
    "rejected_insert_changes_nothing": True,
}
FAILURE_CLASSES = ["malformed_edge_record", "malformed_position",
                   "unknown_variant", "conflicting_edge"]
FAILURE_MAPPING = {
    "malformed_edge_record": "malformed_request",
    "malformed_position": "malformed_request",
    "unknown_variant": "unknown_variant",
    "conflicting_edge": "conflicting_edge",
}
ERROR_ENUM = ["malformed_request", "unknown_variant",
              "conflicting_edge", "internal"]
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
    "path_invariance": "move-order-path-never-changes-the-edge",
    "target_determinism": "one-from-plus-move-exactly-one-target",
    "merge_idempotence": "reinsert-same-edge-no-op",
    "merge_commutativity": "insertion-order-never-changes-the-table",
    "accelerator_only": "bucket-never-decides-equality",
    "rollback": "rejected-insert-leaves-table-bit-identical",
}
VERSIONING = {"base_path": "/graph/route-edge/v1"}
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


def lint(path: Path = CONTRACT, *, variant_path: Path = VARIANT,
         fen_path: Path = FEN, digest_path: Path = DIGEST,
         node_path: Path = NODE,
         legal_moves_path: Path = LEGAL_MOVES) -> None:
    data = _load(path)
    if data.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    _check_closure("top level", data, {"schema_version", "contract"})
    contract = data["contract"]
    if not isinstance(contract, dict):
        raise ContractError("contract must be a mapping")
    if contract.get("id") != "chess-route-edge":
        raise ContractError(
            f"contract.id drift: {contract.get('id')!r}")
    _check_closure(
        "contract", contract,
        {"id", "role", "identity", "move", "record", "merge",
         "failures", "errors", "properties", "links", "versioning"})

    for section in ("role", "identity", "move", "record", "merge",
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

    move = contract["move"]
    _check_closure("move", move, set(MOVE) | {"rule"})
    _check_exact("move", _strip_prose(move), MOVE)

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
                "id_grammar - not safe for the edge record")

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

    digest = _load(digest_path)["contract"]
    if digest.get("role", {}).get("kind") != "lookup-accelerator-only":
        raise ContractError(
            "linkage: digest contract role kind drift - the edge "
            "contract inherits the accelerator-only role")
    _check_exact("linkage digest format",
                 digest.get("digest", {}).get("format"), {
                     "prefix": "pdv1:",
                     "total_length": 69,
                     "regex": "^pdv1:[0-9a-f]{64}$",
                 })

    node = _load(node_path)["contract"]
    node_record = node.get("record", {})
    _check_exact("linkage node snapshot form", {
        k: v for k, v in node_record.items()
        if k in ("snapshot_form", "snapshot_ep_value",
                 "snapshot_clock_normalization")}, {
        "snapshot_form": "canonical-six-field-fen",
        "snapshot_ep_value":
            "from-linked-position-digest-contract-identity",
        "snapshot_clock_normalization": "halfmove-0-fullmove-1",
    })
    node_identity = node.get("identity", {})
    if node_identity.get("equality") != "canonical-field-comparison-only":
        raise ContractError(
            "linkage: node identity equality drift - the edge "
            "contract inherits canonical field comparison")
    if node_identity.get("excluded") != [
            "halfmove_clock", "fullmove_number", "move_order_path",
            "repertoire_context"]:
        raise ContractError(
            "linkage: node identity exclusions drift - edge identity "
            "must exclude exactly the same fields")

    lm = _load(legal_moves_path)["contract"]
    move_model = lm.get("move_model", {})
    shape = move_model.get("shape", {})
    _check_exact("linkage legal-moves move_model shape", {
        k: v for k, v in shape.items() if k != "types"}, {
        "required": ["from_square", "to_square"],
        "optional": ["promotion"],
        "closed_keys": True,
        "from_to_distinct": True,
    })
    types = shape.get("types", {})
    _check_exact("linkage legal-moves promotion enum",
                 types.get("promotion"), {
                     "kind": "string",
                     "enum": ["q", "r", "b", "n"],
                     "presence": "required-exactly-when-promoting",
                 })
    sqg = lm.get("move_model", {}).get("square_grammar", {})
    _check_exact("linkage legal-moves square grammar", {
        k: v for k, v in sqg.items() if k != "rule"}, {
        "files": ["a", "b", "c", "d", "e", "f", "g", "h"],
        "ranks": ["1", "2", "3", "4", "5", "6", "7", "8"],
        "form": "exactly-file-char-then-rank-char",
    })


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        lint(path)
    except ContractError as exc:
        print(f"route-edge contract lint FAILED: {exc}")
        return 1
    print(f"route-edge contract lint ok: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
