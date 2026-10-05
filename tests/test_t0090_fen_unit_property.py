"""T0090: FEN unit and metamorphic property checks of the shipped graph.fen runtime.

Independent of the T0086 reference: properties are stated over the runtime alone.
1. Canonical round trip: every accepted FEN emits back to itself.
2. Colour mirror: swapping colours (reverse ranks, swap case, flip side to move,
   castling letters and en-passant rank) preserves the verdict class.
3. Counter edit: changing the fullmove number within range preserves the verdict.
4. Single-character edits never crash: accept (and round trip) or a typed FenError
   with a closed failure class and its mapped code.
5. Failures are atomic: the same input yields the same verdict before and after refusals.
"""

# ruff: noqa: E501
from __future__ import annotations

import random

import pytest

import graph.fen as fen
from tests import test_t0087_fen_fixture as fix

C = fix.C
CLASSES = set(C["failure_mapping"])
SEEDS = sorted(
    {r["input_fen"] for s in ("happy", "boundary") for r in fix.CASES[s] if "input_fen" in r}
    | {
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
        "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
        "r3k2r/8/8/8/8/8/8/R3K2R b Kq - 12 40",
        "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
        "8/8/8/8/8/8/8/8 w - - 0 1",
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 3",
    }
)
CASTLE_ORDER = "KQkq"


def verdict(text):
    try:
        position = fen.parse_fen(C, text)
    except fen.FenError as exc:
        assert exc.failure_class in CLASSES
        assert exc.code == C["failure_mapping"][exc.failure_class]["error"]
        return ("err", exc.failure_class)
    assert fen.emit_fen(C, position) == text, text
    return ("ok", None)


def mirror(text):
    placement, color, castling, ep, half, full = text.split(" ")
    ranks = [r.swapcase() for r in reversed(placement.split("/"))]
    if castling != "-":
        castling = "".join(sorted(castling.swapcase(), key=CASTLE_ORDER.index))
    if ep != "-":
        ep = ep[0] + str(9 - int(ep[1]))
    return " ".join(["/".join(ranks), "b" if color == "w" else "w", castling, ep, half, full])


@pytest.mark.parametrize("text", SEEDS)
def test_seed_roundtrip_and_mirror_parity(text):
    assert verdict(text) == verdict(mirror(text))
    assert mirror(mirror(text)) == text


@pytest.mark.parametrize("text", SEEDS)
def test_fullmove_edit_keeps_verdict_and_roundtrips(text):
    parts = text.split(" ")
    for number in ("40", "99", "9999"):
        parts[5] = number
        assert verdict(" ".join(parts))[0] == verdict(text)[0]


@pytest.mark.parametrize("seed", [90, 900, 20261005])
def test_single_character_edits_never_crash_and_accepted_ones_round_trip(seed):
    rng = random.Random(seed)
    alphabet = "pnbrqkPNBRQK12345678/ -wbcdefgh0123456789KQkq"
    accepted = rejected = 0
    for text in rng.sample(SEEDS, len(SEEDS)):
        for _ in range(60):
            i = rng.randrange(len(text))
            op = rng.choice(("sub", "del", "ins"))
            ch = rng.choice(alphabet)
            if op == "sub":
                edited = text[:i] + ch + text[i + 1 :]
            elif op == "del":
                edited = text[:i] + text[i + 1 :]
            else:
                edited = text[:i] + ch + text[i:]
            kind, _ = verdict(edited)
            accepted += kind == "ok"
            rejected += kind == "err"
    assert accepted > 0 and rejected > accepted


@pytest.mark.parametrize("bad", [None, 5, b"x", ["a"], "", " ", "x" * 10000, "8/" * 7 + "8\n"])
def test_non_fen_inputs_are_typed_malformed(bad):
    with pytest.raises(fen.FenError) as caught:
        fen.parse_fen(C, bad)
    assert caught.value.failure_class == "malformed_fen"


def test_refusals_do_not_poison_later_parses():
    good = SEEDS[0]
    first = fen.parse_fen(C, good)
    for bad in ("", "8/8 w - - 0 1", good.replace("w", "x"), good + " "):
        with pytest.raises(fen.FenError):
            fen.parse_fen(C, bad)
    assert fen.parse_fen(C, good) == first


def test_run_splitting_and_zero_digit_are_malformed():
    for placement in ("44/8/8/8/8/8/8/8", "0/8/8/8/8/8/8/8", "9/8/8/8/8/8/8/8", "8/8/8/8/8/8/8"):
        assert verdict(f"{placement} w - - 0 1") == ("err", "malformed_fen")


# -- fault injection: one-edit mutants of the runtime must turn the properties red --

import inspect  # noqa: E402
import types  # noqa: E402

MUTANTS = [
    ('if prev_digit and g["adjacent_digits"] == "forbidden":', "if False:"),
    ("if f != g[\"rank_sum\"]:\n            fail(\"malformed_fen\")", "if f > g[\"rank_sum\"]:\n            fail(\"malformed_fen\")"),
    ('if color not in contract["active_color"]["values"]:', "if False:"),
    ('if spec["leading_zeros"] == "forbidden" and value != str(number):', "if False:"),
    ("if number < spec[\"min\"]:", "if False:"),
    ("            str(half),\n            str(full),", "            str(full),\n            str(half),"),
    ("for r in range(len(contract[\"board\"][\"ranks\"]), 0, -1):", "for r in range(1, len(contract[\"board\"][\"ranks\"]) + 1):"),
    ("if type(text) is not str:", "if False:"),
]  # fmt: skip


def _mutant_module(old, new):
    source = inspect.getsource(fen)
    assert source.count(old) == 1, old
    module = types.ModuleType("mutant_fen")
    module.__file__ = fen.__file__
    exec(compile(source.replace(old, new), "mutant_fen", "exec"), module.__dict__)  # noqa: S102
    return module


def _properties_hold(module):
    def check(text):
        try:
            position = module.parse_fen(C, text)
        except module.FenError as exc:
            return ("err", exc.failure_class)
        assert module.emit_fen(C, position) == text
        return ("ok", None)

    for text in SEEDS:
        assert check(text) == check(mirror(text))
    for bad in ("44/8/8/8/8/8/8/8 w - - 0 1", "4k3/8/8/8/8/8/8/4K3 x - - 0 1",
                "4k3/8/8/8/8/8/8/4K3 w - - 00 1", "4k3/8/8/8/8/8/8/4K3 w - - 0 0",
                "7/8/8/8/8/8/8/8 w - - 0 1"):  # fmt: skip
        assert check(bad) == ("err", "malformed_fen"), bad
    for bad in (None, 5):
        try:
            module.parse_fen(C, bad)
        except module.FenError as exc:
            assert exc.failure_class == "malformed_fen"
        except Exception as exc:  # noqa: BLE001 - a crash is a failed property
            raise AssertionError("crash") from exc
        else:
            raise AssertionError("accepted non-text")


def test_properties_hold_for_the_shipped_runtime():
    _properties_hold(fen)


@pytest.mark.parametrize("old,new", MUTANTS, ids=[f"m{i}" for i in range(len(MUTANTS))])
def test_every_mutant_is_killed(old, new):
    with pytest.raises(AssertionError):
        _properties_hold(_mutant_module(old, new))
