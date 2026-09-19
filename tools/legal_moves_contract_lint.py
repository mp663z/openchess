#!/usr/bin/env python3
"""T0077: exact structured lint for the chess legal-moves contract.

Compares every normative structured field of data/contracts/legal_moves.yaml
against the pinned values below - prose rules are checked only for presence
as nonempty documentation strings, never for content. Also validates the
cross-artifact linkage: the variant, turn, castling and en-passant contracts
must exist, pass their own lints, and carry the fields this contract relies
on (including the turn contract's ownership of every non-move-set terminal
outcome). Any deviation raises ContractError and the CLI exits nonzero.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CONTRACT = ROOT / "data" / "contracts" / "legal_moves.yaml"

from tools.castling_contract_lint import lint as _castling_lint  # noqa: E402
from tools.en_passant_contract_lint import lint as _ep_lint  # noqa: E402
from tools.turn_contract_lint import lint as _turn_lint  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402
from tools.variant_contract_lint import lint as _variant_lint  # noqa: E402

MOVE_SHAPE = {
    "required": ["from_square", "to_square"],
    "optional": ["promotion"],
    "closed_keys": True,
    "from_to_distinct": True,
    "types": {
        "from_square": {"kind": "string", "grammar": "square"},
        "to_square": {"kind": "string", "grammar": "square"},
        "promotion": {"kind": "string", "enum": ["q", "r", "b", "n"],
                      "presence": "required-exactly-when-promoting"},
    },
}
SQUARE_GRAMMAR = {
    "files": list("abcdefgh"),
    "ranks": ["1", "2", "3", "4", "5", "6", "7", "8"],
    "form": "exactly-file-char-then-rank-char",
}
PROMO_EXPANSION = {
    "expands_to": 4,
    "values": ["q", "r", "b", "n"],
    "applies_to": ["quiet", "capture"],
    "unpromoted_last_rank_move": "none",
}
MOVE_MODEL_EXTRA = {
    "promotion_required": "pawn-reaching-promotion-rank",
    "promotion_forbidden": "all-other-moves",
}
KNIGHT_DELTAS = [[1, 2], [2, 1], [-1, 2], [-2, 1], [1, -2], [2, -1],
                 [-1, -2], [-2, -1]]
KING_DELTAS = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1],
               [-1, 1], [-1, -1]]
ROOK_DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]]
BISHOP_DIRS = [[1, 1], [1, -1], [-1, 1], [-1, -1]]
QUEEN_DIRS = ROOK_DIRS + BISHOP_DIRS
PAWN = {
    "forward": {
        "white": {"file_delta": 0, "rank_delta": 1},
        "black": {"file_delta": 0, "rank_delta": -1},
    },
    "forward_empty": "single-square-advance-requires-empty",
    "double": {"requires": ["on-start-rank", "both-squares-empty"],
               "ranks": {"white": "2", "black": "7"}},
    "capture_deltas": {
        "white": [[1, 1], [-1, 1]],
        "black": [[1, -1], [-1, -1]],
    },
    "capture": "diagonal-one-square-requires-enemy-or-en-passant",
    "promotion_ranks": {"white": "8", "black": "1"},
    "never_backward": True,
}
OCCUPANCY = {
    "own_piece_square": "unreachable",
    "enemy_piece_square": "capture-only",
    "enemy_king_square": "never-a-capture-target-check-and-checkmate-terminate-first",
    "sliding_block": "any-piece-ends-the-ray-before-it",
}
ATTACK = {
    "definition": "pseudo-legal-capture-to-square",
    "attacker_king_safety": "ignored",
    "target_occupancy": {"empty": "attacked", "enemy_occupied": "attacked",
                         "own_occupied": "attacked"},
    "king_attacks": "adjacent-squares",
    "pawn_attacks": "diagonal-forward-squares",
    "castling_transit": "king-transit-squares-evaluated-with-this-relation",
}
LEGALITY = {
    "filter": "resulting-position-leaves-own-king-unattacked",
    "in_check_rule": "while-in-check-only-evasions-legal",
    "opponent_king_capture": "forbidden-king-squares-attackable-but-never-legal-destinations",
}
MOVE_SET_TERMINAL = {
    "yields": ["check", "checkmate", "stalemate"],
    "check": "side-to-move-king-attacked",
    "checkmate": {"requires": ["check", "zero-legal-moves"]},
    "stalemate": {"requires": ["no-check", "zero-legal-moves"]},
    "scope": "move-set-and-check-state-only",
    "other_outcomes": {
        "owner": "chess-turn-contract",
        "states": ["resignation", "timeout", "draw_agreement",
                   "fifty_move_claim", "seventyfive_move_auto",
                   "fivefold_auto", "threefold_claim",
                   "insufficient_material", "variant_specific"],
    },
}
LINKAGE = {
    "castling": "castling-contract-owns-rights-paths-preconditions",
    "en_passant": "en-passant-contract-owns-target-capture-preconditions",
    "turn": "turn-contract-owns-clocks-advance-and-non-move-set-termination",
    "variant": "variant-contract-owns-identity-and-start",
}
FAILURE_CLASSES = ["malformed_move", "no_piece", "not_players_piece",
                   "unreachable_target", "promotion_missing",
                   "promotion_forbidden", "leaves_king_attacked",
                   "wrong_variant"]
FAILURE_MAPPING = {
    "malformed_move": {"trigger": "move-fails-move_model-shape-schema",
                       "error": "malformed_request"},
    "no_piece": {"trigger": "from-square-empty", "error": "illegal_move"},
    "not_players_piece": {"trigger": "from-square-holds-enemy-piece",
                          "error": "illegal_move"},
    "unreachable_target": {"trigger": "to-square-not-pseudo-legal-for-the-piece",
                           "error": "illegal_move"},
    "promotion_missing": {"trigger": "pawn-reaches-promotion-rank-without-promotion-member",
                          "error": "illegal_move"},
    "promotion_forbidden": {"trigger": "promotion-member-on-non-promotion-move-or-outside-enum",
                            "error": "illegal_move"},
    "leaves_king_attacked": {"trigger": "resulting-position-leaves-own-king-attacked",
                             "error": "illegal_move"},
    "wrong_variant": {"trigger": "move-references-state-outside-the-active-variant",
                      "error": "malformed_request"},
}
ERROR_ENUM = ["malformed_request", "illegal_move", "internal"]
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
         "turn_contract": "data/contracts/turn.yaml",
         "castling_contract": "data/contracts/castling.yaml",
         "en_passant_contract": "data/contracts/en_passant.yaml"}
VERSIONING = {
    "base_path": "/legal-moves/v1",
    "client_pin": "MAJOR",
    "minor_means": "documentation-clarification-only",
    "minor_additions": [],
    "extensible_locations": [],
    "closed_locations": ["move_model.shape", "move_model.square_grammar",
                         "move_model.promotion_expansion", "movement",
                         "attack", "legality", "move_set_terminal_status",
                         "linkage", "failure_classes", "failure_mapping",
                         "errors", "links", "versioning"],
    "new_move_member": "major-bump-required",
    "downgrade_policy": "any-earlier-minor-within-major-without-migration",
    "downgrade_rationale": "minor-versions-share-identical-schema",
    "major_bump": "new-base-path-required",
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {"id", "move_model", "movement", "attack", "legality",
                    "move_set_terminal_status", "linkage",
                    "failure_classes", "failure_mapping", "errors", "links",
                    "versioning"}
ALLOWED_MOVE_MODEL = {"shape", "square_grammar", "promotion_expansion",
                      "promotion_required", "promotion_forbidden", "rule"}
ALLOWED_MOVEMENT = {"knight", "king", "rook", "bishop", "queen", "pawn",
                    "occupancy", "rule"}
ALLOWED_VERSIONING = set(VERSIONING) | {"rule"}


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(f"legal-moves contract: {problem}")


def _keys(node: dict, allowed: set[str], where: str) -> None:
    _need(type(node) is dict, f"{where}: mapping required")
    unknown = set(node) - allowed
    _need(not unknown, f"{where}: unknown keys {sorted(unknown)}")


def _get(node: object, key: str, where: str) -> object:
    _need(type(node) is dict, f"{where}: mapping required")
    _need(key in node, f"{where}: missing key {key!r}")
    return node[key]


def _text(node: object, where: str) -> str:
    _need(type(node) is str and node.strip(),
          f"{where}: nonempty documentation string required")
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


def _no_rule(node: dict) -> dict:
    return {k: v for k, v in node.items() if k != "rule"}


def _check_links(links: dict, root: Path) -> None:
    _strict_eq(links, LINKS, "contract.links")
    vdoc = yaml.safe_load((root / LINKS["variant_contract"]).read_text())
    _variant_lint(vdoc)  # lint BEFORE relying on the linked artifact
    canonical = vdoc["contract"]["identity"]["canonical_fields"]
    _need("side_to_move" in canonical,
          "linked variant contract: side_to_move not a canonical identity field")
    tdoc = yaml.safe_load((root / LINKS["turn_contract"]).read_text())
    _turn_lint(tdoc, root)
    reset_when = tdoc["contract"]["transition"]["on_move"]["halfmove_clock"]["reset_when"]
    _need("pawn_move" in reset_when and "capture" in reset_when,
          "linked turn contract: legal moves include pawn moves and captures - "
          "both must reset the halfmove clock")
    turn_states = tdoc["contract"]["termination"]["states"]
    for state in MOVE_SET_TERMINAL["other_outcomes"]["states"]:
        _need(state in turn_states,
              f"linked turn contract: non-move-set outcome {state!r} missing "
              "from termination.states - ownership transfer is unverifiable")
    for yielded in ("checkmate", "stalemate"):
        _need(yielded in turn_states,
              f"linked turn contract: move-set outcome {yielded!r} missing "
              "from termination.states - the turn machine must accept it")
    cdoc = yaml.safe_load((root / LINKS["castling_contract"]).read_text())
    _castling_lint(cdoc, root)
    _need(cdoc["contract"]["rights"]["irrevocable"] is True,
          "linked castling contract: rights must be irrevocable")
    for path in cdoc["contract"]["move"]["per_side_paths"].values():
        _need("king_transit" in path and "empty_required" in path,
              "linked castling contract: every side path must declare "
              "king_transit and empty_required - transit safety applies "
              "this contract's attack relation")
    edoc = yaml.safe_load((root / LINKS["en_passant_contract"]).read_text())
    _ep_lint(edoc, root)
    _need("pinned_capture" in edoc["contract"]["failure_classes"],
          "linked en-passant contract: pinned_capture failure class required - "
          "it is this contract's legality filter applied to the double removal")


def lint(doc: object, root: Path = ROOT) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _need(doc.get("schema_version") == 1 and type(doc.get("schema_version")) is int,
          "schema_version: exact int 1")
    c = _get(doc, "contract", "document")
    _keys(c, ALLOWED_CONTRACT, "contract")
    for required in ALLOWED_CONTRACT:
        _get(c, required, "contract")
    _need(c["id"] == "chess-legal-moves", "contract.id must be chess-legal-moves")

    mm = _get(c, "move_model", "contract")
    _keys(mm, ALLOWED_MOVE_MODEL, "contract.move_model")
    _strict_eq(_get(mm, "shape", "contract.move_model"),
               MOVE_SHAPE, "contract.move_model.shape")
    _strict_eq(_get(mm, "square_grammar", "contract.move_model"),
               SQUARE_GRAMMAR, "contract.move_model.square_grammar")
    _strict_eq(_get(mm, "promotion_expansion", "contract.move_model"),
               PROMO_EXPANSION, "contract.move_model.promotion_expansion")
    _strict_eq({k: mm[k] for k in MOVE_MODEL_EXTRA}, MOVE_MODEL_EXTRA,
               "contract.move_model.promotion-fields")
    _text(_get(mm, "rule", "contract.move_model"), "contract.move_model.rule")

    mv = _get(c, "movement", "contract")
    _keys(mv, ALLOWED_MOVEMENT, "contract.movement")
    knight = _get(mv, "knight", "contract.movement")
    _keys(knight, {"deltas", "jumps"}, "contract.movement.knight")
    _strict_eq(_get(knight, "deltas", "contract.movement.knight"), KNIGHT_DELTAS,
               "contract.movement.knight.deltas")
    _need(_get(knight, "jumps", "contract.movement.knight") is True,
          "contract.movement.knight.jumps must be true")
    king = _get(mv, "king", "contract.movement")
    _keys(king, {"deltas", "jumps"}, "contract.movement.king")
    _strict_eq(_get(king, "deltas", "contract.movement.king"), KING_DELTAS,
               "contract.movement.king.deltas")
    _need(_get(king, "jumps", "contract.movement.king") is False,
          "contract.movement.king.jumps must be false")
    for piece, dirs in (("rook", ROOK_DIRS), ("bishop", BISHOP_DIRS),
                        ("queen", QUEEN_DIRS)):
        node = _get(mv, piece, "contract.movement")
        _keys(node, {"directions", "slides"}, f"contract.movement.{piece}")
        _strict_eq(_get(node, "directions", f"contract.movement.{piece}"), dirs,
                   f"contract.movement.{piece}.directions")
        _need(_get(node, "slides", f"contract.movement.{piece}") is True,
              f"contract.movement.{piece}.slides must be true")
    pawn = _get(mv, "pawn", "contract.movement")
    _keys(pawn, set(PAWN), "contract.movement.pawn")
    _strict_eq(pawn, PAWN, "contract.movement.pawn.fields")
    occ = _get(mv, "occupancy", "contract.movement")
    _keys(occ, set(OCCUPANCY), "contract.movement.occupancy")
    _strict_eq(occ, OCCUPANCY, "contract.movement.occupancy.fields")
    _text(_get(mv, "rule", "contract.movement"), "contract.movement.rule")

    atk = _get(c, "attack", "contract")
    _keys(atk, set(ATTACK) | {"rule"}, "contract.attack")
    _strict_eq(_no_rule(atk), ATTACK, "contract.attack.fields")
    _text(_get(atk, "rule", "contract.attack"), "contract.attack.rule")

    leg = _get(c, "legality", "contract")
    _keys(leg, {"filter", "in_check_rule", "opponent_king_capture", "rule"},
        "contract.legality")
    _strict_eq(_no_rule(leg), LEGALITY, "contract.legality.fields")
    _text(_get(leg, "rule", "contract.legality"), "contract.legality.rule")

    mst = _get(c, "move_set_terminal_status", "contract")
    _keys(mst, set(MOVE_SET_TERMINAL) | {"rule"},
          "contract.move_set_terminal_status")
    _strict_eq(_no_rule(mst), MOVE_SET_TERMINAL,
               "contract.move_set_terminal_status.fields")
    _text(_get(mst, "rule", "contract.move_set_terminal_status"),
          "contract.move_set_terminal_status.rule")

    lk = _get(c, "linkage", "contract")
    _keys(lk, set(LINKAGE) | {"rule"}, "contract.linkage")
    _strict_eq(_no_rule(lk), LINKAGE, "contract.linkage.fields")
    _text(_get(lk, "rule", "contract.linkage"), "contract.linkage.rule")

    _strict_eq(_get(c, "failure_classes", "contract"), FAILURE_CLASSES,
               "contract.failure_classes")
    _strict_eq(_get(c, "failure_mapping", "contract"), FAILURE_MAPPING,
               "contract.failure_mapping")
    errors = _get(c, "errors", "contract")
    _keys(errors, {"closed_enum", "shape"}, "contract.errors")
    _strict_eq(errors["closed_enum"], ERROR_ENUM, "contract.errors.closed_enum")
    _strict_eq(errors["shape"], ERROR_SHAPE, "contract.errors.shape")

    _check_links(_get(c, "links", "contract"), root)

    ver = _get(c, "versioning", "contract")
    _keys(ver, ALLOWED_VERSIONING, "contract.versioning")
    _strict_eq(_no_rule(ver), VERSIONING, "contract.versioning.fields")
    _text(_get(ver, "rule", "contract.versioning"), "contract.versioning.rule")


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
    print("OK legal-moves contract lint: chess legal-moves contract v1 clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
