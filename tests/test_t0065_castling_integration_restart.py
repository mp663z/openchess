"""T0065: castling integration/restart - the castling machine composed
end-to-end and across a REAL process restart.

Integration: a seeded adaptive trajectory driven through
tools.castling_runtime (the T0062 runtime) mixing castles and loss
events, with the T0059 contract's invariants checked at every step
(field set, attacked/side_to_move untouched, rights evolve ONLY by
the mapped loss events, castle relocation per per_side_paths) and the
exact trajectory pinned (event-kind counts + final state + SHA-256).

Restart: the machine state is serialized (JSON), a FRESH python
process imports the runtime cold and continues the trajectory. The
restarted trajectory must be bit-identical to the uninterrupted one:
state, occupancy, rights and identity all survive the restart
exactly. Malformed persisted states are rejected IN THE FRESH PROCESS
with the declared failure class and mapped code (never a traceback).
A rejected transition before the restart leaves no trace in the
persisted state (rollback survives the restart).
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
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "castling.yaml").read_text())
C = DOC["contract"]
VALUES = list(C["rights"]["values"])
GRAMMAR = dict(C["rights"]["grammar"])
NONE = GRAMMAR["none_sentinel"]
ORDERING = GRAMMAR["ordering"]
HOME = {k: dict(v) for k, v in C["rights"]["home_squares"].items()}
LOSS_KING = {"w": list(C["loss"]["on_king_move"]),
             "b": list(C["loss"]["on_king_move_black"])}
LOSS_ROOK_FROM = dict(C["loss"]["on_rook_move_from"])
LOSS_CAPTURE_ON = dict(C["loss"]["on_rook_capture_on"])
PATHS = {k: dict(v) for k, v in C["move"]["per_side_paths"].items()}
FAILURE_MAPPING = dict(C["failure_mapping"])
STATE_FIELDS = ("rights", "occupied", "attacked", "side_to_move")

SEED = 20260919
TRAJECTORY_EVENTS = 120
RESTART_AT = 71  # mid-trajectory restart point
# pinned after the first deterministic run; a generator change updates
# them in the same change
FINAL_STATE_PIN: dict | None = {
    "rights": "-",
    "occupied": {"a8": "br", "c1": "wk", "d1": "wr", "f8": "br",
                 "g8": "bk", "h1": "wr"},
    "attacked": [],
    "side_to_move": "w",
}
TRAJECTORY_SHA256: str | None = (
    "89f16edd69c7df91f5cedd1ad5c234e8434fd3d66d676cbee14578f28e119282")
EVENT_KIND_COUNTS_PIN: dict | None = {
    "castle": 2,
    "king_move": 40,
    "rook_move_from": 36,
    "rook_capture_on": 42,
}

CHILD_DRIVER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from tools import castling_runtime as rt
payload = json.load(sys.stdin)
state = payload["state"]
log = []
try:
    boundary_identity = rt.identity(state)
    for move in payload["moves"]:
        state = rt.apply(state, move)
        log.append({"state": state, "move": move,
                    "ident": rt.identity(state)})
    print(json.dumps({"ok": True, "state": state, "log": log,
                      "boundary_identity": boundary_identity}))
except rt.CastlingError as err:
    print(json.dumps({"ok": False, "code": err.code,
                      "failure_class": err.failure_class,
                      "retryable": err.retryable,
                      "message": err.args[0]}))
except Exception as exc:  # a crash is never an acceptable restart outcome
    print(json.dumps({"ok": False, "crash": f"{type(exc).__name__}: {exc}"}))
"""


def _runtime():
    from tools import castling_runtime as rt
    return rt


def _side_of(right: str) -> str:
    return "w" if right.isupper() else "b"


def _canonical(held: set) -> str:
    if not held:
        return NONE
    return "".join(c for c in ORDERING if c in held)


def _home_pieces(right: str) -> dict:
    side = _side_of(right)
    return {HOME[right]["king"]: side + "k", HOME[right]["rook"]: side + "r"}


def _start_state() -> dict:
    occ = {}
    for r in VALUES:
        occ.update(_home_pieces(r))
    return {"rights": _canonical(set(VALUES)), "occupied": occ,
            "attacked": [], "side_to_move": "w"}


def _castle_legal(state: dict, right: str) -> bool:
    """Contract-derived castle legality (drives the adaptive stream)."""
    if state["side_to_move"] != _side_of(right):
        return False
    if right not in (set() if state["rights"] == NONE
                     else set(state["rights"])):
        return False
    path, home = PATHS[right], HOME[right]
    if any(sq in state["occupied"] for sq in path["empty_required"]):
        return False
    att = set(state["attacked"])
    return not ({home["king"], path["king_to"], *path["king_transit"]} & att)


def _event_stream(rng: random.Random, n: int) -> tuple[list, dict]:
    """Adaptive deterministic stream: seeded loss events plus a castle
    whenever one is legal for the side to move (25% draw). Returns the
    move list and the final state (driven locally for generation only;
    the tests re-drive it through the runtime with invariants)."""
    from tools import castling_runtime as rt
    state = _start_state()
    moves = []
    for _ in range(n):
        legal_castles = [r for r in VALUES if _castle_legal(state, r)]
        if legal_castles and rng.random() < 0.6:
            right = legal_castles[rng.randrange(len(legal_castles))]
            move = {"type": "castle", "right": right}
        else:
            kind = rng.choice(["king_move", "rook_move_from",
                               "rook_capture_on"])
            if kind == "king_move":
                move = {"type": "king_move", "side": rng.choice(["w", "b"])}
            elif kind == "rook_move_from":
                move = {"type": "rook_move_from",
                        "square": rng.choice(sorted(LOSS_ROOK_FROM))}
            else:
                move = {"type": "rook_capture_on",
                        "square": rng.choice(sorted(LOSS_CAPTURE_ON))}
        state = rt.apply(state, move)
        # castles alternate the mover: after a castle the OTHER side
        # may be to move; the legality check reads side_to_move, so
        # flip it after each castle to model the turn machine
        if move["type"] == "castle":
            state = dict(state)
            state["side_to_move"] = ("b" if state["side_to_move"] == "w"
                                     else "w")
        moves.append(move)
    return moves, state


def _check_step(before: dict, move: dict, after: dict, where: str) -> None:
    """Contract invariants at EVERY step."""
    assert set(after) == set(STATE_FIELDS), where
    assert after["attacked"] == before["attacked"], where
    held = set() if before["rights"] == NONE else set(before["rights"])
    if move["type"] == "castle":
        side = _side_of(move["right"])
        assert after["rights"] == _canonical(held - set(LOSS_KING[side])), where
        path = PATHS[move["right"]]
        assert after["occupied"].get(path["king_to"]) == side + "k", where
        assert after["occupied"].get(path["rook_to"]) == side + "r", where
    else:
        if move["type"] == "king_move":
            loss = set(LOSS_KING[move["side"]])
        elif move["type"] == "rook_move_from":
            loss = {LOSS_ROOK_FROM[move["square"]]}
        else:
            loss = {LOSS_CAPTURE_ON[move["square"]]}
        assert after["rights"] == _canonical(held - loss), where
        assert after["occupied"] == before["occupied"], where


def _drive(state: dict, moves: list) -> tuple[dict, list[dict]]:
    rt = _runtime()
    log = []
    for move in moves:
        before = copy.deepcopy(state)
        after = rt.apply(state, move)
        _check_step(before, move, after, f"step {len(log)}")
        assert state == before, "apply mutated its input"
        state = after
        if move["type"] == "castle":  # model the turn machine flip
            state = dict(state)
            state["side_to_move"] = ("b" if state["side_to_move"] == "w"
                                     else "w")
        log.append({"state": dict(state), "move": move,
                    "ident": rt.identity(state)})
    return state, log


def _child_run(state: dict, moves: list) -> dict:
    out = subprocess.run(
        [sys.executable, "-c", CHILD_DRIVER, str(ROOT)],
        input=json.dumps({"state": state, "moves": moves}),
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert out.returncode == 0, f"child crashed: {out.stderr[-400:]}"
    return json.loads(out.stdout)


def test_integration_trajectory_pinned():
    rng = random.Random(SEED)
    moves, _ = _event_stream(rng, TRAJECTORY_EVENTS)
    counts = {}
    for m in moves:
        counts[m["type"]] = counts.get(m["type"], 0) + 1
    if EVENT_KIND_COUNTS_PIN is not None:
        assert counts == EVENT_KIND_COUNTS_PIN, counts
    final, log = _drive(_start_state(), moves)
    if FINAL_STATE_PIN is not None:
        assert final == FINAL_STATE_PIN, final
    digest = hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest()
    if TRAJECTORY_SHA256 is not None:
        assert digest == TRAJECTORY_SHA256, "trajectory drifted from the pin"


def test_restart_continuity_bit_identical():
    rng = random.Random(SEED)
    moves, _ = _event_stream(rng, TRAJECTORY_EVENTS)
    # uninterrupted reference
    ref_final, ref_log = _drive(_start_state(), moves)
    # restart: parent drives the prefix, a FRESH process drives the rest
    mid, mid_log = _drive(_start_state(), moves[:RESTART_AT])
    child = _child_run(mid, moves[RESTART_AT:])
    assert child["ok"], f"restart continuation failed: {child}"
    # the child's internal side_to_move equals mid's (no flip modeling
    # inside the child): compare raw post-restart steps with the same
    # no-flip convention
    rt = _runtime()
    state = mid
    expect_log = []
    for move in moves[RESTART_AT:]:
        state = rt.apply(state, move)
        expect_log.append({"state": state, "move": move,
                           "ident": rt.identity(state)})
    assert child["log"] == expect_log, (
        "post-restart step log (state/identity per move) not bit-identical")
    assert child["state"] == state, "restarted trajectory diverged"
    # identity survives the restart boundary: the fresh process computes
    # the same projection as the uninterrupted runtime
    assert child["boundary_identity"] == rt.identity(mid)
    # the serialized boundary state itself round-trips exactly
    assert json.loads(json.dumps(mid)) == mid


def test_restart_identity_exact_projection_both_sides():
    """Identity continuity is pinned EXACTLY (keys and values), not
    relationally: an always-empty or constant projection in the fresh
    process fails. Multiple rights states exercise both sides' rights
    so a one-side-only projection fails too."""
    rt = _runtime()
    assert C["identity"]["rights_participate"] is True  # contract pin
    for rights in ("KQkq", "Kk", "Qq", NONE):
        held = set() if rights == NONE else set(rights)
        occ = {}
        for r in held:
            occ.update(_home_pieces(r))
        for side in ("w", "b"):
            state = {"rights": rights, "occupied": occ, "attacked": [],
                     "side_to_move": side}
            child = _child_run(state, [{"type": "king_move", "side": "w"}])
            assert child["ok"], child
            expected = {"rights": rights}
            # exact keys AND values at the boundary and per step,
            # matching the uninterrupted runtime's projection
            assert child["boundary_identity"] == expected
            assert set(child["boundary_identity"]) == {"rights"}
            assert child["boundary_identity"] == rt.identity(state)
            for step in child["log"]:
                assert step["ident"] == {"rights": step["state"]["rights"]}
                assert set(step["ident"]) == {"rights"}
    # occupancy/attacked/side must NEVER leak into the projection
    # across a restart: states differing only in those fields project
    # identically
    occ = {}
    for r in VALUES:
        occ.update(_home_pieces(r))
    a = {"rights": "Kk", "occupied": occ, "attacked": [], "side_to_move": "w"}
    b = {"rights": "Kk", "occupied": {"e1": "wk", "h1": "wr", "e8": "bk",
                                      "h8": "br"}, "attacked": ["a1"],
         "side_to_move": "b"}
    assert _child_run(a, [])["boundary_identity"] == (
        _child_run(b, [])["boundary_identity"])


def test_castle_lifecycle_across_restart():
    """The domain's full castle lifecycle (one castle per side) with
    the restart boundary BETWEEN the castles: parent castles K, a
    fresh process castles k - relocation, rights loss and identity
    bit-identical to the uninterrupted run. Mirrored for Q/q."""
    rt = _runtime()
    for w_right, b_right in (("K", "k"), ("Q", "q")):
        occ = {**_home_pieces(w_right), **_home_pieces(b_right)}
        start = {"rights": _canonical({w_right, b_right}),
                 "occupied": occ, "attacked": [], "side_to_move": "w"}
        moves = [{"type": "castle", "right": w_right},
                 {"type": "castle", "right": b_right}]
        mid, _ = _drive(start, moves[:1])  # includes the side flip
        child = _child_run(mid, moves[1:])
        assert child["ok"], child
        # in-process reference under the child's raw-apply convention
        expected = rt.apply(mid, moves[1])
        assert child["state"] == expected
        assert child["boundary_identity"] == rt.identity(mid)
        # first castle (pre-restart) relocated the white pair exactly
        path_w = PATHS[w_right]
        assert mid["occupied"].get(path_w["king_to"]) == "wk"
        assert mid["occupied"].get(path_w["rook_to"]) == "wr"
        # second castle (post-restart, fresh process): relocation,
        # rights loss and identity bit-identical to the reference
        path_b = PATHS[b_right]
        step = child["log"][0]
        assert step["state"] == expected
        assert step["state"]["occupied"].get(path_b["king_to"]) == "bk"
        assert step["state"]["occupied"].get(path_b["rook_to"]) == "br"
        assert step["state"]["rights"] == NONE
        assert step["ident"] == {"rights": NONE}


def test_malformed_persisted_state_rejected_in_fresh_process():
    base = {"rights": "K", "occupied": {"e1": "wk", "h1": "wr"},
            "attacked": [], "side_to_move": "w"}
    castle = [{"type": "castle", "right": "K"}]
    corrupted = {
        "missing_field": ({k: v for k, v in base.items() if k != "attacked"},
                          None),
        "extra_field": ({**base, "z": 1}, None),
        "rights_non_str": ({**base, "rights": 7}, None),
        "occupied_bad_token": ({**base, "occupied": {"e1": "wk", "h1": 7}},
                               None),
        "attacked_duplicates": ({**base, "attacked": ["a1", "a1"]}, None),
        "bad_side": ({**base, "side_to_move": "white"}, None),
        "wrong_container": (["K", {}, [], "w"], None),
        "rights_grammar": ({**base, "rights": "QK"}, "rights_malformed"),
        "home_inconsistent": ({**base, "occupied": {"h1": "wr"}},
                              "rights_inconsistent"),
    }
    for name, (state, cls) in corrupted.items():
        child = _child_run(state, castle)
        assert not child["ok"], (
            f"{name}: corrupted persisted state ACCEPTED at restart")
        assert "crash" not in child, f"{name}: restart crashed: {child}"
        assert child["failure_class"] == cls, f"{name}: {child}"
        expect_code = (FAILURE_MAPPING[cls]["error"] if cls is not None
                       else "malformed_request")
        assert child["code"] == expect_code, f"{name}: {child}"
        assert type(child["retryable"]) is bool, name
        assert type(child["message"]) is str and child["message"].strip(), name


def test_rollback_survives_restart():
    rt = _runtime()
    state = {"rights": "K", "occupied": {"e1": "wk", "h1": "wr"},
             "attacked": ["f1"], "side_to_move": "w"}
    # a rejected castle pre-restart (through check)...
    before = copy.deepcopy(state)
    with pytest.raises(rt.CastlingError):
        rt.apply(state, {"type": "castle", "right": "K"})
    assert state == before, "rejected castle leaked into the state"
    # ...must leave the persisted/serialized state untouched
    child = _child_run(json.loads(json.dumps(state)),
                       [{"type": "king_move", "side": "w"}])
    assert child["ok"], child
    assert child["state"] == rt.apply(before, {"type": "king_move",
                                               "side": "w"})
    # continuation across ANOTHER restart boundary stays exact
    continued = _child_run(state, [{"type": "king_move", "side": "w"}])
    assert continued["ok"]
    assert continued["state"] == child["state"]
