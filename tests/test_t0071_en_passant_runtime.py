"""T0071: en-passant runtime (graph.en_passant) battery.

R1 the T0069 fixture rows run against the shipped runtime; R2 parity with
the fixture's reference interpreter over the fixture rows and a generated
sweep; R3 the T0070 red tests bound to the runtime; R4 inputs never
mutated and outputs never alias them; R5 hostile inputs fail closed at
every level through every entry point; R6 forged errors never pass
through; R7 one-line source mutants of the runtime are each killed.
"""

from __future__ import annotations

import ast
import copy
import itertools
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph.en_passant as prod  # noqa: E402
from tests import test_t0069_en_passant_fixture as ref  # noqa: E402
from tests import test_t0070_en_passant_red as red  # noqa: E402

PRODUCTION = ROOT / "graph" / "en_passant.py"
CASES = ref.CASES
SECTIONS = ("happy", "boundary", "malformed", "rollback")


def _outcome(fn):
    try:
        return ("ok", fn())
    except prod.EnPassantError as exc:
        return ("err", exc.failure_class, exc.code)
    except ref.EnPassantFailure as exc:
        return ("err", exc.failure_class, exc.error)


# -- R1: fixture rows against the runtime ----------------------------------------


def _fixture_red(mod):
    """Failing reasons when MOD disagrees with any fixture row."""
    why = []
    for section in SECTIONS:
        for case in CASES[section]:
            name = case["name"]
            if case["kind"] == "identity":
                if (
                    mod.identity_value(copy.deepcopy(case["state"]))
                    != case["expect_identity_value"]
                ):
                    why.append(f"identity {name}")
                continue
            state, move = copy.deepcopy(case["state"]), copy.deepcopy(case["move"])
            out = _outcome(lambda s=state, m=move: mod.apply(s, m))
            if section in ("malformed", "rollback"):
                cls = case["expect_failure"]
                if out != ("err", cls, prod.FAILURE_MAPPING[cls]):
                    why.append(f"{section} {name}: {out[:2]}")
                if state != case["state"]:
                    why.append(f"{section} {name}: input mutated")
                if section == "rollback" and state != case["expect_state_after"]:
                    why.append(f"rollback {name}: state after")
                continue
            side = case["state"]["side_to_move"]
            if out[0] != "ok" or out[1] != {
                "ep_target": case["expect_target"],
                "occupied": case["expect_occupied"],
                "side_to_move": ref.OTHER[side],
            }:
                why.append(f"{section} {name}: {out[:2]}")
            if mod.turn_transition(case["move"]["type"], side) != case["expect_turn"]:
                why.append(f"turn {name}")
    return why


def test_r1_fixture_rows():
    assert _fixture_red(prod) == []


def test_r1_failure_mapping_is_the_contracts():
    assert {c: e["error"] for c, e in ref.FAILURE_MAPPING.items()} == prod.FAILURE_MAPPING
    assert set(prod.FAILURE_MAPPING.values()) <= set(ref.ERROR_ENUM)


# -- R2: parity with the fixture reference ------------------------------------

TARGETS = [ref.NONE] + [f + r for f in "abcdefgh" for r in ("3", "6")] + ["e4", "E6", "", "e66"]


def _extra_states():
    """States the fixture lacks: blocked pin lines (a piece between the
    pinning slider and the king), non-pawns on the pawn start ranks, and
    pawns on both start ranks."""
    pinned = next(c for c in CASES["malformed"] if c["name"] == "pinned-capture-horizontal")[
        "state"
    ]
    for square, token in (("b5", "wn"), ("c5", "bn"), ("f5", "wb"), ("g5", "bq")):
        yield dict(copy.deepcopy(pinned), occupied=dict(pinned["occupied"], **{square: token}))
    yield {
        "ep_target": "-",
        "side_to_move": "w",
        "occupied": {
            "a2": "wr",
            "b2": "wp",
            "c2": "wn",
            "e1": "wk",
            "e8": "bk",
            "a7": "bp",
            "b7": "bn",
        },
    }
    yield {
        "ep_target": "-",
        "side_to_move": "b",
        "occupied": {"a2": "wp", "b2": "wb", "e1": "wk", "e8": "bk", "g7": "bp", "h7": "br"},
    }


EXTRA = [{"state": st, "move": {"to": "a1"}} for st in _extra_states()]


def _sweep():
    """Every fixture state, with its target replaced by each declared and a
    few non-canonical values, under every move type from every occupied
    square to every square the fixture uses plus the eight neighbours of
    the from-square."""
    for case in [c for s in SECTIONS for c in CASES[s]] + EXTRA:
        if True:
            base = case["state"]
            squares = sorted(set(base["occupied"]) | {case.get("move", {}).get("to", "a1")})
            for target in TARGETS:
                state = dict(copy.deepcopy(base), ep_target=target)
                for frm in sorted(base["occupied"]):
                    f, r = ord(frm[0]), int(frm[1])
                    near = {
                        chr(f + df) + str(r + dr)
                        for df in (-1, 0, 1)
                        for dr in (-2, -1, 1, 2)
                        if 97 <= f + df <= 104 and 1 <= r + dr <= 8
                    }
                    for to in sorted(set(squares) | near | {target} & ref.ALL_SQUARES):
                        for kind in sorted(ref.MOVE_TYPES):
                            yield state, {"type": kind, "from": frm, "to": to}


SWEEP = list(_sweep())


def test_r2_sweep_is_large_and_mixed():
    outs = {
        (_outcome(lambda s=s, m=m: prod.apply(copy.deepcopy(s), m)))[0:2][0]
        for s, m in SWEEP[:5000]
    }
    assert len(SWEEP) > 20000 and outs == {"ok", "err"}


@pytest.mark.parametrize("chunk", range(8))
def test_r2_parity_with_the_reference(chunk):
    for state, move in SWEEP[chunk::8]:
        a = _outcome(lambda s=state, m=move: prod.apply(copy.deepcopy(s), m))
        b = _outcome(lambda s=state, m=move: ref._apply(copy.deepcopy(s), m))
        assert a == b, (state, move)


@pytest.mark.parametrize("chunk", range(2))
def test_r2_identity_parity_with_the_reference(chunk):
    seen = set()
    for state, _ in SWEEP[chunk::2]:
        key = repr(state)
        if key in seen:
            continue
        seen.add(key)
        assert prod.identity_value(copy.deepcopy(state)) == ref._identity_value(
            copy.deepcopy(state)
        ), state


def test_r2_turn_transition_parity():
    for kind, side in itertools.product(sorted(ref.MOVE_TYPES), ("w", "b")):
        assert prod.turn_transition(kind, side) == ref._expected_turn(kind, side)


# Pawn attack direction: the T0069 direction pins (geometry and ep-capture
# rows), run against the runtime.


def test_r2_pawn_attack_direction_rows():
    for state, move, failure, ident in ref.PAWN_ATTACK_ROWS:
        got = _outcome(lambda s=state, m=move: prod.apply(copy.deepcopy(s), m))
        assert got[0] == "ok" if failure is None else got[1] == failure, (state, got)
        assert prod.identity_value(copy.deepcopy(state)) == ident, state


def test_r2_pawn_attack_geometry():
    for pawn, at, square, hit in ref.PAWN_ATTACK_GEOMETRY:
        assert prod._attacked({at: pawn}, square, pawn[0]) is hit, (pawn, at, square)


# Every attack geometry _attacked handles, judged on the position after an
# en-passant capture. White: e5xd6 (black pawn d5). Black: d4xe3 (white
# pawn e4). Each row pins its own expected class; None means accepted.
_W = ("d6", "w", {"e5": "wp", "d5": "bp"}, {"type": "ep-capture", "from": "e5", "to": "d6"})
_B = ("e3", "b", {"d4": "bp", "e4": "wp"}, {"type": "ep-capture", "from": "d4", "to": "e3"})
PIN = "pinned_capture"
ATTACK_ROWS = {
    # pawn, both colours, both diagonals; wrong-direction pawns do not attack
    "w-pawn-check-right": (_W, {"e4": "wk", "f5": "bp", "h8": "bk"}, PIN),
    "w-pawn-check-left": (_W, {"c4": "wk", "b5": "bp", "h8": "bk"}, PIN),
    "w-pawn-behind-right": (_W, {"e4": "wk", "f3": "bp", "h8": "bk"}, None),
    "w-pawn-behind-left": (_W, {"c4": "wk", "b3": "bp", "h8": "bk"}, None),
    "b-pawn-check-right": (_B, {"e5": "bk", "f4": "wp", "a1": "wk"}, PIN),
    "b-pawn-check-left": (_B, {"c5": "bk", "b4": "wp", "a1": "wk"}, PIN),
    "b-pawn-behind-right": (_B, {"e5": "bk", "f6": "wp", "a1": "wk"}, None),
    "b-pawn-behind-left": (_B, {"c5": "bk", "b6": "wp", "a1": "wk"}, None),
    # knight
    "w-knight-check": (_W, {"a1": "wk", "b3": "bn", "h8": "bk"}, PIN),
    "w-knight-check-wide": (_W, {"a1": "wk", "c2": "bn", "h8": "bk"}, PIN),
    "w-knight-far": (_W, {"a1": "wk", "c4": "bn", "h8": "bk"}, None),
    "b-knight-check": (_B, {"a8": "bk", "b6": "wn", "h1": "wk"}, PIN),
    # adjacent kings
    "w-adjacent-king-diagonal": (_W, {"a1": "wk", "b2": "bk"}, PIN),
    "w-adjacent-king-file": (_W, {"a1": "wk", "a2": "bk"}, PIN),
    "w-king-two-away": (_W, {"a1": "wk", "a3": "bk"}, None),
    # diagonal pins opened by the mover leaving e5
    "w-bishop-diagonal-pin": (_W, {"b2": "wk", "f6": "bb", "h8": "bk"}, PIN),
    "w-queen-diagonal-pin": (_W, {"b2": "wk", "f6": "bq", "h8": "bk"}, PIN),
    "w-bishop-diagonal-blocked": (_W, {"b2": "wk", "f6": "bb", "c3": "wn", "h8": "bk"}, None),
    "w-rook-on-diagonal": (_W, {"b2": "wk", "f6": "br", "h8": "bk"}, None),
    # orthogonal pins: the rank opened by both pawns leaving, and a file
    "w-rook-rank-pin": (_W, {"a5": "wk", "h5": "br", "h8": "bk"}, PIN),
    "w-queen-rank-pin": (_W, {"a5": "wk", "h5": "bq", "h8": "bk"}, PIN),
    "w-rook-rank-blocked": (_W, {"a5": "wk", "h5": "br", "g5": "wn", "h8": "bk"}, None),
    "w-bishop-on-rank": (_W, {"a5": "wk", "h5": "bb", "h8": "bk"}, None),
    "w-rook-file-pin": (_W, {"e1": "wk", "e8": "br", "h8": "bk"}, PIN),
    "w-rook-file-blocked": (_W, {"e1": "wk", "e8": "br", "e3": "wn", "h8": "bk"}, None),
    "b-rook-rank-pin": (_B, {"h4": "bk", "a4": "wr", "a1": "wk"}, PIN),
    "b-bishop-diagonal-pin": (_B, {"g6": "bk", "c2": "wb", "a1": "wk"}, PIN),
    # own pieces never count as attackers, in every geometry
    "w-own-pawn": (_W, {"e4": "wk", "f3": "wp", "h8": "bk"}, None),
    "w-own-knight": (_W, {"a1": "wk", "b3": "wn", "h8": "bk"}, None),
    "w-own-bishop-diagonal": (_W, {"b2": "wk", "f6": "wb", "h8": "bk"}, None),
    "w-own-rook-rank": (_W, {"a5": "wk", "h5": "wr", "h8": "bk"}, None),
    "w-own-queen-file": (_W, {"e1": "wk", "e8": "wq", "h8": "bk"}, None),
    "b-own-knight": (_B, {"a8": "bk", "b6": "bn", "h1": "wk"}, None),
    "b-own-rook-rank": (_B, {"h4": "bk", "a4": "br", "a1": "wk"}, None),
}


def _attack_case(name):
    (target, side, pawns, move), extra, want = ATTACK_ROWS[name]
    state = {"ep_target": target, "side_to_move": side, "occupied": {**pawns, **extra}}
    return state, move, want


@pytest.mark.parametrize("name", list(ATTACK_ROWS))
def test_r2_attack_geometry_rows(name):
    state, move, want = _attack_case(name)
    got = _outcome(lambda: prod.apply(copy.deepcopy(state), move))
    assert got == _outcome(lambda: ref._apply(copy.deepcopy(state), move))
    assert got[0] == "ok" if want is None else got[1] == want, got
    assert prod.identity_value(copy.deepcopy(state)) == ref._identity_value(copy.deepcopy(state))


def _attack_rows_red(mod):
    for name in ATTACK_ROWS:
        state, move, want = _attack_case(name)
        got = _outcome(lambda s=state, m=move: mod.apply(copy.deepcopy(s), m))
        if got[0] != ("ok" if want is None else "err") or (want and got[1] != want):
            return True
    return False


REF_SOURCE = ROOT / "tests" / "test_t0069_en_passant_fixture.py"
REF_PAWN_MUTANT = (
    'pawn_dr = -1 if by_side == "w" else 1',
    'pawn_dr = 1 if by_side == "w" else -1',
)


def test_r2_reference_pawn_direction_mutant_is_killed():
    """Re-inverting the sign in the shared reference turns its own
    direction pins red, so parity can never again lean on an inverted oracle."""
    before, after = REF_PAWN_MUTANT
    source = REF_SOURCE.read_text()
    assert source.count(before) == 1
    module = types.ModuleType("t0069_reference_pawn_mutant")
    module.__file__ = str(REF_SOURCE)
    exec(compile(source.replace(before, after), str(REF_SOURCE), "exec"), module.__dict__)
    for pin in (module.test_pawn_attack_direction, module.test_pawn_attack_direction_rows):
        with pytest.raises(AssertionError):
            pin()
    ref.test_pawn_attack_direction()
    ref.test_pawn_attack_direction_rows()


# Trust boundary (coordinator ruling): non-capture moves are trusted input
# from the legal-moves layer. These rows pin CURRENT behaviour, shared with
# the reference, so any change to it is deliberate.
_TRUST_BASE = {"e1": "wk", "e8": "bk", "d4": "bp", "a2": "wp"}
TRUSTED_NON_CAPTURE_ROWS = {
    # (a) a malformed stored target is cleared by a non-capture move
    "malformed-target-cleared": (
        {"ep_target": "zz", "side_to_move": "w", "occupied": _TRUST_BASE},
        {"type": "quiet", "from": "a2", "to": "a3"},
    ),
    "empty-target-cleared": (
        {"ep_target": "", "side_to_move": "w", "occupied": _TRUST_BASE},
        {"type": "quiet", "from": "a2", "to": "a3"},
    ),
    # (b) the side to move can move an opponent piece
    "opponent-piece-moved": (
        {"ep_target": "-", "side_to_move": "w", "occupied": _TRUST_BASE},
        {"type": "quiet", "from": "d4", "to": "d3"},
    ),
    # (c) from == to
    "from-equals-to": (
        {"ep_target": "-", "side_to_move": "w", "occupied": _TRUST_BASE},
        {"type": "pawn-advance", "from": "e1", "to": "e1"},
    ),
    # (d) a move onto the enemy king; the output fails its own validation
    "king-captured": (
        {"ep_target": "-", "side_to_move": "w", "occupied": _TRUST_BASE},
        {"type": "quiet", "from": "e1", "to": "e8"},
    ),
}


@pytest.mark.parametrize("name", list(TRUSTED_NON_CAPTURE_ROWS))
def test_r2_trusted_non_capture_moves_current_behaviour(name):
    state, move = TRUSTED_NON_CAPTURE_ROWS[name]
    out = prod.apply(copy.deepcopy(state), move)
    assert out == ref._apply(copy.deepcopy(state), move)
    assert out["ep_target"] == prod.NONE and out["side_to_move"] == "b"
    moved = {sq: t for sq, t in state["occupied"].items() if sq != move["from"]}
    moved[move["to"]] = state["occupied"][move["from"]]
    assert out["occupied"] == moved
    if name == "king-captured":
        assert "bk" not in out["occupied"].values()
        assert _typed_malformed(
            lambda: prod.apply(out, {"type": "quiet", "from": "e8", "to": "e7"})
        )


# Occupied target square (coordinator ruling on the T0072 finding): the
# target is the square the pawn passed over, so an occupied target is
# target_inconsistent - (a) another piece on it is never deleted, (b) the
# mover's own king on it never escapes as a raw StopIteration.
def _occupied_target_red(mod):
    for state, move, ident in ref.OCCUPIED_TARGET_ROWS:
        pre = copy.deepcopy(state)
        try:
            mod.apply(state, move)
        except prod.EnPassantError as exc:
            if exc.failure_class != "target_inconsistent" or exc.__cause__ is not None:
                return True
        else:
            return True
        if state != pre or mod.identity_value(copy.deepcopy(state)) != ident:
            return True
    return False


@pytest.mark.parametrize("index", range(len(ref.OCCUPIED_TARGET_ROWS)))
def test_r2_occupied_target_is_inconsistent(index):
    state, move, ident = ref.OCCUPIED_TARGET_ROWS[index]
    pre = copy.deepcopy(state)
    with pytest.raises(prod.EnPassantError) as err:
        prod.apply(state, move)
    assert err.value.failure_class == "target_inconsistent"
    assert err.value.code == prod.FAILURE_MAPPING["target_inconsistent"]
    assert err.value.__cause__ is None
    assert state == pre
    assert prod.identity_value(copy.deepcopy(state)) == ident
    assert _outcome(lambda: prod.apply(copy.deepcopy(state), move)) == _outcome(
        lambda: ref._apply(copy.deepcopy(state), move)
    )


# -- R3: the T0070 red tests bound to the runtime -----------------------------


def _bound_apply(state, move):
    try:
        return prod.apply(state, move)
    except prod.EnPassantError as exc:
        raise ref.EnPassantFailure(exc.failure_class) from None


RED_TESTS = sorted(n for n in dir(red) if n.startswith("test_"))


@pytest.mark.parametrize("name", RED_TESTS)
def test_r3_t0070_red_tests_pass_bound_to_the_runtime(name, monkeypatch):
    monkeypatch.setattr(red, "_apply", _bound_apply)
    getattr(red, name)()


def test_r3_binding_is_live():
    assert len(RED_TESTS) >= 4
    import inspect

    assert all("_apply(" in inspect.getsource(getattr(red, n)) for n in RED_TESTS)


# -- R4: no input mutation, no aliasing -----------------------------------------


def test_r4_inputs_unchanged_and_outputs_detached():
    for state, move in SWEEP[::7]:
        s, m = copy.deepcopy(state), copy.deepcopy(move)
        ids = (id(s["occupied"]), list(s), list(s["occupied"]))
        out = _outcome(lambda s=s, m=m: prod.apply(s, m))
        assert s == state and m == move
        assert ids == (id(s["occupied"]), list(s), list(s["occupied"]))
        if out[0] == "ok":
            assert out[1] is not s and out[1]["occupied"] is not s["occupied"]
            out[1]["occupied"]["a1"] = "wq"
            assert s == state


# -- R5: hostile inputs fail closed ---------------------------------------------

CALLS = []


class _Str(str):
    def __eq__(self, other):
        CALLS.append("eq")
        return str.__eq__(self, other)

    def __hash__(self):
        CALLS.append("hash")
        return str.__hash__(self)


class _Colliding:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        CALLS.append("colliding-eq")
        raise RuntimeError("hostile key compare")


class _Dict(dict):
    def __getitem__(self, key):
        CALLS.append("getitem")
        return dict.__getitem__(self, key)

    def keys(self):
        CALLS.append("keys")
        return dict.keys(self)

    def __iter__(self):
        CALLS.append("iter")
        return dict.__iter__(self)

    def items(self):
        CALLS.append("items")
        return dict.items(self)


def _good():
    case = next(c for c in CASES["happy"] if c["name"] == "capture-white")
    return copy.deepcopy(case["state"]), copy.deepcopy(case["move"])


def _rekey(d, key, new):
    out = {}
    for k, v in d.items():
        out[new if k == key else k] = v
    return out


def _hostile_states():
    state, _ = _good()
    sq = next(iter(state["occupied"]))
    yield "non-dict", [state]
    yield "dict-subclass", _Dict(state)
    yield "occupied-subclass", dict(state, occupied=_Dict(state["occupied"]))
    yield "occupied-list", dict(state, occupied=list(state["occupied"].items()))
    for key in sorted(state):
        yield f"strsub-key-{key}", _rekey(state, key, _Str(key))
        yield f"colliding-key-{key}", _rekey(state, key, _Colliding(key))
        yield f"renamed-key-{key}", _rekey(state, key, key + "_")
        yield (
            f"strsub-value-{key}",
            dict(state, **{key: _Str(state[key])})
            if key != "occupied"
            else dict(state, occupied=_Str("x")),
        )
    yield "extra-key", dict(state, extra="x")
    yield "missing-key", {k: v for k, v in state.items() if k != "side_to_move"}
    yield "occupied-strsub-key", dict(state, occupied=_rekey(state["occupied"], sq, _Str(sq)))
    yield (
        "occupied-colliding-key",
        dict(state, occupied=_rekey(state["occupied"], sq, _Colliding(sq))),
    )
    yield (
        "occupied-strsub-token",
        dict(state, occupied=dict(state["occupied"], **{sq: _Str(state["occupied"][sq])})),
    )
    yield "occupied-bad-square", dict(state, occupied=dict(state["occupied"], i9="wp"))
    yield "occupied-bad-token", dict(state, occupied=dict(state["occupied"], a1="wx"))
    yield "occupied-int-token", dict(state, occupied=dict(state["occupied"], a1=7))
    yield "two-white-kings", dict(state, occupied=dict(state["occupied"], a1="wk"))
    yield (
        "no-black-king",
        dict(state, occupied={k: v for k, v in state["occupied"].items() if v != "bk"}),
    )
    yield "bad-side", dict(state, side_to_move="x")
    yield "int-target", dict(state, ep_target=6)


def _hostile_moves():
    _, move = _good()
    yield "non-dict", [move]
    yield "dict-subclass", _Dict(move)
    for key in sorted(move):
        yield f"strsub-key-{key}", _rekey(move, key, _Str(key))
        yield f"colliding-key-{key}", _rekey(move, key, _Colliding(key))
        yield f"renamed-key-{key}", _rekey(move, key, key + "_")
        yield f"strsub-value-{key}", dict(move, **{key: _Str(move[key])})
    yield "extra-key", dict(move, promo="q")
    yield "bad-type", dict(move, type="castle")
    yield "bad-square", dict(move, to="e9")
    yield "none-from", dict(move, **{"from": None})
    yield "bad-from-square", dict(move, **{"from": "i5"})


HOSTILE_STATES = dict(_hostile_states())
HOSTILE_MOVES = dict(_hostile_moves())


def _typed_malformed(call):
    CALLS.clear()
    try:
        call()
    except prod.EnPassantError as exc:
        return exc.failure_class == "target_malformed" and exc.__cause__ is None and CALLS == []
    return False


@pytest.mark.parametrize("name", list(HOSTILE_STATES))
def test_r5_hostile_state_fails_closed(name):
    _, move = _good()
    state = HOSTILE_STATES[name]
    assert _typed_malformed(lambda: prod.apply(state, move))
    assert _typed_malformed(lambda: prod.identity_value(state))


@pytest.mark.parametrize("name", list(HOSTILE_MOVES))
def test_r5_hostile_move_fails_closed(name):
    state, _ = _good()
    assert _typed_malformed(lambda: prod.apply(state, HOSTILE_MOVES[name]))


def test_r5_no_piece_on_from_square_fails_closed():
    state, _ = _good()
    assert _typed_malformed(lambda: prod.apply(state, {"type": "quiet", "from": "a1", "to": "a2"}))


@pytest.mark.parametrize(
    "args",
    [
        ("castle", "w"),
        ("quiet", "x"),
        (_Str("quiet"), "w"),
        ("quiet", _Str("w")),
        (None, "w"),
        ("quiet", None),
    ],
)
def test_r5_turn_transition_fails_closed(args):
    assert _typed_malformed(lambda: prod.turn_transition(*args))


# -- R6: forged errors never pass through ---------------------------------------


class _Forging(str):
    """A str-subclass key hashing like its text whose __eq__ raises a forged
    EnPassantError: any set build or comparison against a field name would
    run it."""

    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        raise FORGED


FORGED = prod.EnPassantError("pinned_capture")


def test_r6_forged_error_never_passes_through():
    state, move = _good()
    for call in (
        lambda: prod.apply(_rekey(state, "ep_target", _Forging("ep_target")), move),
        lambda: prod.apply(state, _rekey(move, "to", _Forging("to"))),
        lambda: prod.identity_value(
            dict(state, occupied=_rekey(state["occupied"], "d5", _Forging("d5")))
        ),
    ):
        with pytest.raises(prod.EnPassantError) as err:
            call()
        assert err.value is not FORGED and err.value.failure_class == "target_malformed"
        assert err.value.__cause__ is None


def test_r6_every_raise_site_and_except_clause_is_forged():
    raised, caught = set(), set()
    for node in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            raised.add(ast.unparse(exc))
        elif isinstance(node, ast.ExceptHandler):
            caught.add(ast.unparse(node.type) if node.type is not None else "bare")
    assert raised == {"EnPassantError"} and caught == {"EnPassantError"}
    assert type(FORGED).__name__ in raised


# -- R7: one-line source mutants of the runtime are killed -----------------------

MUTANTS = {
    "exact-dict-isinstance": ("        type(value) is dict\n", "        isinstance(value, dict)\n"),
    "key-type-check-off": ("and all(type(k) is str for k in dict.keys(value))", "and True"),
    "key-set-subset": ("set(dict.keys(value)) == keys", "set(dict.keys(value)) >= keys"),
    "side-check-off": (
        ' or side not in _OTHER:\n        _fail("target_malformed")\n    if type(occupied)',
        ':\n        _fail("target_malformed")\n    if type(occupied)',
    ),
    "occupied-isinstance": ("if type(occupied) is not dict:", "if not isinstance(occupied, dict):"),
    "square-domain-off": ("            or square not in _SQUARES\n", ""),
    "token-domain-off": ("            or token not in _PIECES\n", ""),
    "king-count-off": (
        '!= 1:\n            _fail("target_malformed")',
        '> 1:\n            _fail("target_malformed")',
    ),
    "move-type-domain-off": (
        "if type(kind) is not str or kind not in _MOVE_TYPES:",
        "if type(kind) is not str:",
    ),
    "move-square-domain-off": (
        "if type(frm) is not str or frm not in _SQUARES or",
        "if type(frm) is not str or",
    ),
    "grammar-rank-off": ("target[0] in _FILES and target[1] in _RANKS", "target[0] in _FILES"),
    "none-target-class": (
        '_fail("capture_precondition")  # no target',
        '_fail("target_inconsistent")  # no target',
    ),
    "availability-off": ('if target[1] != _SET_ON[_OTHER[side]]["target_rank"]:', "if False:"),
    "captured-pawn-check-off": ('if occ.get(captured) != _OTHER[side] + "p":', "if False:"),
    "destination-check-off": ("        to != target\n        or frm[1]", "        frm[1]"),
    "mover-rank-off": ('        or frm[1] != mover["mover_rank"]\n', ""),
    "adjacency-off": ("        or abs(ord(frm[0]) - ord(target[0])) != 1\n", ""),
    "mover-piece-off": ('        or occ.get(frm) != side + "p"\n', ""),
    "victim-kept": ("if sq not in (frm, captured)}", "if sq != frm}"),
    "occupied-target-check-off": (
        "    if target in occ:\n        # the target is the square",
        "    if False:\n        # the target is the square",
    ),
    "pin-check-off": ("if _attacked(result, king, _OTHER[side]):", "if False:"),
    "slider-blocking-off": ("if chr(97 + f) + str(r) in occ:", "if False:"),
    "target-kept-after-capture": (
        'return {"ep_target": NONE, "occupied": result, "side_to_move": _OTHER[side]}'
        "\n\n\ndef apply",
        'return {"ep_target": target, "occupied": result, "side_to_move": _OTHER[side]}'
        "\n\n\ndef apply",
    ),
    "target-retained-after-move": (
        "new_target = NONE  # lifetime",
        "new_target = target  # lifetime",
    ),
    "set-on-pawn-check-off": ('        and token[1] == "p"\n', ""),
    "set-on-file-check-off": ("        and frm[0] == to[0]\n", ""),
    "set-on-type-check-off": (
        '        kind == "pawn-advance"\n        and token[1]',
        "        token[1]",
    ),
    "no-piece-check-off": (
        'if token is None:\n        _fail("target_malformed")  # no piece',
        'if False:\n        _fail("target_malformed")  # no piece',
    ),
    "identity-always-target": (
        "            return target\n    return NONE",
        "            return target\n    return target",
    ),
    "identity-first-neighbour-only": (
        "for df in (-1, 1):\n        f = file_index + df",
        "for df in (-1,):\n        f = file_index + df",
    ),
    "turn-halfmove-always-reset": (
        'if pawnish and _TURN["halfmove_clock"] == "reset"',
        'if _TURN["halfmove_clock"] == "reset"',
    ),
    "turn-fullmove-any-side": (
        '== "increment-when-black" and side == "b")',
        '== "increment-when-black")',
    ),
    "pawn-direction-inverted": (
        'if dr == (-1 if by_side == "w" else 1) and abs(df) == 1:',
        'if dr == (1 if by_side == "w" else -1) and abs(df) == 1:',
    ),
    "own-piece-filter-off": ("        if token[0] != by_side:\n            continue\n", ""),
    "knight-attack-off": (
        "            if (abs(df), abs(dr)) in ((1, 2), (2, 1)):",
        "            if False:",
    ),
    "king-attack-off": ("            if max(abs(df), abs(dr)) == 1:", "            if False:"),
    "diagonal-attack-off": (
        'or (piece in "bq" and abs(df) == abs(dr))',
        'or (piece in "b" and abs(df) == abs(dr))',
    ),
    "orthogonal-queen-off": (
        '(piece in "rq" and (df == 0 or dr == 0))',
        '(piece in "r" and (df == 0 or dr == 0))',
    ),
    "bishop-diagonal-off": (
        'or (piece in "bq" and abs(df) == abs(dr))',
        'or (piece in "q" and abs(df) == abs(dr))',
    ),
    "turn-domain-off": ("        or move_type not in _MOVE_TYPES\n", ""),
}


def _mutant(name):
    before, after = MUTANTS[name]
    source = PRODUCTION.read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType(f"en_passant_mutant_{name.replace('-', '_')}")
    module.__file__ = str(PRODUCTION)
    exec(compile(source.replace(before, after), str(PRODUCTION), "exec"), module.__dict__)
    module.EnPassantError = prod.EnPassantError
    return module


def _pawn_rows_red(mod):
    for state, move, failure, ident in ref.PAWN_ATTACK_ROWS:
        got = _outcome(lambda s=state, m=move: mod.apply(copy.deepcopy(s), m))
        if got[0] != ("ok" if failure is None else "err") or (failure and got[1] != failure):
            return True
        if mod.identity_value(copy.deepcopy(state)) != ident:
            return True
    return any(
        mod._attacked({at: pawn}, square, pawn[0]) is not hit
        for pawn, at, square, hit in ref.PAWN_ATTACK_GEOMETRY
    )


def _red(mod):
    """True when MOD fails the fixture rows, the parity sweep, identity or
    turn parity, or the hostile battery."""
    try:
        if (
            _fixture_red(mod)
            or _pawn_rows_red(mod)
            or _attack_rows_red(mod)
            or _occupied_target_red(mod)
        ):
            return True
        for state, move in SWEEP:
            if _outcome(lambda s=state, m=move: mod.apply(copy.deepcopy(s), m)) != _outcome(
                lambda s=state, m=move: ref._apply(copy.deepcopy(s), m)
            ):
                return True
        for state, _ in SWEEP[::97]:
            if mod.identity_value(copy.deepcopy(state)) != ref._identity_value(
                copy.deepcopy(state)
            ):
                return True
        for kind, side in itertools.product(sorted(ref.MOVE_TYPES), ("w", "b")):
            if mod.turn_transition(kind, side) != ref._expected_turn(kind, side):
                return True
        _, move = _good()
        state, _ = _good()
        for hostile in HOSTILE_STATES.values():
            if not _typed_malformed(lambda h=hostile: mod.apply(h, move)):
                return True
        for hostile in HOSTILE_MOVES.values():
            if not _typed_malformed(lambda h=hostile: mod.apply(state, h)):
                return True
        empty_from = {"type": "quiet", "from": "a1", "to": "a2"}
        if not _typed_malformed(lambda: mod.apply(state, empty_from)):
            return True
        for args in (("castle", "w"), ("quiet", "x")):
            if not _typed_malformed(lambda a=args: mod.turn_transition(*a)):
                return True
    except BaseException:  # noqa: BLE001 - a raw escape is a kill
        return True
    return False


@pytest.mark.parametrize("name", list(MUTANTS))
def test_r7_source_mutant_is_killed(name):
    assert _red(_mutant(name))


def test_r7_unmutated_runtime_is_green():
    assert not _red(prod)
