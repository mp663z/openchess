"""T0095: chess SAN contract lint.

Enforces the normative CONTENT of data/contracts/san.yaml by EXACT
STRUCTURED COMPARISON, never prose-marker presence: position-context
requirement and legal-move-set resolution, the exact token set
(piece letters, castling tokens with capital O, capture/promotion
markers, promotion pieces, check/checkmate suffixes, annotation
ban, square grammar delegation), the grammar forms (pawn quiet/
capture, piece quiet/capture, promotion, castling, suffix position),
minimal disambiguation with the file-rank-both preference order,
suffix semantics pinned to the resulting position, castling ownership
delegation, the three-class resolution failure model with its exact
mapping (no orphan classes, no undeclared error codes), the closed
error enum + exact shape, canonical serialization with move-identity
round trip, and linkage VERIFIED AGAINST THE ACTUAL sibling
artifacts (legal-moves move shape and promotion enum, turn side
values, castling rights). Anything less passes silently and every
PGN import and move display rots.

    python tools/san_contract_lint.py [path]
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

CONTRACT = ROOT / "data" / "contracts" / "san.yaml"
LEGAL_MOVES = ROOT / "data" / "contracts" / "legal_moves.yaml"
TURN = ROOT / "data" / "contracts" / "turn.yaml"
CASTLING = ROOT / "data" / "contracts" / "castling.yaml"

PIECE_LETTERS = ["N", "B", "R", "Q", "K"]
PROMOTION_PIECES = ["Q", "R", "B", "N"]
PREFERENCE_ORDER = ["file", "rank", "both"]
SUFFIX_VALUES = ["none", "+", "#"]
FAILURE_CLASSES = ["malformed_san", "ambiguous_san", "no_legal_match"]
FAILURE_MAPPING = {
    "malformed_san": {
        "trigger": "any-token-or-grammar-violation",
        "error": "malformed_request",
    },
    "ambiguous_san": {
        "trigger": "several-legal-moves-match",
        "error": "ambiguous_move",
    },
    "no_legal_match": {
        "trigger": "zero-legal-moves-match-or-false-suffix",
        "error": "illegal_move",
    },
}
ERROR_ENUM = ["malformed_request", "ambiguous_move", "illegal_move",
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
LINKS = {
    "legal_moves_contract": "data/contracts/legal_moves.yaml",
    "turn_contract": "data/contracts/turn.yaml",
    "castling_contract": "data/contracts/castling.yaml",
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {
    "id", "context", "tokens", "grammar", "disambiguation", "suffix",
    "castling_link", "resolution_failures", "failure_classes",
    "failure_mapping", "errors", "serialization", "links",
    "versioning",
}
ALLOWED_CONTEXT = {"requires_position", "resolution",
                   "move_output_shape", "rule"}
ALLOWED_TOKENS = {"piece_letters", "pawn_letter", "castling_kingside",
                  "castling_queenside", "castling_character",
                  "capture_marker", "promotion_marker",
                  "promotion_pieces", "check_suffix",
                  "checkmate_suffix", "annotation_suffixes",
                  "square_grammar", "rule"}
ALLOWED_GRAMMAR = {"pawn_quiet", "pawn_capture", "piece_quiet",
                   "piece_capture", "promotion", "castling",
                   "suffix_position", "rule"}
ALLOWED_DISAMBIG = {"applies_when", "preference_order", "minimal",
                    "never_for_pawns", "never_for_king_moves_ambiguity",
                    "rule"}
ALLOWED_SUFFIX = {"values", "semantics", "rule"}
ALLOWED_CASTLING_LINK = {"ownership", "denotation", "link", "rule"}
ALLOWED_RESOLUTION = {"malformed_san", "ambiguous_san",
                      "no_legal_match", "rule"}
ALLOWED_ERRORS = {"closed_enum", "shape"}
ALLOWED_SERIALIZATION = {"canonical", "roundtrip", "rule"}
ALLOWED_LINKS = {"legal_moves_contract", "turn_contract",
                 "castling_contract"}
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
        raise ContractError(
            f"{name}: cannot load {path}: {exc!r}") from exc
    _need(type(doc) is dict and "contract" in doc,
          f"{name}: contract mapping required in {path}")
    return doc["contract"]


def _check_links(root: Path) -> None:
    """Linkage verified against the ACTUAL sibling artifacts."""
    legal = _load(root / LINKS["legal_moves_contract"], "legal-moves link")
    model = _mapping(legal.get("move_model"), "legal_moves.move_model")
    shape = _mapping(model.get("shape"), "legal_moves move shape")
    _exact(shape.get("required"), ["from_square", "to_square"],
           "legal_moves required fields")
    _exact(shape.get("optional"), ["promotion"],
           "legal_moves optional fields")
    types = _mapping(shape.get("types"), "legal_moves move types")
    _exact(_mapping(types.get("promotion"),
                    "legal_moves promotion type").get("enum"),
           ["q", "r", "b", "n"], "legal_moves promotion enum")
    grammar = _mapping(model.get("square_grammar"),
                       "legal_moves square grammar")
    _exact(grammar.get("form"), "exactly-file-char-then-rank-char",
           "legal_moves square grammar form")

    turn = _load(root / LINKS["turn_contract"], "turn link")
    state = _mapping(turn.get("state"), "turn.state")
    _exact(state.get("side_values"), ["w", "b"], "turn side_values")

    castling = _load(root / LINKS["castling_contract"], "castling link")
    rights = _mapping(castling.get("rights"), "castling.rights")
    _exact(rights.get("values"), ["K", "Q", "k", "q"],
           "castling rights values")


def lint(doc: dict, root: Path | None = None) -> None:
    root = root or ROOT
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _exact(doc.get("schema_version"), 1, "schema_version")
    contract = _mapping(doc.get("contract"), "contract")
    _keys(contract, ALLOWED_CONTRACT, "contract")
    _exact(contract.get("id"), "chess-san", "contract.id")

    context = _mapping(contract.get("context"), "contract.context")
    _keys(context, ALLOWED_CONTEXT, "contract.context")
    _exact(context.get("requires_position"), True,
           "context.requires_position")
    _exact(context.get("resolution"),
           "against-legal-move-set-of-position", "context.resolution")
    _exact(context.get("move_output_shape"),
           "legal-moves-contract-move_model", "context.move_output_shape")
    _text(context.get("rule"), "context.rule")

    tokens = _mapping(contract.get("tokens"), "contract.tokens")
    _keys(tokens, ALLOWED_TOKENS, "contract.tokens")
    _exact(tokens.get("piece_letters"), PIECE_LETTERS,
           "tokens.piece_letters")
    _exact(tokens.get("pawn_letter"), "none", "tokens.pawn_letter")
    _exact(tokens.get("castling_kingside"), "O-O",
           "tokens.castling_kingside")
    _exact(tokens.get("castling_queenside"), "O-O-O",
           "tokens.castling_queenside")
    _exact(tokens.get("castling_character"),
           "capital-letter-O-never-digit-zero",
           "tokens.castling_character")
    _exact(tokens.get("capture_marker"), "x", "tokens.capture_marker")
    _exact(tokens.get("promotion_marker"), "=",
           "tokens.promotion_marker")
    _exact(tokens.get("promotion_pieces"), PROMOTION_PIECES,
           "tokens.promotion_pieces")
    _exact(tokens.get("check_suffix"), "+", "tokens.check_suffix")
    _exact(tokens.get("checkmate_suffix"), "#",
           "tokens.checkmate_suffix")
    _exact(tokens.get("annotation_suffixes"), "forbidden",
           "tokens.annotation_suffixes")
    _exact(tokens.get("square_grammar"),
           "exactly-file-char-then-rank-char-per-legal-moves",
           "tokens.square_grammar")
    _text(tokens.get("rule"), "tokens.rule")

    grammar = _mapping(contract.get("grammar"), "contract.grammar")
    _keys(grammar, ALLOWED_GRAMMAR, "contract.grammar")
    _exact(grammar.get("pawn_quiet"), "destination-square",
           "grammar.pawn_quiet")
    _exact(grammar.get("pawn_capture"),
           "origin-file-then-x-then-destination", "grammar.pawn_capture")
    _exact(grammar.get("piece_quiet"),
           "letter-then-disambiguation-then-destination",
           "grammar.piece_quiet")
    _exact(grammar.get("piece_capture"),
           "letter-then-disambiguation-then-x-then-destination",
           "grammar.piece_capture")
    _exact(grammar.get("promotion"),
           "pawn-move-then-equals-then-piece-letter", "grammar.promotion")
    _exact(grammar.get("castling"), "castling-token", "grammar.castling")
    _exact(grammar.get("suffix_position"),
           "immediately-after-move-body", "grammar.suffix_position")
    _text(grammar.get("rule"), "grammar.rule")

    disamb = _mapping(contract.get("disambiguation"),
                      "contract.disambiguation")
    _keys(disamb, ALLOWED_DISAMBIG, "contract.disambiguation")
    _exact(disamb.get("applies_when"),
           "two-or-more-legal-moves-same-piece-same-destination",
           "disambiguation.applies_when")
    _exact(disamb.get("preference_order"), PREFERENCE_ORDER,
           "disambiguation.preference_order")
    _exact(disamb.get("minimal"), True, "disambiguation.minimal")
    _exact(disamb.get("never_for_pawns"),
           "file-prefix-is-always-present-on-pawn-capture",
           "disambiguation.never_for_pawns")
    _exact(disamb.get("never_for_king_moves_ambiguity"),
           "disambiguation-applies-to-king-too",
           "disambiguation.never_for_king_moves_ambiguity")
    _text(disamb.get("rule"), "disambiguation.rule")

    suffix = _mapping(contract.get("suffix"), "contract.suffix")
    _keys(suffix, ALLOWED_SUFFIX, "contract.suffix")
    _exact(suffix.get("values"), SUFFIX_VALUES, "suffix.values")
    _exact(suffix.get("semantics"),
           "checkmate-iff-result-is-checkmate-else-check-iff-result-"
           "is-check-else-none", "suffix.semantics")
    _text(suffix.get("rule"), "suffix.rule")

    clink = _mapping(contract.get("castling_link"),
                     "contract.castling_link")
    _keys(clink, ALLOWED_CASTLING_LINK, "contract.castling_link")
    _exact(clink.get("ownership"),
           "castling-contract-owns-rights-and-legality",
           "castling_link.ownership")
    _exact(clink.get("denotation"),
           "king-two-squares-toward-rook-with-rook-hop",
           "castling_link.denotation")
    _exact(clink.get("link"), LINKS["castling_contract"],
           "castling_link.link")
    _text(clink.get("rule"), "castling_link.rule")

    res = _mapping(contract.get("resolution_failures"),
                   "contract.resolution_failures")
    _keys(res, ALLOWED_RESOLUTION, "contract.resolution_failures")
    _exact(res.get("malformed_san"), "any-grammar-or-token-violation",
           "resolution_failures.malformed_san")
    _exact(res.get("ambiguous_san"),
           "more-than-one-legal-move-matches",
           "resolution_failures.ambiguous_san")
    _exact(res.get("no_legal_match"),
           "parses-but-matches-zero-legal-moves",
           "resolution_failures.no_legal_match")
    _text(res.get("rule"), "resolution_failures.rule")

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
    for code in ("malformed_request", "ambiguous_move", "illegal_move"):
        _need(code in enum, f"errors.closed_enum: {code} required")
    _exact(errors.get("shape"), ERROR_SHAPE, "errors.shape")
    for cls, m in FAILURE_MAPPING.items():
        _need(m["error"] in enum,
              f"failure_mapping.{cls}: error {m['error']!r} not in the"
              " closed enum")

    ser = _mapping(contract.get("serialization"),
                   "contract.serialization")
    _keys(ser, ALLOWED_SERIALIZATION, "contract.serialization")
    _exact(ser.get("canonical"), True, "serialization.canonical")
    _exact(ser.get("roundtrip"), "parse-of-emit-is-move-identity",
           "serialization.roundtrip")
    _text(ser.get("rule"), "serialization.rule")

    links = _mapping(contract.get("links"), "contract.links")
    _keys(links, ALLOWED_LINKS, "contract.links")
    _exact(links, LINKS, "contract.links")
    _check_links(root)

    versioning = _mapping(contract.get("versioning"),
                          "contract.versioning")
    _keys(versioning, ALLOWED_VERSIONING, "contract.versioning")
    base = _text(versioning.get("base_path"), "versioning.base_path")
    _need(re.fullmatch(r"/san/v[1-9][0-9]*", base),
          "versioning.base_path: must be /san/vN with numeric N")
    vrule = _text(versioning.get("rule"), "versioning.rule")
    for marker in ("MINOR", "MAJOR", "downgrade"):
        _need(marker in vrule,
              f"versioning.rule: must state {marker!r}")


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        print(f"FAIL san contract lint: malformed YAML: {exc}")
        return 1
    try:
        lint(doc)
    except ContractError as exc:
        print(f"FAIL san contract lint: {exc}")
        return 1
    except Exception as exc:  # classified rejection, never a traceback
        print(f"FAIL san contract lint: internal error: {exc!r}")
        return 1
    print("OK san contract lint: chess SAN contract v1 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
