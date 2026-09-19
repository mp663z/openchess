"""T0086: the FEN contract is the enforced source of truth.

The normative contract (data/contracts/fen.yaml) declares the
six-field shape, placement grammar, position rules, castling and
en-passant consistency, counters, canonical serialization, and
all-or-nothing parse atomicity. This battery proves the behavior the
contract promises with an EXECUTABLE reference model DERIVED FROM the
contract data (grammar, rules, error mapping all come from the yaml,
never hardcoded), then replays mutations: contradiction, reversal,
bool/int drift, rogue keys, undeclared error codes, and linkage
mutations against the real sibling contracts. A semantic or
structural slip must fail both the lint and the model checks.
"""

from __future__ import annotations

import copy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.fen_contract_lint import lint
from tools.variant_contract_lint import ContractError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "fen.yaml"
DATA_DIR = ROOT / "data" / "contracts"


def _doc():
    return yaml.safe_load(CONTRACT.read_text())


def _lint():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "fen_contract_lint.py")],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


# -- executable reference model, derived from the contract data -------

FILES = "abcdefgh"
KNIGHT = [(1, 2), (2, 1), (2, -1), (1, -2),
          (-1, -2), (-2, -1), (-2, 1), (-1, 2)]
KING = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
        if (dx, dy) != (0, 0)]
ORTHOGONAL = [(1, 0), (-1, 0), (0, 1), (0, -1)]
DIAGONAL = [(1, 1), (1, -1), (-1, 1), (-1, -1)]


def _attacks(board, square, by_white):
    """True if `by_white`'s side attacks `square` on `board`
    ({(file, rank): piece letter})."""
    f, r = square
    pawn_dir = 1 if by_white else -1
    for df in (-1, 1):
        if board.get((f + df, r - pawn_dir)) == ("P" if by_white else "p"):
            return True
    for dx, dy in KNIGHT:
        if board.get((f + dx, r + dy)) == ("N" if by_white else "n"):
            return True
    for dx, dy in KING:
        if board.get((f + dx, r + dy)) == ("K" if by_white else "k"):
            return True
    for directions, sliders in ((ORTHOGONAL, ("R", "Q")),
                                (DIAGONAL, ("B", "Q"))):
        want = tuple(c if by_white else c.lower() for c in sliders)
        for dx, dy in directions:
            nf, nr = f + dx, r + dy
            while 0 <= nf < 8 and 1 <= nr <= 8:
                piece = board.get((nf, nr))
                if piece:
                    if piece in want:
                        return True
                    break
                nf, nr = nf + dx, nr + dy
    return False


class FenError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def parse_fen(contract, text):
    """Contract-derived parser: returns a position tuple or raises
    FenError carrying the contract's failure class and error code.
    All-or-nothing per contract.atomicity: no partial state."""
    c = contract
    mapping = c["failure_mapping"]

    def fail(cls):
        raise FenError(cls, mapping[cls]["error"])

    parts = text.split(" ")
    if parts != c["fields"]["order"] and (
        len(parts) != 6 or any(p == "" for p in parts)
    ):
        fail("malformed_fen")
    if len(parts) != 6 or any(p == "" for p in parts):
        fail("malformed_fen")
    placement, color, castling, ep, half, full = parts

    # placement grammar
    g = c["placement"]
    ranks = placement.split(g["rank_separator"])
    if len(ranks) != g["rank_count"]:
        fail("malformed_fen")
    letters = set(g["piece_letters"])
    digits = set(g["empty_run_digits"])
    board = {}
    for ri, rank in enumerate(ranks):
        rank_no = 8 - ri  # rank_order rank8-to-rank1
        if not rank or sum(ch.isdigit() for ch in rank) == 0 and False:
            pass
        if not rank:
            fail("malformed_fen")
        f = 0
        prev_digit = False
        for ch in rank:
            if ch in digits:
                if prev_digit and g["adjacent_digits"] == "forbidden":
                    fail("malformed_fen")
                f += int(ch)
                prev_digit = True
            elif ch == "0" and g["zero_digit"] == "forbidden":
                fail("malformed_fen")
            elif ch in letters:
                board[(f, rank_no)] = ch
                f += 1
                prev_digit = False
            else:
                fail("malformed_fen")
        if f != g["rank_sum"]:
            fail("malformed_fen")

    # active color
    if color not in c["active_color"]["values"]:
        fail("malformed_fen")
    white_to_move = color == "w"

    # counters
    for value, spec in ((half, c["counters"]["halfmove_clock"]),
                        (full, c["counters"]["fullmove_number"])):
        if spec["digits_only"] and not value.isdigit():
            fail("malformed_fen")
        if int(value) < spec["min"]:
            fail("malformed_fen")
    half_i, full_i = int(half), int(full)

    # castling grammar
    cs = c["castling"]
    if castling == cs["none_sentinel"]:
        rights = ""
    else:
        rights = castling
        if not rights or any(ch not in cs["letters"] for ch in rights):
            fail("malformed_fen")
        if len(set(rights)) != len(rights):
            fail("malformed_fen")
        if "".join(ch for ch in cs["order"] if ch in rights) != rights:
            fail("malformed_fen")

    # en passant grammar
    es = c["en_passant"]
    if ep == es["none_sentinel"]:
        ep_square = None
    else:
        if len(ep) != 2 or ep[0] not in FILES or ep[1] not in es["ranks"]:
            fail("malformed_fen")
        ep_square = ep

    # position rules
    pr = c["position_rules"]
    wk = [sq for sq, p in board.items() if p == "K"]
    bk = [sq for sq, p in board.items() if p == "k"]
    if (len(wk) != 1 and pr["white_kings"] == "exactly-1") or (
        len(bk) != 1 and pr["black_kings"] == "exactly-1"
    ):
        fail("impossible_position")
    if pr["kings_adjacent"] == "forbidden":
        (wf, wr), (bf, br) = wk[0], bk[0]
        if max(abs(wf - bf), abs(wr - br)) <= 1:
            fail("impossible_position")
    if pr["pawns_on_back_ranks"] == "forbidden":
        for (_f, r), p in board.items():
            if p in "Pp" and r in (1, 8):
                fail("impossible_position")
    if pr["non_mover_king_attacked"] == "forbidden":
        mover_white = white_to_move
        non_mover = bk[0] if mover_white else wk[0]
        if _attacks(board, non_mover, by_white=mover_white):
            fail("impossible_position")

    # castling consistency: right requires king + rook on start squares
    start = {"K": ((4, 1), (7, 1), "K", "R"),
             "Q": ((4, 1), (0, 1), "K", "R"),
             "k": ((4, 8), (7, 8), "k", "r"),
             "q": ((4, 8), (0, 8), "k", "r")}
    for right in rights:
        (kf, kr), (rf, rr), kletter, rletter = start[right]
        if board.get((kf, kr)) != kletter or board.get((rf, rr)) != rletter:
            fail("impossible_position")

    # en passant consistency
    if ep_square is not None:
        f, r = FILES.index(ep_square[0]), int(ep_square[1])
        if r == 3:
            if es["rank3_requires"] != "black-to-move" or white_to_move:
                fail("impossible_position")
            if board.get((f, 4)) != "P":
                fail("impossible_position")
        else:
            if es["rank6_requires"] != "white-to-move" or not white_to_move:
                fail("impossible_position")
            if board.get((f, 5)) != "p":
                fail("impossible_position")

    return (board, color, rights, ep_square, half_i, full_i)


def emit_fen(position):
    board, color, rights, ep, half, full = position
    ranks = []
    for r in range(8, 0, -1):
        row, empty = "", 0
        for f in range(8):
            piece = board.get((f, r))
            if piece:
                if empty:
                    row += str(empty)
                    empty = 0
                row += piece
            else:
                empty += 1
        if empty:
            row += str(empty)
        ranks.append(row)
    return " ".join([
        "/".join(ranks), color, rights or "-", ep or "-",
        str(half), str(full),
    ])


# -- happy / boundary / malformed / atomicity behavior -----------------

STARTPOS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def test_lint_clean():
    _lint()


def test_happy_startpos_and_roundtrip():
    doc = _doc()
    pos = parse_fen(doc["contract"], STARTPOS)
    assert pos[1] == "w" and pos[2] == "KQkq" and pos[3] is None
    assert pos[4] == 0 and pos[5] == 1
    assert len(pos[0]) == 32
    assert emit_fen(pos) == STARTPOS  # canonical round-trip identity


def test_happy_midgame_with_ep_target():
    # 1. e4: target recorded on the advance (no capture available).
    fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    pos = parse_fen(_doc()["contract"], fen)
    assert pos[3] == "e3" and pos[1] == "b"
    assert emit_fen(pos) == fen


def test_happy_serialization_roundtrip_fixture_set():
    doc = _doc()
    fixtures = [
        STARTPOS,
        "r3k2r/ppqppppp/2n2n2/2bpp3/2BPP3/2N2N2/PPQPPPPP/R3K2R w KQkq - 6 7",
        "8/8/8/8/8/8/8/K6k w - - 0 1",          # bare kings boundary
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 2",     # real ep capture state
        "r3k3/8/8/8/8/8/8/4K2R b Kq - 12 20",
        "8/8/8/8/8/8/8/K6k b - - 99 240",        # high counters boundary
    ]
    for fen in fixtures:
        assert emit_fen(parse_fen(doc["contract"], fen)) == fen


@pytest.mark.parametrize("bad", [
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0",     # 5 fields
    "8/8/8/8/8/8/8/K6k w - - 0 1 extra",                          # 7 fields
    "8/8/8/8/8/8/8/K6k  w - - 0 1",                               # double space
    "8/8/8/8/8/8/8/K6k w - - 0 1 ",                               # trailing space
    "8/8/8/8/8/8/8/K6k W - - 0 1",                                # bad color
    "8/8/8/8/8/8/8/K6k x - - 0 1",                                # bad color
    "8/8/8/8/8/8/8/K6k w - - x 1",                                # bad halfmove
    "8/8/8/8/8/8/8/K6k w - - -1 1",                               # signed counter
    "8/8/8/8/8/8/8/K6k w - - 0 0",                                # fullmove < min
    "8/8/8/8/8/8/8/K6k w - - 0 -1",                               # bad fullmove
    "9/8/8/8/8/8/8/K6k w - - 0 1",                                # rank sum 9
    "7/8/8/8/8/8/8/K6k w - - 0 1",                                # rank sum 7
    "8/8/8/8/8/8/8/K6k w - - 0 1".replace("8", "44", 1),          # adjacent digits
    "8/8/8/8/8/8/8/K6k w - - 0 1".replace("8", "80", 1),          # zero digit
    "8/8/8/8/8/8/8/K6k w - - 0 1".replace("8", "7X", 1),          # bad letter
    "8/8/8/8/8/8/K6k w - - 0 1",                                  # 7 ranks
    "8/8/8/8/8/8/8/K6k w KK - 0 1",                               # duplicate right
    "8/8/8/8/8/8/8/K5Rk w qK - 0 1",                              # wrong order
    "8/8/8/8/8/8/8/K5Rk w X - 0 1",                               # bad right letter
    "8/8/8/8/8/8/8/K6k w - e4 0 1",                               # bad ep rank
    "8/8/8/8/8/8/8/K6k w - zz 0 1",                               # bad ep square
])
def test_malformed_fens_rejected_as_malformed_request(bad):
    doc = _doc()
    with pytest.raises(FenError) as exc:
        parse_fen(doc["contract"], bad)
    assert exc.value.failure_class == "malformed_fen"
    assert exc.value.code == "malformed_request"


@pytest.mark.parametrize("bad", [
    "8/8/8/8/8/8/8/7k w - - 0 1",            # no white king
    "8/8/8/8/8/8/8/K7 w - - 0 1",            # no black king
    "K7/8/8/8/8/8/8/K6k w - - 0 1",          # two white kings
    "8/8/8/8/8/8/8/KK5k w - - 0 1",          # two white kings, one rank
    "8/8/8/8/8/8/8/Kk6 w - - 0 1",           # adjacent kings
    "8/8/8/8/8/8/k7/K7 b - - 0 1",           # adjacent kings vertical
    "P6k/8/8/8/8/8/8/K7 w - - 0 1",          # pawn on rank 8
    "7k/8/8/8/8/8/8/p6K w - - 0 1",          # pawn on rank 1
    "4k3/8/8/8/8/8/8/K3R3 w - - 0 1",        # non-mover king attacked
    "k3r3/8/8/8/8/8/8/4K3 b - - 0 1",        # non-mover king attacked (black)
    "r3k2r/8/8/8/8/8/8/R3K3 b Kq - 0 1",    # K right, no white rook h1
    "r6k/8/8/8/8/8/8/4K3 b q - 0 1",        # q right, black king off e8
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e3 0 1",
    # ^ ep rank 3 requires black to move
    "4k3/8/8/8/3p4/8/8/4K3 b - d3 0 2",
    # ^ ep rank 3 requires the advancing WHITE pawn on rank 4
    "4k3/8/8/3pP3/8/8/8/4K3 b - d6 0 2",
    # ^ ep rank 6 requires white to move
    "4k3/8/8/8/8/8/8/4K3 w - d6 0 2",
    # ^ ep rank 6 requires the advancing black pawn on rank 5
])
def test_impossible_positions_rejected_as_illegal_position(bad):
    doc = _doc()
    with pytest.raises(FenError) as exc:
        parse_fen(doc["contract"], bad)
    assert exc.value.failure_class == "impossible_position"
    assert exc.value.code == "illegal_position"


def test_rejected_parse_yields_no_partial_state():
    # Atomicity: parse returns either the complete position or an
    # error; there is no observable partial state from a rejected FEN.
    doc = _doc()
    result = None
    try:
        result = parse_fen(
            doc["contract"],
            "8/8/8/8/8/8/8/Kk6 w KQkq - 0 1",  # adjacent kings
        )
    except FenError as exc:
        result = ("error", exc.code)
    assert result == ("error", "illegal_position")


def test_error_shape_and_closed_enum():
    contract = _doc()["contract"]
    assert set(contract["errors"]["closed_enum"]) == {
        "malformed_request", "illegal_position", "internal"}
    fields = contract["errors"]["shape"]["error"]["fields"]
    for name in ("code", "message", "retryable"):
        assert fields[name]["required"] is True
    for cls, m in contract["failure_mapping"].items():
        assert m["error"] in contract["errors"]["closed_enum"], cls


# -- mutation replay ---------------------------------------------------

def _mutants():
    doc = _doc()
    c = doc["contract"]
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    add("five-field order", ["contract", "fields", "order"],
        c["fields"]["order"][:5])
    add("exactly_six false", ["contract", "fields", "exactly_six"], False)
    add("rank_count 7", ["contract", "placement", "rank_count"], 7)
    add("rank sum 9", ["contract", "placement", "rank_sum"], 9)
    add("zero digit allowed", ["contract", "placement", "zero_digit"],
        "allowed")
    add("adjacent digits allowed",
        ["contract", "placement", "adjacent_digits"], "allowed")
    add("two white kings", ["contract", "position_rules", "white_kings"],
        "exactly-2")
    add("adjacent kings allowed",
        ["contract", "position_rules", "kings_adjacent"], "allowed")
    add("back-rank pawns allowed",
        ["contract", "position_rules", "pawns_on_back_ranks"], "allowed")
    add("non-mover attack allowed",
        ["contract", "position_rules", "non_mover_king_attacked"],
        "allowed")
    add("castling order contradiction", ["contract", "castling", "order"],
        "KkQq")
    add("castling consistency dropped",
        ["contract", "castling", "consistency"], "none")
    add("ep storage identity contradiction",
        ["contract", "en_passant", "storage"],
        "recorded-only-when-capture-is-legal")
    add("ep ranks drift", ["contract", "en_passant", "ranks"],
        ["3", "4", "5", "6"])
    add("halfmove min -1", ["contract", "counters", "halfmove_clock"],
        {"type": "integer", "digits_only": True, "min": -1})
    add("fullmove min 0", ["contract", "counters", "fullmove_number"],
        {"type": "integer", "digits_only": True, "min": 0})
    add("counter digits_only false",
        ["contract", "counters", "halfmove_clock", "digits_only"], False)
    add("canonical false", ["contract", "serialization", "canonical"],
        False)
    add("roundtrip dropped", ["contract", "serialization", "roundtrip"],
        "best-effort")
    add("atomicity dropped", ["contract", "atomicity", "parse"],
        "partial-state-allowed")
    add("mapping contradiction", ["contract", "failure_mapping",
        "malformed_fen", "error"], "illegal_position")
    add("undeclared error code", ["contract", "failure_mapping",
        "impossible_position", "error"], "weird_error")
    add("enum narrowed", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    m = copy.deepcopy(doc)
    m["contract"]["rogue"] = {"x": 1}
    out.append(("rogue contract key", m))
    m = copy.deepcopy(doc)
    m["contract"]["placement"]["rogue"] = 1
    out.append(("rogue placement key", m))
    m = copy.deepcopy(doc)
    m["contract"]["failure_mapping"]["extra_class"] = {
        "trigger": "t", "error": "malformed_request"}
    out.append(("orphan failure-mapping class", m))
    m = copy.deepcopy(doc)
    del m["contract"]["position_rules"]
    out.append(("missing position_rules section", m))
    m = copy.deepcopy(doc)
    m["contract"]["errors"]["shape"]["error"]["fields"]["code"][
        "required"] = False
    out.append(("error shape reversal", m))
    m = copy.deepcopy(doc)
    m["contract"]["errors"]["shape"]["error"]["fields"]["retryable"][
        "type"] = "integer"
    out.append(("retryable type drift", m))
    return out


def test_mutations_fail_lint():
    for name, mutant in _mutants():
        try:
            lint(mutant)
        except ContractError:
            continue
        pytest.fail(f"mutation accepted by lint: {name}")


def test_mutants_never_silent_subset():
    assert len(_mutants()) >= 24


# -- linkage: sibling contracts drift -> FEN lint fails ----------------

def _lint_with_root(tmp_path, mutate=None):
    contracts = tmp_path / "data" / "contracts"
    shutil.copytree(DATA_DIR, contracts)
    if mutate:
        mutate(contracts)
    lint(_doc(), root=tmp_path)


def test_linkage_clean_copy_passes(tmp_path):
    _lint_with_root(tmp_path)


def test_linkage_en_passant_storage_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "en_passant.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["target"]["storage_vs_identity"]["storage"] = (
            "recorded-only-when-capture-is-legal")
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_en_passant_rank_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "en_passant.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["target"]["grammar"]["ranks"] = ["3", "4", "5", "6"]
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_turn_bound_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "turn.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["state"]["bounds"]["fullmove_number"]["min"] = 0
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_castling_values_drift_fails(tmp_path):
    def mutate(contracts):
        path = contracts / "castling.yaml"
        doc = yaml.safe_load(path.read_text())
        doc["contract"]["rights"]["values"] = ["K", "Q"]
        path.write_text(yaml.safe_dump(doc, sort_keys=False))

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)


def test_linkage_missing_sibling_fails(tmp_path):
    def mutate(contracts):
        (contracts / "en_passant.yaml").unlink()

    with pytest.raises(ContractError):
        _lint_with_root(tmp_path, mutate)
