"""T0041: chess variant + position-identity contract lint.

Enforces the normative CONTENT of data/contracts/variant.yaml, not just
its shape: the canonical identity fields in exact order, the registry
rules, well-formed start FENs, the closed failure classes and error
enum, and the no-single-hash / fail-closed guarantees. Anything less
passes silently and the whole position-identity model rots.

    python tools/variant_contract_lint.py [path]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "variant.yaml"

CANONICAL_FIELDS = ["variant", "board", "side_to_move",
                    "castling_rights", "en_passant"]
FAILURE_CLASSES = ["wrong_field_count", "bad_board", "bad_side",
                   "bad_castling", "bad_en_passant", "bad_counters",
                   "illegal_position"]
PIECE_CHARS = set("pnbrqkPNBRQK")


class ContractError(Exception):
    pass


def _need(cond: bool, problem: str) -> None:
    if not cond:
        raise ContractError(problem)


def _mapping(node: object, where: str) -> dict:
    _need(type(node) is dict, f"{where}: mapping required")
    return node


def _text(node: object, where: str) -> str:
    _need(type(node) is str and node.strip(), f"{where}: nonempty text")
    return node


def _check_fen(fen: object, where: str) -> None:
    _need(type(fen) is str, f"{where}: FEN string required")
    fields = fen.split(" ")
    _need(len(fields) == 6, f"{where}: FEN must have 6 fields")
    board, side, castling, ep, half, full = fields
    ranks = board.split("/")
    _need(len(ranks) == 8, f"{where}: board must have 8 ranks")
    for rank in ranks:
        squares = 0
        for ch in rank:
            if ch.isdigit():
                squares += int(ch)
            else:
                _need(ch in PIECE_CHARS,
                      f"{where}: bad piece char {ch!r}")
                squares += 1
        _need(squares == 8, f"{where}: every rank must have 8 squares")
    _need(side in ("w", "b"), f"{where}: side must be w or b")
    _need(castling == "-" or all(c in "KQkq" for c in castling)
          and len(set(castling)) == len(castling),
          f"{where}: castling field malformed")
    _need(ep == "-" or (len(ep) == 2 and ep[0] in "abcdefgh"
                        and ep[1] in "36"),
          f"{where}: en-passant field malformed")
    _need(half.isdigit() and full.isdigit() and int(full) >= 1,
          f"{where}: counters must be non-negative/positive ints")


def lint(doc: object) -> None:
    _need(type(doc) is dict, "document must be a mapping")
    _need(doc.get("schema_version") == 1
          and type(doc.get("schema_version")) is int,
          "schema_version: exact int 1")
    contract = _mapping(doc.get("contract"), "contract")
    _need(contract.get("id") == "chess-variant",
          "contract.id: must be chess-variant")

    versioning = _mapping(contract.get("versioning"), "contract.versioning")
    base = _text(versioning.get("base_path"), "versioning.base_path")
    _need(base.startswith("/variant/v"),
          "versioning.base_path: must be /variant/vN")
    rule = _text(versioning.get("rule"), "versioning.rule")
    _need("MINOR" in rule and "MAJOR" in rule and "downgrade" in rule,
          "versioning.rule: must state MINOR additive, MAJOR bump and "
          "rollback semantics")

    identity = _mapping(contract.get("identity"), "contract.identity")
    _need(identity.get("canonical_fields") == CANONICAL_FIELDS,
          "identity.canonical_fields: exact ordered tuple "
          f"{CANONICAL_FIELDS}")
    irule = _text(identity.get("rule"), "identity.rule")
    for marker in ("never a single hash", "move-order path",
                   "never participates"):
        _need(marker in irule, f"identity.rule: must state {marker!r}")
    hrule = _text(identity.get("hash_rule"), "identity.hash_rule")
    _need("collision" in hrule.lower() and "field comparison" in hrule,
          "identity.hash_rule: collisions must fall back to full "
          "field comparison")

    variants = _mapping(contract.get("variants"), "contract.variants")
    vrule = _text(variants.get("registry_rule"),
                  "variants.registry_rule")
    _need("fail closed" in vrule and "never coerced" in vrule,
          "variants.registry_rule: unknown ids fail closed, never "
          "coerced to standard")
    entries = variants.get("entries")
    _need(type(entries) is list and entries,
          "variants.entries: nonempty list")
    seen: set[str] = set()
    standard = None
    for i, entry in enumerate(entries):
        where = f"variants.entries[{i}]"
        entry = _mapping(entry, where)
        vid = _text(entry.get("id"), f"{where}.id")
        _need(vid not in seen, f"{where}: duplicate variant id {vid!r}")
        seen.add(vid)
        _text(entry.get("name"), f"{where}.name")
        _need(entry.get("castling") in ("orthodox", "chess960"),
              f"{where}.castling: orthodox or chess960")
        _need(entry.get("status") in ("stable", "experimental"),
              f"{where}.status: stable or experimental")
        _check_fen(entry.get("start_fen"), f"{where}.start_fen")
        if vid == "standard":
            standard = entry
    _need(standard is not None, "variants.entries: standard required")
    _need(standard.get("start_fen")
          == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
          "standard.start_fen: must be the exact orthodox start")

    fen = _mapping(contract.get("fen"), "contract.fen")
    _need(fen.get("failure_classes") == FAILURE_CLASSES,
          f"fen.failure_classes: exact ordered set {FAILURE_CLASSES}")
    frule = _text(fen.get("rule"), "fen.rule")
    _need("round-trip" in frule and "never partially applied" in frule,
          "fen.rule: round-trip exactness and no partial application")
    _text(fen.get("illegal_position_rule"), "fen.illegal_position_rule")

    errors = _mapping(contract.get("errors"), "contract.errors")
    enum = errors.get("closed_enum")
    _need(type(enum) is list and enum,
          "errors.closed_enum: nonempty list")
    _need(len(set(enum)) == len(enum),
          "errors.closed_enum: duplicate codes")
    for code in ("malformed_request", "unknown_variant",
                 "illegal_position"):
        _need(code in enum, f"errors.closed_enum: {code} required")

    privacy = _mapping(contract.get("privacy"), "contract.privacy")
    _need(privacy.get("chess_content") == "allowed-here",
          "privacy.chess_content: must be allowed-here (position data "
          "is user chess content; T0005 governs origins)")


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
    print("OK variant contract lint: chess variant + position-identity "
          "contract v1 clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
