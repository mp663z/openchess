"""T0041: chess variant + position-identity contract lint.

Enforces the normative CONTENT of data/contracts/variant.yaml, not just
its shape: exact allowed-key schemas at every level (a typo'd field is
a violation, never silently ignored), the canonical identity fields in
exact order, the registry rules, the exact FEN grammar marker, the
structured illegal-position policy with its semantic markers, semantic
legality of every start FEN (king counts, back-rank pawns, king
adjacency, castling-rights consistency, impossible-check rejection),
the full exact error shape, the closed error enum (format + uniqueness
+ required codes), and the privacy rule. Anything less passes silently
and the whole position-identity model rots.

    python tools/variant_contract_lint.py [path]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "variant.yaml"

CANONICAL_FIELDS = [
    "variant",
    "board",
    "side_to_move",
    "castling_rights",
    "en_passant",
]
FAILURE_CLASSES = [
    "wrong_field_count",
    "bad_board",
    "bad_side",
    "bad_castling",
    "bad_en_passant",
    "bad_counters",
    "illegal_position",
]
PIECE_CHARS = set("pnbrqkPNBRQK")
ERROR_CODE_RE = re.compile(r"^[a-z][a-z_]*$")
FEN_GRAMMAR = "fen-1.0-fields"
ILLEGAL_POSITION_MARKERS = (
    "illegal_position",
    "both kings in check",
    "missing king",
    "pawn on the back rank",
    "castling rights inconsistent",
)
ERROR_SHAPE = {
    "error": {
        "fields": {
            "code": {"type": "string", "required": True},
            "message": {"type": "string", "required": True},
            "retryable": {"type": "boolean", "required": True},
        }
    }
}

ALLOWED_TOP = {"schema_version", "contract"}
ALLOWED_CONTRACT = {"id", "versioning", "identity", "variants", "fen", "errors", "privacy"}
ALLOWED_VERSIONING = {"base_path", "rule"}
ALLOWED_IDENTITY = {"canonical_fields", "rule", "hash_rule"}
ALLOWED_VARIANTS = {"registry_rule", "entries"}
ALLOWED_ENTRY = {"id", "name", "start_fen", "castling", "status"}
ALLOWED_FEN = {"grammar", "rule", "failure_classes", "illegal_position_rule"}
ALLOWED_ERRORS = {"closed_enum", "shape"}
ALLOWED_PRIVACY = {"chess_content", "rule"}

ORTHODOX_CASTLING_SQUARES = {
    "K": (7, 4, 7, 7),  # king e1, rook h1   (row 0 = rank 8)
    "Q": (7, 4, 7, 0),  # king e1, rook a1
    "k": (0, 4, 0, 7),  # king e8, rook h8
    "q": (0, 4, 0, 0),  # king e8, rook a8
}


class ContractError(Exception):
    pass


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(problem)


def _mapping(node: object, where: str) -> dict:
    _need(type(node) is dict, f"{where}: mapping required")
    return node


def _keys(node: dict, allowed: set[str], where: str) -> None:
    unknown = set(node) - allowed
    _need(not unknown, f"{where}: unknown keys {sorted(unknown)}")
    missing = allowed - set(node)
    _need(not missing, f"{where}: missing keys {sorted(missing)}")


def _text(node: object, where: str) -> str:
    _need(type(node) is str and node.strip(), f"{where}: nonempty text")
    return node


def _parse_board(board: str, where: str) -> list[list[str]]:
    ranks = board.split("/")
    _need(len(ranks) == 8, f"{where}: board must have 8 ranks")
    grid: list[list[str]] = []
    for rank in ranks:
        row: list[str] = []
        for ch in rank:
            if ch.isdigit():
                row.extend(["."] * int(ch))
            else:
                _need(ch in PIECE_CHARS, f"{where}: bad piece char {ch!r}")
                row.append(ch)
        _need(len(row) == 8, f"{where}: every rank must have 8 squares")
        grid.append(row)
    return grid


def _attacks(grid: list[list[str]], r: int, c: int, by_white: bool) -> bool:
    """Is square (r, c) attacked by the given side?"""
    pawn = "P" if by_white else "p"
    dr = -1 if by_white else 1
    for dc in (-1, 1):
        rr, cc = r + dr, c + dc
        if 0 <= rr < 8 and 0 <= cc < 8 and grid[rr][cc] == pawn:
            return True
    knight = "N" if by_white else "n"
    for dr, dc in ((1, 2), (2, 1), (-1, 2), (-2, 1), (1, -2), (2, -1), (-1, -2), (-2, -1)):
        rr, cc = r + dr, c + dc
        if 0 <= rr < 8 and 0 <= cc < 8 and grid[rr][cc] == knight:
            return True
    king = "K" if by_white else "k"
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            rr, cc = r + dr, c + dc
            if 0 <= rr < 8 and 0 <= cc < 8 and grid[rr][cc] == king:
                return True
    sliders = (("B", "Q") if by_white else ("b", "q"), ("R", "Q") if by_white else ("r", "q"))
    diag = ((1, 1), (1, -1), (-1, 1), (-1, -1))
    straight = ((0, 1), (0, -1), (1, 0), (-1, 0))
    for pieces, dirs in zip(sliders, (diag, straight), strict=True):
        for dr, dc in dirs:
            rr, cc = r + dr, c + dc
            while 0 <= rr < 8 and 0 <= cc < 8:
                sq = grid[rr][cc]
                if sq != ".":
                    if sq in pieces:
                        return True
                    break
                rr, cc = rr + dr, cc + dc
    return False


def _check_position_semantics(
    grid: list[list[str]], castling_field: str, side: str, castling_kind: str, where: str
) -> None:
    flat = [sq for row in grid for sq in row]
    _need(flat.count("K") == 1, f"{where}: exactly one white king")
    _need(flat.count("k") == 1, f"{where}: exactly one black king")
    _need(
        not any(p in ("P", "p") for p in grid[0] + grid[-1]),
        f"{where}: pawns on the back rank are illegal",
    )
    kw = next((r, c) for r in range(8) for c in range(8) if grid[r][c] == "K")
    kb = next((r, c) for r in range(8) for c in range(8) if grid[r][c] == "k")
    _need(max(abs(kw[0] - kb[0]), abs(kw[1] - kb[1])) > 1, f"{where}: kings may not be adjacent")
    # the side NOT to move may not be in check (impossible position)
    waiting = kb if side == "w" else kw
    _need(
        not _attacks(grid, waiting[0], waiting[1], by_white=(side == "w")),
        f"{where}: side not to move is in check - impossible position",
    )
    if castling_kind == "orthodox" and castling_field != "-":
        for right in castling_field:
            kr, kc, rr, rc = ORTHODOX_CASTLING_SQUARES[right]
            king = "K" if right.isupper() else "k"
            rook = "R" if right.isupper() else "r"
            _need(
                grid[kr][kc] == king,
                f"{where}: castling right {right} inconsistent - no {king} on its home square",
            )
            _need(
                grid[rr][rc] == rook,
                f"{where}: castling right {right} inconsistent - no {rook} on its home square",
            )


def _check_fen(fen: object, where: str, castling_kind: str) -> None:
    _need(type(fen) is str, f"{where}: FEN string required")
    fields = fen.split(" ")
    _need(len(fields) == 6, f"{where}: FEN must have 6 fields")
    board, side, castling, ep, half, full = fields
    grid = _parse_board(board, where)
    _need(side in ("w", "b"), f"{where}: side must be w or b")
    _need(
        castling == "-"
        or all(c in "KQkq" for c in castling)
        and len(set(castling)) == len(castling),
        f"{where}: castling field malformed",
    )
    _need(
        ep == "-" or (len(ep) == 2 and ep[0] in "abcdefgh" and ep[1] in "36"),
        f"{where}: en-passant field malformed",
    )
    _need(
        half.isdigit() and full.isdigit() and int(full) >= 1,
        f"{where}: counters must be non-negative/positive ints",
    )
    _check_position_semantics(grid, castling, side, castling_kind, where)


def _check_shape(node: object, expected: dict, where: str) -> None:
    node = _mapping(node, where)
    _need(
        set(node) == set(expected),
        f"{where}: exact keys {sorted(expected)} required, got {sorted(node)}",
    )
    for key, sub in expected.items():
        if isinstance(sub, dict) and sub and all(isinstance(v, dict) for v in sub.values()):
            _check_shape(node[key], sub, f"{where}.{key}")
        else:
            _need(node[key] == sub, f"{where}.{key}: exact value {sub!r} required")


def lint(doc: object) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _keys(doc, ALLOWED_TOP, "document")
    _need(
        doc.get("schema_version") == 1 and type(doc.get("schema_version")) is int,
        "schema_version: exact int 1",
    )
    contract = _mapping(doc.get("contract"), "contract")
    _keys(contract, ALLOWED_CONTRACT, "contract")
    _need(contract.get("id") == "chess-variant", "contract.id: must be chess-variant")

    versioning = _mapping(contract.get("versioning"), "contract.versioning")
    _keys(versioning, ALLOWED_VERSIONING, "contract.versioning")
    base = _text(versioning.get("base_path"), "versioning.base_path")
    _need(base.startswith("/variant/v"), "versioning.base_path: must be /variant/vN")
    rule = _text(versioning.get("rule"), "versioning.rule")
    _need(
        "MINOR" in rule and "MAJOR" in rule and "downgrade" in rule,
        "versioning.rule: must state MINOR additive, MAJOR bump and rollback semantics",
    )

    identity = _mapping(contract.get("identity"), "contract.identity")
    _keys(identity, ALLOWED_IDENTITY, "contract.identity")
    _need(
        identity.get("canonical_fields") == CANONICAL_FIELDS,
        f"identity.canonical_fields: exact ordered tuple {CANONICAL_FIELDS}",
    )
    irule = _text(identity.get("rule"), "identity.rule")
    for marker in ("never a single hash", "move-order path", "never participates"):
        _need(marker in irule, f"identity.rule: must state {marker!r}")
    hrule = _text(identity.get("hash_rule"), "identity.hash_rule")
    _need(
        "collision" in hrule.lower() and "field comparison" in hrule,
        "identity.hash_rule: collisions must fall back to full field comparison",
    )

    variants = _mapping(contract.get("variants"), "contract.variants")
    _keys(variants, ALLOWED_VARIANTS, "contract.variants")
    vrule = _text(variants.get("registry_rule"), "variants.registry_rule")
    _need(
        "fail closed" in vrule and "never coerced" in vrule,
        "variants.registry_rule: unknown ids fail closed, never coerced to standard",
    )
    entries = variants.get("entries")
    _need(type(entries) is list and entries, "variants.entries: nonempty list")
    seen: set[str] = set()
    standard = None
    for i, entry in enumerate(entries):
        where = f"variants.entries[{i}]"
        entry = _mapping(entry, where)
        _keys(entry, ALLOWED_ENTRY, where)
        vid = _text(entry.get("id"), f"{where}.id")
        _need(vid not in seen, f"{where}: duplicate variant id {vid!r}")
        seen.add(vid)
        _text(entry.get("name"), f"{where}.name")
        kind = entry.get("castling")
        _need(kind in ("orthodox", "chess960"), f"{where}.castling: orthodox or chess960")
        _need(
            entry.get("status") in ("stable", "experimental"),
            f"{where}.status: stable or experimental",
        )
        _check_fen(entry.get("start_fen"), f"{where}.start_fen", kind)
        if vid == "standard":
            standard = entry
    _need(standard is not None, "variants.entries: standard required")
    _need(
        standard.get("start_fen") == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        "standard.start_fen: must be the exact orthodox start",
    )

    fen = _mapping(contract.get("fen"), "contract.fen")
    _keys(fen, ALLOWED_FEN, "contract.fen")
    _need(fen.get("grammar") == FEN_GRAMMAR, f"fen.grammar: exact {FEN_GRAMMAR!r} required")
    _need(
        fen.get("failure_classes") == FAILURE_CLASSES,
        f"fen.failure_classes: exact ordered set {FAILURE_CLASSES}",
    )
    frule = _text(fen.get("rule"), "fen.rule")
    _need(
        "round-trip" in frule and "never partially applied" in frule,
        "fen.rule: round-trip exactness and no partial application",
    )
    ipr = _text(fen.get("illegal_position_rule"), "fen.illegal_position_rule")
    for marker in ILLEGAL_POSITION_MARKERS:
        _need(marker in ipr, f"fen.illegal_position_rule: must state {marker!r}")

    errors = _mapping(contract.get("errors"), "contract.errors")
    _keys(errors, ALLOWED_ERRORS, "contract.errors")
    enum = errors.get("closed_enum")
    _need(type(enum) is list and enum, "errors.closed_enum: nonempty list")
    for i, code in enumerate(enum):
        _need(
            type(code) is str and ERROR_CODE_RE.match(code),
            f"errors.closed_enum[{i}]: snake_case string required, got {code!r}",
        )
    _need(len(set(enum)) == len(enum), "errors.closed_enum: duplicate codes")
    for code in ("malformed_request", "unknown_variant", "illegal_position"):
        _need(code in enum, f"errors.closed_enum: {code} required")
    _check_shape(errors.get("shape"), ERROR_SHAPE, "errors.shape")

    privacy = _mapping(contract.get("privacy"), "contract.privacy")
    _keys(privacy, ALLOWED_PRIVACY, "contract.privacy")
    _need(
        privacy.get("chess_content") == "allowed-here",
        "privacy.chess_content: must be allowed-here (position data "
        "is user chess content; T0005 governs origins)",
    )
    prule = _text(privacy.get("rule"), "privacy.rule")
    _need(
        "T0005" in prule and "user" in prule,
        "privacy.rule: must name the T0005 license boundary and the user-content basis",
    )


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else CONTRACT
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        print(f"FAIL variant contract lint: malformed YAML: {exc}")
        return 1
    try:
        lint(doc)
    except ContractError as exc:
        print(f"FAIL variant contract lint: {exc}")
        return 1
    print("OK variant contract lint: chess variant + position-identity contract v1 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
