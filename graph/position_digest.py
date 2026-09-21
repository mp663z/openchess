"""Contract-derived production position digest runtime.

Implements data/contracts/position_digest.yaml through its linked variant,
en-passant and FEN contracts.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from graph.fen import FenError, _attack, emit_fen, parse_fen

ROOT = Path(__file__).resolve().parents[1]

CONTRACT = ROOT / "data" / "contracts" / "position_digest.yaml"
VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
EN_PASSANT = ROOT / "data" / "contracts" / "en_passant.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"


def _docs():
    return (
        yaml.safe_load(CONTRACT.read_text())["contract"],
        yaml.safe_load(VARIANT.read_text())["contract"],
        yaml.safe_load(EN_PASSANT.read_text())["contract"],
        yaml.safe_load(FEN.read_text())["contract"],
    )


def _lint():
    from tools.position_digest_contract_lint import main

    assert main([str(CONTRACT)]) == 0


class DigestError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(contract, cls):
    raise DigestError(cls, contract["failures"]["mapping"][cls])


def _ep_identity(digest_contract, ep_contract, fen_contract, position):
    """Identity value of the en-passant component: the target square
    only when at least one legal en-passant capture exists against
    it, else the none sentinel - exactly the linked contract's
    storage_vs_identity.identity_value."""
    board, color, _rights, ep, _half, _full = position
    sentinel = ep_contract["target"]["grammar"]["none_sentinel"]
    if ep is None:
        return sentinel
    files = fen_contract["board"]["files"]
    mover = ep_contract["capture"]["mover"]
    white_to_move = color == fen_contract["active_color"]["values"][0]
    side = mover["white" if white_to_move else "black"]
    tf = files.index(ep[0])
    target_rank = int(side["target_rank"])
    mover_rank = int(side["mover_rank"])
    captured_rank = int(side["captured_rank"])
    own_pawn = "P" if white_to_move else "p"
    own_king = "K" if white_to_move else "k"
    for df in (-1, 1):
        f = tf + df
        if not 0 <= f < len(files):
            continue
        if board.get((f, mover_rank)) != own_pawn:
            continue
        # preconditions: target set (given), adjacent pawn (given),
        # enemy pawn on the captured square (FEN contract already
        # pinned it), own king unattacked after the capture.
        after = dict(board)
        del after[(f, mover_rank)]
        del after[(tf, captured_rank)]
        after[(tf, target_rank)] = own_pawn
        king_sq = next(sq for sq, p in after.items() if p == own_king)
        if not _attack(fen_contract["board"], king_sq, after, not white_to_move):
            return ep
    return sentinel


def encode(digest_contract, variant_contract, ep_contract, fen_contract, variant_id, position):
    """Canonical byte encoding: the variant contract's canonical_fields
    in declared order, joined per the digest contract's encoding."""
    board, color, rights, ep, half, full = position
    placement = emit_fen(fen_contract, position).split(" ")[0]
    components = {
        "variant": variant_id,
        "board": placement,
        "side_to_move": color,
        "castling_rights": rights or fen_contract["castling"]["none_sentinel"],
        "en_passant": _ep_identity(digest_contract, ep_contract, fen_contract, position),
    }
    order = variant_contract["identity"]["canonical_fields"]
    return " ".join(components[field] for field in order)


def digest(digest_contract, encoding):
    d = digest_contract["digest"]
    raw = hashlib.new(d["algorithm"].replace("-", ""), encoding.encode("utf-8")).digest()
    assert len(raw) == d["output_bytes"]
    text = raw.hex()
    assert len(text) == d["hex_length"]
    return d["format"]["prefix"] + text


def digest_fen(variant_id, fen_text):
    dc, vc, ec, fc = _docs()
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if variant_id not in ids:
        _fail(dc, "unknown_variant")
    try:
        position = parse_fen(fc, fen_text)
    except FenError:
        _fail(dc, "malformed_position")
    return digest(dc, encode(dc, vc, ec, fc, variant_id, position))


def parse_digest(text):
    dc, _vc, _ec, _fc = _docs()
    if re.fullmatch(dc["digest"]["format"]["regex"], text) is None:
        _fail(dc, "malformed_digest")
    return text


def emit_digest(text):
    return parse_digest(text)
