"""T0045: variant unit/property battery for the T0044 runtime.

Where the T0042 fixture pins REVIEWED cases, this battery pins
PROPERTIES that must hold over generated inputs (seeded, deterministic,
no new dependencies):

- round-trip: identity(parse_position(v, fen_of(identity))) == identity
  for every accepted record, over every registry entry's own start_fen
  and every fixture happy case under its declared variant, x seeded
  counter mutations (counters never participate in identity).
- fail-closed fuzz: seeded field-level and byte-level corruptions of
  valid inputs raise ONLY VariantError carrying the contract's exact
  error shape - never a traceback, never a bare ValueError, never a
  wrong-typed attribute.
- additive invariance: project_additive(record + seeded additive
  fields) == identity(record) for every happy record.
- identity separation: flipping side_to_move always changes identity;
  changing only counters never does.
"""

from __future__ import annotations

import json
import random
import re
import string
from pathlib import Path

import pytest
import yaml

from tools import variant_runtime as rt

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests" / "fixtures" / "variant" / "cases.json").read_text())
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "variant.yaml").read_text())
REGISTRY = [e["id"] for e in DOC["contract"]["variants"]["entries"]]
ERROR_ENUM = set(DOC["contract"]["errors"]["closed_enum"])
FAILURE_CLASSES = {"wrong_field_count", "bad_board", "bad_side", "bad_castling",
                   "bad_en_passant", "bad_counters", "illegal_position"}
CANONICAL = ["variant", "board", "side_to_move", "castling_rights", "en_passant"]

HAPPY = CASES["happy"]
SEED = 20260919
FUZZ_CASES = 400
# exact per-category fuzz distribution for SEED+3 (fixed seed ->
# deterministic; a generator change must update this pin in the same
# change, which is where review sees it)
EXPECTED_CATEGORY_COUNTS = {
    "wrong_field_count": 57,
    "bad_board": 49,
    "bad_side": 41,
    "bad_castling": 48,
    "bad_en_passant": 48,
    "bad_counters": 55,
    "wrong_types": 48,
    "illegal_position": 54,
}


def _assert_contract_error_shape(err: rt.VariantError) -> None:
    assert type(err.code) is str and err.code in ERROR_ENUM
    assert err.failure_class is None or (
        type(err.failure_class) is str and err.failure_class in FAILURE_CLASSES
    )
    assert type(err.retryable) is bool
    assert type(err.args[0]) is str and err.args[0].strip()


def _fen_of(record: dict, half: int, full: int) -> str:
    return (f"{record['board']} {record['side_to_move']} "
            f"{record['castling_rights']} {record['en_passant']} {half} {full}")


def test_roundtrip_identity_over_counter_mutations():
    rng = random.Random(SEED)
    for case in HAPPY:
        position = rt.parse_position(case["variant"], case["fen"])
        ident = rt.identity(position)
        assert list(ident) == CANONICAL
        for _ in range(8):
            half = rng.randint(0, 150)
            full = rng.randint(1, 200)
            again = rt.parse_position(case["variant"], _fen_of(ident, half, full))
            assert rt.identity(again) == ident


def test_counters_never_participate_side_always_does():
    rng = random.Random(SEED + 1)
    accepted_flips = dict.fromkeys(REGISTRY_ENTRIES, 0)
    for case in HAPPY:
        ident = rt.identity(rt.parse_position(case["variant"], case["fen"]))
        other_side = "b" if ident["side_to_move"] == "w" else "w"
        flipped = _fen_of({**ident, "side_to_move": other_side},
                          rng.randint(0, 100), rng.randint(1, 100))
        try:
            other = rt.parse_position(case["variant"], flipped)
        except rt.VariantError:
            continue  # the flipped position need not be legal
        accepted_flips[case["variant"]] += 1
        assert rt.identity(other) != ident
        diff = {f for f in CANONICAL if rt.identity(other)[f] != ident[f]}
        assert diff == {"side_to_move"}
    # the property must not go vacuous: at least one accepted flip per
    # registry variant, with the counts visible in the failure message
    for vid in REGISTRY_ENTRIES:
        assert accepted_flips[vid] >= 1, (
            f"no accepted side-flip for {vid}; counts: {accepted_flips}")


def test_additive_invariance():
    rng = random.Random(SEED + 2)
    alphabet = string.ascii_lowercase
    for case in HAPPY:
        ident = rt.identity(rt.parse_position(case["variant"], case["fen"]))
        for _ in range(8):
            extra = {
                "x_" + "".join(rng.choice(alphabet) for _ in range(6)): rng.randint(0, 10**6)
                for _ in range(rng.randint(1, 4))
            }
            assert not set(extra) & set(CANONICAL)
            assert rt.project_additive({**ident, **extra}) == ident


CATEGORIES = [
    "wrong_field_count",
    "bad_board",
    "bad_side",
    "bad_castling",
    "bad_en_passant",
    "bad_counters",
    "wrong_types",
    "illegal_position",
]

# semantic illegal_position corruptions of the standard start position
_START = HAPPY[0]["fen"]
_ILLEGAL_FENS = [
    # missing white king
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQ1BNR w KQkq - 0 1",
    # two white kings
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RKBQKBNR w KQkq - 0 1",
    # pawn on the back rank (black pawn on b8; b7 pawn and c8 knight
    # removed so counts stay legal and the back-rank check fires FIRST)
    "rp1qkbnr/1ppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    # castling rights inconsistent with pieces (no white rooks)
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/1NBQKBN1 w KQkq - 0 1",
    # impossible en-passant: black to move, ep e3, but no white pawn
    # ever reached e4 (start board) - origin inconsistency
    _START.replace(" w KQkq - 0 1", " b KQkq e3 0 1"),
]


def _corruptions(rng: random.Random):
    """Seeded invalid inputs across EVERY attack surface, each yielded
    with its category so hit counts can be pinned exactly (the seed is
    fixed, so category counts are deterministic)."""
    base = HAPPY[0]
    fen = base["fen"]
    for _ in range(FUZZ_CASES):
        which = rng.randrange(len(CATEGORIES))
        category = CATEGORIES[which]
        if category == "wrong_field_count":
            parts = fen.split(" ")
            yield category, base["variant"], " ".join(parts[: rng.randrange(1, 6)])
        elif category == "bad_board":
            board = list(fen.split(" ")[0])
            board[rng.randrange(len(board))] = rng.choice("xZ@!")  # never a piece char
            yield category, base["variant"], " ".join(["".join(board)] + fen.split(" ")[1:])
        elif category == "bad_side":
            yield category, base["variant"], fen.replace(
                " w ", f" {rng.choice(['W', 'x', 'ww'])} ", 1)
        elif category == "bad_castling":
            parts = fen.split(" ")
            parts[2] = rng.choice(["XYZ", "KQ-", "", "AAAA"])
            yield category, base["variant"], " ".join(parts)
        elif category == "bad_en_passant":
            parts = fen.split(" ")
            parts[3] = rng.choice(["e4", "i3", "e0", "e33", "", "E3"])
            yield category, base["variant"], " ".join(parts)
        elif category == "bad_counters":
            parts = fen.split(" ")
            parts[rng.choice([4, 5])] = rng.choice(["-1", "x", "01", "1.5", ""])
            yield category, base["variant"], " ".join(parts)
        elif category == "illegal_position":
            yield category, base["variant"], rng.choice(_ILLEGAL_FENS)
        else:  # wrong_types: non-string / wrong-typed inputs
            bad_variant = rng.choice([None, 42, {}, ["standard"], "Crazyhouse", "STANDARD", ""])
            bad_fen = rng.choice([None, 7, {}, ["x"], fen.replace(" ", "  ")])
            yield category, bad_variant, bad_fen


def test_fail_closed_fuzz():
    rng = random.Random(SEED + 3)
    counts = dict.fromkeys(CATEGORIES, 0)
    for category, variant, fen in _corruptions(rng):
        counts[category] += 1
        try:
            rt.parse_position(variant, fen)
        except rt.VariantError as err:
            _assert_contract_error_shape(err)
            if category in FAILURE_CLASSES:
                # discriminating: each generated case fails at ITS OWN
                # stage, never a collateral one
                assert err.failure_class == category, (
                    f"{category} case failed as {err.failure_class}: {variant!r} {fen!r}")
        except Exception as other:  # noqa: BLE001 - the property IS the exception type
            raise AssertionError(
                f"non-VariantError escape: {type(other).__name__}: {other}"
            ) from other
        else:
            raise AssertionError(f"corrupted input accepted: {variant!r} {fen!r}")
    # RNG selection can never starve a class: exact per-category counts
    # are pinned (fixed seed -> deterministic distribution)
    assert counts == EXPECTED_CATEGORY_COUNTS


REGISTRY_ENTRIES = {e["id"]: e for e in DOC["contract"]["variants"]["entries"]}


def test_registry_entries_exercised_via_own_start_fens():
    """Every registry id is exercised locally: its OWN registered
    start_fen parses, round-trips under counter mutations, and unknown
    ids fail closed. The fixture's happy section must also cover every
    registry id - coverage asserted here, not assumed."""
    assert {c["variant"] for c in HAPPY} >= set(REGISTRY_ENTRIES), (
        "happy fixture coverage does not span the registry")
    rng = random.Random(SEED + 4)
    for vid, entry in REGISTRY_ENTRIES.items():
        position = rt.parse_position(vid, entry["start_fen"])
        ident = rt.identity(position)
        for _ in range(4):
            again = rt.parse_position(
                vid, _fen_of(ident, rng.randint(0, 150), rng.randint(1, 200)))
            assert rt.identity(again) == ident
        with pytest.raises(rt.VariantError) as ei:
            rt.parse_position(vid + "-nope", entry["start_fen"])
        _assert_contract_error_shape(ei.value)
    for case in HAPPY:
        rt.parse_position(case["variant"], case["fen"])
        with pytest.raises(rt.VariantError) as ei:
            rt.parse_position(case["variant"] + "-nope", case["fen"])
        _assert_contract_error_shape(ei.value)


def test_error_shape_on_every_fixture_malformed_case():
    for case in CASES["malformed"]:
        with pytest.raises(rt.VariantError) as ei:
            rt.parse_position("standard", case["fen"])
        _assert_contract_error_shape(ei.value)


def test_empty_castling_field_rejected_regression():
    """Regression for the vacuous-truth lint gap this battery found:
    all() over an empty castling string is True, so '' was accepted as
    castling rights. '-' is the ONLY no-rights form."""
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w  - 0 1"
    with pytest.raises(rt.VariantError) as ei:
        rt.parse_position("standard", fen)
    _assert_contract_error_shape(ei.value)
    assert ei.value.failure_class == "bad_castling"


def test_identity_projection_is_exact_and_ordered():
    ident = rt.identity(rt.parse_position(HAPPY[0]["variant"], HAPPY[0]["fen"]))
    assert list(ident) == CANONICAL
    assert re.fullmatch(r"[rnbqkpRNBQKP1-8/]+", ident["board"])
    assert ident["side_to_move"] in ("w", "b")
