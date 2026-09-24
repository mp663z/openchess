"""T0116: position-digest runtime (graph.position_digest) battery.

R1 the T0114 fixture rows run against the shipped runtime; R2 parity with
the T0113 contract reference over the fixture, the T0089 FEN sweep (every
single-character edit of the FEN fixtures plus generated positions) for
every declared and an unknown variant, en-passant identity rows, and a
digest-text sweep; R3 the T0115 red tests bound to the runtime; R4 hostile
inputs fail closed with a typed DigestError; R5 no shared state and exact
outputs; R6 the forge set; R7 one-line source mutants are each killed.
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph.position_digest as prod  # noqa: E402
from tests import test_t0089_fen_runtime as fen_battery  # noqa: E402
from tests import test_t0113_position_digest_contract as ref  # noqa: E402
from tests import test_t0114_fixture as fix  # noqa: E402
from tests import test_t0115_position_digest_red as red  # noqa: E402

PRODUCTION = ROOT / "graph" / "position_digest.py"
CASES = fix.CASES
MAPPING = fix.FAILURE_MAPPING
VARIANTS = [e["id"] for e in prod._docs()[1]["variants"]["entries"]]
GOOD_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
GOOD_DIGEST = CASES["happy"][0]["expect_digest"]


def _call(fn, *args):
    try:
        out = fn(*args)
    except (prod.DigestError, ref.DigestError) as exc:
        return ("err", exc.failure_class, exc.code)
    return ("ok", type(out).__name__, out)


def _typed(mod, fn_name, args, failure_class):
    try:
        getattr(mod, fn_name)(*args)
    except prod.DigestError as exc:
        return (
            type(exc) is prod.DigestError
            and exc.failure_class == failure_class
            and exc.code == MAPPING[failure_class]
            and exc.__cause__ is None
        )
    except BaseException:  # noqa: BLE001 - a raw escape is a failure
        return False
    return False


# -- R1: fixture rows against the runtime -----------------------------------------


def _fixture_red(mod):
    why = []
    for section in ("happy", "boundary", "malformed", "rollback"):
        for case in CASES[section]:
            kind = case["kind"]
            if kind == "digest":
                got = _call(mod.digest_fen, case["input"]["variant"], case["input"]["fen"])
                want = (
                    ("ok", "str", case["expect_digest"])
                    if "expect_digest" in case
                    else ("err", case["expect_failure"], MAPPING[case["expect_failure"]])
                )
            elif kind == "digest-parse":
                got = _call(mod.parse_digest, case["input_text"])
                want = (
                    ("ok", "str", case["expect_text"])
                    if "expect_text" in case
                    else ("err", case["expect_failure"], MAPPING[case["expect_failure"]])
                )
            elif kind == "digest-equivalence":
                a, b = (mod.digest_fen("standard", fen) for fen in case["inputs"])
                got, want = (a == b), (case["expect"] == "equal")
            elif kind == "rollback-digest-parse":
                got = (
                    _call(mod.parse_digest, case["reject_text"])[0],
                    _call(mod.parse_digest, case["then_text"]),
                )
                want = ("err", ("ok", "str", case["expect_text"]))
            else:
                rej, then = case["reject_input"], case["then_input"]
                got = (
                    _call(mod.digest_fen, rej["variant"], rej["fen"])[0],
                    _call(mod.digest_fen, then["variant"], then["fen"]),
                )
                want = ("err", ("ok", "str", case["expect_digest"]))
            if got != want:
                why.append(case["name"])
            if section == "malformed":
                repaired = fix._apply_repair(case)
                if kind == "digest-parse":
                    ok = _call(mod.parse_digest, repaired)[0] == "ok"
                else:
                    ok = _call(mod.digest_fen, repaired["variant"], repaired["fen"])[0] == "ok"
                if not ok:
                    why.append(case["name"] + "/repair")
    return why


def test_r1_fixture_rows():
    assert _fixture_red(prod) == []


def test_r1_failure_mapping_is_the_contracts():
    assert prod._docs()[0]["failures"]["mapping"] == MAPPING


# -- R2: parity with the T0113 reference --------------------------------------------

FENS = fen_battery.SWEEP[::12]

# en-passant identity: the target counts only when a legal capture exists
EP_ROWS = [
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",  # capturable
    "4k3/8/8/2Pp4/8/8/8/4K3 w - d6 0 1",  # capturable from the other side
    "4k3/8/8/3p4/8/8/8/4K3 w - d6 0 1",  # no adjacent pawn
    "4k3/8/8/K2pP2r/8/8/8/8 w - d6 0 1",  # rank pin: both pawns leave the rank
    "4k3/8/8/3pP3/8/8/8/K3r3 w - d6 0 1",  # file pin: the mover leaves the e-file
    "7b/8/8/3pP3/8/8/8/K3k3 w - d6 0 1",  # diagonal pin through e5
    "4k3/8/8/3pP3/8/8/8/K5b1 w - d6 0 1",  # capture leaves the bishop unblocked?
    "4k3/8/8/8/3Pp3/8/8/4K3 b - d3 0 1",  # black capturable
    "4k3/8/8/8/3Pp3/8/8/4K2R b - d3 0 1",  # black, unrelated rook
    "8/8/8/8/k2Pp2R/8/8/4K3 b - d3 0 1",  # black rank pin
    "4k3/8/8/3pP3/8/8/8/2n1K3 w - d6 0 1",  # own king already in check by a knight
    "4k3/8/8/3pPp2/4K3/8/8/8 w - d6 0 1",  # king in check by f5? (black pawn attacks e4)
]


def test_r2_digest_parity_over_the_fen_sweep():
    assert len(FENS) > 4000
    for fen in FENS + EP_ROWS:
        for variant in VARIANTS + (["chess960-unknown"] if len(fen) % 4 == 0 else []):
            a = _call(prod.digest_fen, variant, fen)
            assert a == _call(ref.digest_fen, variant, fen), (variant, fen)


def test_r2_sweep_reaches_every_outcome():
    outs = {_call(prod.digest_fen, "standard", fen)[0:2][-1] for fen in FENS}
    outs.add(_call(prod.digest_fen, "nope", GOOD_FEN)[1])
    assert {"str", "malformed_position", "unknown_variant"} <= outs


def test_r2_ep_identity_rows_match_the_reference_and_split_both_ways():
    same = 0
    for fen in EP_ROWS:
        cleared = fen.replace(" d6 ", " - ").replace(" d3 ", " - ")
        a, b = prod.digest_fen("standard", fen), prod.digest_fen("standard", cleared)
        assert (a == b) == (ref.digest_fen("standard", fen) == ref.digest_fen("standard", cleared))
        same += a == b
    assert 0 < same < len(EP_ROWS)


def _digest_texts():
    base = GOOD_DIGEST
    yield base
    for i in range(len(base)):
        yield base[:i] + base[i + 1 :]
        for ch in "0af9AFgz: \n\t-_":
            if ch != base[i]:
                yield base[:i] + ch + base[i + 1 :]
    for extra in ("0", " ", "\n", "\r\n", "\x00"):
        yield base + extra
        yield extra + base
    yield ""
    yield "pdv1:"
    yield "pdv1:" + "0" * 63
    yield "pdv1:" + "0" * 65
    yield "PDV1:" + base[5:]


DIGEST_TEXTS = list(dict.fromkeys(_digest_texts()))


def test_r2_digest_text_parity():
    for text in DIGEST_TEXTS:
        assert _call(prod.parse_digest, text) == _call(ref.parse_digest, text), repr(text)
        assert _call(prod.emit_digest, text) == _call(ref.emit_digest, text), repr(text)
    accepted = [t for t in DIGEST_TEXTS if _call(prod.parse_digest, t)[0] == "ok"]
    assert accepted and all(len(t) == 69 for t in accepted)


# -- R3: the T0115 red tests bound to the runtime --------------------------------------

RED_TESTS = sorted(n for n in dir(red) if n.startswith("test_"))


@pytest.mark.parametrize("name", RED_TESTS)
def test_r3_t0115_red_tests_pass_bound_to_the_runtime(name, monkeypatch):
    monkeypatch.setattr(red, "digest_fen", prod.digest_fen)
    monkeypatch.setattr(red, "parse_digest", prod.parse_digest)
    monkeypatch.setattr(red, "DigestError", prod.DigestError)
    fn = getattr(red, name)
    marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
    if marks:
        for value in marks[0].args[1]:
            fn(value)
    else:
        fn()


def test_r3_binding_is_live():
    assert len(RED_TESTS) >= 4
    assert red.digest_fen is ref.digest_fen and red.parse_digest is ref.parse_digest


# -- R4: hostile inputs fail closed ------------------------------------------------------


class _Str(str):
    pass


FORGED = prod.DigestError("malformed_digest", MAPPING["malformed_digest"])


class _ForgingStr(str):
    """A str subclass equal-hashing to its text whose __eq__ raises a forged
    DigestError: a membership test against the registry would run it."""

    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        raise FORGED


HOSTILE_VARIANTS = {
    "none": None,
    "int": 1,
    "bytes": b"standard",
    "list": ["standard"],
    "tuple": ("standard",),
    "dict": {"standard": 1},
    "str-subclass": _Str("standard"),
    "forging-str-subclass": _ForgingStr("standard"),
    "empty": "",
    "case": "Standard",
    "padded": "standard ",
}

HOSTILE_FENS = {
    "none": None,
    "int": 0,
    "bytes": GOOD_FEN.encode(),
    "list": [GOOD_FEN],
    "str-subclass": _Str(GOOD_FEN),
    "huge-halfmove": "4k3/8/8/8/8/8/8/4K3 w - - " + "9" * 5000 + " 1",
    "huge-fullmove": "4k3/8/8/8/8/8/8/4K3 w - - 0 " + "9" * 5000,
    "tabs": GOOD_FEN.replace(" ", "\t"),
    "garbage": "p" * 100000,
}

HOSTILE_DIGESTS = {
    "none": None,
    "int": 1,
    "bytes": GOOD_DIGEST.encode(),
    "list": [GOOD_DIGEST],
    "str-subclass": _Str(GOOD_DIGEST),
    "forging-str-subclass": _ForgingStr(GOOD_DIGEST),
    "trailing-newline": GOOD_DIGEST + "\n",
}


@pytest.mark.parametrize("name", list(HOSTILE_VARIANTS))
def test_r4_hostile_variant_fails_closed(name):
    assert _typed(prod, "digest_fen", (HOSTILE_VARIANTS[name], GOOD_FEN), "unknown_variant")


@pytest.mark.parametrize("name", list(HOSTILE_FENS))
def test_r4_hostile_fen_fails_closed(name):
    assert _typed(prod, "digest_fen", ("standard", HOSTILE_FENS[name]), "malformed_position")


@pytest.mark.parametrize("name", list(HOSTILE_DIGESTS))
def test_r4_hostile_digest_text_fails_closed(name):
    for fn in ("parse_digest", "emit_digest"):
        assert _typed(prod, fn, (HOSTILE_DIGESTS[name],), "malformed_digest")


# Coordinator standing rule: the three hostile str forms at every string
# input, each pinning the typed class, an empty hostile-call log and the
# input unchanged.
STR_BOUNDARIES = {
    "variant": ("digest_fen", lambda h: (h, GOOD_FEN), "standard", "unknown_variant"),
    "fen": ("digest_fen", lambda h: ("standard", h), GOOD_FEN, "malformed_position"),
    "digest-text": ("parse_digest", lambda h: (h,), GOOD_DIGEST, "malformed_digest"),
    "emit-digest-text": ("emit_digest", lambda h: (h,), GOOD_DIGEST, "malformed_digest"),
}


@pytest.mark.parametrize("form", fen_battery.HOSTILE_FORMS)
@pytest.mark.parametrize("boundary", list(STR_BOUNDARIES))
def test_r4_hostile_str_forms_at_every_string_input(boundary, form):
    fn, build, text, failure_class = STR_BOUNDARIES[boundary]
    hostile = fen_battery.hostile_str(form, text, collide_with="standard")
    kind = type(hostile)
    fen_battery.HOSTILE_CALLS.clear()
    assert _typed(prod, fn, build(hostile), failure_class)
    assert fen_battery.HOSTILE_CALLS == []
    assert type(hostile) is kind and str.__eq__(hostile, text)


def test_r4_variant_checked_before_the_fen():
    assert _typed(prod, "digest_fen", (None, None), "unknown_variant")
    assert _typed(prod, "digest_fen", (_ForgingStr("standard"), None), "unknown_variant")


# -- R5: exact outputs, no shared state ----------------------------------------------------


def test_r5_outputs_exact_and_docs_unshared():
    before = prod._parsed_docs()
    snapshot = repr(before)
    out = prod.digest_fen("standard", GOOD_FEN)
    assert type(out) is str
    parsed = prod.parse_digest(GOOD_DIGEST)
    assert type(parsed) is str and parsed == GOOD_DIGEST
    docs = prod._docs()
    docs[0]["failures"]["mapping"]["malformed_digest"] = "tampered"
    for payload in list(HOSTILE_VARIANTS.values()) + list(HOSTILE_DIGESTS.values()):
        _typed(prod, "digest_fen", (payload, GOOD_FEN), "unknown_variant")
        _typed(prod, "parse_digest", (payload,), "malformed_digest")
    assert repr(prod._parsed_docs()) == snapshot
    assert prod.digest_fen("standard", GOOD_FEN) == out


# -- R6: the forge set -----------------------------------------------------------------------


def test_r6_raise_sites_and_except_clauses():
    raised, caught = set(), set()
    for node in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            raised.add(ast.unparse(exc))
        elif isinstance(node, ast.ExceptHandler):
            caught.add(ast.unparse(node.type) if node.type is not None else "bare")
    assert raised == {"DigestError"} and caught == {"FenError"}


def test_r6_forged_fen_error_from_the_linked_parser_is_fresh(monkeypatch):
    forged = prod.FenError("malformed_fen", "malformed_request")

    def forging_parse(_contract, _text):
        raise forged

    monkeypatch.setattr(prod, "parse_fen", forging_parse)
    with pytest.raises(prod.DigestError) as err:
        prod.digest_fen("standard", GOOD_FEN)
    assert err.value is not forged and err.value.__cause__ is None
    assert err.value.failure_class == "malformed_position"


def test_r6_forged_digest_error_from_a_hostile_key_is_fresh():
    for fn, args in (
        (prod.digest_fen, (_ForgingStr("standard"), GOOD_FEN)),
        (prod.parse_digest, (_ForgingStr(GOOD_DIGEST),)),
    ):
        with pytest.raises(prod.DigestError) as err:
            fn(*args)
        assert err.value is not FORGED and err.value.__cause__ is None


# -- R7: one-line source mutants are killed ------------------------------------------------

MUTANTS = {
    "ep-none-short-circuit-to-target": (
        "    if ep is None:\n        return sentinel\n",
        "    if ep is None:\n        return ep\n",
    ),
    "ep-side-inverted": (
        'side = mover["white" if white_to_move else "black"]',
        'side = mover["black" if white_to_move else "white"]',
    ),
    "ep-one-neighbour-only": (
        "    for df in (-1, 1):\n        f = tf + df",
        "    for df in (-1,):\n        f = tf + df",
    ),
    "ep-adjacent-pawn-off": (
        "        if board.get((f, mover_rank)) != own_pawn:\n            continue\n",
        "",
    ),
    "ep-own-pawn-wrong-colour": (
        'own_pawn = "P" if white_to_move else "p"',
        'own_pawn = "p" if white_to_move else "P"',
    ),
    "ep-captured-kept": ("        del after[(tf, captured_rank)]\n", ""),
    "ep-mover-kept": ("        del after[(f, mover_rank)]\n", ""),
    "ep-pin-check-off": (
        'if not _attack(fen_contract["board"], king_sq, after, not white_to_move):',
        "if True:",
    ),
    "ep-attacker-side-inverted": (
        "king_sq, after, not white_to_move)",
        "king_sq, after, white_to_move)",
    ),
    "ep-checked-on-original-board": (
        "king_sq, after, not white_to_move)",
        "king_sq, board, not white_to_move)",
    ),
    "ep-always-sentinel": (
        "            return ep\n    return sentinel",
        "            return sentinel\n    return sentinel",
    ),
    "encode-castling-sentinel-dropped": (
        '"castling_rights": rights or fen_contract["castling"]["none_sentinel"],',
        '"castling_rights": rights,',
    ),
    "encode-side-dropped": ('"side_to_move": color,', '"side_to_move": "w",'),
    "encode-raw-ep-target": (
        '"en_passant": _ep_identity(digest_contract, ep_contract, fen_contract, position),',
        '"en_passant": ep or "-",',
    ),
    "encode-order-reversed": (
        'return " ".join(components[field] for field in order)',
        'return " ".join(components[field] for field in reversed(order))',
    ),
    "encode-separator": (
        'return " ".join(components[field] for field in order)',
        'return "".join(components[field] for field in order)',
    ),
    "digest-no-prefix": ('return d["format"]["prefix"] + text', "return text"),
    "digest-uppercase": ("    text = raw.hex()\n", "    text = raw.hex().upper()\n"),
    "variant-type-check-off": (
        "if type(variant_id) is not str or variant_id not in ids:",
        "if variant_id not in ids:",
    ),
    "variant-isinstance": (
        "if type(variant_id) is not str or variant_id not in ids:",
        "if not isinstance(variant_id, str) or variant_id not in ids:",
    ),
    "variant-registry-off": (
        "if type(variant_id) is not str or variant_id not in ids:",
        "if type(variant_id) is not str:",
    ),
    "variant-wrong-class": (
        '_fail(dc, "unknown_variant")  # exact',
        '_fail(dc, "malformed_position")  # exact',
    ),
    "position-wrong-class": (
        '        _fail(dc, "malformed_position")\n    return digest',
        '        _fail(dc, "unknown_variant")\n    return digest',
    ),
    "digest-text-type-check-off": ("if type(text) is not str or re.fullmatch(", "if re.fullmatch("),
    "digest-text-isinstance": (
        "if type(text) is not str or re.fullmatch(",
        "if not isinstance(text, str) or re.fullmatch(",
    ),
    "digest-text-match-not-fullmatch": (
        're.fullmatch(dc["digest"]["format"]["regex"], text)',
        're.match(dc["digest"]["format"]["regex"], text)',
    ),
    "digest-text-search": (
        're.fullmatch(dc["digest"]["format"]["regex"], text)',
        're.search(dc["digest"]["format"]["regex"].strip("^$"), text)',
    ),
    "emit-accepts-anything": (
        "def emit_digest(text):\n    return parse_digest(text)",
        "def emit_digest(text):\n    return text",
    ),
}
EQUIVALENT_EDITS = {
    # an off-board neighbour file is never a board key, so the pawn check
    # skips it with the same result
    "ep-file-bounds-off": ("        if not 0 <= f < len(files):\n            continue\n", ""),
    # the registry holds one variant; revisit when a second variant lands
    "encode-variant-dropped": ('"variant": variant_id,', '"variant": "standard",'),
}


def _mutant(name):
    before, after = {**MUTANTS, **EQUIVALENT_EDITS}[name]
    source = PRODUCTION.read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType(f"pd_mutant_{name.replace('-', '_')}")
    module.__file__ = str(PRODUCTION)
    exec(compile(source.replace(before, after), str(PRODUCTION), "exec"), module.__dict__)
    module.DigestError = prod.DigestError
    return module


def _red(mod):
    try:
        if _fixture_red(mod):
            return True
        for payload in HOSTILE_VARIANTS.values():
            if not _typed(mod, "digest_fen", (payload, GOOD_FEN), "unknown_variant"):
                return True
        for payload in HOSTILE_FENS.values():
            if not _typed(mod, "digest_fen", ("standard", payload), "malformed_position"):
                return True
        for payload in HOSTILE_DIGESTS.values():
            for fn in ("parse_digest", "emit_digest"):
                if not _typed(mod, fn, (payload,), "malformed_digest"):
                    return True
        for text in DIGEST_TEXTS:
            if _call(mod.parse_digest, text) != _call(ref.parse_digest, text):
                return True
        for fen in EP_ROWS + FENS[::25]:
            if _call(mod.digest_fen, "standard", fen) != _call(ref.digest_fen, "standard", fen):
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
