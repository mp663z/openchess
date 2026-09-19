"""T0059: chess castling contract lint.

Enforces the normative CONTENT of data/contracts/castling.yaml by EXACT
STRUCTURED COMPARISON, never prose-marker presence: the rights value
set and canonical form (never empty-string), home-square pairs,
irrevocability, the exact loss mapping (king move loses both sides'
rights; rook from/on home square loses exactly that right), the exact
per-side move paths (king two squares toward the rook, rook to the
transit square, exact empty_required and king_transit squares), the
six preconditions, turn linkage (exactly one transition, halfmove
increment, fullmove when black), identity participation, the explicit
failure_mapping (no orphan classes, no undeclared error codes), the
closed error enum + exact shape, and versioning. Cross-artifact
linkage is VERIFIED AGAINST THE ACTUAL variant and turn contract
artifacts (each linted first): the variant contract pins castling
rights as canonical identity fields and orthodox castling kind; the
turn contract's halfmove reset set must NOT contain a castling kind.

    python tools/castling_contract_lint.py [path]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.turn_contract_lint import lint as _turn_lint  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402
from tools.variant_contract_lint import lint as _variant_lint  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "castling.yaml"

RIGHTS_VALUES = ["K", "Q", "k", "q"]
RIGHTS_GRAMMAR = {
    "none_sentinel": "-",
    "ordering": "KQkq",
    "duplicates": "forbidden",
    "empty": "forbidden",
    "membership": "subset-of-values",
}
HOME_SQUARES = {
    "K": {"king": "e1", "rook": "h1"},
    "Q": {"king": "e1", "rook": "a1"},
    "k": {"king": "e8", "rook": "h8"},
    "q": {"king": "e8", "rook": "a8"},
}
LOSS = {
    "on_king_move": ["K", "Q"],
    "on_king_move_black": ["k", "q"],
    "on_rook_move_from": {"h1": "K", "a1": "Q", "h8": "k", "a8": "q"},
    "on_rook_capture_on": {"h1": "K", "a1": "Q", "h8": "k", "a8": "q"},
}
MOVE_PATHS = {
    "K": {"king_from": "e1", "king_to": "g1", "rook_from": "h1", "rook_to": "f1",
          "king_transit": ["f1"], "empty_required": ["f1", "g1"]},
    "Q": {"king_from": "e1", "king_to": "c1", "rook_from": "a1", "rook_to": "d1",
          "king_transit": ["d1"], "empty_required": ["d1", "c1", "b1"]},
    "k": {"king_from": "e8", "king_to": "g8", "rook_from": "h8", "rook_to": "f8",
          "king_transit": ["f8"], "empty_required": ["f8", "g8"]},
    "q": {"king_from": "e8", "king_to": "c8", "rook_from": "a8", "rook_to": "d8",
          "king_transit": ["d8"], "empty_required": ["d8", "c8", "b8"]},
}
KING_DELTAS = "exactly-two-squares-toward-rook"
PRECONDITIONS = [
    "right held for that side and path",
    "king and rook on their home squares",
    "empty_required squares all empty",
    "king not in check on king_from",
    "king not attacked on any king_transit square",
    "king not attacked on king_to",
]
TURN_LINKAGE = {"transitions": "exactly-one", "halfmove_clock": "increment",
                "fullmove_number": "increment-when-black"}
FAILURE_CLASSES = ["rights_malformed", "rights_inconsistent", "path_blocked",
                   "king_unmoved_required", "through_check"]
FAILURE_MAPPING = {
    "rights_malformed": {"trigger": "rights-field-not-canonical",
                         "error": "malformed_request"},
    "rights_inconsistent": {"trigger": "right-held-but-home-squares-not-occupied",
                            "error": "illegal_position"},
    "path_blocked": {"trigger": "empty_required-square-occupied",
                     "error": "illegal_move"},
    "king_unmoved_required": {"trigger": "right-not-held-or-piece-already-moved",
                              "error": "illegal_move"},
    "through_check": {"trigger": "king-from-transit-or-to-attacked",
                      "error": "illegal_move"},
}
ERROR_ENUM = ["malformed_request", "illegal_position", "illegal_move", "internal"]
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
         "turn_contract": "data/contracts/turn.yaml"}
VERSIONING = {"base_path": "/castling/v1"}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {"id", "rights", "loss", "move", "turn_linkage", "identity",
                    "failure_classes", "failure_mapping", "errors", "links",
                    "versioning"}
ALLOWED_PATH = {"king_from", "king_to", "rook_from", "rook_to", "king_transit",
                "empty_required"}
ALLOWED_LOSS = {"on_king_move", "on_king_move_black", "on_rook_move_from",
                "on_rook_capture_on", "rule"}


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(f"castling contract: {problem}")


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


def _check_links(links: dict, root: Path) -> None:
    _strict_eq(links, LINKS, "contract.links")
    vdoc = yaml.safe_load((root / LINKS["variant_contract"]).read_text())
    _variant_lint(vdoc)  # lint BEFORE relying on the linked artifact
    variants = vdoc["contract"]["variants"]["entries"]
    standard = next((e for e in variants if e["id"] == "standard"), None)
    _need(standard is not None, "linked variant contract: no standard entry")
    _need(standard.get("castling") == "orthodox",
          "linked variant contract: standard castling kind is not orthodox")
    canonical = vdoc["contract"]["identity"]["canonical_fields"]
    _need("castling_rights" in canonical,
          "linked variant contract: castling_rights not a canonical identity field")
    tdoc = yaml.safe_load((root / LINKS["turn_contract"]).read_text())
    _turn_lint(tdoc)
    reset_when = tdoc["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_when"]
    _need("castling" not in reset_when and "castle" not in reset_when,
          "linked turn contract: castling must never reset the halfmove clock")
    errors = tdoc["contract"]["errors"]["closed_enum"]
    _need(type(errors) is list and errors, "linked turn contract: empty error enum")


def lint(doc: object, root: Path = ROOT) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _need(doc.get("schema_version") == 1 and type(doc.get("schema_version")) is int,
          "schema_version: exact int 1")
    c = _get(doc, "contract", "document")
    _keys(c, ALLOWED_CONTRACT, "contract")
    for required in ALLOWED_CONTRACT:
        _get(c, required, "contract")
    _need(c["id"] == "chess-castling", "contract.id must be chess-castling")

    rights = c["rights"]
    _keys(rights, {"values", "grammar", "canonical_form", "home_squares",
                   "irrevocable", "rule"}, "contract.rights")
    _strict_eq(_get(rights, "values", "contract.rights"),
               RIGHTS_VALUES, "contract.rights.values")
    _strict_eq(_get(rights, "grammar", "contract.rights"),
               RIGHTS_GRAMMAR, "contract.rights.grammar")
    _need(_get(rights, "irrevocable", "contract.rights") is True,
          "contract.rights.irrevocable must be true")
    _strict_eq(_get(rights, "home_squares", "contract.rights"),
               HOME_SQUARES, "contract.rights.home_squares")
    _text(_get(rights, "canonical_form", "contract.rights"),
          "contract.rights.canonical_form")
    _text(_get(rights, "rule", "contract.rights"), "contract.rights.rule")

    loss = _get(c, "loss", "contract")
    _keys(loss, ALLOWED_LOSS, "contract.loss")
    _strict_eq({k: v for k, v in loss.items() if k != "rule"}, LOSS, "contract.loss")
    _text(_get(loss, "rule", "contract.loss"), "contract.loss.rule")

    move = _get(c, "move", "contract")
    _keys(move, {"per_side_paths", "king_deltas", "preconditions", "rule"},
          "contract.move")
    paths = _get(move, "per_side_paths", "contract.move")
    _need(type(paths) is dict, "contract.move.per_side_paths: mapping required")
    for side, path in paths.items():
        _keys(path, ALLOWED_PATH, f"contract.move.per_side_paths.{side}")
    _strict_eq(paths, MOVE_PATHS, "contract.move.per_side_paths")
    _need(_get(move, "king_deltas", "contract.move") == KING_DELTAS,
          "contract.move.king_deltas")
    _strict_eq(_get(move, "preconditions", "contract.move"),
               PRECONDITIONS, "contract.move.preconditions")
    _text(_get(move, "rule", "contract.move"), "contract.move.rule")
    # internal consistency: home squares and paths agree per right
    for right, home in HOME_SQUARES.items():
        path = MOVE_PATHS[right]
        _need(path["king_from"] == home["king"] and path["rook_from"] == home["rook"],
              f"{right}: move path disagrees with home squares")
        _need(path["king_to"] in path["empty_required"],
              f"{right}: king_to must be empty_required")
        for t in path["king_transit"]:
            _need(t in path["empty_required"], f"{right}: transit square {t} not emptied")

    linkage = _get(c, "turn_linkage", "contract")
    _keys(linkage, {"transitions", "halfmove_clock", "fullmove_number", "rule"},
          "contract.turn_linkage")
    _strict_eq({k: v for k, v in linkage.items() if k != "rule"},
               TURN_LINKAGE, "contract.turn_linkage.fields")
    _text(_get(linkage, "rule", "contract.turn_linkage"),
          "contract.turn_linkage.rule")

    identity = _get(c, "identity", "contract")
    _keys(identity, {"rights_participate", "rule"}, "contract.identity")
    _need(_get(identity, "rights_participate", "contract.identity") is True,
          "contract.identity.rights_participate must be true")
    _text(_get(identity, "rule", "contract.identity"), "contract.identity.rule")

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
    _strict_eq(_get(c, "versioning", "contract"), VERSIONING, "contract.versioning")
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
    print("OK castling contract lint: chess castling contract v1 clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
