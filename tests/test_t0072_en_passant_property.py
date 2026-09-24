"""T0072: en-passant unit/property battery over production graph.en_passant.

The T0069 fixture pins reviewed cases and T0071 pins parity with the
fixture's reference. This battery pins PROPERTIES over seeded generated
positions, judged by an independent model written here (a ray-scan attack
detector and the contract's structured fields, read from the YAML):

- P-total: every call returns an exact state or raises a typed
  EnPassantError of a contract class with the contract's code.
- P-atomic, P-deterministic: inputs never change, outputs share no object
  with inputs, two calls agree.
- P-side, P-lifetime, P-delta: the turn passes, the target is set exactly
  by a two-square advance from the start rank, and the board changes by
  exactly the move (the en-passant victim removed from the captured
  square, never the destination).
- P-capture-sound, P-capture-complete: an en-passant outcome agrees with
  the preconditions and the independent attack detector on the RESULTING
  board, and each failure class names a precondition that failed.
- P-precedence: with several faults, the class follows the current order
  (the contract is silent; every order fails closed): grammar, none
  target, occupied target square, target rank, victim, capture geometry, pin.
- P-identity: identity_value is the target exactly when some adjacent
  pawn's en-passant capture succeeds through apply (and the independent
  model agrees); canonicalising the target is idempotent.
- P-mirror-colour, P-mirror-file: flipping ranks with colours, or files,
  maps every outcome (value or class) of apply and identity_value.
- P-turn: turn_transition over its whole closed domain.

Hostile rows follow the coordinator's rule for batteries over a production
entry point: a list subclass, a dict subclass and str subclasses at every
boundary, keys in three forms (plain subclass, __eq__ raises, __hash__
collides). Each row pins the typed failure, an empty hostile-call log and
the input unchanged.
"""

from __future__ import annotations

import copy
import itertools
import random
import types
from pathlib import Path

import pytest
import yaml

import graph.en_passant as prod
from tests import test_t0069_en_passant_fixture as t69
from tests import test_t0071_en_passant_runtime as t71
from tests.test_t0089_fen_runtime import HOSTILE_CALLS, HOSTILE_FORMS, hostile_str

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "graph" / "en_passant.py"
C = yaml.safe_load((ROOT / "data" / "contracts" / "en_passant.yaml").read_text())["contract"]
NONE = C["target"]["grammar"]["none_sentinel"]
FILES = "".join(C["target"]["grammar"]["files"])
CLASSES = tuple(C["failure_classes"])
MAPPING = {cls: e["error"] for cls, e in C["failure_mapping"].items()}
OTHER = {"w": "b", "b": "w"}
SET_ON = {"w": C["target"]["set_on"]["white"], "b": C["target"]["set_on"]["black"]}
MOVER = {"w": C["capture"]["mover"]["white"], "b": C["capture"]["mover"]["black"]}
TURN = C["turn_linkage"]
MOVE_TYPES = ("pawn-advance", "ep-capture", "quiet")


# -- independent model -------------------------------------------------------------


def _sq(f, r):
    return FILES[f] + str(r) if 0 <= f < 8 and 1 <= r <= 8 else None


def _fr(square):
    return FILES.index(square[0]), int(square[1])


def attacked(board, square, by):
    """Ray scan outward from SQUARE (a different algorithm from the
    runtime's piece scan): is SQUARE attacked by side BY?"""
    f0, r0 = _fr(square)
    fwd = 1 if by == "w" else -1
    for df in (-1, 1):
        if board.get(_sq(f0 + df, r0 - fwd)) == by + "p":
            return True
    for df, dr in itertools.product((-2, -1, 1, 2), repeat=2):
        if abs(df) != abs(dr) and board.get(_sq(f0 + df, r0 + dr)) == by + "n":
            return True
    for df, dr in itertools.product((-1, 0, 1), repeat=2):
        if (df, dr) == (0, 0):
            continue
        if board.get(_sq(f0 + df, r0 + dr)) == by + "k":
            return True
        sliders = ("r", "q") if 0 in (df, dr) else ("b", "q")
        f, r = f0 + df, r0 + dr
        while _sq(f, r) is not None:
            token = board.get(_sq(f, r))
            if token is not None:
                if token[0] == by and token[1] in sliders:
                    return True
                break
            f, r = f + df, r + dr
    return False


def _faults(state, move):
    """The en-passant preconditions that fail, in the pinned order, and the
    resulting board with its pin verdict."""
    target, occ, side = state["ep_target"], state["occupied"], state["side_to_move"]
    frm, to = move["from"], move["to"]
    grammar = target == NONE or (
        len(target) == 2 and target[0] in FILES and target[1] in C["target"]["grammar"]["ranks"]
    )
    if not grammar:
        return ["grammar"], None
    if target == NONE:
        return ["none"], None
    if target in occ:
        # the square the pawn passed over is empty in a consistent position
        return ["occupied"], None
    faults = []
    if target[1] != SET_ON[OTHER[side]]["target_rank"]:
        faults.append("rank")
    victim = target[0] + MOVER[side]["captured_rank"]
    if occ.get(victim) != OTHER[side] + "p":
        faults.append("victim")
    if (
        to != target
        or frm[1] != MOVER[side]["mover_rank"]
        or abs(FILES.index(frm[0]) - FILES.index(target[0])) != 1
        or occ.get(frm) != side + "p"
    ):
        faults.append("geometry")
    board = {s: t for s, t in occ.items() if s not in (frm, victim)}
    board[to] = side + "p"
    if not faults:
        king = next(s for s, t in board.items() if t == side + "k")
        if attacked(board, king, OTHER[side]):
            faults.append("pin")
    return faults, board


FAULT_CLASS = {
    "grammar": "target_malformed",
    "none": "capture_precondition",
    "occupied": "target_inconsistent",
    "rank": "target_inconsistent",
    "victim": "target_inconsistent",
    "geometry": "capture_precondition",
    "pin": "pinned_capture",
}


def _set_on(state, move):
    side, token = state["side_to_move"], state["occupied"][move["from"]]
    frm, to, spec = move["from"], move["to"], SET_ON[state["side_to_move"]]
    if (
        move["type"] == "pawn-advance"
        and token[1] == "p"
        and frm[0] == to[0]
        and frm[1] == spec["from_rank"]
        and to[1] == spec["to_rank"]
    ):
        return to[0] + spec["target_rank"]
    del side
    return NONE


# -- mirrors -------------------------------------------------------------------


def _m_colour_sq(s):
    return s[0] + str(9 - int(s[1])) if len(s) == 2 and s[1].isdigit() else s


def _m_file_sq(s):
    return FILES[7 - FILES.index(s[0])] + s[1] if len(s) == 2 and s[0] in FILES else s


def _mirror_state(state, colour):
    msq = _m_colour_sq if colour else _m_file_sq
    occ = {msq(s): (OTHER[t[0]] + t[1] if colour else t) for s, t in state["occupied"].items()}
    side = OTHER[state["side_to_move"]] if colour else state["side_to_move"]
    return {"ep_target": msq(state["ep_target"]), "occupied": occ, "side_to_move": side}


def _mirror_move(move, colour):
    msq = _m_colour_sq if colour else _m_file_sq
    return {"type": move["type"], "from": msq(move["from"]), "to": msq(move["to"])}


# -- generator -------------------------------------------------------------------


def _free(rng, board, rank=None):
    while True:
        s = _sq(rng.randrange(8), rank if rank else rng.randint(1, 8))
        if s not in board:
            return s


def _state(rng):
    side = rng.choice("wb")
    other = OTHER[side]
    mr = int(MOVER[side]["mover_rank"])
    tf = rng.randrange(8)
    board = {}
    if rng.random() < 0.9:
        board[_sq(tf, mr)] = other + "p"
    for df in (-1, 1):
        if _sq(tf + df, mr) and rng.random() < 0.6:
            board[_sq(tf + df, mr)] = side + "p"
    board[_free(rng, board, mr if rng.random() < 0.4 else None)] = side + "k"
    board[_free(rng, board)] = other + "k"
    for _ in range(rng.randint(0, 4)):
        colour = rng.choice("wb")
        board[_free(rng, board, mr if rng.random() < 0.4 else None)] = colour + rng.choice("pnbrq")
    roll = rng.random()
    if roll < 0.04:
        target = rng.choice(["e4", "", "e66", "E6", FILES[tf] + str(mr), "i6", "6e"])
    elif roll < 0.75:
        target = FILES[tf] + SET_ON[other]["target_rank"]
    elif roll < 0.85:
        target = NONE
    elif roll < 0.93:
        target = rng.choice(FILES) + SET_ON[other]["target_rank"]
    else:
        target = FILES[tf] + SET_ON[side]["target_rank"]
    if target in board:
        # a random occupant is an occupied target: keep it (target_inconsistent)
        pass
    elif target != NONE and len(target) == 2 and target[0] in FILES and rng.random() < 0.06:
        own_king = next(sq for sq, t in board.items() if t == side + "k")
        if rng.random() < 0.5:
            del board[own_king]
            board[target] = side + "k"  # the mover's own king on the target
        else:
            board[target] = rng.choice("wb") + rng.choice("pnbrq")
    return {"ep_target": target, "occupied": board, "side_to_move": side}


def _moves(rng, state):
    side, occ, target = state["side_to_move"], state["occupied"], state["ep_target"]
    mr = int(MOVER[side]["mover_rank"])
    grammar = len(target) == 2 and target[0] in FILES and target[1] in "36"
    if grammar:
        tf = FILES.index(target[0])
        for df in (-1, 1):
            if _sq(tf + df, mr):
                yield {"type": "ep-capture", "from": _sq(tf + df, mr), "to": target}
    yield {"type": "ep-capture", "from": rng.choice(sorted(occ)), "to": _sq(rng.randrange(8), 6)}
    pawns = sorted(s for s, t in occ.items() if t == side + "p")
    if pawns and grammar:
        yield {"type": "ep-capture", "from": rng.choice(pawns), "to": target}
    yield {"type": "quiet", "from": _free(rng, occ), "to": _free(rng, occ)}
    fwd = 1 if side == "w" else -1
    for s, t in sorted(occ.items()):
        if t == side + "p":
            f, r = _fr(s)
            for step in (1, 2):
                if _sq(f, r + fwd * step):
                    yield {"type": "pawn-advance", "from": s, "to": _sq(f, r + fwd * step)}
    own = sorted(s for s, t in occ.items() if t[0] == side)
    yield {"type": "quiet", "from": rng.choice(own), "to": _free(rng, occ)}
    start = int(SET_ON[side]["from_rank"])
    f = rng.randrange(8)
    if _sq(f, start) not in occ:
        board = dict(occ, **{_sq(f, start): side + "p"})
        for step in (1, 2):
            yield (
                dict(state, occupied=board),
                {"type": "pawn-advance", "from": _sq(f, start), "to": _sq(f, start + fwd * step)},
            )
        two = _sq(f, start + 2 * fwd)
        # near misses of the set-on event: a quiet two-square pawn move, a
        # diagonal two-rank "advance", a non-pawn "advance"
        yield dict(state, occupied=board), {"type": "quiet", "from": _sq(f, start), "to": two}
        side_file = _sq(f + (1 if f < 7 else -1), start + 2 * fwd)
        yield (
            dict(state, occupied=board),
            {"type": "pawn-advance", "from": _sq(f, start), "to": side_file},
        )
        knight = dict(occ, **{_sq(f, start): side + "n"})
        yield (
            dict(state, occupied=knight),
            {"type": "pawn-advance", "from": _sq(f, start), "to": two},
        )


def _edge_rows():
    """Named rows at both edges and both colours (White to move; the colour
    mirror adds Black): the only capturer on the a- or h-file, the target
    on the a- or h-file, the landed pawn as the only blocker on a file and
    a diagonal, and the rank pin opened by the double removal."""
    k = {"e1": "wk", "e8": "bk"}
    return {
        "a-file-capturer": ("b6", dict(k, b5="bp", a5="wp"), "a5", None),
        "h-file-capturer": ("g6", dict(k, g5="bp", h5="wp"), "h5", None),
        "a-file-target": ("a6", dict(k, a5="bp", b5="wp"), "b5", None),
        "h-file-target": ("h6", dict(k, h5="bp", g5="wp"), "g5", None),
        "landed-pawn-blocks-file": (
            "d6",
            {"d1": "wk", "e8": "bk", "d8": "br", "d5": "bp", "e5": "wp"},
            "e5",
            None,
        ),
        "landed-pawn-blocks-diagonal": (
            "d6",
            {"a3": "wk", "e8": "bk", "f8": "bb", "d5": "bp", "e5": "wp"},
            "e5",
            None,
        ),
        "rank-pin-opened": (
            "d6",
            {"a5": "wk", "e8": "bk", "h5": "br", "d5": "bp", "e5": "wp"},
            "e5",
            "pinned_capture",
        ),
    }


EDGE_ROWS = _edge_rows()


def _edge_cases():
    for name, (target, occ, frm, want) in EDGE_ROWS.items():
        state = {"ep_target": target, "occupied": occ, "side_to_move": "w"}
        move = {"type": "ep-capture", "from": frm, "to": target}
        yield name, state, move, want
        yield name + "-black", _mirror_state(state, True), _mirror_move(move, True), want


def _cases():
    rng = random.Random(72)
    out = []
    for _ in range(700):
        state = _state(rng)
        for item in _moves(rng, state):
            out.append(item if isinstance(item, tuple) else (state, item))
    for _name, state, move, _want in _edge_cases():
        out.append((state, move))
    for state, move, _ident in t69.OCCUPIED_TARGET_ROWS:
        out.append((state, move))
    return out


CASES = _cases()


# -- properties -------------------------------------------------------------------


def _outcome(fn):
    try:
        return ("ok", fn())
    except prod.EnPassantError as exc:
        return ("err", exc.failure_class, exc.code)


def _shape_ok(out):
    return (
        type(out) is dict
        and set(out) == {"ep_target", "occupied", "side_to_move"}
        and all(type(v) is str for k, v in out.items() if k != "occupied")
        and type(out["occupied"]) is dict
        and all(type(s) is str and type(t) is str for s, t in out["occupied"].items())
    )


def _mirror_out(out, colour):
    if out[0] != "ok":
        return out
    return ("ok", _mirror_state(out[1], colour))


def _check(mod, state, move):
    """Names of the properties this (state, move) case violates under MOD."""
    bad = set()
    before = copy.deepcopy(state), copy.deepcopy(move)
    try:
        got = _outcome(lambda: mod.apply(state, move))
    except Exception:  # noqa: BLE001 - a raw escape violates totality
        return {"P-total"}
    if (state, move) != before:
        bad.add("P-atomic")
    if got[0] == "ok":
        out = got[1]
        if not _shape_ok(out):
            return bad | {"P-total"}
        if out is state or out["occupied"] is state["occupied"]:
            bad.add("P-atomic")
        if _outcome(lambda: mod.apply(copy.deepcopy(state), move)) != got:
            bad.add("P-deterministic")
        if out["side_to_move"] != OTHER[state["side_to_move"]]:
            bad.add("P-side")
    elif got[1] not in CLASSES or got[2] != MAPPING[got[1]]:
        return bad | {"P-total"}
    occ, side = state["occupied"], state["side_to_move"]
    if move["type"] == "ep-capture":
        faults, board = _faults(state, move)
        if got[0] == "ok":
            if faults:
                bad.add("P-capture-sound")
            if out["occupied"] != board:
                bad.add("P-delta")
            if out["ep_target"] != NONE:
                bad.add("P-lifetime")
        else:
            if not faults or (faults == ["pin"] and got[1] != "pinned_capture"):
                bad.add("P-capture-complete")
            elif got[1] not in {FAULT_CLASS[f] for f in faults}:
                bad.add("P-capture-sound")
            elif got[1] != FAULT_CLASS[faults[0]]:
                bad.add("P-precedence")
    elif move["from"] not in occ:
        if got[:2] != ("err", "target_malformed"):
            bad.add("P-total")
    elif got[0] != "ok":
        bad.add("P-total")
    else:
        want = {s: t for s, t in occ.items() if s != move["from"]}
        want[move["to"]] = occ[move["from"]]
        if out["occupied"] != want:
            bad.add("P-delta")
        if out["ep_target"] != _set_on(state, move):
            bad.add("P-lifetime")
    for colour, prop in ((True, "P-mirror-colour"), (False, "P-mirror-file")):
        mirrored = _outcome(
            lambda c=colour: mod.apply(_mirror_state(state, c), _mirror_move(move, c))
        )
        if mirrored != _mirror_out(got, colour):
            bad.add(prop)
    del side
    return bad


def _check_identity(mod, state):
    bad = set()
    target, side = state["ep_target"], state["side_to_move"]
    got = mod.identity_value(copy.deepcopy(state))
    via_apply, via_model = False, False
    if target != NONE and _faults(state, {"from": "a1", "to": target})[0] != ["grammar"]:
        tf = FILES.index(target[0])
        for df in (-1, 1):
            frm = _sq(tf + df, int(MOVER[side]["mover_rank"]))
            if frm is None:
                continue
            move = {"type": "ep-capture", "from": frm, "to": target}
            if _outcome(lambda m=move: mod.apply(copy.deepcopy(state), m))[0] == "ok":
                via_apply = True
            if not _faults(state, move)[0]:
                via_model = True
    want = target if via_model else NONE
    if got != (target if via_apply else NONE) or got != want:
        bad.add("P-identity")
    canon = dict(state, ep_target=got)
    if mod.identity_value(canon) != got:
        bad.add("P-identity")
    for colour, prop in ((True, "P-mirror-colour"), (False, "P-mirror-file")):
        mirror = _m_colour_sq if colour else _m_file_sq
        if mod.identity_value(_mirror_state(state, colour)) != mirror(got):
            bad.add(prop)
    return bad


def _check_turn(mod):
    for kind, side in itertools.product(MOVE_TYPES, "wb"):
        want = {
            "halfmove_clock": "reset"
            if kind != "quiet" and TURN["halfmove_clock"] == "reset"
            else "increment",
            "fullmove_number": "increment"
            if TURN["fullmove_number"] == "increment-when-black" and side == "b"
            else "same",
        }
        if _outcome(lambda k=kind, s=side: mod.turn_transition(k, s)) != ("ok", want):
            return {"P-turn"}
    return set()


IDENTITY_STATES = list({repr(s): s for s, _m in CASES}.values())


def _violations(mod, cases=None, states=None):
    bad = set()
    try:
        for state, move in CASES if cases is None else cases:
            bad |= _check(mod, state, move)
        for state in IDENTITY_STATES if states is None else states:
            bad |= _check_identity(mod, state)
        bad |= _check_turn(mod)
        if _hostile_red(mod):
            bad.add("hostile")
        if _domain_red(mod):
            bad.add("malformed-domain")
    except Exception:  # noqa: BLE001 - a raw escape is a violation
        bad.add("P-total")
    return sorted(bad)


# -- hostile rows (coordinator rule, 20:47) -----------------------------------------


def _tagged(obj, name):
    """The log entry for a call on one of this battery's hostile objects:
    (owning row token, method) once the row has claimed the object."""
    tag = obj.__dict__.get("_hostile_owner")
    return name if tag is None else (tag, name)


class _List(list):
    def __iter__(self):
        HOSTILE_CALLS.append(_tagged(self, "list.__iter__"))
        return list.__iter__(self)

    def __len__(self):
        HOSTILE_CALLS.append(_tagged(self, "list.__len__"))
        return list.__len__(self)

    def __getitem__(self, i):
        HOSTILE_CALLS.append(_tagged(self, "list.__getitem__"))
        return list.__getitem__(self, i)


class _Dict(dict):
    def _log(name):  # noqa: N805 - class-body helper
        base = getattr(dict, name)

        def method(self, *args, **kwargs):
            HOSTILE_CALLS.append(_tagged(self, "dict." + name))
            return base(self, *args, **kwargs)

        return method

    for _n in (
        "__getitem__",
        "__iter__",
        "__len__",
        "__contains__",
        "keys",
        "items",
        "values",
        "get",
        "copy",
        "__eq__",
    ):
        locals()[_n] = _log(_n)
    del _n, _log
    __hash__ = None


def _good():
    state = {
        "ep_target": "d6",
        "occupied": {"e1": "wk", "e8": "bk", "d5": "bp", "e5": "wp"},
        "side_to_move": "w",
    }
    return state, {"type": "ep-capture", "from": "e5", "to": "d6"}


def _rekey(d, key, new):
    return {(new if k == key else k): v for k, v in d.items()}


def _key_forms(key):
    """Plain str-subclass key, key whose __eq__ raises, and a renamed key
    whose __hash__ collides with the real field name."""
    return {
        "plain": hostile_str("plain", key),
        "eq-raises": hostile_str("eq-raises", key),
        "hash-collides": hostile_str("hash-collides", key + "_", collide_with=key),
    }


def _hostile_rows():
    state, move = _good()
    sq = "d5"
    rows = {}
    for entry in ("apply", "identity"):
        rows[f"{entry}:state-list-subclass"] = (entry, _List([state]), move)
        rows[f"{entry}:state-dict-subclass"] = (entry, _Dict(state), move)
        rows[f"{entry}:occupied-list-subclass"] = (
            entry,
            dict(state, occupied=_List(state["occupied"].items())),
            move,
        )
        rows[f"{entry}:occupied-dict-subclass"] = (
            entry,
            dict(state, occupied=_Dict(state["occupied"])),
            move,
        )
        for key in sorted(state):
            for form, hk in _key_forms(key).items():
                rows[f"{entry}:state-key-{key}-{form}"] = (entry, _rekey(state, key, hk), move)
        for form, hk in _key_forms(sq).items():
            rows[f"{entry}:occupied-key-{form}"] = (
                entry,
                dict(state, occupied=_rekey(state["occupied"], sq, hk)),
                move,
            )
        for form in HOSTILE_FORMS:
            for key in ("ep_target", "side_to_move"):
                rows[f"{entry}:state-value-{key}-{form}"] = (
                    entry,
                    dict(state, **{key: hostile_str(form, state[key])}),
                    move,
                )
            rows[f"{entry}:occupied-token-{form}"] = (
                entry,
                dict(state, occupied=dict(state["occupied"], **{sq: hostile_str(form, "bp")})),
                move,
            )
    rows["apply:move-list-subclass"] = ("apply", state, _List([move]))
    rows["apply:move-dict-subclass"] = ("apply", state, _Dict(move))
    for key in sorted(move):
        for form, hk in _key_forms(key).items():
            rows[f"apply:move-key-{key}-{form}"] = ("apply", state, _rekey(move, key, hk))
        for form in HOSTILE_FORMS:
            rows[f"apply:move-value-{key}-{form}"] = (
                "apply",
                state,
                dict(move, **{key: hostile_str(form, move[key])}),
            )
    for form in HOSTILE_FORMS:
        rows[f"turn:move-type-{form}"] = ("turn", hostile_str(form, "quiet"), "w")
        rows[f"turn:side-{form}"] = ("turn", "quiet", hostile_str(form, "w"))
    return rows


def _hostile_objects(value):
    """Every hostile object (str subclass, _List, _Dict) reachable from
    VALUE, walked with base methods only so no hostile method runs."""
    out = []
    stack = [value]
    while stack:
        node = stack.pop()
        kind = type(node)
        if isinstance(node, dict):
            if kind is not dict:
                out.append(node)
            for key, val in dict.items(node):
                stack += [key, val]
        elif isinstance(node, list):
            if kind is not list:
                out.append(node)
            stack += list(list.__iter__(node))
        elif isinstance(node, tuple):
            stack += list(node)
        elif isinstance(node, str) and kind is not str:
            out.append(node)
    return out


def _claim(rows):
    """Give every row's hostile objects that row's own token (its name):
    the row then fails only on calls carrying its token, while calls from
    other modules' hostile objects are recorded but cannot fail it."""
    for name, row in rows.items():
        for obj in _hostile_objects(row[1:]):
            assert "_hostile_owner" not in obj.__dict__, (name, "object shared by two rows")
            obj._hostile_owner = name
    return rows


HOSTILE_ROWS = _claim(_hostile_rows())


def _domain_rows():
    """Well-typed exact inputs outside the closed domain: typed
    target_malformed, input unchanged."""
    state, move = _good()
    occ = state["occupied"]
    rows = {
        "state-extra-key": ("apply", dict(state, extra="x"), move),
        "state-missing-key": ("apply", {k: v for k, v in state.items() if k != "ep_target"}, move),
        "side-unknown": ("apply", dict(state, side_to_move="x"), move),
        "square-off-board": ("apply", dict(state, occupied=dict(occ, i9="wn")), move),
        "token-unknown": ("apply", dict(state, occupied=dict(occ, a1="wx")), move),
        "two-white-kings": ("apply", dict(state, occupied=dict(occ, a1="wk")), move),
        "no-black-king": (
            "apply",
            dict(state, occupied={k: v for k, v in occ.items() if v != "bk"}),
            move,
        ),
        "move-extra-key": ("apply", state, dict(move, promo="q")),
        "move-type-unknown": ("apply", state, dict(move, type="castle")),
        "move-from-off-board": ("apply", state, dict(move, **{"from": "e9"})),
        "move-to-off-board": ("apply", state, dict(move, to="i6")),
        "move-from-empty": ("apply", state, {"type": "quiet", "from": "a3", "to": "a4"}),
        "target-off-grammar": ("apply", dict(state, ep_target="d4"), dict(move, to="d4")),
        "identity-side-unknown": ("identity", dict(state, side_to_move="x"), None),
        "identity-two-kings": ("identity", dict(state, occupied=dict(occ, a1="bk")), None),
        "turn-type-unknown": ("turn", "castle", "w"),
        "turn-side-unknown": ("turn", "quiet", "x"),
    }
    return rows


DOMAIN_ROWS = _domain_rows()


def _fingerprint(value):
    """Structure, exact types, identities and text of VALUE, read with base
    methods only, so no hostile method runs."""
    if isinstance(value, dict):
        return (
            type(value),
            id(value),
            tuple((_fingerprint(k), _fingerprint(v)) for k, v in dict.items(value)),
        )
    if isinstance(value, list):
        return (
            type(value),
            id(value),
            tuple(_fingerprint(list.__getitem__(value, i)) for i in range(list.__len__(value))),
        )
    if isinstance(value, str):
        return (type(value), id(value), str.__str__(value))
    return (type(value), id(value))


def _inputs(row):
    """Fingerprint of a row's inputs. Each input is fingerprinted on its
    own: fingerprinting the transient row[1:] slice would read only that
    fresh tuple's id - never the inputs' contents - and the id could
    differ between two slices whenever allocation shifts in between."""
    return tuple(_fingerprint(value) for value in row[1:])


def _hostile_call(mod, row):
    entry, a, b = row
    if entry == "apply":
        return mod.apply(a, b)
    if entry == "identity":
        return mod.identity_value(a)
    return mod.turn_transition(a, b)


EXPECTED_REASON = (
    "EnPassantError",
    "target_malformed",
    MAPPING["target_malformed"],
    True,
    [],
    True,
)


def _hostile_row_reason(mod, token, row):
    """(reason, foreign): reason is (exception type, failure_class, code,
    cause-is-None, calls carrying this row's TOKEN, input unchanged) and
    must equal EXPECTED_REASON; foreign lists every other logged call
    (legacy untagged names or other rows'/modules' tokens) for visibility
    only - it never fails the row."""
    before = _inputs(row)
    HOSTILE_CALLS.clear()
    try:
        _hostile_call(mod, row)
        kind, failure_class, code, fresh = "accepted", None, None, None
    except prod.EnPassantError as exc:
        kind = type(exc).__name__
        failure_class, code = exc.failure_class, exc.code
        fresh = exc.__cause__ is None
    except Exception as exc:  # noqa: BLE001 - a raw escape fails the row
        kind, failure_class, code, fresh = "raw:" + type(exc).__name__, None, None, None
    calls = list(HOSTILE_CALLS)
    own = [c for c in calls if type(c) is tuple and c[0] == token]
    foreign = [c for c in calls if not (type(c) is tuple and c[0] == token)]
    reason = (kind, failure_class, code, fresh, own, _inputs(row) == before)
    return reason, foreign


def _hostile_row_ok(mod, token, row):
    return _hostile_row_reason(mod, token, row)[0] == EXPECTED_REASON


def _hostile_red(mod):
    return not all(_hostile_row_ok(mod, name, row) for name, row in HOSTILE_ROWS.items())


def _domain_red(mod):
    return not all(_hostile_row_ok(mod, name, row) for name, row in DOMAIN_ROWS.items())


# -- tests -----------------------------------------------------------------------

PROPERTIES = (
    "P-total",
    "P-atomic",
    "P-deterministic",
    "P-side",
    "P-lifetime",
    "P-delta",
    "P-capture-sound",
    "P-capture-complete",
    "P-precedence",
    "P-identity",
    "P-mirror-colour",
    "P-mirror-file",
    "P-turn",
    "hostile",
    "malformed-domain",
)


def test_properties_hold_on_production():
    assert _violations(prod) == []


def test_sweep_is_large_and_reaches_every_outcome():
    assert len(CASES) > 5000
    seen = set()
    for state, move in CASES:
        got = _outcome(lambda s=state, m=move: prod.apply(copy.deepcopy(s), m))
        seen.add((move["type"], got[0] if got[0] == "ok" else got[1], state["side_to_move"]))
    for side in "wb":
        for outcome in ("ok", *CLASSES):
            assert ("ep-capture", outcome, side) in seen, (outcome, side)
        assert ("pawn-advance", "ok", side) in seen and ("quiet", "ok", side) in seen
    kinds = {prod.identity_value(s) == s["ep_target"] != NONE for s in IDENTITY_STATES}
    assert kinds == {True, False}
    set_targets = {
        prod.apply(copy.deepcopy(s), m)["ep_target"] != NONE
        for s, m in CASES
        if m["type"] == "pawn-advance" and m["from"] in s["occupied"]
    }
    assert set_targets == {True, False}


@pytest.mark.parametrize("name", [c[0] for c in _edge_cases()])
def test_edge_rows(name):
    _n, state, move, want = next(c for c in _edge_cases() if c[0] == name)
    got = _outcome(lambda: prod.apply(copy.deepcopy(state), move))
    assert got[0] == "ok" if want is None else got[:2] == ("err", want)
    ident = prod.identity_value(copy.deepcopy(state))
    assert ident == (state["ep_target"] if want is None else NONE)
    assert _check(prod, state, move) == set() and _check_identity(prod, state) == set()


@pytest.mark.parametrize("name", list(HOSTILE_ROWS))
def test_hostile_row_fails_closed_without_calls_and_unchanged(name):
    reason, foreign = _hostile_row_reason(prod, name, HOSTILE_ROWS[name])
    assert reason == EXPECTED_REASON, ("foreign calls", foreign)


@pytest.mark.parametrize("name", list(DOMAIN_ROWS))
def test_malformed_domain_row_fails_closed_unchanged(name):
    reason, foreign = _hostile_row_reason(prod, name, DOMAIN_ROWS[name])
    assert reason == EXPECTED_REASON, ("foreign calls", foreign)


def test_every_hostile_row_owns_its_hostile_objects():
    for name, row in HOSTILE_ROWS.items():
        objs = _hostile_objects(row[1:])
        assert objs, name
        assert all(obj.__dict__["_hostile_owner"] == name for obj in objs), name


def test_input_fingerprint_reads_contents_and_is_allocation_stable():
    """The unchanged-input check sees the inputs' contents and does not
    depend on where a transient tuple happens to be allocated."""
    name = "identity:state-key-occupied-hash-collides"
    row = HOSTILE_ROWS[name]
    first = _inputs(row)
    junk = [tuple(range(i)) for i in range(64)]  # shift the allocator
    assert _inputs(row) == first
    del junk
    state, move = _good()
    plain = ("apply", state, move)
    before = _inputs(plain)
    state["side_to_move"] = "b"
    assert _inputs(plain) != before
    move["to"] = "d3"
    state["side_to_move"] = "w"
    assert _inputs(plain) != before


def test_row_scoping_foreign_calls_do_not_fail_own_calls_do():
    """Self-test of the scoped log: a call on another owner's hostile
    object is recorded as foreign and does not fail the row; a call on
    the row's own hostile object fails it."""
    token = "self-test-row"
    state, _move = _good()
    own_key = hostile_str("hash-collides", "occupied_", collide_with="occupied", owner=token)
    stranger = hostile_str("plain", "x", owner="some-other-module")
    legacy = hostile_str("plain", "y")
    row = ("identity", _rekey(state, "occupied", own_key), None)

    def fake(touch):
        def identity_value(_state):
            touch()
            prod._fail("target_malformed")

        return types.SimpleNamespace(identity_value=identity_value)

    reason, foreign = _hostile_row_reason(fake(lambda: hash(stranger) + hash(legacy)), token, row)
    assert reason == EXPECTED_REASON
    assert foreign == [("some-other-module", "__hash__"), "__hash__"]
    reason, foreign = _hostile_row_reason(fake(lambda: hash(own_key)), token, row)
    assert reason != EXPECTED_REASON and reason[4] == [(token, "__hash__")]
    assert foreign == []
    reason, _ = _hostile_row_reason(prod, token, row)
    assert reason == EXPECTED_REASON


def test_hostile_rows_cover_every_boundary_and_form():
    names = set(HOSTILE_ROWS)
    for entry in ("apply", "identity"):
        for boundary in ("state", "occupied"):
            for kind in ("list-subclass", "dict-subclass"):
                assert f"{entry}:{boundary}-{kind}" in names
        for key in ("ep_target", "occupied", "side_to_move"):
            for form in ("plain", "eq-raises", "hash-collides"):
                assert f"{entry}:state-key-{key}-{form}" in names
    for key in ("from", "to", "type"):
        for form in HOSTILE_FORMS:
            assert f"apply:move-key-{key}-{form}" in names
            assert f"apply:move-value-{key}-{form}" in names
    assert {"apply:move-list-subclass", "apply:move-dict-subclass"} <= names


def test_model_attack_detector_matches_the_runtime_probe():
    rng = random.Random(7202)
    for _ in range(3000):
        board = {}
        for _ in range(rng.randint(1, 6)):
            board[_free(rng, board)] = rng.choice("wb") + rng.choice("pnbrqk")
        square = _free(rng, board)
        for by in "wb":
            assert attacked(board, square, by) is prod._attacked(board, square, by)


# -- source mutants --------------------------------------------------------------

NEW_MUTANTS = {
    "identity-lower-bound-exclusive": ("if 0 <= f <= 7:", "if 0 < f <= 7:"),
    "identity-upper-bound-short": ("if 0 <= f <= 7:", "if 0 <= f <= 6:"),
    "set-on-from-rank-off": ('        and frm[1] == set_on["from_rank"]\n', ""),
    "set-on-to-rank-off": ('        and to[1] == set_on["to_rank"]\n', ""),
    "adjacency-sign-lost": (
        "or abs(ord(frm[0]) - ord(target[0])) != 1",
        "or ord(frm[0]) - ord(target[0]) != 1",
    ),
    "pin-judged-on-original-board": (
        "if _attacked(result, king, _OTHER[side]):",
        "if _attacked(occ, king, _OTHER[side]):",
    ),
    "landed-pawn-not-placed": ('    result[to] = side + "p"\n    king', "    king"),
    "moved-piece-not-placed": ("    result[to] = token\n", ""),
    "moved-piece-origin-kept": (
        "result = {sq: t for sq, t in occ.items() if sq != frm}",
        "result = dict(occ)",
    ),
    "side-not-passed-on-move": (
        'return {"ep_target": new_target, "occupied": result, "side_to_move": _OTHER[side]}',
        'return {"ep_target": new_target, "occupied": result, "side_to_move": side}',
    ),
    "availability-white-only": (
        'if target[1] != _SET_ON[_OTHER[side]]["target_rank"]:',
        'if side == "w" and target[1] != _SET_ON[_OTHER[side]]["target_rank"]:',
    ),
    "occupied-aliased": ("        occ[square] = token\n    for colour", "    for colour"),
}

# one-line edits that cannot change any outcome, asserted green
EQUIVALENT_EDITS = {
    # an off-board neighbour file never holds a pawn: .get is None and the
    # capture fails its geometry, so the loop continues
    "identity-bounds-widened": ("if 0 <= f <= 7:", "if -1 <= f <= 8:"),
    "identity-bounds-off": ("if 0 <= f <= 7:", "if True:"),
    # the target is kept if ANY neighbour's capture is legal
    "identity-neighbour-order-swapped": ("for df in (-1, 1):", "for df in (1, -1):"),
    # a same-file mover square is the victim square, which already holds the
    # enemy pawn, so the mover-piece check fails with the same class
    "adjacency-allows-same-file": (
        "or abs(ord(frm[0]) - ord(target[0])) != 1",
        "or abs(ord(frm[0]) - ord(target[0])) > 1",
    ),
    # the contract gives the same rank for mover and victim
    "victim-rank-from-mover-rank": (
        'captured = target[0] + mover["captured_rank"]',
        'captured = target[0] + mover["mover_rank"]',
    ),
}


def _edit(name, before, after):
    source = PRODUCTION.read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType(f"en_passant_t0072_{name.replace('-', '_')}")
    module.__file__ = str(PRODUCTION)
    exec(compile(source.replace(before, after), str(PRODUCTION), "exec"), module.__dict__)
    module.EnPassantError = prod.EnPassantError
    return module


ALL_MUTANTS = {**t71.MUTANTS, **NEW_MUTANTS}


def _mutant_violations(name):
    before, after = ALL_MUTANTS[name]
    return _violations(_edit(name, before, after))


# exact properties each mutant violates; more or fewer is a changed defect
EXPECTED_KILLS = {
    "exact-dict-isinstance": ["hostile"],
    "key-type-check-off": ["hostile"],
    "key-set-subset": ["malformed-domain"],
    "side-check-off": ["malformed-domain"],
    "occupied-isinstance": ["hostile"],
    "square-domain-off": ["malformed-domain"],
    "token-domain-off": ["malformed-domain"],
    "king-count-off": ["malformed-domain"],
    "move-type-domain-off": ["malformed-domain"],
    "move-square-domain-off": ["malformed-domain"],
    "grammar-rank-off": ["P-capture-sound", "malformed-domain"],
    "none-target-class": ["P-capture-sound"],
    "availability-off": ["P-capture-sound", "P-identity", "P-precedence"],
    "captured-pawn-check-off": ["P-capture-sound", "P-identity", "P-precedence", "P-total"],
    "destination-check-off": ["P-capture-sound"],
    "mover-rank-off": ["P-capture-sound"],
    "adjacency-off": ["P-capture-sound"],
    "mover-piece-off": ["P-capture-sound", "P-identity", "P-total"],
    "victim-kept": ["P-capture-complete", "P-capture-sound", "P-delta", "P-identity"],
    "occupied-target-check-off": ["P-capture-sound", "P-delta", "P-total"],
    "pin-check-off": ["P-capture-sound", "P-identity"],
    "slider-blocking-off": ["P-capture-complete", "P-identity"],
    "target-kept-after-capture": ["P-lifetime"],
    "target-retained-after-move": ["P-lifetime"],
    "set-on-pawn-check-off": ["P-lifetime"],
    "set-on-file-check-off": ["P-lifetime"],
    "set-on-type-check-off": ["P-lifetime"],
    "no-piece-check-off": ["P-total", "malformed-domain"],
    "identity-always-target": ["P-identity"],
    "identity-first-neighbour-only": ["P-identity", "P-mirror-file"],
    "turn-halfmove-always-reset": ["P-turn"],
    "turn-fullmove-any-side": ["P-turn"],
    "pawn-direction-inverted": ["P-capture-complete", "P-capture-sound", "P-identity"],
    "own-piece-filter-off": ["P-capture-complete", "P-identity"],
    "knight-attack-off": ["P-capture-sound", "P-identity"],
    "king-attack-off": ["P-capture-sound", "P-identity"],
    "diagonal-attack-off": ["P-capture-sound", "P-identity"],
    "orthogonal-queen-off": ["P-capture-sound", "P-identity"],
    "bishop-diagonal-off": ["P-capture-sound", "P-identity"],
    "turn-domain-off": ["malformed-domain"],
    "identity-lower-bound-exclusive": ["P-identity", "P-mirror-file"],
    "identity-upper-bound-short": ["P-identity", "P-mirror-file"],
    "set-on-from-rank-off": ["P-lifetime"],
    "set-on-to-rank-off": ["P-lifetime"],
    "adjacency-sign-lost": ["P-capture-complete", "P-identity", "P-mirror-file"],
    "pin-judged-on-original-board": ["P-capture-complete", "P-capture-sound", "P-identity"],
    "landed-pawn-not-placed": ["P-capture-complete", "P-delta", "P-identity"],
    "moved-piece-not-placed": ["P-delta"],
    "moved-piece-origin-kept": ["P-delta"],
    "side-not-passed-on-move": ["P-side"],
    "availability-white-only": ["P-capture-sound", "P-identity", "P-mirror-colour", "P-precedence"],
    "occupied-aliased": ["P-capture-complete", "P-capture-sound", "P-total"],
}


def test_mutant_table_is_closed():
    assert list(EXPECTED_KILLS) == list(ALL_MUTANTS)
    assert all(EXPECTED_KILLS.values())


@pytest.mark.parametrize("name", list(ALL_MUTANTS))
def test_source_mutant_violates_exactly_its_properties(name):
    assert _mutant_violations(name) == EXPECTED_KILLS[name]


@pytest.mark.parametrize("name", list(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name):
    before, after = EQUIVALENT_EDITS[name]
    assert _violations(_edit(name, before, after)) == []


def test_every_property_with_a_one_line_defect_has_a_killer():
    hit = {p for props in EXPECTED_KILLS.values() for p in props}
    # P-atomic and P-deterministic guard defects no one-line edit of the
    # current copy-based source produces; they stay as regression properties
    assert set(PROPERTIES) - hit == {"P-atomic", "P-deterministic"}
