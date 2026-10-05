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

import hashlib
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

VECTORS = [
    (
        START,
        "standard rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -",
        "66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2cae32a8b88ffe",
    ),
    (
        "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
        "standard 4k3/8/8/8/8/8/8/4K3 w - -",
        "5a52f52530c2a06135595264d05d0e93dc422ee8fb28818eef7d85a706bb949b",
    ),
    (
        "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 3",
        "standard 4k3/8/8/3pP3/8/8/8/4K3 w - d6",
        "a47758824cb6f3b306247c58860fd0f2bfc9207b9cdd7370a6b6ecb049b1adaf",
    ),
    (
        "4k3/8/8/8/3Pp3/8/8/4K3 b - d3 0 3",
        "standard 4k3/8/8/8/3Pp3/8/8/4K3 b - d3",
        "8e244350ce416780efc5181e7bae3fb83b745f83d7e70e18f0726fa0f03138da",
    ),
]
# black to move, target on rank 3: lower-file (c4), a-file (a4) and h-file (h4) capturers
BLACK_EP = [
    "4k3/8/8/8/2pP4/8/8/4K3 b - d3 0 2",
    "4k3/8/8/8/pP6/8/8/4K3 b - b3 0 2",
    "4k3/8/8/8/6Pp/8/8/4K3 b - g3 0 2",
    "4k3/8/8/8/3Pp3/8/8/4K3 b - d3 0 2",
]
# the capture is legal only because the capturer lands on the target square
WHITE_EP_LANDING = [
    "3r3k/8/8/3pP3/8/8/8/3K4 w - d6 0 2",
    "3k4/8/8/8/3Pp3/8/8/3R3K b - d3 0 2",
]
# black pawn captures en passant but its own king would be exposed on the rank
# the neighbour on the mover rank is not the mover's pawn: the target is unusable
EP_WRONG_NEIGHBOUR = [
    "4k3/8/8/2Np4/8/8/8/4K3 w - d6 0 2",  # own knight on the capturer square
    "4k3/8/8/2pp4/8/8/8/4K3 w - d6 0 2",  # enemy pawn on the capturer square
    "4k3/8/8/3pN3/8/8/8/4K3 w - d6 0 2",  # upper-file knight
    "4k3/8/8/8/3Pn3/8/8/4K3 b - d3 0 2",  # black to move, enemy knight on the capturer square
]
BLACK_EP_PINNED = ["8/8/8/8/k2Pp2R/8/8/4K3 b - d3 0 2"]


def _fields(text):
    return text.split(" ")


def _with(text, **kw):
    placement, color, castling, ep, half, full = _fields(text)
    values = dict(placement=placement, color=color, castling=castling, ep=ep, half=half, full=full)
    values.update(kw)
    return " ".join(values[k] for k in ("placement", "color", "castling", "ep", "half", "full"))


class Crash(Exception):
    """Foreign exception: not a semantic failure, never an AssertionError."""


def accept(module, variant, text):
    """Digest of a legal position; a refusal of legal input is a semantic failure."""
    try:
        return module.digest_fen(variant, text)
    except module.DigestError as exc:
        raise AssertionError(f"refused legal position {text!r}: {exc.failure_class}") from exc


def properties(module, variants=("standard",)):
    """Raises AssertionError when any property fails (a crash is a Crash, never a kill)."""
    try:
        _properties(module, variants)
    except AssertionError:
        raise
    except module.DigestError as exc:
        # every input in the property battery is legal: a refusal is a semantic failure
        raise AssertionError(f"refused legal input: {exc.failure_class}") from exc
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
            far = "99" if ep == "-" else "0"  # an en-passant target needs halfmove 0
            assert accept(module, variant, _with(text, half="0", full="40")) == accept(
                module, variant, _with(text, half=far, full="321")
            )

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
    # a non-pawn or enemy-pawn neighbour never makes the target usable
    for text in EP_WRONG_NEIGHBOUR:
        assert accept(module, "standard", text) == accept(
            module, "standard", _with(text, ep="-")
        ), text
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
    # known-answer vectors: literal encodings hashed independently, then the runtime must agree
    for fen, encoding, hexdigest in VECTORS:
        assert hashlib.sha256(encoding.encode()).hexdigest() == hexdigest
        assert module.digest_fen("standard", fen) == "pdv1:" + hexdigest
    # large counters are legal and never identity (no halfmove or fullmove refusal)
    for text in (START, SEEDS[4], SEEDS[6]):
        if _fields(text)[3] == "-":
            assert accept(module, "standard", _with(text, half="150", full="9999")) == accept(
                module, "standard", _with(text, half="0", full="1")
            )
    # usable en passant, black to move: lower-file, upper-file, a-file and h-file capturers
    for text in BLACK_EP + WHITE_EP_LANDING:
        assert module.digest_fen("standard", text) != module.digest_fen(
            "standard", _with(text, ep="-")
        ), text
    # black-to-move en passant that exposes the capturer's own king is not usable
    for text in BLACK_EP_PINNED:
        assert module.digest_fen("standard", text) == module.digest_fen(
            "standard", _with(text, ep="-")
        ), text
    if len(variants) > 1:
        assert module.digest_fen(variants[0], START) != module.digest_fen(variants[1], START)


def test_properties_hold_for_the_shipped_runtime():
    properties(pd, tuple(VARIANTS))


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_declared_variant_is_deterministic_and_distinct(variant):
    first = accept(pd, variant, START)
    assert first == accept(pd, variant, START)
    others = {accept(pd, v, START) for v in VARIANTS if v != variant}
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


class _DigestStr(str):
    pass


_GOOD_DIGEST = "pdv1:" + VECTORS[0][2]  # literal vector: no runtime call at import
_DIGEST_REFUSALS = [
    None,
    5,
    "",
    "sha256:xyz",
    "SHA256:" + "0" * 64,
    START,
    _DigestStr(_GOOD_DIGEST),  # a str subclass with the exact digest text
]


@pytest.mark.parametrize("bad", _DIGEST_REFUSALS, ids=lambda b: type(b).__name__ + str(b)[:8])
@pytest.mark.parametrize("fn", ["parse_digest", "emit_digest"])
def test_malformed_digest_text_is_typed(fn, bad):
    with pytest.raises(pd.DigestError) as caught:
        getattr(pd, fn)(bad)
    assert caught.value.failure_class == "malformed_digest"


@pytest.mark.parametrize("fn", ["parse_digest", "emit_digest"])
def test_exact_digest_text_is_accepted_unchanged(fn):
    try:
        got = getattr(pd, fn)(_GOOD_DIGEST)
    except pd.DigestError as exc:
        raise AssertionError(f"{fn} refused a valid digest: {exc.failure_class}") from exc
    assert got == _GOOD_DIGEST


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
    ('return " ".join(components[field] for field in order)', 'return "".join(components[field] for field in order)'),
    ('return " ".join(components[field] for field in order)', 'return "  ".join(components[field] for field in order)'),
    ('for field in order)', 'for field in reversed(order))'),
    ('for field in order)', 'for field in sorted(order))'),
    ("    return digest(dc, encode(dc, vc, ec, fc, variant_id, position))", "    if position[4] > 50:\n        _fail(dc, \"malformed_position\")\n    return digest(dc, encode(dc, vc, ec, fc, variant_id, position))"),
    ("if type(text) is not str or re.fullmatch", "if not isinstance(text, str) or re.fullmatch"),
    ("    return digest(dc, encode(dc, vc, ec, fc, variant_id, position))", "    _fail(dc, \"malformed_position\")"),
    ("if board.get((f, mover_rank)) != own_pawn:", "if board.get((f, mover_rank)) is None:"),
    ("    raise DigestError(cls, contract[\"failures\"][\"mapping\"][cls])", "    pass"),
    ("    for df in (-1, 1):", "    for df in (1,):"),
    ("    for df in (-1, 1):", "    for df in (-1,):"),
    ("if not 0 <= f < len(files):", "if not 0 < f < len(files):"),
    ("if not 0 <= f < len(files):", "if not 0 <= f < len(files) - 1:"),
    ('white_to_move = color == fen_contract["active_color"]["values"][0]', "white_to_move = True"),
    ('own_pawn = "P" if white_to_move else "p"', 'own_pawn = "P"'),
    ('own_pawn = "P" if white_to_move else "p"', 'own_pawn = "p"'),
    ('own_king = "K" if white_to_move else "k"', 'own_king = "K"'),
    ('own_king = "K" if white_to_move else "k"', 'own_king = "k"'),
    ("        after[(tf, target_rank)] = own_pawn\n", "        pass\n"),
    ("        del after[(tf, captured_rank)]\n", "        pass\n"),
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
        for fn in ("parse_digest", "emit_digest"):
            for bad in _DIGEST_REFUSALS:
                try:
                    getattr(module, fn)(bad)
                except module.DigestError:
                    pass
                else:
                    raise AssertionError(f"{fn} accepted {bad!r}")

    with pytest.raises(AssertionError):
        hostile(module)


def test_docs_are_detached_copies_on_every_call():
    first = pd._docs()
    second = pd._docs()
    assert first is not second and all(a is not b for a, b in zip(first, second, strict=True))
    first[0]["digest"]["format"]["prefix"] = "tampered:"
    assert pd._docs()[0]["digest"]["format"]["prefix"] == "pdv1:"


def test_docs_aliasing_mutant_is_killed():
    module = _mutant("return copy.deepcopy(_parsed_docs())", "return _parsed_docs()")
    first = module._docs()
    first[0]["digest"]["format"]["prefix"] = "tampered:"
    assert module._docs()[0]["digest"]["format"]["prefix"] != "pdv1:"  # the mutant leaks
