"""T0056: turn integration/restart - the turn machine composed
end-to-end and across a REAL process restart.

Integration: a seeded multi-move trajectory driven through
tools.turn_runtime (the T0053 runtime) with the T0050 contract's
invariants checked at every step, termination tracked, and the exact
trajectory pinned (SHA-256 + final state + move counts).

Restart: the machine state is serialized (JSON), a FRESH python
process imports the runtime cold and continues the trajectory. The
restarted trajectory must be bit-identical to the uninterrupted one:
state, counters, termination status and identity all survive the
restart exactly. Malformed persisted states are rejected IN THE FRESH
PROCESS with the declared failure class and mapped code (never a
traceback). A rejected transition before the restart leaves no trace
in the persisted state (rollback survives the restart).
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
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "turn.yaml").read_text())
C = DOC["contract"]
SIDE_VALUES = list(C["state"]["side_values"])
STATE_FIELDS = list(C["state"]["fields"])
ON_MOVE = dict(C["transition"]["on_move"])
RESET_WHEN = list(ON_MOVE["halfmove_clock"]["reset_when"])
FMW = dict(ON_MOVE["fullmove_number"])
TERMINATION_STATES = list(C["termination"]["states"])
FIFTY = dict(C["termination"]["fifty_move"])
FAILURE_MAPPING = dict(C["failure_mapping"])

SEED = 20260919
TRAJECTORY_MOVES = 240
RESTART_AT = 137  # mid-trajectory restart point (odd: a black-to-move boundary)
# pinned after first deterministic run; a generator change updates them
# in the same change
FINAL_STATE_PIN = {"side_to_move": "w", "halfmove_clock": 0, "fullmove_number": 121}
TRAJECTORY_SHA256 = "9a0dc298a6e0cc93bff38d4c7a5871fa1480e93f27cfcb73cef588d7bd374bbd"
MOVE_KIND_COUNTS_PIN = {"quiet": 138, "pawn_move": 49, "capture": 53}

CHILD_DRIVER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from tools import turn_runtime as rt
payload = json.load(sys.stdin)
state = payload["state"]
log = []
try:
    boundary_identity = rt.identity(state)
    for move in payload["moves"]:
        state = rt.apply_move(state, move)
        term = rt.termination_for(state)
        log.append({"state": state, "move": move, "term": term,
                    "ident": rt.identity(state)})
    print(json.dumps({"ok": True, "state": state, "log": log,
                      "boundary_identity": boundary_identity}))

except rt.TurnError as err:
    print(json.dumps({"ok": False, "code": err.code,
                      "failure_class": err.failure_class,
                      "retryable": err.retryable,
                      "message": err.args[0]}))
except Exception as exc:  # a crash is never an acceptable restart outcome
    print(json.dumps({"ok": False, "crash": f"{type(exc).__name__}: {exc}"}))
"""


def _runtime():
    from tools import turn_runtime as rt
    return rt


def _move_stream(rng: random.Random, n: int) -> list[str]:
    kinds = ["quiet", "quiet", "pawn_move", "capture"]  # weighted, contract-derived set
    assert set(kinds) <= set(RESET_WHEN) | {"quiet"}
    return [kinds[rng.randrange(len(kinds))] for _ in range(n)]


def _drive(state: dict, moves: list[str]) -> tuple[dict, list[dict]]:
    rt = _runtime()
    log = []
    for move in moves:
        before = copy.deepcopy(state)
        state = rt.apply_move(state, move)
        # contract invariants at EVERY step
        assert set(state) == set(STATE_FIELDS)
        assert state["side_to_move"] != before["side_to_move"]
        assert state["fullmove_number"] == before["fullmove_number"] + (
            FMW["amount"] if (FMW["when"] == "black") == (before["side_to_move"] == "b") else 0)
        if move in RESET_WHEN:
            assert state["halfmove_clock"] == 0
        else:
            assert state["halfmove_clock"] == before["halfmove_clock"] + 1
        log.append({"state": dict(state), "move": move,
                    "term": rt.termination_for(state),
                    "ident": rt.identity(state)})
    return state, log


def _child_run(state: dict, moves: list[str]) -> dict:
    out = subprocess.run(
        [sys.executable, "-c", CHILD_DRIVER, str(ROOT)],
        input=json.dumps({"state": state, "moves": moves}),
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert out.returncode == 0, f"child crashed: {out.stderr[-400:]}"
    return json.loads(out.stdout)


def test_integration_trajectory_pinned():
    rng = random.Random(SEED)
    moves = _move_stream(rng, TRAJECTORY_MOVES)
    counts = {k: moves.count(k) for k in set(moves)}
    assert counts == MOVE_KIND_COUNTS_PIN, counts
    start = {"side_to_move": "w", "halfmove_clock": 0, "fullmove_number": 1}
    final, log = _drive(start, moves)
    assert final == FINAL_STATE_PIN, final
    digest = hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest()
    assert digest == TRAJECTORY_SHA256, "trajectory drifted from the pin"


def test_restart_continuity_bit_identical():
    rng = random.Random(SEED)
    moves = _move_stream(rng, TRAJECTORY_MOVES)
    start = {"side_to_move": "w", "halfmove_clock": 0, "fullmove_number": 1}
    # uninterrupted reference
    ref_final, ref_log = _drive(start, moves)
    # restart: parent drives the prefix, a FRESH process drives the rest
    mid, mid_log = _drive(start, moves[:RESTART_AT])
    child = _child_run(mid, moves[RESTART_AT:])
    assert child["ok"], f"restart continuation failed: {child}"
    assert child["state"] == ref_final, "restarted trajectory diverged"
    assert child["log"] == ref_log[RESTART_AT:], (
        "post-restart step log (state/term/identity per move) not bit-identical")
    # identity survives the restart boundary: the fresh process computes
    # the same projection as the uninterrupted runtime
    rt = _runtime()
    assert child["boundary_identity"] == rt.identity(mid)
    # the serialized boundary state itself round-trips exactly
    assert json.loads(json.dumps(mid)) == mid


def test_termination_survives_restart():
    # drive quiet moves to both thresholds; restart in between
    start = {"side_to_move": "w", "halfmove_clock": 98, "fullmove_number": 60}
    mid, _ = _drive(start, ["quiet"])  # 99
    child = _child_run(mid, ["quiet", "quiet"])  # 100 claim, 101
    assert child["ok"], child
    claim, auto = FIFTY["claim"], FIFTY["automatic"]
    step100 = child["log"][0]
    assert step100["state"]["halfmove_clock"] == claim["threshold"]
    assert step100["term"] == {"state": claim["state"], "automatic": claim["automatic"]}
    # in-process reference must match the restarted result exactly
    ref, ref_log = _drive(mid, ["quiet", "quiet"])
    assert child["log"] == ref_log and child["state"] == ref
    # automatic threshold across a restart
    start150 = {"side_to_move": "b", "halfmove_clock": 149, "fullmove_number": 90}
    child150 = _child_run(start150, ["quiet"])
    assert child150["ok"], child150
    assert child150["log"][0]["term"] == {
        "state": auto["state"], "automatic": auto["automatic"]}


def test_restart_identity_exact_projection_both_sides():
    """Identity continuity is pinned EXACTLY (keys and values), not
    relationally: an always-empty or constant projection in the fresh
    process fails. Both side-to-move values are exercised so a
    one-side-only projection fails too."""
    rt = _runtime()
    _ident = dict(C["transition"]["identity"])
    ident_fields = ["side_to_move"] if _ident["side_to_move_participates"] else []
    if _ident["counters_participate"]:
        ident_fields += ["halfmove_clock", "fullmove_number"]
    assert ident_fields == ["side_to_move"], ident_fields  # contract pin
    for side in SIDE_VALUES:
        state = {"side_to_move": side, "halfmove_clock": 41,
                 "fullmove_number": 22}
        child = _child_run(state, ["quiet"])
        assert child["ok"], child
        expected = {"side_to_move": side}
        # exact keys AND values at the boundary, per continued step,
        # and matching the uninterrupted runtime's projection
        assert child["boundary_identity"] == expected
        assert set(child["boundary_identity"]) == {"side_to_move"}
        assert child["boundary_identity"] == rt.identity(state)
        for step in child["log"]:
            assert step["ident"] == {"side_to_move": step["state"]["side_to_move"]}
            assert set(step["ident"]) == {"side_to_move"}
    # counters must NEVER leak into the projection across a restart:
    # two boundary states differing only in counters project identically
    a = {"side_to_move": "w", "halfmove_clock": 3, "fullmove_number": 9}
    b = {"side_to_move": "w", "halfmove_clock": 144, "fullmove_number": 90}
    assert _child_run(a, [])["boundary_identity"] == _child_run(b, [])["boundary_identity"]


def test_malformed_persisted_state_rejected_in_fresh_process():
    base = {"side_to_move": "w", "halfmove_clock": 40, "fullmove_number": 21}
    corrupted = {
        "missing_side": {k: v for k, v in base.items() if k != "side_to_move"},
        "bool_counter": {**base, "halfmove_clock": True},
        "negative_counter": {**base, "fullmove_number": -3},
        "extra_field": {**base, "z": 1},
        "bad_side": {**base, "side_to_move": "white"},
        "wrong_container": ["w", 40, 21],
    }
    expect = {
        "missing_side": "no_side_to_move",
        "bool_counter": "bad_counter",
        "negative_counter": "bad_counter",
        "extra_field": "bad_counter",
        "bad_side": "no_side_to_move",
        "wrong_container": "bad_counter",
    }
    assert set(corrupted) == set(expect)
    for name, state in corrupted.items():
        child = _child_run(state, ["quiet"])
        assert not child["ok"], f"{name}: corrupted persisted state ACCEPTED at restart"
        assert "crash" not in child, f"{name}: restart crashed: {child}"
        cls = expect[name]
        assert child["failure_class"] == cls, f"{name}: {child}"
        assert child["code"] == FAILURE_MAPPING[cls]["error"], f"{name}: {child}"
        assert type(child["retryable"]) is bool, name
        assert type(child["message"]) is str and child["message"].strip(), name


def test_rollback_survives_restart():
    rt = _runtime()
    state = {"side_to_move": "w", "halfmove_clock": 12, "fullmove_number": 7}
    # a rejected transition pre-restart...
    before = copy.deepcopy(state)
    with pytest.raises(rt.TurnError):
        rt.apply_move(state, "null")
    assert state == before, "rejected transition leaked into the state"
    # ...must leave the persisted/serialized state untouched
    child = _child_run(json.loads(json.dumps(state)), ["quiet"])
    assert child["ok"], child
    assert child["state"] == {"side_to_move": "b", "halfmove_clock": 13,
                              "fullmove_number": 7}
    # continuation across ANOTHER restart boundary stays exact
    continued = _child_run(state, ["quiet"])
    assert continued["ok"]
    ref, _ = _drive(state, ["quiet"])
    assert continued["state"] == ref
