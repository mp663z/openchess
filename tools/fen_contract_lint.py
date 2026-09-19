"""T0086: chess FEN contract lint.

Enforces the normative CONTENT of data/contracts/fen.yaml by EXACT
STRUCTURED COMPARISON, never prose-marker presence: the six-field
shape and order, the placement grammar (rank count/order/separator,
piece letters, empty-run digits, zero/adjacent-digit bans, rank sum),
the position rules (exactly one king per side, kings never adjacent,
no back-rank pawns, non-mover king never attacked), the castling
grammar + king/rook start-square consistency, the en-passant storage
semantics and rank/side/pawn-presence rules, the counter types and
bounds, canonical serialization with round-trip identity, all-or-
nothing parse atomicity, the explicit per-class failure mapping (no
orphan classes, no undeclared error codes), the closed error enum +
exact shape, and the linkage VERIFIED AGAINST THE ACTUAL sibling
artifacts (turn counter bounds, en-passant storage/ranks, castling
rights grammar). Prose fields are required to be nonempty
documentation only. Anything less passes silently and position
interchange rots.

    python tools/fen_contract_lint.py [path]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # sibling-tool import when run as a script

from tools.variant_contract_lint import ContractError  # noqa: E402

CONTRACT = ROOT / "data" / "contracts" / "fen.yaml"
TURN = ROOT / "data" / "contracts" / "turn.yaml"
EN_PASSANT = ROOT / "data" / "contracts" / "en_passant.yaml"
CASTLING = ROOT / "data" / "contracts" / "castling.yaml"

FIELD_ORDER = ["placement", "active_color", "castling", "en_passant",
               "halfmove_clock", "fullmove_number"]
BOARD_FILES = ["a", "b", "c", "d", "e", "f", "g", "h"]
BOARD_RANKS = ["1", "2", "3", "4", "5", "6", "7", "8"]
KNIGHT_DELTAS = [[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2],
                 [-2, -1], [-2, 1], [-1, 2]]
KING_DELTAS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1],
               [0, -1], [1, -1]]
ROOK_DIRECTIONS = [[1, 0], [-1, 0], [0, 1], [0, -1]]
BISHOP_DIRECTIONS = [[1, 1], [1, -1], [-1, 1], [-1, -1]]
WHITE_PAWN_DELTAS = [[1, 1], [-1, 1]]
BLACK_PAWN_DELTAS = [[1, -1], [-1, -1]]
CASTLING_HOME_SQUARES = {"K": {"king": "e1", "rook": "h1"},
                         "Q": {"king": "e1", "rook": "a1"},
                         "k": {"king": "e8", "rook": "h8"},
                         "q": {"king": "e8", "rook": "a8"}}
EP_SET_ON = {"event": "pawn-two-square-advance",
             "target_file": "advancing-pawn-file",
             "white": {"from_rank": "2", "to_rank": "4",
                       "target_rank": "3"},
             "black": {"from_rank": "7", "to_rank": "5",
                       "target_rank": "6"}}
PIECE_LETTERS = ["p", "n", "b", "r", "q", "k",
                 "P", "N", "B", "R", "Q", "K"]
EMPTY_RUN_DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8"]
CASTLING_LETTERS = ["K", "Q", "k", "q"]
EP_RANKS = ["3", "6"]
FAILURE_CLASSES = ["malformed_fen", "impossible_position"]
FAILURE_MAPPING = {
    "malformed_fen": {
        "trigger": "any-fields-or-grammar-violation",
        "error": "malformed_request",
    },
    "impossible_position": {
        "trigger": "any-position_rules-castling-consistency-or-en-passant-violation",
        "error": "illegal_position",
    },
}
ERROR_ENUM = ["malformed_request", "illegal_position", "internal"]
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}
LINKS = {
    "turn": "data/contracts/turn.yaml",
    "en_passant": "data/contracts/en_passant.yaml",
    "castling": "data/contracts/castling.yaml",
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {
    "id", "board", "fields", "placement", "position_rules",
    "active_color", "castling", "en_passant", "counters",
    "serialization", "atomicity", "failure_classes", "failure_mapping",
    "errors", "links", "versioning",
}
ALLOWED_BOARD = {"files", "ranks", "file_index", "rank_index",
                 "attack", "rule"}
ALLOWED_ATTACK = {"knight_deltas", "king_deltas", "rook_directions",
                  "bishop_directions", "queen_directions",
                  "white_pawn_capture_deltas",
                  "black_pawn_capture_deltas", "rule"}
ALLOWED_FIELDS = {"order", "exactly_six", "separator", "empty_field",
                  "rule"}
ALLOWED_PLACEMENT = {"rank_count", "rank_separator", "rank_order",
                     "piece_letters", "case_rule", "empty_run_digits",
                     "zero_digit", "adjacent_digits", "rank_sum", "rule"}
ALLOWED_POSITION = {"white_kings", "black_kings", "kings_adjacent",
                    "pawns_on_back_ranks", "non_mover_king_attacked",
                    "white_pawns_max", "black_pawns_max",
                    "white_pieces_max", "black_pieces_max",
                    "promotion_budget", "rule"}
ALLOWED_COLOR = {"values", "rule"}
ALLOWED_CASTLING = {"none_sentinel", "letters", "order", "duplicates",
                    "consistency", "home_squares", "rule"}
ALLOWED_EP = {"none_sentinel", "storage", "ranks", "rank3_requires",
              "rank6_requires", "pawn_presence", "target_square",
              "origin_square", "halfmove_clock", "set_on",
              "halfmove_reset_source", "link", "rule"}
ALLOWED_COUNTERS = {"halfmove_clock", "fullmove_number", "link", "rule"}
ALLOWED_COUNTER = {"type", "grammar", "leading_zeros", "min"}
ALLOWED_SERIALIZATION = {"canonical", "roundtrip", "emit_of_parse", "rule"}
ALLOWED_ATOMICITY = {"parse", "rule"}
ALLOWED_ERRORS = {"closed_enum", "shape"}
ALLOWED_LINKS = {"turn", "en_passant", "castling"}
ALLOWED_VERSIONING = {"base_path", "rule"}

ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _need(ok: bool, msg: str) -> None:
    if not ok:
        raise ContractError(msg)


def _mapping(value, name: str) -> dict:
    _need(type(value) is dict, f"{name}: mapping required")
    return value


def _text(value, name: str) -> str:
    _need(type(value) is str and value.strip(),
          f"{name}: nonempty documentation string required")
    return value


def _keys(mapping: dict, allowed: set, name: str) -> None:
    extra = set(mapping) - allowed
    missing = allowed - set(mapping)
    _need(not extra, f"{name}: undeclared keys {sorted(extra)}")
    _need(not missing, f"{name}: missing keys {sorted(missing)}")


def _exact(actual, expected, name: str) -> None:
    _need(actual == expected and type(actual) is type(expected),
          f"{name}: expected {expected!r}, got {actual!r}")


def _load(path: Path, name: str) -> dict:
    try:
        doc = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"{name}: cannot load {path}: {exc!r}") from exc
    _need(type(doc) is dict and "contract" in doc,
          f"{name}: contract mapping required in {path}")
    return doc["contract"]


def _check_links(contract: dict, root: Path) -> None:
    """Linkage is verified against the ACTUAL sibling artifacts, never
    trusted from this document alone: a sibling drifting breaks the
    FEN contract lint."""
    turn = _load(root / LINKS["turn"], "turn link")
    bounds = _mapping(_mapping(turn.get("state"), "turn.state")
                      .get("bounds"), "turn.state.bounds")
    _exact(_mapping(bounds.get("halfmove_clock"),
                    "turn halfmove bounds").get("min"),
           0, "turn halfmove_clock min")
    _exact(_mapping(bounds.get("fullmove_number"),
                    "turn fullmove bounds").get("min"),
           1, "turn fullmove_number min")

    ep = _load(root / LINKS["en_passant"], "en-passant link")
    target = _mapping(ep.get("target"), "en_passant.target")
    grammar = _mapping(target.get("grammar"), "en_passant.target.grammar")
    _exact(grammar.get("ranks"), EP_RANKS, "en_passant grammar ranks")
    _exact(grammar.get("none_sentinel"), "-",
           "en_passant none sentinel")
    svi = _mapping(target.get("storage_vs_identity"),
                   "en_passant storage_vs_identity")
    _exact(svi.get("storage"), "recorded-on-every-two-square-advance",
           "en_passant storage semantics")

    castling = _load(root / LINKS["castling"], "castling link")
    rights = _mapping(castling.get("rights"), "castling.rights")
    _exact(rights.get("values"), CASTLING_LETTERS,
           "castling rights values")
    cgrammar = _mapping(rights.get("grammar"), "castling rights grammar")
    _exact(cgrammar.get("none_sentinel"), "-", "castling none sentinel")
    _exact(cgrammar.get("ordering"), "KQkq", "castling ordering")
    _exact(cgrammar.get("duplicates"), "forbidden",
           "castling duplicates")
    _exact(_mapping(rights.get("home_squares"),
                    "castling rights home_squares"),
           CASTLING_HOME_SQUARES, "castling home_squares")

    ep_doc = _load(root / LINKS["en_passant"], "en-passant link")
    set_on = _mapping(_mapping(ep_doc.get("target"),
                               "en_passant.target").get("set_on"),
                      "en_passant set_on")
    _exact({k: v for k, v in set_on.items() if k != "rule"},
           EP_SET_ON, "en_passant set_on")

    turn_doc = _load(root / LINKS["turn"], "turn link")
    on_move = _mapping(_mapping(turn_doc.get("transition"),
                                "turn.transition").get("on_move"),
                       "turn on_move")
    hc = _mapping(on_move.get("halfmove_clock"),
                  "turn on_move halfmove_clock")
    _need("pawn_move" in (hc.get("reset_when") or []),
          "turn halfmove reset_when must include pawn_move")
    _exact(hc.get("reset_to"), 0, "turn halfmove reset_to")


def lint(doc: dict, root: Path | None = None) -> None:
    root = root or ROOT
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _exact(doc.get("schema_version"), 1, "schema_version")
    contract = _mapping(doc.get("contract"), "contract")
    _keys(contract, ALLOWED_CONTRACT, "contract")
    _exact(contract.get("id"), "chess-fen", "contract.id")

    board = _mapping(contract.get("board"), "contract.board")
    _keys(board, ALLOWED_BOARD, "contract.board")
    _exact(board.get("files"), BOARD_FILES, "board.files")
    _exact(board.get("ranks"), BOARD_RANKS, "board.ranks")
    _exact(board.get("file_index"), "file-a-is-index-0",
           "board.file_index")
    _exact(board.get("rank_index"), "rank1-is-index-1",
           "board.rank_index")
    attack = _mapping(board.get("attack"), "contract.board.attack")
    _keys(attack, ALLOWED_ATTACK, "contract.board.attack")
    _exact(attack.get("knight_deltas"), KNIGHT_DELTAS,
           "attack.knight_deltas")
    _exact(attack.get("king_deltas"), KING_DELTAS, "attack.king_deltas")
    _exact(attack.get("rook_directions"), ROOK_DIRECTIONS,
           "attack.rook_directions")
    _exact(attack.get("bishop_directions"), BISHOP_DIRECTIONS,
           "attack.bishop_directions")
    _exact(attack.get("queen_directions"),
           "rook-directions-union-bishop-directions",
           "attack.queen_directions")
    _exact(attack.get("white_pawn_capture_deltas"), WHITE_PAWN_DELTAS,
           "attack.white_pawn_capture_deltas")
    _exact(attack.get("black_pawn_capture_deltas"), BLACK_PAWN_DELTAS,
           "attack.black_pawn_capture_deltas")
    _text(attack.get("rule"), "attack.rule")
    _text(board.get("rule"), "board.rule")

    fields = _mapping(contract.get("fields"), "contract.fields")
    _keys(fields, ALLOWED_FIELDS, "contract.fields")
    _exact(fields.get("order"), FIELD_ORDER, "fields.order")
    _exact(fields.get("exactly_six"), True, "fields.exactly_six")
    _exact(fields.get("separator"), "single-ascii-space",
           "fields.separator")
    _exact(fields.get("empty_field"), "forbidden", "fields.empty_field")
    _text(fields.get("rule"), "fields.rule")

    placement = _mapping(contract.get("placement"), "contract.placement")
    _keys(placement, ALLOWED_PLACEMENT, "contract.placement")
    _exact(placement.get("rank_count"), 8, "placement.rank_count")
    _exact(placement.get("rank_separator"), "/",
           "placement.rank_separator")
    _exact(placement.get("rank_order"), "rank8-to-rank1",
           "placement.rank_order")
    _exact(placement.get("piece_letters"), PIECE_LETTERS,
           "placement.piece_letters")
    _exact(placement.get("case_rule"),
           "uppercase-white-lowercase-black", "placement.case_rule")
    _exact(placement.get("empty_run_digits"), EMPTY_RUN_DIGITS,
           "placement.empty_run_digits")
    _exact(placement.get("zero_digit"), "forbidden",
           "placement.zero_digit")
    _exact(placement.get("adjacent_digits"), "forbidden",
           "placement.adjacent_digits")
    _exact(placement.get("rank_sum"), 8, "placement.rank_sum")
    _text(placement.get("rule"), "placement.rule")

    pos = _mapping(contract.get("position_rules"),
                   "contract.position_rules")
    _keys(pos, ALLOWED_POSITION, "contract.position_rules")
    for side in ("white_kings", "black_kings"):
        _exact(pos.get(side), "exactly-1", f"position_rules.{side}")
    for ban in ("kings_adjacent", "pawns_on_back_ranks",
                "non_mover_king_attacked"):
        _exact(pos.get(ban), "forbidden", f"position_rules.{ban}")
    for bound in ("white_pawns_max", "black_pawns_max"):
        _exact(pos.get(bound), 8, f"position_rules.{bound}")
    for bound in ("white_pieces_max", "black_pieces_max"):
        _exact(pos.get(bound), 16, f"position_rules.{bound}")
    _exact(pos.get("promotion_budget"),
           "excess-officers-over-start-set-covered-by-missing-pawns",
           "position_rules.promotion_budget")
    _text(pos.get("rule"), "position_rules.rule")

    color = _mapping(contract.get("active_color"),
                     "contract.active_color")
    _keys(color, ALLOWED_COLOR, "contract.active_color")
    _exact(color.get("values"), ["w", "b"], "active_color.values")
    _text(color.get("rule"), "active_color.rule")

    castling = _mapping(contract.get("castling"), "contract.castling")
    _keys(castling, ALLOWED_CASTLING, "contract.castling")
    _exact(castling.get("none_sentinel"), "-", "castling.none_sentinel")
    _exact(castling.get("letters"), CASTLING_LETTERS,
           "castling.letters")
    _exact(castling.get("order"), "KQkq", "castling.order")
    _exact(castling.get("duplicates"), "forbidden",
           "castling.duplicates")
    _exact(castling.get("consistency"),
           "right-requires-king-and-rook-on-start-squares",
           "castling.consistency")
    _exact(castling.get("home_squares"),
           "from-linked-castling-contract-home_squares",
           "castling.home_squares")
    _text(castling.get("rule"), "castling.rule")

    ep = _mapping(contract.get("en_passant"), "contract.en_passant")
    _keys(ep, ALLOWED_EP, "contract.en_passant")
    _exact(ep.get("none_sentinel"), "-", "en_passant.none_sentinel")
    _exact(ep.get("storage"), "recorded-on-every-two-square-advance",
           "en_passant.storage")
    _exact(ep.get("ranks"), EP_RANKS, "en_passant.ranks")
    _exact(ep.get("rank3_requires"), "black-to-move",
           "en_passant.rank3_requires")
    _exact(ep.get("rank6_requires"), "white-to-move",
           "en_passant.rank6_requires")
    _exact(ep.get("pawn_presence"),
           "advancing-pawn-on-destination-square",
           "en_passant.pawn_presence")
    _exact(ep.get("target_square"), "empty", "en_passant.target_square")
    _exact(ep.get("origin_square"), "empty", "en_passant.origin_square")
    _exact(ep.get("halfmove_clock"), "must-be-zero",
           "en_passant.halfmove_clock")
    _exact(ep.get("set_on"), "from-linked-en-passant-contract-set_on",
           "en_passant.set_on")
    _exact(ep.get("halfmove_reset_source"),
           "turn-contract-counter-reset-on-pawn-move",
           "en_passant.halfmove_reset_source")
    _exact(ep.get("link"), LINKS["en_passant"], "en_passant.link")
    _text(ep.get("rule"), "en_passant.rule")

    counters = _mapping(contract.get("counters"), "contract.counters")
    _keys(counters, ALLOWED_COUNTERS, "contract.counters")
    _exact(_mapping(counters.get("halfmove_clock"),
                    "counters.halfmove_clock"),
           {"type": "integer", "grammar": "ascii-digits-0-9-only",
            "leading_zeros": "forbidden", "min": 0},
           "counters.halfmove_clock")
    _exact(_mapping(counters.get("fullmove_number"),
                    "counters.fullmove_number"),
           {"type": "integer", "grammar": "ascii-digits-0-9-only",
            "leading_zeros": "forbidden", "min": 1},
           "counters.fullmove_number")
    _exact(counters.get("link"), LINKS["turn"], "counters.link")
    _text(counters.get("rule"), "counters.rule")

    ser = _mapping(contract.get("serialization"),
                   "contract.serialization")
    _keys(ser, ALLOWED_SERIALIZATION, "contract.serialization")
    _exact(ser.get("canonical"), True, "serialization.canonical")
    _exact(ser.get("roundtrip"), "parse-of-emit-is-identity",
           "serialization.roundtrip")
    _exact(ser.get("emit_of_parse"), "exact-input-string-reproduced",
           "serialization.emit_of_parse")
    _text(ser.get("rule"), "serialization.rule")

    atomic = _mapping(contract.get("atomicity"), "contract.atomicity")
    _keys(atomic, ALLOWED_ATOMICITY, "contract.atomicity")
    _exact(atomic.get("parse"), "all-or-nothing", "atomicity.parse")
    _text(atomic.get("rule"), "atomicity.rule")

    classes = contract.get("failure_classes")
    _exact(classes, FAILURE_CLASSES, "failure_classes")
    mapping = _mapping(contract.get("failure_mapping"),
                       "contract.failure_mapping")
    _exact(mapping, FAILURE_MAPPING, "failure_mapping")
    _need(set(mapping) == set(classes),
          "failure_mapping must cover exactly the declared classes")

    errors = _mapping(contract.get("errors"), "contract.errors")
    _keys(errors, ALLOWED_ERRORS, "contract.errors")
    enum = errors.get("closed_enum")
    _need(type(enum) is list, "errors.closed_enum: list required")
    for i, code in enumerate(enum):
        _need(type(code) is str and ERROR_CODE_RE.match(code),
              f"errors.closed_enum[{i}]: snake_case string required,"
              f" got {code!r}")
    _need(len(set(enum)) == len(enum),
          "errors.closed_enum: duplicate codes")
    for code in ("malformed_request", "illegal_position"):
        _need(code in enum, f"errors.closed_enum: {code} required")
    _exact(errors.get("shape"), ERROR_SHAPE, "errors.shape")
    for cls, m in FAILURE_MAPPING.items():
        _need(m["error"] in enum,
              f"failure_mapping.{cls}: error {m['error']!r} not in the"
              " closed enum")

    links = _mapping(contract.get("links"), "contract.links")
    _keys(links, ALLOWED_LINKS, "contract.links")
    _exact(links, LINKS, "contract.links")
    _check_links(contract, root)

    versioning = _mapping(contract.get("versioning"),
                          "contract.versioning")
    _keys(versioning, ALLOWED_VERSIONING, "contract.versioning")
    base = _text(versioning.get("base_path"), "versioning.base_path")
    _need(re.fullmatch(r"/fen/v[1-9][0-9]*", base),
          "versioning.base_path: must be /fen/vN with numeric N")
    vrule = _text(versioning.get("rule"), "versioning.rule")
    for marker in ("MINOR", "MAJOR", "downgrade"):
        _need(marker in vrule, f"versioning.rule: must state {marker!r}")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        print(f"FAIL fen contract lint: malformed YAML: {exc}")
        return 1
    try:
        lint(doc)
    except ContractError as exc:
        print(f"FAIL fen contract lint: {exc}")
        return 1
    except Exception as exc:  # classified rejection, never a traceback
        print(f"FAIL fen contract lint: internal error: {exc!r}")
        return 1
    print("OK fen contract lint: chess FEN contract v1 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
