"""T0082 Chess/legal moves/fuzz-fault: deterministic fuzz and fault
battery over production tools.legal_moves_runtime.

- fuzz: a deterministic sweep (no seeded draw decides coverage) of
  every piece kind, for each side, on every square, with a fixed
  blocker pattern and two king placements; legal_moves, is_attacked
  on all 64 squares, terminal_status and apply (every legal move and
  every other from/to pair of the mover, with each promotion variant)
  are compared with an INDEPENDENT model written here from the rules
  (0x88-free integer board, no code shared with the runtime);
- happy/boundary: checkmate, stalemate, check, promotion expansion
  (quiet and capture), pins, double pushes, enemy king never a target;
- malformed: every failure class the module raises, in the pinned
  validation order, with typed errors (exact class, mapped code, no
  __cause__/__context__);
- rollback: inputs are unchanged after every accepted and rejected
  call; returned states and move lists are detached from the input;
- hostile types at each boundary (state, occupied, move containers;
  keys in three str-subclass forms; str-subclass values, nested) fail
  closed with an empty hostile-call log and unchanged input;
- forges: the module raises LegalMovesError and (from its constructor)
  ValueError and has no except clauses; the constructor's shape rules
  are pinned;
- one-edit source mutants of the module are pinned red; the whole
  battery runs against each.
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

from tools import legal_moves_runtime as rt  # noqa: E402

SOURCE_PATH = ROOT / "tools" / "legal_moves_runtime.py"
SOURCE = SOURCE_PATH.read_text()
FILES = "abcdefgh"
PROMOS = ("q", "r", "b", "n")
CODE = {
    "malformed_move": "malformed_request",
    "wrong_variant": "malformed_request",
    **{
        c: "illegal_move"
        for c in (
            "no_piece",
            "not_players_piece",
            "unreachable_target",
            "promotion_missing",
            "promotion_forbidden",
            "leaves_king_attacked",
        )
    },
}


# -- independent model ----------------------------------------------------------------


def _xy(sq):
    return FILES.index(sq[0]), int(sq[1]) - 1


def _name(x, y):
    return f"{FILES[x]}{y + 1}"


def _inside(x, y):
    return 0 <= x < 8 and 0 <= y < 8


LINES = {
    "r": [(1, 0), (-1, 0), (0, 1), (0, -1)],
    "b": [(1, 1), (1, -1), (-1, 1), (-1, -1)],
}
LINES["q"] = LINES["r"] + LINES["b"]
JUMPS = {
    "n": [(1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2)],
    "k": [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)],
}


def m_attacks(occ, side):
    out = set()
    for sq, tok in occ.items():
        if tok[0] != side:
            continue
        x, y = _xy(sq)
        kind = tok[1]
        if kind == "p":
            dy = 1 if side == "w" else -1
            for dx in (-1, 1):
                if _inside(x + dx, y + dy):
                    out.add(_name(x + dx, y + dy))
        elif kind in JUMPS:
            for dx, dy in JUMPS[kind]:
                if _inside(x + dx, y + dy):
                    out.add(_name(x + dx, y + dy))
        else:
            for dx, dy in LINES[kind]:
                cx, cy = x + dx, y + dy
                while _inside(cx, cy):
                    out.add(_name(cx, cy))
                    if _name(cx, cy) in occ:
                        break
                    cx, cy = cx + dx, cy + dy
    return out


def m_targets(occ, sq):
    tok = occ[sq]
    side, kind = tok[0], tok[1]
    enemy = "b" if side == "w" else "w"
    x, y = _xy(sq)
    out = []

    def free_or_enemy(t):
        return t not in occ or (occ[t][0] == enemy and occ[t][1] != "k")

    if kind == "p":
        dy = 1 if side == "w" else -1
        start = 1 if side == "w" else 6
        if _inside(x, y + dy) and _name(x, y + dy) not in occ:
            out.append(_name(x, y + dy))
            if y == start and _name(x, y + 2 * dy) not in occ:
                out.append(_name(x, y + 2 * dy))
        for dx in (-1, 1):
            if _inside(x + dx, y + dy):
                t = _name(x + dx, y + dy)
                if t in occ and occ[t][0] == enemy and occ[t][1] != "k":
                    out.append(t)
    elif kind in JUMPS:
        for dx, dy in JUMPS[kind]:
            if _inside(x + dx, y + dy) and free_or_enemy(_name(x + dx, y + dy)):
                out.append(_name(x + dx, y + dy))
    else:
        for dx, dy in LINES[kind]:
            cx, cy = x + dx, y + dy
            while _inside(cx, cy):
                t = _name(cx, cy)
                if t in occ:
                    if free_or_enemy(t):
                        out.append(t)
                    break
                out.append(t)
                cx, cy = cx + dx, cy + dy
    return set(out)


def m_after(occ, fr, to, promo):
    new = {k: v for k, v in occ.items() if k not in (fr, to)}
    new[to] = occ[fr][0] + promo if promo else occ[fr]
    return new


def m_in_check(occ, side):
    king = next(sq for sq, tok in occ.items() if tok == side + "k")
    return king in m_attacks(occ, "b" if side == "w" else "w")


def m_promotes(occ, fr, to):
    side = occ[fr][0]
    return occ[fr][1] == "p" and to[1] == ("8" if side == "w" else "1")


def m_legal(state):
    occ, side = state["occupied"], state["side_to_move"]
    moves = []
    for fr in sorted(occ):
        if occ[fr][0] != side:
            continue
        for to in sorted(m_targets(occ, fr)):
            for promo in PROMOS if m_promotes(occ, fr, to) else (None,):
                if not m_in_check(m_after(occ, fr, to, promo), side):
                    mv = {"from_square": fr, "to_square": to}
                    if promo:
                        mv["promotion"] = promo
                    moves.append(mv)
    return moves


def m_terminal(state):
    check = m_in_check(state["occupied"], state["side_to_move"])
    zero = not m_legal(state)
    return {
        (True, True): "checkmate",
        (False, True): "stalemate",
        (True, False): "check",
        (False, False): "none",
    }[(check, zero)]


def m_apply(state, move):
    """(outcome, value): the expected apply result by the pinned order."""
    occ, side = state["occupied"], state["side_to_move"]
    fr, to = move["from_square"], move["to_square"]
    promo = move.get("promotion")
    if fr not in occ:
        return ("err", "no_piece")
    if occ[fr][0] != side:
        return ("err", "not_players_piece")
    if to not in m_targets(occ, fr):
        return ("err", "unreachable_target")
    if "promotion" in move and promo not in PROMOS:
        return ("err", "promotion_forbidden")
    if m_promotes(occ, fr, to) and "promotion" not in move:
        return ("err", "promotion_missing")
    if not m_promotes(occ, fr, to) and "promotion" in move:
        return ("err", "promotion_forbidden")
    if m_in_check(m_after(occ, fr, to, promo), side):
        return ("err", "leaves_king_attacked")
    return ("ok", {"occupied": m_after(occ, fr, to, promo), "side_to_move": side})


# -- outcome capture ----------------------------------------------------------------


def _typed(module, call):
    try:
        return ("ok", call())
    except module.LegalMovesError as exc:
        fresh = exc.__cause__ is None and exc.__context__ is None
        return ("err", exc.failure_class, exc.code, type(exc) is module.LegalMovesError, fresh)


def _want_err(failure_class):
    code = "malformed_request" if failure_class is None else CODE[failure_class]
    return ("err", failure_class, code, True, True)


class Fail(AssertionError):
    pass


def _need(cond, why):
    if not cond:
        raise Fail(why)


def _canon(value):
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


# -- the sweep --------------------------------------------------------------------------

BLOCKERS = {"d4": "wp", "e5": "bp", "c6": "bn", "f3": "wb"}
KING_SETS = ({"w": "a1", "b": "h8"}, {"w": "h1", "b": "a8"})


def _sweep_states():
    """Deterministic: every (side, piece kind, square, king set), with the
    blocker pattern minus any square the piece or kings occupy."""
    out = []
    for kings, side, kind, (fx, fy) in itertools.product(
        KING_SETS, "wb", "pnbrq", itertools.product(range(8), range(8))
    ):
        sq = _name(fx, fy)
        if sq in kings.values():
            continue
        if kind == "p" and fy in (0, 7):
            continue
        occ = {k: v for k, v in BLOCKERS.items() if k != sq and k not in kings.values()}
        occ[kings["w"]] = "wk"
        occ[kings["b"]] = "bk"
        occ[sq] = side + kind
        for mover in "wb":
            out.append({"occupied": occ, "side_to_move": mover})
    return out


SWEEP = _sweep_states()


def _check_state(module, state):
    # a private copy: a mutating mutant must not pollute shared rows
    state = copy.deepcopy(state)
    snap = copy.deepcopy(state)
    moves = module.legal_moves(state)
    _need(type(moves) is list and all(type(m) is dict for m in moves), "move list types")
    _need(_canon(moves) == m_legal(state), f"legal_moves differs {state}")
    _need(len({id(m) for m in moves}) == len(moves), "move dicts shared")
    for sq in (_name(x, y) for x in range(8) for y in range(8)):
        for side in "wb":
            got = module.is_attacked(state, sq, side)
            _need(type(got) is bool, "is_attacked not an exact bool")
            _need(got == (sq in m_attacks(state["occupied"], side)), f"is_attacked {sq} {side}")
    _need(module.terminal_status(state) == m_terminal(state), "terminal_status differs")
    side = state["side_to_move"]
    tried = []
    for fr in sorted(state["occupied"]):
        if state["occupied"][fr][0] != side:
            continue
        for to in (_name(x, y) for x in range(8) for y in range(8)):
            if to == fr:
                continue
            pawn = state["occupied"][fr][1] == "p"
            for promo in (None, "q", "n", "k") if pawn else (None, "q"):
                mv = {"from_square": fr, "to_square": to}
                if promo:
                    mv["promotion"] = promo
                tried.append(mv)
    tried.append(
        {"from_square": "a2" if "a2" not in state["occupied"] else "b3", "to_square": "a3"}
    )
    for mv in tried:
        want = m_apply(state, mv)
        got = _typed(module, lambda m=mv: module.apply(state, m))
        if want[0] == "ok":
            _need(got[0] == "ok" and _canon(got[1]) == want[1], f"apply {mv}")
            _need(
                got[1] is not state and got[1]["occupied"] is not state["occupied"],
                "apply result aliases the input",
            )
        else:
            _need(got == _want_err(want[1]), f"apply {mv}: {got} != {want}")
    _need(state == snap, "input state mutated")


def _probe_sweep(module):
    failures = []
    for i, state in enumerate(SWEEP):
        try:
            _check_state(module, state)
        except BaseException as exc:  # noqa: BLE001 - any escape is a failure
            failures.append(f"sweep[{i}]: {type(exc).__name__} {exc}"[:160])
            if len(failures) > 3:
                break
    return failures


# -- fixed happy / boundary / malformed rows ------------------------------------------

START_KINGS = {"e1": "wk", "e8": "bk"}


def _st(occ, side="w"):
    return {"occupied": dict(occ), "side_to_move": side}


FIXED_STATES = {
    "back-rank-mate": _st({"g1": "wk", "h8": "bk", "a8": "wr", "g7": "bp", "h7": "bp"}, "b"),
    "stalemate": _st({"a8": "bk", "c7": "wq", "e1": "wk"}, "b"),
    "check-with-escape": _st({"e1": "wk", "e8": "bk", "e5": "wr"}, "b"),
    "promotion-quiet-and-capture": _st({"e1": "wk", "h8": "bk", "b7": "wp", "a8": "bn"}),
    "black-promotion": _st({"e1": "wk", "h8": "bk", "g2": "bp", "h1": "wr"}, "b"),
    "pinned-knight": _st({"e1": "wk", "e3": "wn", "e8": "bk", "e7": "br"}),
    "double-push-blocked-second": _st({"e1": "wk", "e8": "bk", "d2": "wp", "d4": "bp"}),
    "double-push-blocked-first": _st({"e1": "wk", "e8": "bk", "d2": "wp", "d3": "bn"}),
    "enemy-king-adjacent-target": _st({"e1": "wk", "e3": "bk", "d2": "wp"}, "w"),
    "king-cannot-step-into-pawn-attack": _st({"e1": "wk", "e8": "bk", "e3": "bp"}),
    "promotion-blocked": _st({"e1": "wk", "h8": "bk", "b7": "wp", "b8": "bn"}),
    "lone-kings": _st({"a1": "wk", "h8": "bk"}),
    "rook-ray-stops-at-pieces": _st({"e1": "wk", "e8": "bk", "a1": "wr", "a3": "wp", "c1": "bn"}),
    "pawn-single-step-off-home-rank": _st({"e1": "wk", "e8": "bk", "c3": "wp", "f6": "bp"}),
}
FIXED_TERMINAL = {
    "back-rank-mate": "checkmate",
    "stalemate": "stalemate",
    "check-with-escape": "check",
    "lone-kings": "none",
}


def _check_fixed(module):
    for name, state in FIXED_STATES.items():
        try:
            _check_state(module, state)
        except BaseException as exc:  # noqa: BLE001
            raise Fail(f"{name}: {type(exc).__name__} {exc}"[:200]) from None
    fixed = copy.deepcopy(FIXED_STATES)
    for name, want in FIXED_TERMINAL.items():
        _need(module.terminal_status(fixed[name]) == want, f"terminal {name}")
    promo = module.legal_moves(fixed["promotion-quiet-and-capture"])
    pawn = [m for m in promo if m["from_square"] == "b7"]
    _need(
        sorted((m["to_square"], m["promotion"]) for m in pawn)
        == sorted((t, p) for t in ("a8", "b8") for p in PROMOS),
        "promotion expansion",
    )
    _need(all(set(m) == {"from_square", "to_square", "promotion"} for m in pawn), "promo keys")
    # no move targets the enemy king
    for state in fixed.values():
        enemy = ("b" if state["side_to_move"] == "w" else "w") + "k"
        _need(
            all(state["occupied"].get(m["to_square"]) != enemy for m in module.legal_moves(state)),
            "enemy king targeted",
        )


BASE = _st({"e1": "wk", "e8": "bk", "a2": "wp", "b7": "wp", "c3": "bn", "h2": "wr"})


def _malformed_rows():
    """(label, call-builder, expected failure class) - pinned order."""
    rows = []

    def move(**kw):
        return {"from_square": "a2", "to_square": "a3", **kw}

    for label, mv in (
        ("move-none", None),
        ("move-list", [("from_square", "a2"), ("to_square", "a3")]),
        ("move-extra-key", move(extra=1)),
        ("move-renamed-key", {"from_square": "a2", "To_square": "a3"}),
        ("move-missing-to", {"from_square": "a2"}),
        ("move-bad-square", move(to_square="a9")),
        ("move-newline-square", move(to_square="a3\n")),
        ("move-same-square", move(to_square="a2")),
        ("move-promo-int", move(promotion=1)),
        ("move-square-int", move(from_square=12)),
    ):
        rows.append((label, BASE, mv, "malformed_move"))
    rows += [
        ("no-piece", BASE, {"from_square": "d4", "to_square": "d5"}, "no_piece"),
        ("enemy-piece", BASE, {"from_square": "c3", "to_square": "d5"}, "not_players_piece"),
        ("unreachable", BASE, {"from_square": "a2", "to_square": "a5"}, "unreachable_target"),
        (
            "unreachable-before-promo",
            BASE,
            {"from_square": "a2", "to_square": "a5", "promotion": "k"},
            "unreachable_target",
        ),
        (
            "promo-outside-enum",
            BASE,
            {"from_square": "b7", "to_square": "b8", "promotion": "k"},
            "promotion_forbidden",
        ),
        ("promo-missing", BASE, {"from_square": "b7", "to_square": "b8"}, "promotion_missing"),
        (
            "promo-forbidden",
            BASE,
            {"from_square": "a2", "to_square": "a3", "promotion": "q"},
            "promotion_forbidden",
        ),
        (
            "leaves-king",
            FIXED_STATES["pinned-knight"],
            {"from_square": "e3", "to_square": "c4"},
            "leaves_king_attacked",
        ),
        (
            "enemy-king-capture",
            FIXED_STATES["enemy-king-adjacent-target"],
            {"from_square": "d2", "to_square": "e3"},
            "unreachable_target",
        ),
    ]
    for label, state in (
        ("state-none", None),
        ("state-list", [("occupied", {}), ("side_to_move", "w")]),
        ("state-extra", {**BASE, "x": 1}),
        ("state-renamed", {"Occupied": BASE["occupied"], "side_to_move": "w"}),
        ("state-missing", {"occupied": BASE["occupied"]}),
        ("occupied-list", {**BASE, "occupied": list(BASE["occupied"].items())}),
        ("side-bad", {**BASE, "side_to_move": "x"}),
        ("side-newline", {**BASE, "side_to_move": "w\n"}),
        ("square-bad", {**BASE, "occupied": {**BASE["occupied"], "i1": "wp"}}),
        ("square-newline", {**BASE, "occupied": {**BASE["occupied"], "a4\n": "wp"}}),
        ("token-bad", {**BASE, "occupied": {**BASE["occupied"], "a4": "wx"}}),
        ("token-long", {**BASE, "occupied": {**BASE["occupied"], "a4": "wpp"}}),
        ("token-int", {**BASE, "occupied": {**BASE["occupied"], "a4": 7}}),
        ("two-white-kings", {**BASE, "occupied": {**BASE["occupied"], "a4": "wk"}}),
        (
            "no-black-king",
            {**BASE, "occupied": {k: v for k, v in BASE["occupied"].items() if v != "bk"}},
        ),
    ):
        rows.append((label, state, {"from_square": "a2", "to_square": "a3"}, None))
    return rows


MALFORMED = _malformed_rows()


def _check_malformed(module):
    for label, state, mv, fc in copy.deepcopy(MALFORMED):
        snaps = (copy.deepcopy(state), copy.deepcopy(mv))
        got = _typed(module, lambda s=state, m=mv: module.apply(s, m))
        _need(got == _want_err(fc), f"{label}: {got}")
        _need((state, mv) == snaps, f"{label}: input changed")
        if fc is None:
            for call in (
                lambda s=state: module.legal_moves(s),
                lambda s=state: module.terminal_status(s),
                lambda s=state: module.is_attacked(s, "a1", "w"),
            ):
                _need(_typed(module, call) == _want_err(None), f"{label}: other entry point")
    for label, sq, attacker in (
        ("square-bad", "z9", "w"),
        ("square-int", 1, "w"),
        ("square-newline", "a1\n", "w"),
        ("attacker-bad", "a1", "x"),
        ("attacker-none", "a1", None),
    ):
        got = _typed(module, lambda q=sq, a=attacker: module.is_attacked(copy.deepcopy(BASE), q, a))
        _need(got == _want_err(None), f"is_attacked {label}: {got}")


# -- hostile types ------------------------------------------------------------------------

HOSTILE = []


class _Armed:
    on = False


class SK(str):
    """Plain str subclass."""


class EqRaises(str):
    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("eq")
            raise RuntimeError("hostile __eq__")
        return str.__eq__(self, other)

    def __hash__(self):
        return str.__hash__(self)


class HashCollides:
    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if _Armed.on:
            HOSTILE.append("hk-eq")
            raise RuntimeError("hostile __eq__")
        return NotImplemented


class ListSub(list):
    pass


class DictSub(dict):
    pass


class LyingDict(dict):
    def _log(self, name):
        if _Armed.on:
            HOSTILE.append(name)

    def __getitem__(self, key):
        self._log("getitem")
        return dict.__getitem__(self, key)

    def __iter__(self):
        self._log("iter")
        return dict.__iter__(self)

    def keys(self):
        self._log("keys")
        return dict.keys(self)

    def items(self):
        self._log("items")
        return dict.items(self)

    def __contains__(self, key):
        self._log("contains")
        return dict.__contains__(self, key)

    def get(self, key, default=None):
        self._log("get")
        return dict.get(self, key, default)


KEY_FORMS = {"plain": SK, "eq-raises": EqRaises, "hash-collides": HashCollides}
GOOD_MOVE = {"from_square": "a2", "to_square": "a3"}


def _rekey(mapping, field, form):
    return {(KEY_FORMS[form](k) if k == field else k): v for k, v in mapping.items()}


def _hostile_rows():
    """(label, state, move, expected failure class), built fresh."""
    rows = []
    base = copy.deepcopy(BASE)
    occ = base["occupied"]
    for label, cls in (("dict-subclass", DictSub), ("lying-dict", LyingDict)):
        rows.append((f"state-{label}", cls(base), GOOD_MOVE, None))
        rows.append((f"occupied-{label}", {**base, "occupied": cls(occ)}, GOOD_MOVE, None))
        rows.append((f"move-{label}", base, cls(GOOD_MOVE), "malformed_move"))
    rows.append(("state-list-subclass", ListSub(base.items()), GOOD_MOVE, None))
    rows.append(("move-list-subclass", base, ListSub(GOOD_MOVE.items()), "malformed_move"))
    for form in KEY_FORMS:
        for field in ("occupied", "side_to_move"):
            rows.append((f"state-key-{form}-{field}", _rekey(base, field, form), GOOD_MOVE, None))
        rows.append(
            (f"occupied-key-{form}", {**base, "occupied": _rekey(occ, "a2", form)}, GOOD_MOVE, None)
        )
        for field in ("from_square", "to_square"):
            rows.append(
                (f"move-key-{form}-{field}", base, _rekey(GOOD_MOVE, field, form), "malformed_move")
            )
        rows.append(
            (
                f"move-key-{form}-promotion",
                base,
                {**GOOD_MOVE, KEY_FORMS[form]("promotion"): "q"},
                "malformed_move",
            )
        )
    rows.append(("side-str-subclass", {**base, "side_to_move": SK("w")}, GOOD_MOVE, None))
    rows.append(
        ("token-str-subclass", {**base, "occupied": {**occ, "a2": SK("wp")}}, GOOD_MOVE, None)
    )
    rows.append(
        (
            "king-token-str-subclass",
            {**base, "occupied": {**occ, "e1": EqRaises("wk")}},
            GOOD_MOVE,
            None,
        )
    )
    for field in ("from_square", "to_square"):
        rows.append(
            (
                f"move-value-str-subclass-{field}",
                base,
                {**GOOD_MOVE, field: SK(GOOD_MOVE[field])},
                "malformed_move",
            )
        )
    rows.append(
        (
            "move-promotion-str-subclass",
            base,
            {"from_square": "b7", "to_square": "b8", "promotion": SK("q")},
            "malformed_move",
        )
    )
    return rows


def _snap(obj):
    if isinstance(obj, dict):
        return (
            type(obj),
            [(type(k), getattr(k, "name", None) or str(k), _snap(v)) for k, v in dict.items(obj)],
        )
    if isinstance(obj, list):
        return (type(obj), [_snap(v) for v in list.__iter__(obj)])
    return (type(obj), obj)


def _check_hostile(module):
    for label, state, mv, fc in _hostile_rows():
        snaps = (_snap(state), _snap(mv))
        HOSTILE.clear()
        _Armed.on = True
        try:
            try:
                got = _typed(module, lambda s=state, m=mv: module.apply(s, m))
            except BaseException as exc:  # noqa: BLE001
                got = ("raw", type(exc).__name__)
            calls = list(HOSTILE)
        finally:
            _Armed.on = False
        _need(got == _want_err(fc), f"hostile {label}: {got}")
        _need(calls == [], f"hostile {label}: calls {calls}")
        _need((_snap(state), _snap(mv)) == snaps, f"hostile {label}: input changed")


# -- the probe --------------------------------------------------------------------------


def _probe(module, sweep=True):
    failures = []
    for label, check in (
        ("fixed", _check_fixed),
        ("malformed", _check_malformed),
        ("hostile", _check_hostile),
    ):
        try:
            check(module)
        except BaseException as exc:  # noqa: BLE001 - any escape is a failure
            failures.append(f"{label}: {type(exc).__name__} {exc}"[:200])
    if sweep:
        failures += _probe_sweep(module)
    return failures


def _source_mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"tools._t0082_mutant_{name}")
    module.__file__ = str(SOURCE_PATH)
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    finally:
        del sys.modules[module.__name__]
    return module


# -- mutants: one edit each to tools/legal_moves_runtime.py ------------------------------

MUTANTS = {
    "state-key-guard-off": [("    if any(type(key) is not str for key in state) or ", "    if ")],
    "move-key-guard-off": [
        ("    if any(type(key) is not str for key in move):\n", "    if False:\n")
    ],
    "state-dict-isinstance": [
        ("    if type(state) is not dict:\n", "    if not isinstance(state, dict):\n")
    ],
    "occupied-isinstance": [
        ("    if type(occ) is not dict:\n", "    if not isinstance(occ, dict):\n")
    ],
    "move-dict-isinstance": [
        ("    if type(move) is not dict:\n", "    if not isinstance(move, dict):\n")
    ],
    "grammar-isinstance": [
        (
            "    return (type(square) is str and len(square) == 2",
            "    return (isinstance(square, str) and len(square) == 2",
        )
    ],
    "token-isinstance": [
        (
            "        if not (type(tok) is str and len(tok) == 2",
            "        if not (isinstance(tok, str) and len(tok) == 2",
        )
    ],
    "side-isinstance": [
        (
            "    if type(stm) is not str or stm not in _SIDES:",
            "    if not isinstance(stm, str) or stm not in _SIDES:",
        )
    ],
    "king-count-at-most-one": [("        if kings != 1:\n", "        if kings > 1:\n")],
    "closed-keys-off": [("        if not set(move) <= allowed:\n", "        if False:\n")],
    "from-to-distinct-off": [("    if _FROM_TO_DISTINCT and fr == to:\n", "    if False:\n")],
    "promotion-type-off": [
        ('    if "promotion" in move and type(move["promotion"]) is not str:\n', "    if False:\n")
    ],
    "enemy-king-targetable": [
        (
            "                  _exclude_enemy_king: bool = _ENEMY_KING_NEVER_TARGET",
            "                  _exclude_enemy_king: bool = False",
        )
    ],
    "slider-ray-passes-pieces": [
        (
            "                    break  # any piece ends the ray before the next square",
            "                    pass",
        )
    ],
    "attack-ray-passes-pieces": [
        (
            "                        break  # the first piece's square is attacked",
            "                        pass",
        )
    ],
    "double-push-any-rank": [
        ("            if (_RANKS[y] == _DOUBLE_RANKS[_SIDE_NAME[side]]", "            if (True")
    ],
    "pawn-captures-empty": [
        (
            "                if hit is not None and hit[0] != side:\n",
            "                if hit is None or hit[0] != side:\n",
        )
    ],
    "promotion-expansion-single": [
        (
            "            for pr in (_PROMO_VALUES if promoting else [None]):",
            "            for pr in (_PROMO_VALUES[:1] if promoting else [None]):",
        )
    ],
    "legal-moves-king-safety-off": [
        ("                if _leaves_king_safe(occ, mv, side):\n", "                if True:\n")
    ],
    "apply-king-safety-off": [
        ("    if not _leaves_king_safe(occ, move, side):\n", "    if False:\n")
    ],
    "apply-occupancy-in-place": [("    new = dict(occ)\n", "    new = occ\n")],
    "terminal-check-or-zero": [("    if check and zero:\n", "    if check or zero:\n")],
    "promotion-missing-off": [
        ('    if promoting and "promotion" not in move:\n', "    if False:\n")
    ],
    "promotion-enum-off": [
        (
            '    if "promotion" in move and move["promotion"] not in _PROMO_ENUM:\n',
            "    if False:\n",
        )
    ],
    "is-attacked-wrong-side": [
        (
            '    return square in _attacked_squares(state["occupied"], attacker)',
            '    return square in _attacked_squares(state["occupied"], _OTHER[attacker])',
        )
    ],
    "no-piece-as-enemy": [
        (
            '        _fail("no_piece", f"from_square {fr} is empty")',
            '        _fail("not_players_piece", f"from_square {fr} is empty")',
        )
    ],
}


def test_mutants_apply_exactly_once():
    for name, edits in MUTANTS.items():
        _source_mutant(name, edits)


def test_identity_source_copy_is_green():
    assert _probe(_source_mutant("_identity", [])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    # fixed, malformed and hostile rows first; the sweep only when they
    # are all green (each mutant is also red on the sweep alone below)
    module = _source_mutant(name, MUTANTS[name])
    assert _probe(module, sweep=False) or _probe_sweep(module), name


# -- forges ---------------------------------------------------------------------------------


def test_module_raises_and_catches_only_the_declared_classes():
    """AST scope of the forge set: raised classes and except clauses."""
    tree = ast.parse(SOURCE)
    raised = {
        n.exc.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Raise)
        and isinstance(n.exc, ast.Call)
        and isinstance(n.exc.func, ast.Name)
    }
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]
    assert raised == {"LegalMovesError", "ValueError"}
    assert handlers == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": "nope", "message": "m"},
        {"code": SK("illegal_move"), "message": "m"},
        {"code": "illegal_move", "message": " "},
        {"code": "illegal_move", "message": SK("m")},
        {"code": "illegal_move", "message": "m", "failure_class": "nope"},
        {"code": "illegal_move", "message": "m", "failure_class": SK("no_piece")},
        {"code": "malformed_request", "message": "m", "failure_class": "no_piece"},
        {"code": "illegal_move", "message": "m", "retryable": 0},
    ],
)
def test_error_constructor_refuses_out_of_shape(kwargs):
    with pytest.raises(ValueError):
        rt.LegalMovesError(**kwargs)


def test_mutants_do_not_pollute_shared_rows():
    pristine = copy.deepcopy((BASE, FIXED_STATES, MALFORMED, SWEEP[:50]))
    for name in ("apply-occupancy-in-place", "no-piece-as-enemy"):
        _probe(_source_mutant(name, MUTANTS[name]), sweep=False)
        _probe_sweep(_source_mutant(name, MUTANTS[name]))
    assert (BASE, FIXED_STATES, MALFORMED, SWEEP[:50]) == pristine


def test_every_raise_site_emits_a_fresh_typed_error():
    """Every declared class the module raises is reachable and fresh."""
    seen = {
        got[1] for _, s, m, _ in MALFORMED for got in [_typed(rt, lambda s=s, m=m: rt.apply(s, m))]
    }
    assert seen == set(CODE) - {"wrong_variant"} | {None}


# -- tests ------------------------------------------------------------------------------------


def test_sweep_is_not_vacuous():
    kinds = {
        (s["occupied"][sq][1], s["side_to_move"])
        for s in SWEEP
        for sq in s["occupied"]
        if sq not in BLOCKERS
    }
    assert {k for k, _ in kinds} == set("pnbrqk")
    outcomes = {m_terminal(s) for s in SWEEP}
    assert {"none", "check"} <= outcomes


def test_fixed_rows():
    _check_fixed(rt)


def test_malformed_rows():
    _check_malformed(rt)


def test_hostile_rows():
    _check_hostile(rt)


@pytest.mark.parametrize("part", range(4))
def test_sweep_matches_the_model(part):
    chunk = SWEEP[part::4]
    for state in chunk:
        _check_state(rt, state)


def test_returned_values_are_detached():
    state = copy.deepcopy(BASE)
    moves = rt.legal_moves(state)
    moves[0]["to_square"] = "h8"
    assert rt.legal_moves(state)[0]["to_square"] != "h8"
    after = rt.apply(state, dict(GOOD_MOVE))
    after["occupied"]["a1"] = "bq"
    assert state == BASE and "a1" not in state["occupied"]


# semantic mutants must also fire on the sweep ALONE (structural and
# hostile-type mutants are not reachable from well-formed sweep states)
SWEEP_KILLS = sorted(
    n
    for n in MUTANTS
    if not any(
        tag in n
        for tag in (
            "key-guard",
            "isinstance",
            "king-count",
            "closed-keys",
            "distinct",
            "promotion-type",
            "promotion-enum",
        )
    )
)


@pytest.mark.parametrize("name", SWEEP_KILLS)
def test_semantic_mutant_is_red_on_the_sweep_alone(name):
    assert _probe_sweep(_source_mutant(name, MUTANTS[name])) != [], name
