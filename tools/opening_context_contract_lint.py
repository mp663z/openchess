"""T0140: chess opening-context contract lint.

Enforces the normative CONTENT of data/contracts/opening_context.yaml
by EXACT STRUCTURED COMPARISON, never prose-marker presence: the
path-attributed classification role with the identity boundary against
the transposition-node contract (node identity EXCLUDES
move_order_path; this contract is where the path is load-bearing), the
registry shape and invariants (exact entry shape, code grammar from
the registry itself, unique codes, unique NONEMPTY move sequences,
nonempty names, moves per the linked legal-moves move_model), the
resolution rule (longest registry prefix wins, none sentinel, empty
path always unclassified, determinism, prefix stability), the exact
four-field context record (both-sentinels-or-neither), the merge
semantics (insert-or-return-existing, conflicting_context rejected,
rejected insert changes nothing), the five-class failure model with
its exact mapping, the error surface shape, the behavioral properties,
the link set, versioning, and linkage VERIFIED AGAINST THE ACTUAL
sibling artifacts AND the actual opening registry data. Anything less
passes silently and every theory-currency relevance join rots.

    python tools/opening_context_contract_lint.py [path]
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

CONTRACT = ROOT / "data" / "contracts" / "opening_context.yaml"
VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
LEGAL_MOVES = ROOT / "data" / "contracts" / "legal_moves.yaml"
NODE = ROOT / "data" / "contracts" / "transposition_node.yaml"
REGISTRY = ROOT / "data" / "openings" / "registry.yaml"

LINKS = {
    "variant_contract": "data/contracts/variant.yaml",
    "legal_moves_contract": "data/contracts/legal_moves.yaml",
    "transposition_node_contract":
        "data/contracts/transposition_node.yaml",
    "opening_registry": "data/openings/registry.yaml",
}
ROLE = {
    "kind": "path-attributed-opening-classification",
    "attribution":
        "by-variant-plus-ordered-move-path-never-by-node-identity",
    "identity_boundary":
        "node-contract-excludes-move-order-path-this-contract-owns-it",
    "origin": "variant-initial-position",
    "links": {
        "variant": "data/contracts/variant.yaml",
        "transposition_node": "data/contracts/transposition_node.yaml",
    },
}
REGISTRY_PINS = {
    "source": "data/openings/registry.yaml",
    "entries_shape": ["code", "name", "moves"],
    "code_grammar": "from-linked-registry-code_grammar",
    "codes_unique": True,
    "move_sequences_unique": True,
    "move_sequences_nonempty": True,
    "names_nonempty": True,
    "moves_form":
        "from-linked-legal-moves-contract-move_model-long-algebraic",
    "none_sentinel": "-",
}
CODE_GRAMMAR = {
    "pattern": "^[A-E][0-9][0-9]$",
    "alphabet": "printable-ascii-only",
    "whitespace": "forbidden",
    "control_bytes": "forbidden",
}
IDENTITY = {
    "key": ["variant", "path_moves"],
    "path": "ordered-list-of-move-model-texts",
    "equality": "exact-path-sequence-comparison-only",
    "excluded": ["node_canonical_identity", "move_timestamps",
                 "source_game"],
}
RESOLUTION = {
    "match": "longest-registry-prefix-wins",
    "prefix": "entry-moves-equal-path-prefix-exactly",
    "no_match": "none-sentinel",
    "empty_path": "always-unclassified",
    "determinism": "same-variant-plus-path-always-same-context",
    "extension_refinement":
        "extension-resolves-to-prior-or-strictly-longer-extending-entry",
}
RECORD = {
    "fields": ["variant", "path_moves", "opening_code",
               "opening_name"],
    "variant_form": "registry-id-verbatim",
    "path_form": "ordered-list-of-move-model-texts",
    "unclassified": "both-code-and-name-none-sentinel",
    "derived_fields_stored": "none",
    "links": {
        "variant": "data/contracts/variant.yaml",
        "legal_moves": "data/contracts/legal_moves.yaml",
    },
}
MERGE = {
    "insert": "insert-or-return-existing",
    "atomic": True,
    "consumes_records":
        "exact-stored-records-validated-against-receiver-registry",
    "idempotent": True,
    "commutative": True,
    "associative": True,
    "same_key_never_two_records": True,
    "conflicting_context": "rejected-as-conflicting_context",
    "conflict_in_batch": "whole-merge-rejected-nothing-committed",
    "rejected_insert_changes_nothing": True,
}
FAILURE_CLASSES = ["malformed_context_record", "malformed_path",
                   "unknown_variant", "unknown_opening_code",
                   "conflicting_context"]
FAILURE_MAPPING = {
    "malformed_context_record": "malformed_request",
    "malformed_path": "malformed_request",
    "unknown_variant": "unknown_variant",
    "unknown_opening_code": "unknown_opening_code",
    "conflicting_context": "conflicting_context",
}
ERROR_ENUM = ["malformed_request", "unknown_variant",
              "unknown_opening_code", "conflicting_context",
              "internal"]
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
    "path_attribution":
        "same-node-different-paths-may-carry-different-contexts",
    "longest_prefix": "longest-matching-registry-entry-wins",
    "extension_refinement":
        "extension-resolves-to-prior-or-strictly-longer-extending-entry",
    "shorter_key_immutability":
        "stored-shorter-records-never-change-under-extensions",
    "resolution_determinism":
        "same-variant-plus-path-always-same-context",
    "merge_idempotence": "reinsert-same-record-no-op",
    "merge_commutativity": "insertion-order-never-changes-the-table",
    "rollback": "rejected-insert-leaves-table-bit-identical",
}
VERSIONING = {"base_path": "/graph/opening-context/v1"}
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


def _valid_move_text(lm: dict, move: object) -> bool:
    """A move text is well-formed exactly per the linked legal-moves
    move_model: square then square then an optional promotion letter,
    grammar and enum READ from the sibling."""
    shape = lm.get("move_model", {}).get("shape", {})
    grammar = lm.get("move_model", {}).get("square_grammar", {})
    promo = shape.get("types", {}).get("promotion", {}).get("enum", [])
    files = grammar.get("files", [])
    ranks = grammar.get("ranks", [])
    if not isinstance(move, str) or not move.isascii():
        return False
    if len(move) not in (4, 5):
        return False
    if (move[0] not in files or move[1] not in ranks
            or move[2] not in files or move[3] not in ranks):
        return False
    if shape.get("from_to_distinct") and move[:2] == move[2:4]:
        return False
    return not (len(move) == 5 and move[4] not in promo)


def lint(path: Path = CONTRACT, *, variant_path: Path = VARIANT,
         legal_moves_path: Path = LEGAL_MOVES, node_path: Path = NODE,
         registry_path: Path = REGISTRY) -> None:
    data = _load(path)
    if data.get("schema_version") != 1:
        raise ContractError("schema_version must be 1")
    _check_closure("top level", data, {"schema_version", "contract"})
    contract = data["contract"]
    if not isinstance(contract, dict):
        raise ContractError("contract must be a mapping")
    if contract.get("id") != "chess-opening-context":
        raise ContractError(
            f"contract.id drift: {contract.get('id')!r}")
    _check_closure(
        "contract", contract,
        {"id", "role", "registry", "identity", "resolution",
         "record", "merge", "failures", "errors", "properties",
         "links", "versioning"})

    for section in ("role", "registry", "identity", "resolution",
                    "record", "merge", "failures", "errors",
                    "properties", "links", "versioning"):
        if section not in contract:
            raise ContractError(f"contract.{section}: missing section")

    role = contract["role"]
    _check_closure("role", role, set(ROLE) | {"rule"})
    _check_exact("role", _strip_prose(role), ROLE)

    registry = contract["registry"]
    _check_closure("registry", registry, set(REGISTRY_PINS) | {"rule"})
    _check_exact("registry", _strip_prose(registry), REGISTRY_PINS)

    identity = contract["identity"]
    _check_closure("identity", identity, set(IDENTITY) | {"rule"})
    _check_exact("identity", _strip_prose(identity), IDENTITY)

    resolution = contract["resolution"]
    _check_closure("resolution", resolution,
                   set(RESOLUTION) | {"rule"})
    _check_exact("resolution", _strip_prose(resolution), RESOLUTION)

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
    registry_ids = variant.get("variants", {}).get("entries")
    if not isinstance(registry_ids, list) or not registry_ids:
        raise ContractError("linkage: variant registry missing or empty")
    for entry in registry_ids:
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ContractError(
                "linkage: variant registry entry without id")
        vid = entry["id"]
        if id_pattern.fullmatch(vid) is None or not vid.isascii():
            raise ContractError(
                f"linkage: variant id {vid!r} violates the linked "
                "id_grammar - not safe for the context record")

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
    _check_exact("linkage legal-moves promotion enum",
                 shape.get("types", {}).get("promotion"), {
                     "kind": "string",
                     "enum": ["q", "r", "b", "n"],
                     "presence": "required-exactly-when-promoting",
                 })
    sqg = move_model.get("square_grammar", {})
    _check_exact("linkage legal-moves square grammar", {
        k: v for k, v in sqg.items() if k != "rule"}, {
        "files": ["a", "b", "c", "d", "e", "f", "g", "h"],
        "ranks": ["1", "2", "3", "4", "5", "6", "7", "8"],
        "form": "exactly-file-char-then-rank-char",
    })

    node = _load(node_path)["contract"]
    node_identity = node.get("identity", {})
    if node_identity.get("equality") != "canonical-field-comparison-only":
        raise ContractError(
            "linkage: node identity equality drift - the context "
            "contract's identity boundary depends on canonical "
            "field comparison")
    if node_identity.get("excluded") != [
            "halfmove_clock", "fullmove_number", "move_order_path",
            "repertoire_context"]:
        raise ContractError(
            "linkage: node identity exclusions drift - the context "
            "contract owns exactly the move_order_path the node "
            "contract excludes")

    # The ACTUAL opening registry data, verified against every
    # invariant the contract pins.
    reg_doc = _load(registry_path)
    if reg_doc.get("schema_version") != 1:
        raise ContractError("registry: schema_version must be 1")
    _check_closure("registry top level", reg_doc,
                   {"schema_version", "registry"})
    reg = reg_doc["registry"]
    if not isinstance(reg, dict):
        raise ContractError("registry: must be a mapping")
    if reg.get("id") != "chess-opening-registry":
        raise ContractError(
            f"registry.id drift: {reg.get('id')!r}")
    _check_closure("registry", reg,
                   {"id", "code_grammar", "none_sentinel", "entries"})
    _check_exact("registry code_grammar", {
        k: v for k, v in reg.get("code_grammar", {}).items()
        if k != "rule"}, CODE_GRAMMAR)
    if reg.get("none_sentinel") != REGISTRY_PINS["none_sentinel"]:
        raise ContractError("registry: none_sentinel drift")
    entries = reg.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ContractError("registry: entries missing or empty")
    code_pattern = re.compile(CODE_GRAMMAR["pattern"])
    seen_codes = set()
    seen_sequences = set()
    for i, entry in enumerate(entries):
        where = f"registry entry {i}"
        if not isinstance(entry, dict):
            raise ContractError(f"{where}: must be a mapping")
        _check_closure(where, entry, {"code", "name", "moves"})
        code = entry.get("code")
        if (not isinstance(code, str) or not code.isascii()
                or code_pattern.fullmatch(code) is None):
            raise ContractError(
                f"{where}: code {code!r} violates the pinned "
                "code_grammar")
        if code in seen_codes:
            raise ContractError(f"{where}: duplicate code {code!r}")
        seen_codes.add(code)
        name = entry.get("name")
        if (not isinstance(name, str) or not name
                or not name.isascii()):
            raise ContractError(
                f"{where}: name must be nonempty printable ASCII")
        moves = entry.get("moves")
        if (not isinstance(moves, list) or not moves):
            raise ContractError(
                f"{where}: moves must be a nonempty list")
        for move in moves:
            if not _valid_move_text(lm, move):
                raise ContractError(
                    f"{where}: move {move!r} violates the linked "
                    "legal-moves move_model")
        seq = tuple(moves)
        if seq in seen_sequences:
            raise ContractError(
                f"{where}: duplicate move sequence "
                f"{list(seq)!r} - longest-prefix resolution "
                "could tie")
        seen_sequences.add(seq)


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        lint(path)
    except ContractError as exc:
        print(f"opening-context contract lint FAILED: {exc}")
        return 1
    print(f"opening-context contract lint ok: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
