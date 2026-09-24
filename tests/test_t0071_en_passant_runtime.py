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
                if mod.identity_value(copy.deepcopy(case["state"])) != \
                        case["expect_identity_value"]:
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
                    "side_to_move": ref.OTHER[side]}:
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
    pinned = next(c for c in CASES["malformed"]
                  if c["name"] == "pinned-capture-horizontal")["state"]
    for square, token in (("b5", "wn"), ("c5", "bn"), ("f5", "wb"), ("g5", "bq")):
        yield dict(copy.deepcopy(pinned), occupied=dict(pinned["occupied"], **{square: token}))
    yield {"ep_target": "-", "side_to_move": "w", "occupied": {
        "a2": "wr", "b2": "wp", "c2": "wn", "e1": "wk", "e8": "bk", "a7": "bp", "b7": "bn"}}
    yield {"ep_target": "-", "side_to_move": "b", "occupied": {
        "a2": "wp", "b2": "wb", "e1": "wk", "e8": "bk", "g7": "bp", "h7": "br"}}


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
                    near = {chr(f + df) + str(r + dr) for df in (-1, 0, 1)
                            for dr in (-2, -1, 1, 2)
                            if 97 <= f + df <= 104 and 1 <= r + dr <= 8}
                    for to in sorted(set(squares) | near | {target} & ref.ALL_SQUARES):
                        for kind in sorted(ref.MOVE_TYPES):
                            yield state, {"type": kind, "from": frm, "to": to}


SWEEP = list(_sweep())


def test_r2_sweep_is_large_and_mixed():
    outs = {(_outcome(lambda s=s, m=m: prod.apply(copy.deepcopy(s), m)))[0:2][0]
            for s, m in SWEEP[:5000]}
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
        assert prod.identity_value(copy.deepcopy(state)) == \
            ref._identity_value(copy.deepcopy(state)), state


def test_r2_turn_transition_parity():
    for kind, side in itertools.product(sorted(ref.MOVE_TYPES), ("w", "b")):
        assert prod.turn_transition(kind, side) == ref._expected_turn(kind, side)


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
        yield f"strsub-value-{key}", dict(state, **{key: _Str(state[key])}) \
            if key != "occupied" else dict(state, occupied=_Str("x"))
    yield "extra-key", dict(state, extra="x")
    yield "missing-key", {k: v for k, v in state.items() if k != "side_to_move"}
    yield "occupied-strsub-key", dict(state, occupied=_rekey(state["occupied"], sq, _Str(sq)))
    yield "occupied-colliding-key", dict(state, occupied=_rekey(
        state["occupied"], sq, _Colliding(sq)))
    yield "occupied-strsub-token", dict(state, occupied=dict(
        state["occupied"], **{sq: _Str(state["occupied"][sq])}))
    yield "occupied-bad-square", dict(state, occupied=dict(state["occupied"], i9="wp"))
    yield "occupied-bad-token", dict(state, occupied=dict(state["occupied"], a1="wx"))
    yield "occupied-int-token", dict(state, occupied=dict(state["occupied"], a1=7))
    yield "two-white-kings", dict(state, occupied=dict(state["occupied"], a1="wk"))
    yield "no-black-king", dict(state, occupied={
        k: v for k, v in state["occupied"].items() if v != "bk"})
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
        return exc.failure_class == "target_malformed" and exc.__cause__ is None \
            and CALLS == []
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
    assert _typed_malformed(lambda: prod.apply(state, {"type": "quiet", "from": "a1",
                                                       "to": "a2"}))


@pytest.mark.parametrize("args", [
    ("castle", "w"), ("quiet", "x"), (_Str("quiet"), "w"), ("quiet", _Str("w")),
    (None, "w"), ("quiet", None)])
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
    for call in (lambda: prod.apply(_rekey(state, "ep_target", _Forging("ep_target")), move),
                 lambda: prod.apply(state, _rekey(move, "to", _Forging("to"))),
                 lambda: prod.identity_value(dict(state, occupied=_rekey(
                     state["occupied"], "d5", _Forging("d5"))))):
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
    "exact-dict-isinstance": ("return type(value) is dict and \\",
                              "return isinstance(value, dict) and \\"),
    "key-type-check-off": ("all(type(k) is str for k in dict.keys(value)) and \\",
                           "True and \\"),
    "key-set-subset": ("set(dict.keys(value)) == keys", "set(dict.keys(value)) >= keys"),
    "side-check-off": (" or side not in _OTHER:\n        _fail(\"target_malformed\")\n"
                       "    if type(occupied)",
                       ":\n        _fail(\"target_malformed\")\n    if type(occupied)"),
    "occupied-isinstance": ("if type(occupied) is not dict:", "if not isinstance(occupied, dict):"),
    "square-domain-off": ("if type(square) is not str or square not in _SQUARES or \\",
                          "if type(square) is not str or \\"),
    "token-domain-off": ("type(token) is not str or token not in _PIECES:",
                         "type(token) is not str:"),
    "king-count-off": ("!= 1:\n            _fail(\"target_malformed\")",
                       "> 1:\n            _fail(\"target_malformed\")"),
    "move-type-domain-off": ("if type(kind) is not str or kind not in _MOVE_TYPES:",
                             "if type(kind) is not str:"),
    "move-square-domain-off": ("if type(frm) is not str or frm not in _SQUARES or \\",
                               "if type(frm) is not str or \\"),
    "grammar-rank-off": ("target[0] in _FILES and target[1] in _RANKS",
                         "target[0] in _FILES"),
    "none-target-class": ("_fail(\"capture_precondition\")  # no target",
                          "_fail(\"target_inconsistent\")  # no target"),
    "availability-off": ("if target[1] != _SET_ON[_OTHER[side]][\"target_rank\"]:",
                         "if False:"),
    "captured-pawn-check-off": ("if occ.get(captured) != _OTHER[side] + \"p\":",
                                "if False:"),
    "destination-check-off": ("if to != target or frm[1]", "if frm[1]"),
    "mover-rank-off": ("frm[1] != mover[\"mover_rank\"] or \\\n", "\\\n"),
    "adjacency-off": ("abs(ord(frm[0]) - ord(target[0])) != 1 or ", ""),
    "mover-piece-off": (" or occ.get(frm) != side + \"p\":", ":"),
    "victim-kept": ("if sq not in (frm, captured)}", "if sq != frm}"),
    "pin-check-off": ("if _attacked(result, king, _OTHER[side]):", "if False:"),
    "slider-blocking-off": ("if chr(97 + f) + str(r) in occ:", "if False:"),
    "target-kept-after-capture": ("return {\"ep_target\": NONE, \"occupied\": result, "
                                  "\"side_to_move\": _OTHER[side]}\n\n\ndef apply",
                                  "return {\"ep_target\": target, \"occupied\": result, "
                                  "\"side_to_move\": _OTHER[side]}\n\n\ndef apply"),
    "target-retained-after-move": ("new_target = NONE  # lifetime",
                                   "new_target = target  # lifetime"),
    "set-on-pawn-check-off": ("token[1] == \"p\" and frm[0] == to[0]", "frm[0] == to[0]"),
    "set-on-file-check-off": ("and frm[0] == to[0] and \\", "and \\"),
    "set-on-type-check-off": ("if kind == \"pawn-advance\" and token[1]", "if token[1]"),
    "no-piece-check-off": ("if token is None:\n        _fail(\"target_malformed\")  # no piece",
                           "if False:\n        _fail(\"target_malformed\")  # no piece"),
    "identity-always-target": ("            return target\n    return NONE",
                               "            return target\n    return target"),
    "identity-first-neighbour-only": ("for df in (-1, 1):\n        f = file_index + df",
                                      "for df in (-1,):\n        f = file_index + df"),
    "turn-halfmove-always-reset": ("if pawnish and _TURN[\"halfmove_clock\"] == \"reset\"",
                                   "if _TURN[\"halfmove_clock\"] == \"reset\""),
    "turn-fullmove-any-side": ("== \"increment-when-black\" and side == \"b\")",
                               "== \"increment-when-black\")"),
    "turn-domain-off": ("if type(move_type) is not str or move_type not in _MOVE_TYPES or \\",
                        "if type(move_type) is not str or \\"),
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


def _red(mod):
    """True when MOD fails the fixture rows, the parity sweep, identity or
    turn parity, or the hostile battery."""
    try:
        if _fixture_red(mod):
            return True
        for state, move in SWEEP:
            if _outcome(lambda s=state, m=move: mod.apply(copy.deepcopy(s), m)) != \
                    _outcome(lambda s=state, m=move: ref._apply(copy.deepcopy(s), m)):
                return True
        for state, _ in SWEEP[::97]:
            if mod.identity_value(copy.deepcopy(state)) != \
                    ref._identity_value(copy.deepcopy(state)):
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
