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
    for key in node:
        _need(type(key) is str, f"{where}: non-string key {key!r}")
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
        prev_digit = False
        for ch in rank:
            if ch.isdigit():
                _need(
                    ch in "12345678",
                    f"{where}: FEN empty-square digits are exactly 1-8, got {ch!r}",
                )
                _need(not prev_digit, f"{where}: adjacent digits are noncanonical FEN")
                prev_digit = True
                row.extend(["."] * int(ch))
            else:
                _need(ch in PIECE_CHARS, f"{where}: bad piece char {ch!r}")
                prev_digit = False
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


def _ep_capture_legal(
    grid: list[list[str]],
    ep: str,
    side: str,
    row: int,
    file_idx: int,
    enemy: str,
) -> bool:
    """True iff at least one adjacent enemy pawn can legally capture en
    passant: the capture is simulated (capturer to the EP square, the
    double-stepped pawn removed) and must not leave the CAPTURING
    side's own king attacked - an adjacent-but-pinned pawn cannot
    capture."""
    ep_row = 5 if ep[1] == "3" else 2
    king_char = "k" if side == "b" else "K"
    for df in (-1, 1):
        cf = file_idx + df
        if not (0 <= cf < 8) or grid[row][cf] != enemy:
            continue
        sim = [r[:] for r in grid]
        sim[row][cf] = "."
        sim[row][file_idx] = "."
        sim[ep_row][file_idx] = enemy
        kings = [(r, c) for r in range(8) for c in range(8) if sim[r][c] == king_char]
        if len(kings) != 1:
            continue  # malformed position: no capture counts as legal
        if not _attacks(sim, kings[0][0], kings[0][1], by_white=(side == "b")):
            return True
    return False


def _check_ep_semantics(grid: list[list[str]], ep: str, side: str, where: str) -> None:
    """En-passant must be a state a real game could reach: the
    double-stepped pawn exists, its origin and skipped squares are
    empty, the side to move is the NON-stepping side, and at least one
    enemy pawn stands adjacent able to capture."""
    file_idx = ord(ep[0]) - ord("a")
    if ep[1] == "3":  # white just double-stepped; black to move
        _need(side == "b", f"{where}: en-passant rank 3 requires black to move")
        pawn, enemy, row, origin, skipped = "P", "p", 4, 6, 5
    else:  # rank 6: black just double-stepped; white to move
        _need(side == "w", f"{where}: en-passant rank 6 requires white to move")
        pawn, enemy, row, origin, skipped = "p", "P", 3, 1, 2
    _need(
        grid[row][file_idx] == pawn,
        f"{where}: en-passant square {ep} has no double-stepped {pawn!r} pawn",
    )
    _need(
        grid[origin][file_idx] == "." and grid[skipped][file_idx] == ".",
        f"{where}: en-passant origin/skipped squares must be empty",
    )
    _need(
        _ep_capture_legal(grid, ep, side, row, file_idx, enemy),
        f"{where}: en-passant square {ep} has no LEGAL {enemy!r} capture "
        "- unreachable state or every candidate capture leaves its own "
        "king in check",
    )


def _check_position_semantics(
    grid: list[list[str]], castling_field: str, side: str, castling_kind: str, where: str
) -> None:
    flat = [sq for row in grid for sq in row]
    _need(flat.count("K") == 1, f"{where}: exactly one white king")
    _need(flat.count("k") == 1, f"{where}: exactly one black king")
    for pawn_char, color in (("P", "white"), ("p", "black")):
        pawns = flat.count(pawn_char)
        _need(
            pawns <= 8,
            f"{where}: {color} has {pawns} pawns - at most 8 can exist",
        )
        upper = color == "white"
        total = sum(1 for sq in flat if sq != "." and sq.isupper() == upper)
        _need(
            total <= 16,
            f"{where}: {color} has {total} pieces - at most 16 can exist "
            "(promotion can exceed original piece counts, never the "
            "piece or pawn supply)",
        )
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
    if castling_kind == "chess960":
        _need(
            castling_field == "-",
            f"{where}: chess960 castling rights are not yet representable "
            "in this contract - castling field must be '-' until the "
            "lint can verify rights against the start arrangement",
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
        re.fullmatch(r"0|[1-9][0-9]{0,9}", half) is not None
        and re.fullmatch(r"[1-9][0-9]{0,9}", full) is not None,
        f"{where}: counters must be canonical bounded ints (halfmove "
        "'0' or 1-9 leading, fullmove >= 1, no leading zeros, at most "
        "10 digits - conversion never runs on unbounded input)",
    )
    _check_position_semantics(grid, castling, side, castling_kind, where)
    if ep != "-":
        _check_ep_semantics(grid, ep, side, where)


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
    _need(
        re.fullmatch(r"/variant/v[1-9][0-9]*", base),
        "versioning.base_path: must be /variant/vN with numeric N",
    )
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
    except Exception as exc:  # classified rejection, never a traceback
        print(f"FAIL variant contract lint: internal error: {exc!r}")
        return 1
    print("OK variant contract lint: chess variant + position-identity contract v1 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
