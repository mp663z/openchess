"""T0089: FEN runtime (graph.fen) battery.

R1 the T0087 fixture rows run against the shipped runtime; R2 parity with
the T0086 contract reference over every fixture FEN, every single-character
edit of them and a seeded sweep of generated positions; R3 the T0088 red
tests bound to the runtime; R4 hostile payloads fail closed as a typed
malformed_fen; R5 atomicity, determinism and no poisoning; R6 the forge
set; R7 one-line source mutants of the runtime are each killed.
"""

from __future__ import annotations

import ast
import copy
import inspect
import random
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph.fen as prod  # noqa: E402
from tests import test_t0086_fen_contract as ref  # noqa: E402
from tests import test_t0087_fen_fixture as fix  # noqa: E402
from tests import test_t0088_fen_red as red  # noqa: E402

PRODUCTION = ROOT / "graph" / "fen.py"
C = fix.C
CASES = fix.CASES
MALFORMED = C["failure_mapping"]["malformed_fen"]["error"]


def ENTRY(mod):
    """The runtime's external parse entry point."""
    return mod.parse_fen


def _outcome(parse, emit, text):
    try:
        position = parse(C, text)
    except (prod.FenError, ref.FenError) as exc:
        return ("err", exc.failure_class, exc.code)
    return ("ok", position, emit(C, position))


def _typed_malformed(mod, payload):
    try:
        ENTRY(mod)(C, payload)
    except prod.FenError as exc:
        return (
            type(exc) is prod.FenError
            and exc.failure_class == "malformed_fen"
            and exc.code == MALFORMED
            and exc.__cause__ is None
        )
    except BaseException:  # noqa: BLE001 - a raw escape is a failure
        return False
    return False


# -- R1: fixture rows against the runtime ----------------------------------------


def _fixture_red(mod):
    why = []
    parse, emit = ENTRY(mod), mod.emit_fen
    for section in ("happy", "boundary"):
        for case in CASES[section]:
            got = _outcome(parse, emit, case["input_fen"])
            if got[0] != "ok" or got[2] != case["expect_fen"]:
                why.append(case["name"])
    for case in CASES["malformed"]:
        got = _outcome(parse, emit, case["input_fen"])
        want = C["failure_mapping"][case["expect_failure"]]["error"]
        if got != ("err", case["expect_failure"], want):
            why.append(case["name"])
        repaired = fix._repaired(case)
        if _outcome(parse, emit, repaired)[0] != "ok":
            why.append(case["name"] + "/repair")
    for case in CASES["rollback"]:
        got = _outcome(parse, emit, case["reject_fen"])
        if got[:2] != ("err", case["expect_failure"]):
            why.append(case["name"])
        then = _outcome(parse, emit, case["then_fen"])
        if then[0] != "ok" or then[2] != case["expect_fen"]:
            why.append(case["name"] + "/then")
    return why


def test_r1_fixture_rows():
    assert _fixture_red(prod) == []


def test_r1_error_shape():
    assert set(C["failure_mapping"]) == {"malformed_fen", "impossible_position"}
    with pytest.raises(prod.FenError) as err:
        ENTRY(prod)(C, "8/8/8/8/8/8/8/8 w - - 0 1")
    assert err.value.failure_class == "impossible_position"
    assert err.value.code == C["failure_mapping"]["impossible_position"]["error"]


# -- R2: parity with the T0086 reference ----------------------------------------


def _fixture_fens():
    fens = []
    for section in ("happy", "boundary"):
        fens += [c["input_fen"] for c in CASES[section]]
    for case in CASES["malformed"]:
        fens += [case["input_fen"], fix._repaired(case)]
    for case in CASES["rollback"]:
        fens += [case["reject_fen"], case["then_fen"]]
    return list(dict.fromkeys(fens))


ALPHABET = "pnbrqkPNBRQK0123456789/ wb-acdefghKQkqx"


def _edits():
    for fen in _fixture_fens():
        yield fen
        for i in range(len(fen)):
            yield fen[:i] + fen[i + 1 :]
            for ch in ALPHABET:
                if ch != fen[i]:
                    yield fen[:i] + ch + fen[i + 1 :]
        for i in range(0, len(fen) + 1, 3):
            for ch in "p1/ 0":
                yield fen[:i] + ch + fen[i:]


def _generated(seed=89, count=12000):
    """Seeded positions aimed at the position rules: kings (sometimes
    missing, doubled or adjacent), pawns on any rank, sliders, knights,
    castling rights with and without the home pieces, en-passant targets
    with and without the double-advanced pawn, both sides to move."""
    rng = random.Random(seed)
    files = "abcdefgh"
    for _ in range(count):
        board = {}
        kings = rng.choice([("K", "k")] * 8 + [("K",), ("K", "k", "k")])
        homes = rng.random() < 0.3
        if homes:
            board.update({(4, 1): "K", (4, 8): "k"})
            for f, r, p in ((0, 1, "R"), (7, 1, "R"), (0, 8, "r"), (7, 8, "r")):
                if rng.random() < 0.7:
                    board[(f, r)] = p
            kings = ()
        for k in kings:
            board[(rng.randrange(8), rng.randrange(1, 9))] = k
        for _ in range(rng.randrange(0, 9)):
            board[(rng.randrange(8), rng.randrange(1, 9))] = rng.choice("PpNnBbRrQq")
        side = rng.choice("wb")
        if rng.random() < 0.25:
            f = rng.randrange(8)
            if side == "w":
                board[(f, 5)] = "p"
                board.pop((f, 6), None)
                board.pop((f, 7), None)
                ep = files[f] + "6"
            else:
                board[(f, 4)] = "P"
                board.pop((f, 3), None)
                board.pop((f, 2), None)
                ep = files[f] + "3"
            if rng.random() < 0.2:
                ep = files[rng.randrange(8)] + rng.choice("36")
        else:
            ep = "-"
        castling = "".join(c for c in "KQkq" if rng.random() < 0.4) or "-"
        half = "0" if ep != "-" or rng.random() < 0.5 else str(rng.randrange(1, 60))
        position = (board, side, "" if castling == "-" else castling, None, int(half), 1)
        text = ref.emit_fen(C, position)
        parts = text.split(" ")
        parts[3] = ep
        yield " ".join(parts)


SWEEP = list(dict.fromkeys(list(_edits()) + list(_generated())))


def test_r2_sweep_is_large_and_mixed():
    outs = {}
    for text in SWEEP[::5]:
        got = _outcome(ENTRY(prod), prod.emit_fen, text)
        outs[got[0] if got[0] == "ok" else got[1]] = True
    assert len(SWEEP) > 50000
    assert set(outs) == {"ok", "malformed_fen", "impossible_position"}


@pytest.mark.parametrize("chunk", range(4))
def test_r2_parity_with_the_reference(chunk):
    for text in SWEEP[chunk::4]:
        a = _outcome(ENTRY(prod), prod.emit_fen, text)
        b = _outcome(ref.parse_fen, ref.emit_fen, text)
        assert a == b, text


def test_r2_accepted_fens_round_trip_byte_for_byte():
    for text in SWEEP[::3]:
        got = _outcome(ENTRY(prod), prod.emit_fen, text)
        if got[0] == "ok":
            assert got[2] == text


# Rows the sweep reaches only rarely, each pinned to its expected result.
PINNED = {
    # mover pawn attacks the non-mover king, both colours and both diagonals
    "4k3/3P4/8/8/8/8/8/4K3 w - - 0 1": "impossible_position",
    "4k3/5P2/8/8/8/8/8/4K3 w - - 0 1": "impossible_position",
    "4k3/8/8/8/8/8/3p4/4K3 b - - 0 1": "impossible_position",
    "4k3/8/8/8/8/8/5p2/4K3 b - - 0 1": "impossible_position",
    # pawns that do not attack the king (in front of it or behind it)
    "4k3/4P3/8/8/8/8/8/4K3 w - - 0 1": "ok",
    "4k3/8/8/8/8/8/4p3/4K3 b - - 0 1": "ok",
    "8/8/8/8/8/2k5/1P6/4K3 b - - 0 1": "ok",
    # en-passant target square occupied
    "4k3/8/3n4/3pP3/8/8/8/4K3 w - d6 0 1": "impossible_position",
    "4k3/8/8/8/3Pp3/3N4/8/4K3 b - d3 0 1": "impossible_position",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1": "ok",
    # en-passant origin square occupied
    "4k3/3n4/8/3pP3/8/8/8/4K3 w - d6 0 1": "impossible_position",
}


def test_r2_pinned_rows():
    for text, want in PINNED.items():
        a = _outcome(ENTRY(prod), prod.emit_fen, text)
        assert a == _outcome(ref.parse_fen, ref.emit_fen, text), text
        assert (a[0] if a[0] == "ok" else a[1]) == want, text


# -- R3: the T0088 red tests bound to the runtime --------------------------------

RED_TESTS = sorted(n for n in dir(red) if n.startswith("test_"))
RAW_RED = "test_executable_raw_parser_mutant_is_killed_for_hostile_payloads"


def _bind(monkeypatch):
    monkeypatch.setattr(red, "parse_fen", ENTRY(prod))
    monkeypatch.setattr(red, "emit_fen", prod.emit_fen)
    monkeypatch.setattr(red, "FenError", prod.FenError)


@pytest.mark.parametrize("name", [n for n in RED_TESTS if n != RAW_RED])
def test_r3_t0088_red_tests_pass_bound_to_the_runtime(name, monkeypatch):
    _bind(monkeypatch)
    fn = getattr(red, name)
    params = getattr(fn, "pytestmark", [])
    if params:
        for value in params[0].args[1]:
            fn(value)
    else:
        fn()


@pytest.mark.parametrize("payload", [None, True, 0, [], {}, b"fen"])
def test_r3_raw_parser_red_delta_is_closed(payload):
    """T0088 pins that the pre-implementation parser leaks raw exceptions
    on these payloads; the runtime maps every one to a typed FenError."""
    with pytest.raises((AttributeError, TypeError)):
        ref.parse_fen(C, payload)
    assert _typed_malformed(prod, payload)


def test_r3_binding_is_live():
    assert len(RED_TESTS) >= 5
    src = inspect.getsource(red)
    assert "from tests.test_t0086_fen_contract import FenError, emit_fen, parse_fen" in src


# -- R4: hostile payloads fail closed --------------------------------------------

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class _Str(str):
    pass


class _LyingStr(str):
    def split(self, *args, **kwargs):
        return START.split(" ")


HUGE = "9" * 5000  # beyond the interpreter's int-string limit (4300)

HOSTILE = {
    "none": None,
    "bool": True,
    "int": 0,
    "float": 1.5,
    "list": [START],
    "tuple": tuple(START.split(" ")),
    "dict": {"fen": START},
    "bytes": START.encode(),
    "bytearray": bytearray(START.encode()),
    "str-subclass-valid": _Str(START),
    "str-subclass-lying-split": _LyingStr("x"),
    "huge-halfmove": "4k3/8/8/8/8/8/8/4K3 w - - " + HUGE + " 1",
    "huge-fullmove": "4k3/8/8/8/8/8/8/4K3 w - - 0 " + HUGE,
    "empty": "",
    "spaces-only": "     ",
    "tab-separated": START.replace(" ", "\t"),
    "trailing-space": START + " ",
    "leading-space": " " + START,
    "double-space": START.replace(" w ", "  w "),
    "newline-end": START + "\n",
    "nul": START.replace("8", "\x00", 1),
    "arabic-indic-digit-counter": "4k3/8/8/8/8/8/8/4K3 w - - \u0663 1",
    "fullwidth-digit-counter": "4k3/8/8/8/8/8/8/4K3 w - - 0 \uff11",
    "superscript-digit-placement": "4k3/8/8/8/8/8/8/\u00b3K4 w - - 0 1",
    "lone-surrogate": START.replace("w", "\ud800"),
    "plus-sign-counter": "4k3/8/8/8/8/8/8/4K3 w - - +0 1",
    "negative-counter": "4k3/8/8/8/8/8/8/4K3 w - - -1 1",
    "underscore-counter": "4k3/8/8/8/8/8/8/4K3 w - - 0 1_0",
    "long-garbage": "p" * 100000,
    "nine-ranks": "8/" * 8 + "8 w - - 0 1",
}


@pytest.mark.parametrize("name", list(HOSTILE))
def test_r4_hostile_payload_fails_closed(name):
    assert _typed_malformed(prod, HOSTILE[name])


def test_r4_str_payloads_agree_with_the_reference_where_it_is_total():
    for name, payload in HOSTILE.items():
        if type(payload) is str and HUGE not in payload:
            assert _outcome(ref.parse_fen, ref.emit_fen, payload)[:2] == (
                "err",
                "malformed_fen",
            ), name


# -- R5: atomicity, determinism, no poisoning -------------------------------------


def test_r5_contract_untouched_and_results_fresh():
    before = copy.deepcopy(C)
    first = ENTRY(prod)(C, START)
    for text in SWEEP[::50]:
        _outcome(ENTRY(prod), prod.emit_fen, text)
    for payload in HOSTILE.values():
        _typed_malformed(prod, payload)
    second = ENTRY(prod)(C, START)
    assert before == C
    assert first == second and first[0] is not second[0]
    first[0][(0, 4)] = "Q"
    assert ENTRY(prod)(C, START) == second


def test_r5_rejection_never_poisons_the_next_parse():
    for payload in list(HOSTILE.values()) + [c["input_fen"] for c in CASES["malformed"]]:
        _typed_malformed(prod, payload) or _outcome(ENTRY(prod), prod.emit_fen, payload)
        assert prod.emit_fen(C, ENTRY(prod)(C, START)) == START


# -- R6: the forge set ------------------------------------------------------------


def test_r6_raise_sites_and_except_clauses():
    raised, caught = set(), set()
    for node in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            raised.add(ast.unparse(exc))
        elif isinstance(node, ast.ExceptHandler):
            caught.add(ast.unparse(node.type) if node.type is not None else "bare")
    assert raised == {"FenError"} and caught == {"ValueError"}


def test_r6_forged_value_error_at_the_counter_boundary_is_fresh(monkeypatch):
    forged = ValueError("forged")

    def forging_int(value, *args):
        if value == "7":
            raise forged
        return int(value, *args)

    monkeypatch.setattr(prod, "int", forging_int, raising=False)
    with pytest.raises(prod.FenError) as err:
        ENTRY(prod)(C, "4k3/8/8/8/8/8/8/4K3 w - - 7 1")
    assert err.value is not forged and err.value.__cause__ is None
    assert err.value.failure_class == "malformed_fen" and err.value.code == MALFORMED


# -- R7: one-line source mutants of the runtime are killed ------------------------

MUTANTS = {
    "type-check-off": ('    if type(text) is not str:\n        fail("malformed_fen")\n', ""),
    "type-check-isinstance": ("if type(text) is not str:", "if not isinstance(text, str):"),
    "field-count-off": ('if len(parts) != len(contract["fields"]["order"]) or any(', "if any("),
    "rank-count-off": (
        '    if len(rank_list) != g["rank_count"]:\n        fail("malformed_fen")\n',
        "",
    ),
    "adjacent-digits-off": ('if prev_digit and g["adjacent_digits"] == "forbidden":', "if False:"),
    "digit-flag-kept": (
        "                f += 1\n                prev_digit = False\n",
        "                f += 1\n",
    ),
    "letters-accept-any": ("            elif ch in letters:", "            elif True:"),
    "rank-sum-off": ('        if f != g["rank_sum"]:\n            fail("malformed_fen")\n', ""),
    "rank-sum-at-most": ('if f != g["rank_sum"]:', 'if f > g["rank_sum"]:'),
    "color-off": (
        '    if color not in contract["active_color"]["values"]:\n        fail("malformed_fen")\n',
        "",
    ),
    "white-to-move-inverted": ('white_to_move = color == "w"', 'white_to_move = color != "w"'),
    "int-limit-reraise": ("            number = None\n", "            raise\n"),
    "leading-zeros-off": (
        'if spec["leading_zeros"] == "forbidden" and value != str(number):',
        "if False:",
    ),
    "counter-min-off": (
        '        if number < spec["min"]:\n            fail("malformed_fen")\n',
        "",
    ),
    "counter-min-off-by-one": ('if number < spec["min"]:', 'if number < spec["min"] - 1:'),
    "castling-order-off": (
        'if "".join(ch for ch in cs["order"] if ch in rights) != rights:',
        "if False:",
    ),
    "ep-length-off": ("if len(ep) != 2 or ep[0]", "if ep[0]"),
    "ep-file-off": (" or ep[0] not in files or ", " or "),
    "ep-rank-off": (' or ep[1] not in es["ranks"]:', ":"),
    "white-king-count-off": ('(len(wk) != 1 and pr["white_kings"] == "exactly-1") or ', ""),
    "black-king-count-off": (
        ' or (\n        len(bk) != 1 and pr["black_kings"] == "exactly-1"\n    )',
        "",
    ),
    "back-rank-one-side": ("back = (ranks[0], ranks[-1])", "back = (ranks[0],)"),
    "back-rank-white-pawns-only": (
        'if pce in "Pp" and str(r) in back:',
        'if pce == "P" and str(r) in back:',
    ),
    "promotion-budget-off": ("if excess > pmax - pawns:", "if False:"),
    "promotion-budget-lax": ("if excess > pmax - pawns:", "if excess > pmax:"),
    "non-mover-check-off": (
        'if _attack(contract["board"], non_mover, board, by_white=mover_white):',
        "if False:",
    ),
    "non-mover-wrong-king": (
        "non_mover = bk[0] if mover_white else wk[0]",
        "non_mover = wk[0] if mover_white else bk[0]",
    ),
    "castling-king-off": (
        '            board.get((kf, int(home["king"][1]))) != king_letter\n            or ',
        "            ",
    ),
    "castling-rook-off": (
        '\n            or board.get((rf, int(home["rook"][1]))) != rook_letter',
        "",
    ),
    "ep-side-to-move-off": (
        '        if (requires == "black-to-move" and white_to_move) or (\n'
        '            requires == "white-to-move" and not white_to_move\n'
        '        ):\n            fail("impossible_position")\n',
        "",
    ),
    "ep-pawn-off": (
        '        if board.get((f, int(side["to_rank"]))) != pawn:\n'
        '            fail("impossible_position")\n',
        "",
    ),
    "ep-target-empty-off": ('if es["target_square"] == "empty" and (f, r) in board:', "if False:"),
    "ep-origin-empty-off": (
        'if es["origin_square"] == "empty" and (f, int(side["from_rank"])) in board:',
        "if False:",
    ),
    "ep-halfmove-off": ('if es["halfmove_clock"] == "must-be-zero" and half_i != 0:', "if False:"),
    "attack-pawn-off": ("        if board.get((f - df, r - dr)) == pawn:", "        if False:"),
    "attack-pawn-direction": (
        "board.get((f - df, r - dr)) == pawn",
        "board.get((f + df, r + dr)) == pawn",
    ),
    "attack-knight-off": (
        'if board.get((f + df, r + dr)) == ("N" if by_white else "n"):',
        "if False:",
    ),
    "attack-slider-no-block": ("                    break\n", "                    pass\n"),
    "attack-queen-not-rook": (
        '((a["rook_directions"], ("R", "Q"))',
        '((a["rook_directions"], ("R",))',
    ),
    "attack-queen-not-bishop": (
        '(a["bishop_directions"], ("B", "Q")))',
        '(a["bishop_directions"], ("B",)))',
    ),
    "emit-empty-run-dropped": (
        "        if empty:\n            row += str(empty)\n        rank_list",
        "        rank_list",
    ),
}

# One-guard edits no input can separate under the current contract.
EQUIVALENT_EDITS = {
    # every field has its own non-empty guard (rank count, color set,
    # castling, ep length, counter grammar)
    "empty-field-off": (' or any(part == "" for part in parts):', ":"),
    # an empty rank sums to 0 and fails the rank-sum check
    "empty-rank-off": ('        if not rank:\n            fail("malformed_fen")\n', ""),
    # int() plus the no-leading-zeros round trip and the minimum reject every non-canonical counter
    "counter-grammar-off": ('and not re.fullmatch(r"[0-9]+", value):', "and False:"),
    # same as counter-grammar-off: the round trip str(int(value)) == value rejects any trailing text
    "counter-grammar-search": ('re.fullmatch(r"[0-9]+", value)', 're.match(r"[0-9]+", value)'),
    # a foreign letter is dropped by the order projection, so the order check rejects it
    "castling-letters-off": (
        'if not rights or any(ch not in cs["letters"] for ch in rights):',
        "if not rights:",
    ),
    # a repeated letter appears once in the order projection, so the order check rejects it
    "castling-duplicates-off": ("if len(set(rights)) != len(rights):", "if False:"),
    # adjacent kings leave the non-mover king attacked by the mover king
    "kings-adjacent-off": ("if max(abs(wf - bf), abs(wr - br)) <= 1:", "if False:"),
    # same as kings-adjacent-off
    "kings-adjacent-strict": (
        "if max(abs(wf - bf), abs(wr - br)) <= 1:",
        "if max(abs(wf - bf), abs(wr - br)) < 1:",
    ),
    # a ninth pawn makes the promotion budget negative, so any officer count fails it
    "pawn-max-off": ("if pawns > pmax or len(mine) > tmax:", "if len(mine) > tmax:"),
    # with one king, a seventeenth piece always exceeds the promotion budget
    "piece-max-off": ("if pawns > pmax or len(mine) > tmax:", "if pawns > pmax:"),
    # the king-adjacency rule runs before the check rule and rejects every king attack
    "attack-king-off": (
        'if board.get((f + df, r + dr)) == ("K" if by_white else "k"):',
        "if False:",
    ),
}


def _mutant(name):
    before, after = {**MUTANTS, **EQUIVALENT_EDITS}[name]
    source = PRODUCTION.read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType(f"fen_mutant_{name.replace('-', '_')}")
    module.__file__ = str(PRODUCTION)
    exec(compile(source.replace(before, after), str(PRODUCTION), "exec"), module.__dict__)
    module.FenError = prod.FenError
    return module


def _pinned_red(mod):
    for text, want in PINNED.items():
        got = _outcome(ENTRY(mod), mod.emit_fen, text)
        if (got[0] if got[0] == "ok" else got[1]) != want:
            return True
    return False


def _red(mod):
    try:
        if _fixture_red(mod) or _pinned_red(mod):
            return True
        for payload in HOSTILE.values():
            if not _typed_malformed(mod, payload):
                return True
        for text in SWEEP:
            if _outcome(ENTRY(mod), mod.emit_fen, text) != _outcome(
                ref.parse_fen, ref.emit_fen, text
            ):
                return True
    except BaseException:  # noqa: BLE001 - a raw escape is a kill
        return True
    return False


@pytest.mark.parametrize("name", list(MUTANTS))
def test_r7_source_mutant_is_killed(name):
    assert _red(_mutant(name))


@pytest.mark.parametrize("name", list(EQUIVALENT_EDITS))
def test_r7_equivalent_edit_stays_green(name):
    assert not _red(_mutant(name))


def test_r7_unmutated_runtime_is_green():
    assert not _red(prod)
