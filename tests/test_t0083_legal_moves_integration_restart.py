"""T0083 Chess/legal moves/integration/restart: the production legal-moves
runtime (tools/legal_moves_runtime.py) composed end to end and across a REAL
process restart.

Integration: a seeded game from the standard start is driven through
legal_moves/apply/is_attacked/terminal_status with the contract invariants
checked at every ply (the chosen move is in the legal set, exactly one piece
relocates, a capture removes exactly the victim, a promotion places the
declared piece, the mover's own king is never left attacked, no input is
mutated, terminal_status agrees with the legal set and the check state). The
exact trajectory is pinned (ply count, final state, SHA-256 of the log).

Restart: the position is serialized to JSON, a FRESH python process imports
the runtime cold and continues the game. The continuation must be
bit-identical to the uninterrupted one at every restart point, and the
terminal classifications must survive. Malformed persisted states and moves
are refused IN THE FRESH PROCESS with the declared class and mapped code, never
a traceback; a rejected move before the restart leaves no trace in the
persisted state. One-edit source mutants of the runtime run in the fresh
process and are each caught by a semantic mismatch against the model log.
Test-only: no production change.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import legal_moves_runtime as rt  # noqa: E402

SOURCE = (ROOT / "tools" / "legal_moves_runtime.py").read_text()
SEED = 20261005
PLIES = 120
RESTARTS = (0, 1, 17, 60, 119)

BACK = "rnbqkbnr"


def start_state():
    occ = {}
    for i, f in enumerate("abcdefgh"):
        occ[f + "1"] = "w" + BACK[i]
        occ[f + "2"] = "wp"
        occ[f + "7"] = "bp"
        occ[f + "8"] = "b" + BACK[i]
    return {"occupied": occ, "side_to_move": "w"}


def flip(state):
    state = copy.deepcopy(state)
    state["side_to_move"] = "b" if state["side_to_move"] == "w" else "w"
    return state


CHILD = r"""
import json, sys, types
root = sys.argv[1]
sys.path.insert(0, root)
payload = json.load(sys.stdin)
if payload.get("source"):
    rt = types.ModuleType("mutant_runtime")
    rt.__file__ = root + "/tools/legal_moves_runtime.py"
    exec(compile(payload["source"], rt.__file__, "exec"), rt.__dict__)
else:
    from tools import legal_moves_runtime as rt


def flip(state):
    out = json.loads(json.dumps(state))
    out["side_to_move"] = "b" if out["side_to_move"] == "w" else "w"
    return out


state = payload["state"]
log = []
try:
    boundary = {"count": len(rt.legal_moves(state)), "terminal": rt.terminal_status(state)}
    for move in payload["moves"]:
        after = rt.apply(state, move)
        state = flip(after)
        log.append({"move": move, "state": state,
                    "count": len(rt.legal_moves(state)),
                    "terminal": rt.terminal_status(state)})
    print(json.dumps({"ok": True, "state": state, "log": log, "boundary": boundary}))
except rt.LegalMovesError as err:
    print(json.dumps({"ok": False, "code": err.code,
                      "failure_class": err.failure_class,
                      "retryable": err.retryable,
                      "message": str(err)}))
except Exception as exc:
    print(json.dumps({"ok": False, "crash": type(exc).__name__ + ": " + str(exc)}))
"""


def child_run(state, moves, source=None):
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT)],
        input=json.dumps({"state": state, "moves": moves, "source": source}),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert out.returncode == 0, f"child crashed: {out.stderr[-400:]}"
    return json.loads(out.stdout)


def _king_square(state, side):
    return next(sq for sq, tok in state["occupied"].items() if tok == side + "k")


def check_ply(state, move, after):
    """Contract invariants for one accepted ply (state is pre-flip)."""
    side = state["side_to_move"]
    occ, new = state["occupied"], after["occupied"]
    assert after["side_to_move"] == side, "apply must not advance the turn"
    src, dst = move["from_square"], move["to_square"]
    mover = occ[src]
    assert mover[0] == side
    victim = occ.get(dst)
    assert victim is None or victim[0] != side
    assert src not in new or src == dst
    placed = new[dst]
    if "promotion" in move:
        assert mover[1] == "p" and placed == side + move["promotion"]
    else:
        assert placed == mover
    expected = {k: v for k, v in occ.items() if k not in (src, dst)}
    expected[dst] = placed
    assert new == expected
    assert len(new) == len(occ) - (1 if victim else 0)
    other = "b" if side == "w" else "w"
    assert rt.is_attacked(after, _king_square(after, side), other) is False


def drive(state, moves, collect=True):
    log = []
    for move in moves:
        before = copy.deepcopy(state)
        assert move in rt.legal_moves(state)
        after = rt.apply(state, move)
        assert state == before, "apply mutated its input"
        check_ply(state, move, after)
        state = flip(after)
        legal = rt.legal_moves(state)
        status = rt.terminal_status(state)
        in_check = rt.is_attacked(
            state,
            _king_square(state, state["side_to_move"]),
            "b" if state["side_to_move"] == "w" else "w",
        )
        assert status == (
            "none"
            if legal and not in_check
            else "check"
            if legal
            else "checkmate"
            if in_check
            else "stalemate"
        )
        if collect:
            log.append({"move": move, "state": state, "count": len(legal), "terminal": status})
    return state, log


def game(seed=SEED, plies=PLIES):
    rng = random.Random(seed)
    state, moves = start_state(), []
    for _ in range(plies):
        legal = rt.legal_moves(state)
        if not legal:
            break
        move = legal[rng.randrange(len(legal))]
        moves.append(move)
        state = flip(rt.apply(state, move))
    return moves


MOVES = game()
FINAL_STATE, REF_LOG = drive(start_state(), MOVES)
TRAJECTORY_SHA256 = "0325953ec994cf1cee5507de29879549712681141af69cfc4bdaad64e5c2b14d"
PINNED_PLIES = 120


def test_integration_trajectory_pinned():
    assert len(MOVES) == PINNED_PLIES
    assert hashlib.sha256(json.dumps(REF_LOG, sort_keys=True).encode()).hexdigest() == (
        TRAJECTORY_SHA256
    )
    kinds = {tok[1] for st in (e["state"] for e in REF_LOG) for tok in st["occupied"].values()}
    assert {"p", "n", "b", "r", "q", "k"} <= kinds


def test_trajectory_exercises_captures_and_check():
    pieces = [len(e["state"]["occupied"]) for e in REF_LOG]
    assert pieces[0] == 32 and min(pieces) < 32, "no capture occurred"
    assert any(e["terminal"] == "check" for e in REF_LOG)


@pytest.mark.parametrize("at", RESTARTS)
def test_restart_continuity_bit_identical(at):
    mid, _ = drive(start_state(), MOVES[:at], collect=False)
    child = child_run(json.loads(json.dumps(mid)), MOVES[at:])
    assert child["ok"], child
    assert child["log"] == json.loads(json.dumps(REF_LOG[at:]))
    assert child["state"] == FINAL_STATE
    assert child["boundary"] == {
        "count": len(rt.legal_moves(mid)),
        "terminal": rt.terminal_status(mid),
    }


def _st(pieces, side="w"):
    return {"occupied": pieces, "side_to_move": side}


TERMINALS = {
    "checkmate": (_st({"g1": "wk", "h8": "bk", "a8": "wr", "g7": "bp", "h7": "bp"}, "b"), None),
    "stalemate": (_st({"a8": "bk", "c7": "wq", "e1": "wk"}, "b"), "stalemate"),
    "check": (_st({"e1": "wk", "e8": "bk", "e5": "wr"}, "b"), "check"),
    "none": (_st({"e1": "wk", "e8": "bk"}), "none"),
}


def test_terminal_classification_survives_restart():
    for name, (state, expected) in TERMINALS.items():
        if name == "checkmate":
            state = _st({"g8": "bk", "a8": "wr", "g6": "wk"}, "b")
            expected = "checkmate"
        child = child_run(json.loads(json.dumps(state)), [])
        assert child["ok"], child
        assert child["boundary"]["terminal"] == expected == rt.terminal_status(state), name
        assert child["boundary"]["count"] == len(rt.legal_moves(state)), name
        assert (child["boundary"]["count"] == 0) == (expected in ("checkmate", "stalemate"))


def test_promotion_expansion_survives_restart():
    state = _st({"e1": "wk", "h8": "bk", "b7": "wp", "a8": "bn"})
    moves = [m for m in rt.legal_moves(state) if m["from_square"] == "b7"]
    assert sorted((m["to_square"], m["promotion"]) for m in moves) == sorted(
        (sq, p) for sq in ("a8", "b8") for p in "qrbn"
    )
    for move in moves:
        child = child_run(state, [move])
        assert child["ok"], child
        after = flip(rt.apply(state, move))
        assert child["state"] == after
        assert child["state"]["occupied"][move["to_square"]] == "w" + move["promotion"]
        assert "b7" not in child["state"]["occupied"]


def test_boundary_unpromoted_move_to_last_rank_is_refused_after_restart():
    state = _st({"e1": "wk", "h8": "bk", "b7": "wp"})
    child = child_run(state, [{"from_square": "b7", "to_square": "b8"}])
    assert child["ok"] is False and "crash" not in child
    assert child["failure_class"] == "promotion_missing" and child["code"] == "illegal_move"


BASE = {"occupied": {"e1": "wk", "e8": "bk", "a2": "wp"}, "side_to_move": "w"}
MOVE = [{"from_square": "a2", "to_square": "a3"}]
CORRUPT = {
    "missing_field": {"occupied": BASE["occupied"]},
    "extra_field": {**BASE, "z": 1},
    "occupied_not_a_map": {**BASE, "occupied": ["e1"]},
    "bad_square_key": {**BASE, "occupied": {**BASE["occupied"], "i9": "wp"}},
    "bad_token": {**BASE, "occupied": {**BASE["occupied"], "a3": "xx"}},
    "no_white_king": {**BASE, "occupied": {"e8": "bk", "a2": "wp"}},
    "two_black_kings": {**BASE, "occupied": {**BASE["occupied"], "d8": "bk"}},
    "bad_side": {**BASE, "side_to_move": "white"},
    "wrong_container": [BASE["occupied"], "w"],
}


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_malformed_persisted_state_rejected_in_fresh_process(name):
    child = child_run(CORRUPT[name], MOVE)
    assert child["ok"] is False and "crash" not in child, child
    assert child["failure_class"] is None
    assert child["code"] == "malformed_request"
    assert child["retryable"] is False
    assert type(child["message"]) is str and child["message"].strip()


ILLEGAL = {
    "no_piece": ({"from_square": "c4", "to_square": "c5"}, "no_piece"),
    "not_players_piece": ({"from_square": "e8", "to_square": "e7"}, "not_players_piece"),
    "unreachable_target": ({"from_square": "a2", "to_square": "a5"}, "unreachable_target"),
    "promotion_forbidden": (
        {"from_square": "a2", "to_square": "a3", "promotion": "q"},
        "promotion_forbidden",
    ),
}


@pytest.mark.parametrize("name", sorted(ILLEGAL))
def test_illegal_move_classes_survive_restart(name):
    move, failure_class = ILLEGAL[name]
    child = child_run(BASE, [move])
    assert child["ok"] is False and "crash" not in child, child
    assert (child["failure_class"], child["code"]) == (failure_class, "illegal_move")
    with pytest.raises(rt.LegalMovesError) as error:
        rt.apply(BASE, move)
    assert (error.value.failure_class, error.value.code) == (failure_class, "illegal_move")


def test_malformed_move_is_refused_after_restart():
    child = child_run(BASE, [{"from_square": "a2"}])
    assert child["ok"] is False and "crash" not in child
    assert child["code"] == "malformed_request"


def test_move_leaving_own_king_attacked_is_refused_after_restart():
    pinned = _st({"e1": "wk", "e3": "wn", "e8": "bk", "e7": "br"})
    move = {"from_square": "e3", "to_square": "c4"}
    child = child_run(pinned, [move])
    assert child["ok"] is False and "crash" not in child
    assert (child["failure_class"], child["code"]) == ("leaves_king_attacked", "illegal_move")


def test_rollback_survives_restart():
    pinned = _st({"e1": "wk", "e3": "wn", "e8": "bk", "e7": "br"})
    before = copy.deepcopy(pinned)
    with pytest.raises(rt.LegalMovesError):
        rt.apply(pinned, {"from_square": "e3", "to_square": "c4"})
    assert pinned == before, "a rejected move leaked into the state"
    legal = rt.legal_moves(pinned)
    child = child_run(json.loads(json.dumps(pinned)), [legal[0]])
    assert child["ok"], child
    assert child["state"] == flip(rt.apply(before, legal[0]))
    again = child_run(pinned, [legal[0]])
    assert again["state"] == child["state"] and again["log"] == child["log"]


def test_a_refusal_mid_continuation_reports_the_error_not_a_partial_log():
    mid, _ = drive(start_state(), MOVES[:10], collect=False)
    bad = {"from_square": "a1", "to_square": "h8"}
    child = child_run(mid, [*MOVES[10:12], bad, *MOVES[12:14]])
    assert child["ok"] is False and "log" not in child and "crash" not in child
    assert child["code"] == "illegal_move"


# -- one-edit mutants of the runtime, executed in the fresh process ----------------


def _once(old, new):
    assert SOURCE.count(old) == 1, old
    return SOURCE.replace(old, new)


EDITS = {
    "knight-delta-dropped": _once(
        '_KNIGHT_DELTAS = [tuple(d) for d in _MOVEMENT["knight"]["deltas"]]',
        '_KNIGHT_DELTAS = [tuple(d) for d in _MOVEMENT["knight"]["deltas"]][1:]',
    ),
    "bishop-direction-dropped": _once(
        '_BISHOP_DIRS = [tuple(d) for d in _MOVEMENT["bishop"]["directions"]]',
        '_BISHOP_DIRS = [tuple(d) for d in _MOVEMENT["bishop"]["directions"]][1:]',
    ),
    "apply-skips-king-safety": _once(
        '    if not _leaves_king_safe(occ, move, side):\n        _fail("leaves_king_attacked"',
        '    if False:\n        _fail("leaves_king_attacked"',
    ),
    "legal-set-skips-king-safety": _once(
        "                if _leaves_king_safe(occ, mv, side):",
        "                if True:",
    ),
    "promotion-expansion-truncated": _once(
        "for pr in (_PROMO_VALUES if promoting else [None])",
        "for pr in (_PROMO_VALUES[:1] if promoting else [None])",
    ),
    "stalemate-reported-as-checkmate": _once(
        '    if zero:\n        return "stalemate"', '    if zero:\n        return "checkmate"'
    ),
}

PIN_STATE = _st({"e1": "wk", "e3": "wn", "e8": "bk", "e7": "br"})
PIN_MOVE = {"from_square": "e3", "to_square": "c4"}
PROMO_STATE = _st({"e1": "wk", "h8": "bk", "b7": "wp", "a8": "bn"})
STALE = TERMINALS["stalemate"][0]


def _deviations(source):
    """Run every probe in a fresh process against the mutant; return the
    set of probe tags whose result differs from the model-expected one."""
    found = set()
    game_run = child_run(start_state(), MOVES, source=source)
    pin = child_run(PIN_STATE, [PIN_MOVE], source=source)
    promo = child_run(
        PROMO_STATE, [{"from_square": "b7", "to_square": "b8", "promotion": "q"}], source=source
    )
    legal = child_run(PIN_STATE, [], source=source)
    stale = child_run(STALE, [], source=source)
    promo_count = child_run(PROMO_STATE, [], source=source)
    for run in (game_run, pin, promo, legal, stale, promo_count):
        assert "crash" not in run, f"mutant crashed instead of deviating: {run}"
    if game_run.get("log") != json.loads(json.dumps(REF_LOG)):
        found.add("game")
    if not (pin["ok"] is False and pin.get("failure_class") == "leaves_king_attacked"):
        found.add("pin-refusal")
    expect_promo = flip(
        rt.apply(PROMO_STATE, {"from_square": "b7", "to_square": "b8", "promotion": "q"})
    )
    if promo.get("state") != expect_promo:
        found.add("promotion")
    if legal.get("boundary", {}).get("count") != len(rt.legal_moves(PIN_STATE)):
        found.add("legal-count")
    if promo_count.get("boundary", {}).get("count") != len(rt.legal_moves(PROMO_STATE)):
        found.add("promotion-count")
    if stale.get("boundary", {}).get("terminal") != "stalemate":
        found.add("terminal")
    return found


EXPECTED_KILL = {
    "knight-delta-dropped": "game",
    "bishop-direction-dropped": "game",
    "apply-skips-king-safety": "pin-refusal",
    "legal-set-skips-king-safety": "legal-count",
    "promotion-expansion-truncated": "promotion-count",
    "stalemate-reported-as-checkmate": "terminal",
}


def test_unmutated_runtime_has_no_deviation():
    assert _deviations(None) == set()


@pytest.mark.parametrize("label", sorted(EDITS))
def test_runtime_mutant_is_caught_semantically_in_the_fresh_process(label):
    killed_by = _deviations(EDITS[label])
    assert EXPECTED_KILL[label] in killed_by, (label, killed_by)
