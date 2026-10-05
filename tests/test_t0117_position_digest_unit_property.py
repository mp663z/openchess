# ruff: noqa: E501
"""T0117: unit and property checks of the shipped graph.position_digest runtime.

Stated over the runtime alone (no T0113 reference):
1. Determinism and format: same input, same digest; output matches the contract regex
   and round-trips through parse_digest/emit_digest.
2. Identity: halfmove and fullmove counters never change the digest; board, side to move,
   castling rights and variant each do.
3. En passant: the target counts only when a legal capture exists; an unusable target
   digests exactly like "-", a usable one differs.
4. Injectivity on a seeded sample: distinct positions never share a digest.
5. Precedence and typing: unknown variant wins over a malformed FEN; typed DigestError
   only, with the contract's mapped failure.
6. One-edit mutants of the runtime turn these properties red.
"""

from __future__ import annotations

import inspect
import random
import re
import types

import pytest

import graph.position_digest as pd

DC, VC, _EC, _FC = pd._docs()
VARIANTS = [e["id"] for e in VC["variants"]["entries"]]
REGEX = DC["digest"]["format"]["regex"]
CLASSES = set(DC["failures"]["mapping"])

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SEEDS = [
    START,
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R b Kq - 12 40",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 3",
    "4k3/8/8/8/8/8/4P3/4K3 b - - 5 9",
]


def _fields(text):
    return text.split(" ")


def _with(text, **kw):
    placement, color, castling, ep, half, full = _fields(text)
    values = dict(placement=placement, color=color, castling=castling, ep=ep, half=half, full=full)
    values.update(kw)
    return " ".join(values[k] for k in ("placement", "color", "castling", "ep", "half", "full"))


class Crash(Exception):
    """Foreign exception: not a semantic failure, never an AssertionError."""


def properties(module, variants=("standard",)):
    """Raises AssertionError when any property fails (a crash is a Crash, never a kill)."""
    try:
        _properties(module, variants)
    except AssertionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise Crash(f"crash {type(exc).__name__}: {exc}") from exc


def _properties(module, variants):
    for variant in variants:
        for text in SEEDS:
            d = module.digest_fen(variant, text)
            assert d == module.digest_fen(variant, text)
            assert re.fullmatch(REGEX, d) and module.emit_digest(module.parse_digest(d)) == d
            # counters are not identity
            ep = _fields(text)[3]
            far = "17" if ep == "-" else "0"  # an en-passant target needs halfmove 0
            assert module.digest_fen(
                variant, _with(text, half="0", full="40")
            ) == module.digest_fen(variant, _with(text, half=far, full="321"))

    base = module.digest_fen("standard", SEEDS[2])
    assert base != module.digest_fen("standard", _with(SEEDS[2], color="b"))
    assert base != module.digest_fen("standard", _with(SEEDS[2], castling="Kq"))
    assert base != module.digest_fen("standard", _with(SEEDS[2], castling="-"))
    assert module.digest_fen("standard", SEEDS[4]) != module.digest_fen(
        "standard", "4k3/8/8/8/8/8/8/3K4 w - - 0 1"
    )
    # en passant: usable target is identity, unusable one is not
    assert module.digest_fen("standard", SEEDS[5]) != module.digest_fen(
        "standard", _with(SEEDS[5], ep="-")
    )
    assert module.digest_fen("standard", SEEDS[1]) == module.digest_fen(
        "standard", _with(SEEDS[1], ep="-")
    )
    # piece colour alone is identity
    assert module.digest_fen("standard", SEEDS[4]) != module.digest_fen(
        "standard", "4K3/8/8/8/8/8/8/4k3 w - - 0 1"
    )
    # an en-passant capture that would expose the capturer's own king is not usable
    pinned = "4k3/8/8/2KpP2r/8/8/8/8 w - d6 0 2"
    assert module.digest_fen("standard", pinned) == module.digest_fen(
        "standard", _with(pinned, ep="-")
    )
    # colour mirror of an asymmetric position never collides with the original
    for text in (SEEDS[2], SEEDS[4], SEEDS[6], pinned):
        placement, color, castling, ep, half, full = _fields(text)
        flipped = "/".join(r.swapcase() for r in reversed(placement.split("/")))
        mirrored = " ".join(
            [
                flipped,
                "b" if color == "w" else "w",
                castling.swapcase() if castling != "-" else "-",
                "-",
                half,
                full,
            ]
        )
        if flipped != placement:
            assert module.digest_fen("standard", text) != module.digest_fen("standard", mirrored)
    if len(variants) > 1:
        assert module.digest_fen(variants[0], START) != module.digest_fen(variants[1], START)


def test_properties_hold_for_the_shipped_runtime():
    properties(pd, tuple(VARIANTS))


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_declared_variant_is_deterministic_and_distinct(variant):
    first = pd.digest_fen(variant, START)
    assert first == pd.digest_fen(variant, START)
    others = {pd.digest_fen(v, START) for v in VARIANTS if v != variant}
    assert first not in others


@pytest.mark.parametrize("seed", [117, 1170, 20261005])
def test_seeded_edits_are_typed_and_collision_free(seed):
    rng = random.Random(seed)
    alphabet = "pnbrqkPNBRQK12345678/ -wbKQkqe3"
    by_identity = {}
    for text in SEEDS:
        for _ in range(80):
            i = rng.randrange(len(text))
            edited = text[:i] + rng.choice(alphabet) + text[i + 1 :]
            try:
                d = pd.digest_fen("standard", edited)
            except pd.DigestError as exc:
                assert exc.failure_class in CLASSES
                continue
            parts = _fields(edited)
            # identity key excludes counters; equal digests need an equal identity or unusable ep
            key = (parts[0], parts[1], parts[2])
            previous = by_identity.setdefault(d, key)
            assert previous == key, (edited, previous)


def test_unknown_variant_wins_over_malformed_fen_and_types_are_exact():
    with pytest.raises(pd.DigestError) as caught:
        pd.digest_fen("no-such-variant", "not a fen")
    assert caught.value.failure_class == "unknown_variant"
    with pytest.raises(pd.DigestError) as caught:
        pd.digest_fen("standard", "not a fen")
    assert caught.value.failure_class == "malformed_position"

    class Liar(str):
        def __eq__(self, other):
            return True

        __hash__ = str.__hash__

    for bad in (Liar("standard"), None, 3, ["standard"]):
        with pytest.raises(pd.DigestError) as caught:
            pd.digest_fen(bad, START)
        assert caught.value.failure_class == "unknown_variant"


@pytest.mark.parametrize("bad", [None, 5, "", "sha256:xyz", "SHA256:" + "0" * 64, START])
def test_malformed_digest_text_is_typed(bad):
    with pytest.raises(pd.DigestError) as caught:
        pd.parse_digest(bad)
    assert caught.value.failure_class == "malformed_digest"


MUTANTS = [
    ('"side_to_move": color,', '"side_to_move": "w",'),
    ('"board": placement,', '"board": placement.lower(),'),
    ('"castling_rights": rights or fen_contract["castling"]["none_sentinel"],', '"castling_rights": fen_contract["castling"]["none_sentinel"],'),
    ("    return sentinel\n\n\ndef encode", "    return ep\n\n\ndef encode"),
    ("        if not _attack(fen_contract[\"board\"], king_sq, after, not white_to_move):\n            return ep", "        if True:\n            return ep"),
    ('    if ep is None:\n        return sentinel', '    if ep is None:\n        return "-" + sentinel'),
    ('if type(variant_id) is not str or variant_id not in ids:', 'if variant_id not in ids:'),
    ("    return d[\"format\"][\"prefix\"] + text" if False else 'return d["format"]["prefix"] + text', 'return d["format"]["prefix"] + text.upper()'),
    ("position = parse_fen(fc, fen_text)", "position = parse_fen(fc, fen_text.strip())"),
]  # fmt: skip


def _mutant(old, new):
    source = inspect.getsource(pd)
    assert source.count(old) == 1, old
    module = types.ModuleType("mutant_digest")
    module.__file__ = pd.__file__
    exec(compile(source.replace(old, new), "mutant_digest", "exec"), module.__dict__)  # noqa: S102
    return module


@pytest.mark.parametrize("old,new", MUTANTS, ids=[f"m{i}" for i in range(len(MUTANTS))])
def test_every_mutant_is_killed(old, new):
    module = _mutant(old, new)

    def hostile(module):
        # properties plus the typing/precedence probes
        properties(module, tuple(VARIANTS))

        class Liar(str):
            def __eq__(self, other):
                return True

            __hash__ = str.__hash__

        for bad in ("x", None, Liar("x")):
            try:
                module.digest_fen(bad, START)
            except module.DigestError:
                pass
            else:
                raise AssertionError("accepted unknown variant")
        try:
            module.digest_fen("standard", "  " + START)
        except module.DigestError:
            pass
        else:
            raise AssertionError("accepted padded fen")

    with pytest.raises(AssertionError):
        hostile(module)
