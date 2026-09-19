"""T0064: castling fuzz/fault campaign against the T0062 runtime.

Where T0063 pins properties over 400 seeded cases, this task runs a
LARGER classified campaign with three added duties:

1. CLASSIFICATION: every campaign case ends exactly one of accept
   (new state satisfying the contract-derived transition invariants)
   or reject (CastlingError with closed-enum code, declared failure
   class or the pinned structural None, exact-bool retryable, nonempty
   message). Any other exception type is a CRASH and fails the suite
   with the case recorded - a fuzz that cannot tell reject from crash
   turns real crashes green.
2. FAULT INJECTION: the same campaign detectors are run against
   deliberately faulted apply wrappers (king move keeps rights, rook
   event keeps rights, castle keeps rights, castle leaves the rook
   home, validation skipped, input mutated). Every fault must be
   detected by its OWN campaign check (an invariant violation on the
   accept path, a missing rejection, or the no-mutation assert),
   never a collateral one.
3. DETERMINISM: the campaign is pure in its seed; two runs produce a
   byte-identical classified outcome log, and the log's SHA-256 is
   pinned so an RNG/generator edit cannot silently change coverage.

Every corruption generator GUARANTEES invalidity: a predicate
re-derives full castle-legality from the contract and asserts the
produced input actually violates it, so a weakened generator (a
"corruption" that yields a legal input) fails the suite. All semantics
derive from data/contracts/castling.yaml, never hardcoded: rights
values/grammar/ordering, home squares, per-side paths, loss-event
mappings, failure classes, the closed error enum and the normative
class-to-code mapping.

Probed runtime surface (pinned here as boundary rows): occupancy is a
CLOSED map over the contract-declared square universe - a piece on any
undeclared square is malformed_request; attacked may name any square
(a attacked rook_from or non-path square never blocks); non-string
rights/occupancy/attacked shapes are the structural malformed_request
with failure_class None; a wrong piece on a held right's home square
is rights_inconsistent.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path

import pytest
import yaml

from tools import castling_runtime as rt

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
ERROR_ENUM = set(C["errors"]["closed_enum"])
FAILURE_CLASSES = set(C["failure_classes"])
STATE_FIELDS = ("rights", "occupied", "attacked", "side_to_move")
DECLARED_SQUARES = set()
for _pair in HOME.values():
    DECLARED_SQUARES.update(_pair.values())
for _p in PATHS.values():
    DECLARED_SQUARES.update(
        [_p["king_from"], _p["king_to"], _p["rook_from"], _p["rook_to"]]
    )
    DECLARED_SQUARES.update(_p["king_transit"])
    DECLARED_SQUARES.update(_p["empty_required"])
DECLARED_SQUARES.update(LOSS_ROOK_FROM)
DECLARED_SQUARES.update(LOSS_CAPTURE_ON)

SEED = 20260919
CAMPAIGN_CASES = 2500
# exact campaign distribution pins for SEED (fixed seed -> deterministic;
# a generator change updates these pins in the same change)
EXPECTED_OUTCOME_COUNTS: dict[str, int] | None = {
    "accept": 1397,
    "reject:rights_malformed:malformed_request": 62,
    "reject:king_unmoved_required:illegal_move": 60,
    "reject:rights_inconsistent:illegal_position": 55,
    "reject:path_blocked:illegal_move": 79,
    "reject:through_check:illegal_move": 78,
    "reject:None:malformed_request": 769,
}
EXPECTED_LOG_SHA256: str | None = (
    "692eb1e4e031b07d6775249b2ba9fb97672ebc25a4debd5268eb3b613b238281")


def _side_of(right: str) -> str:
    return "w" if right.isupper() else "b"


def _canonical(held: set) -> str:
    if not held:
        return NONE
    return "".join(c for c in ORDERING if c in held)


def _held(rights: str) -> set:
    return set() if rights == NONE else set(rights)


def _home_pieces(right: str) -> dict:
    side = _side_of(right)
    return {HOME[right]["king"]: side + "k", HOME[right]["rook"]: side + "r"}


def _good_state(rng: random.Random, rights: str | None = None) -> dict:
    """A contract-valid state: canonical rights, held rights' home
    pieces exactly, occupancy closed over declared squares."""
    if rights is None:
        rights = _canonical({v for v in VALUES if rng.random() < 0.7})
    occ = {}
    for r in VALUES:
        if r in rights:
            occ.update(_home_pieces(r))
    return {"rights": rights, "occupied": occ, "attacked": [],
            "side_to_move": rng.choice(["w", "b"])}


def _castling_state(right: str, extra_rights: set | None = None) -> dict:
    held = {right} | (extra_rights or set())
    return {"rights": _canonical(held), "occupied": _home_pieces(right),
            "attacked": [], "side_to_move": _side_of(right)}


def _valid_move(rng: random.Random):
    """A contract-valid (state, move) pair: castle success or a loss
    event on a consistent state."""
    if rng.random() < 0.5:
        right = rng.choice(VALUES)
        extra = {v for v in VALUES if v != right and rng.random() < 0.5}
        return _castling_state(right, extra), {"type": "castle", "right": right}
    state = _good_state(rng)
    kind = rng.choice(["king_move", "rook_move_from", "rook_capture_on"])
    if kind == "king_move":
        return state, {"type": "king_move", "side": rng.choice(["w", "b"])}
    if kind == "rook_move_from":
        return state, {"type": "rook_move_from",
                       "square": rng.choice(sorted(LOSS_ROOK_FROM))}
    return state, {"type": "rook_capture_on",
                   "square": rng.choice(sorted(LOSS_CAPTURE_ON))}


class Crash(Exception):
    """A non-CastlingError escape: the campaign's crash classification."""


def _classify(apply_fn, state, move) -> str:
    """Run one case; classify accept / reject:<class>; Crash on escape."""
    try:
        out = apply_fn(state, move)
    except rt.CastlingError as err:
        _assert_error_shape(err)
        return f"reject:{err.failure_class}:{err.code}"
    except Exception as exc:  # noqa: BLE001 - classification, not handling
        raise Crash(f"{type(exc).__name__}: {exc}") from exc
    return "accept:" + json.dumps(out, sort_keys=True)


def _assert_error_shape(err: rt.CastlingError) -> None:
    assert type(err.code) is str and err.code in ERROR_ENUM
    assert err.failure_class is None or (
        type(err.failure_class) is str and err.failure_class in FAILURE_CLASSES
    )
    assert type(err.retryable) is bool
    assert type(err.args[0]) is str and err.args[0].strip()
    if err.failure_class is not None:
        # the contract's class-to-code mapping is normative: the right
        # class with the wrong code is a mapping violation, not a
        # shaped error
        assert err.code == FAILURE_MAPPING[err.failure_class]["error"], (
            f"class {err.failure_class} must emit code"
            f" {FAILURE_MAPPING[err.failure_class]['error']}, got {err.code}")
    else:
        # the pinned structural choice: defects outside the five named
        # classes are always malformed_request
        assert err.code == "malformed_request", (
            f"structural defects must be malformed_request, got {err.code}")


def _check_accept(before: dict, move: dict, after: dict, where: str) -> None:
    """Contract-derived transition invariants; the accept-path detector."""
    assert type(after) is dict, where
    assert set(after) == set(STATE_FIELDS), f"{where}: accept changed the field set"
    assert after["attacked"] == before["attacked"], f"{where}: attacked changed"
    assert after["side_to_move"] == before["side_to_move"], (
        f"{where}: side_to_move changed")
    held = _held(before["rights"])
    if move["type"] == "castle":
        right, path = move["right"], PATHS[move["right"]]
        side = _side_of(right)
        assert after["rights"] == _canonical(held - set(LOSS_KING[side])), (
            f"{where}: castle must remove BOTH of the side's rights")
        assert after["occupied"].get(path["king_to"]) == side + "k", (
            f"{where}: king did not relocate to the contract king_to square")
        assert after["occupied"].get(path["rook_to"]) == side + "r", (
            f"{where}: rook did not relocate to the contract rook_to square")
        assert HOME[right]["king"] not in after["occupied"], where
        assert HOME[right]["rook"] not in after["occupied"], where
        untouched = (set(before["occupied"]) - set(HOME[right].values())
                     - {path["king_to"], path["rook_to"]})
        for sq in untouched:
            assert after["occupied"].get(sq) == before["occupied"][sq], where
    else:
        if move["type"] == "king_move":
            loss = set(LOSS_KING[move["side"]])
        elif move["type"] == "rook_move_from":
            loss = {LOSS_ROOK_FROM[move["square"]]}
        else:
            loss = {LOSS_CAPTURE_ON[move["square"]]}
        assert after["rights"] == _canonical(held - loss), (
            f"{where}: loss event did not remove exactly the mapped rights")
        assert after["occupied"] == before["occupied"], (
            f"{where}: loss event changed occupancy")


CATEGORIES = [
    "rights_grammar", "right_not_held", "home_occupancy", "path_blocked",
    "through_check", "state_field_set", "move_shape", "move_domain",
    "state_container", "rights_type", "occupied_container",
    "occupied_key", "occupied_token", "attacked_shape",
    "side_to_move_shape", "wrong_turn",
]
CLASS_FOR_CATEGORY = {
    "rights_grammar": "rights_malformed",
    "right_not_held": "king_unmoved_required",
    "home_occupancy": "rights_inconsistent",
    "path_blocked": "path_blocked",
    "through_check": "through_check",
    "state_field_set": None,      # pinned structural choice
    "move_shape": None,
    "move_domain": None,
    "state_container": None,
    "rights_type": None,
    "occupied_container": None,
    "occupied_key": None,
    "occupied_token": None,
    "attacked_shape": None,
    "side_to_move_shape": None,
    "wrong_turn": None,
}


def _corrupt(rng: random.Random, category: str):
    """Produce one GUARANTEED-invalid input for the category."""
    right = rng.choice(VALUES)
    state = _castling_state(right)
    move = {"type": "castle", "right": right}
    if category == "rights_grammar":
        state["rights"] = rng.choice(["", "KK", "QK", "KX", "-K", "kK",
                                      "KQkqKQ", "qK", "KKKK"])
    elif category == "right_not_held":
        state["rights"] = _canonical(
            set(VALUES) - {right, *LOSS_KING[_side_of(right)]})
    elif category == "home_occupancy":
        home = HOME[right]
        sq = rng.choice([home["king"], home["rook"]])
        if rng.random() < 0.5:
            del state["occupied"][sq]
        else:
            state["occupied"][sq] = rng.choice(["wn", "bn", "bq"])
    elif category == "path_blocked":
        sq = rng.choice(PATHS[right]["empty_required"])
        state["occupied"][sq] = rng.choice(["wn", "bn", "wp", "bp"])
    elif category == "through_check":
        home, path = HOME[right], PATHS[right]
        state["attacked"] = [
            rng.choice([home["king"], path["king_to"], *path["king_transit"]])]
    elif category == "state_field_set":
        if rng.random() < 0.5:
            del state[rng.choice(list(STATE_FIELDS))]
        else:
            state["extra"] = 1
    elif category == "move_shape":
        move = dict(move)
        if rng.random() < 0.5:
            del move["right"]
        else:
            move["speed"] = "fast"
    elif category == "move_domain":
        move = rng.choice([
            {"type": "teleport", "right": right},
            {"type": "castle", "right": "X"},
            {"type": "castle", "right": right.lower()
             if right.isupper() else right.upper()},
            {"type": "king_move", "side": "x"},
            {"type": "rook_move_from", "square": "e4"},
            "castle", 7, None, True, [{"type": "castle", "right": right}],
        ])
    elif category == "state_container":
        state = rng.choice([None, [], "w", 7, {"w"}, [1, 2], True])
    elif category == "rights_type":
        state["rights"] = rng.choice([7, None, True, ["K"], b"K",
                                      {"r": "K"}, 1.5])
    elif category == "occupied_container":
        state["occupied"] = rng.choice([None, [], "e1", 7, True,
                                        {("e1",)}, ["e1"]])
    elif category == "occupied_key":
        # dict keys are hashable by construction in Python; the
        # malformed-key family is wrong type / off-domain / near-miss
        bad_key = rng.choice([7, None, True, "e4", "E1", "e1 ",
                              ("e1",), 1.5])
        state["occupied"] = dict(state["occupied"])
        state["occupied"][bad_key] = "wp"
    elif category == "occupied_token":
        sq = rng.choice(list(state["occupied"]))
        state["occupied"] = dict(state["occupied"])
        state["occupied"][sq] = rng.choice([7, None, True, "w", "wkk",
                                            "WK", "xk", "wz", ["wk"], "",
                                            " wk", "wk "])
    elif category == "attacked_shape":
        state["attacked"] = rng.choice([
            None, "e1", 7, True, {"e1": True}, {"e1"},
            [7], [None], [True], [["e1"]], [{"e1": 1}],
            ["e4"], ["E1"], ["e1 ", "f1"],
            ["e1", "e1"], ["f1", "f1", "g1"],
        ])
    elif category == "side_to_move_shape":
        state["side_to_move"] = rng.choice([7, None, True, "W", "B",
                                            "", "ｗ", ["w"], "white"])
    elif category == "wrong_turn":
        state["side_to_move"] = "b" if _side_of(right) == "w" else "w"
    else:
        raise AssertionError(f"unknown category {category}")
    return state, move


def _state_valid(state) -> bool:
    if type(state) is not dict or set(state) != set(STATE_FIELDS):
        return False
    rights = state["rights"]
    if type(rights) is not str:
        return False
    held = _held(rights) if rights != NONE else set()
    if rights != NONE and (not rights or rights != _canonical(held)):
        return False
    if rights == NONE and rights != _canonical(set()):
        return False
    if not held <= set(VALUES):
        return False
    occ, att, side = state["occupied"], state["attacked"], state["side_to_move"]
    if type(occ) is not dict or not all(
            type(k) is str and k in DECLARED_SQUARES
            and type(v) is str and len(v) == 2
            and v[0] in "wb" and v[1] in "kqrbnp"
            for k, v in occ.items()):
        return False
    if type(att) is not list or not all(
            type(sq) is str and sq in DECLARED_SQUARES for sq in att):
        return False
    if len(att) != len(set(att)):  # duplicate-free, like the runtime
        return False
    if type(side) is not str or side not in ("w", "b"):
        return False
    # held rights need their exact home pieces
    return all(occ.get(sq) == piece for r in held
               for sq, piece in _home_pieces(r).items())


def _castle_legal(state, move) -> bool:
    """Full contract legality for a castle attempt."""
    if type(move) is not dict or set(move) != {"type", "right"}:
        return False
    if move["type"] != "castle" or move["right"] not in VALUES:
        return False
    right = move["right"]
    if state["side_to_move"] != _side_of(right):
        return False
    if right not in _held(state["rights"]):
        return False
    path, home = PATHS[right], HOME[right]
    occ = state["occupied"]
    for sq in path["empty_required"]:
        if sq in occ:
            return False
    att = set(state["attacked"])
    return not ({home["king"], path["king_to"], *path["king_transit"]} & att)


def _violates_contract(state, move) -> bool:
    """Guaranteed-invalidity predicate: the corruption must REALLY break
    the contract (a 'corruption' yielding a legal input weakens the
    fuzz silently). Campaign corruptions always attempt a castle."""
    if not _state_valid(state):
        return True
    return not _castle_legal(state, move)


def _campaign(apply_fn, seed: int, n: int) -> list[str]:
    rng = random.Random(seed)
    log = []
    for i in range(n):
        if rng.random() < 0.55:
            state, move = _valid_move(rng)
            before = copy.deepcopy(state)
            rec = _classify(apply_fn, state, move)
            assert rec.startswith("accept:"), (
                f"case {i}: valid input rejected: {rec}")
            _check_accept(before, move, json.loads(rec[len("accept:"):]),
                          f"case {i}")
            assert state == before, f"case {i}: input mutated"
        else:
            category = CATEGORIES[rng.randrange(len(CATEGORIES))]
            state, move = _corrupt(rng, category)
            assert _violates_contract(state, move), (
                f"case {i}: {category} corruption produced a LEGAL input")
            before = copy.deepcopy(state)
            rec = _classify(apply_fn, state, move)
            cls = CLASS_FOR_CATEGORY[category]
            expect = ("reject:None:malformed_request" if cls is None
                      else f"reject:{cls}:{FAILURE_MAPPING[cls]['error']}")
            assert rec == expect, (
                f"case {i}: {category} -> {rec}, expected reject: {expect}")
            assert state == before, f"case {i}: rejected input mutated"
        log.append(rec)
    return log


def test_campaign_clean_run():
    log = _campaign(rt.apply, SEED, CAMPAIGN_CASES)
    counts: dict[str, int] = {}
    for rec in log:
        key = rec if rec.startswith("reject") else "accept"
        counts[key] = counts.get(key, 0) + 1
    if EXPECTED_OUTCOME_COUNTS is not None:
        assert counts == EXPECTED_OUTCOME_COUNTS, counts


def test_determinism_pinned_log():
    a = _campaign(rt.apply, SEED, CAMPAIGN_CASES)
    b = _campaign(rt.apply, SEED, CAMPAIGN_CASES)
    assert a == b, "campaign not deterministic for a fixed seed"
    digest = hashlib.sha256("\n".join(a).encode()).hexdigest()
    if EXPECTED_LOG_SHA256 is not None:
        assert digest == EXPECTED_LOG_SHA256, (
            "campaign log changed - generator or runtime edit; update the"
            " pin only with a reviewed generator change")


def _boundary_corpus() -> list[tuple[str, object, object, object]]:
    """name, state, move, expected: 'ACCEPT' or failure class (None =
    the pinned structural malformed_request)."""
    full = _castling_state("K", set(VALUES) - {"K"})
    rows = []

    def add(name, state, move, expected):
        rows.append((name, state, move, expected))

    # accept boundaries
    add("attacked-rook-from", _castling_state("K") | {"attacked": ["h1"]},
        {"type": "castle", "right": "K"}, "ACCEPT")
    add("attacked-b1-not-king-path", _castling_state("Q") | {"attacked": ["b1"]},
        {"type": "castle", "right": "Q"}, "ACCEPT")
    add("all-rights-held", full, {"type": "castle", "right": "K"}, "ACCEPT")
    add("no-rights-king-move", {"rights": NONE, "occupied": {}, "attacked": [],
                                "side_to_move": "w"},
        {"type": "king_move", "side": "w"}, "ACCEPT")
    add("rook-capture-on-unheld-home", _good_state(random.Random(1), "Kk"),
        {"type": "rook_capture_on", "square": "a8"}, "ACCEPT")
    # class boundaries
    add("rights-empty", _castling_state("K") | {"rights": ""},
        {"type": "castle", "right": "K"}, "rights_malformed")
    add("rights-wrong-order", _castling_state("K") | {"rights": "QK"},
        {"type": "castle", "right": "K"}, "rights_malformed")
    add("rights-duplicates", _castling_state("K") | {"rights": "KK"},
        {"type": "castle", "right": "K"}, "rights_malformed")
    add("rights-huge", _castling_state("K") | {"rights": "K" * 1000},
        {"type": "castle", "right": "K"}, "rights_malformed")
    add("wrong-home-piece",
        _castling_state("K") | {"occupied": {"e1": "wq", "h1": "wr"}},
        {"type": "castle", "right": "K"}, "rights_inconsistent")
    add("king-attacked", _castling_state("K") | {"attacked": ["e1"]},
        {"type": "castle", "right": "K"}, "through_check")
    add("transit-attacked", _castling_state("K") | {"attacked": ["f1"]},
        {"type": "castle", "right": "K"}, "through_check")
    # structural boundaries (pinned choice: malformed_request, class None)
    add("rights-list", _castling_state("K") | {"rights": ["K"]},
        {"type": "castle", "right": "K"}, None)
    add("occupied-list", _castling_state("K") | {"occupied": ["e1"]},
        {"type": "castle", "right": "K"}, None)
    add("occupied-nonstr-value",
        _castling_state("K") | {"occupied": {"e1": "wk", "h1": 7}},
        {"type": "castle", "right": "K"}, None)
    add("occupied-undeclared-square",
        _castling_state("K") | {"occupied": {"e1": "wk", "h1": "wr", "d4": "wp"}},
        {"type": "castle", "right": "K"}, None)
    add("attacked-dict", _castling_state("K") | {"attacked": {"e1": True}},
        {"type": "castle", "right": "K"}, None)
    add("attacked-nonstr-member", _castling_state("K") | {"attacked": [7]},
        {"type": "castle", "right": "K"}, None)
    add("unicode-side", _castling_state("K") | {"side_to_move": "ｗ"},
        {"type": "castle", "right": "K"}, None)
    add("castle-other-sides-turn", _castling_state("K") | {"side_to_move": "b"},
        {"type": "castle", "right": "K"}, None)
    add("state-none", None, {"type": "castle", "right": "K"}, None)
    add("move-none", _castling_state("K"), None, None)
    add("move-string", _castling_state("K"), "castle", None)
    add("castle-right-undeclared", _castling_state("K"),
        {"type": "castle", "right": "X"}, None)
    add("rook-from-undeclared-square", _good_state(random.Random(2)),
        {"type": "rook_move_from", "square": "e4"}, None)
    # string-token grammar siblings (mutant: str-only token validation)
    for tok in ("w", "wkk", "WK", "xk", "wz", "", " wk", "wk "):
        st = _castling_state("K")
        st["occupied"] = {**st["occupied"], "e1": tok}
        add(f"token-grammar-{tok!r}", st, {"type": "castle", "right": "K"}, None)
    # duplicate attacked lists on valid squares (mutant: no dup check)
    for att in (["e1", "e1"], ["f1", "f1", "g1"], ["h1"] * 3):
        st = _castling_state("K")
        st["attacked"] = att
        add(f"attacked-dup-{att!r}", st, {"type": "castle", "right": "K"}, None)
    return rows


def test_fault_boundary_corpus():
    corpus = _boundary_corpus()
    assert len(corpus) == 36, len(corpus)  # pinned corpus size
    for name, state, move, expected in corpus:
        before = copy.deepcopy(state)
        rec = _classify(rt.apply, state, move)
        if expected == "ACCEPT":
            assert rec.startswith("accept:"), (
                f"{name}: boundary legal input rejected: {rec}")
            _check_accept(before, move, json.loads(rec[len("accept:"):]), name)
        else:
            want = ("reject:None:malformed_request" if expected is None
                    else f"reject:{expected}:{FAILURE_MAPPING[expected]['error']}")
            assert rec == want, f"{name}: {rec}, expected {want}"
        assert state == before, f"{name}: input mutated"


def _faulty_king_keeps_rights(state, move):
    out = rt.apply(state, move)
    if type(move) is dict and move.get("type") == "king_move":
        out["rights"] = state["rights"]  # fault: king move loses nothing
    return out


def _faulty_rook_event_keeps_rights(state, move):
    out = rt.apply(state, move)
    if type(move) is dict and move.get("type") in (
            "rook_move_from", "rook_capture_on"):
        out["rights"] = state["rights"]  # fault: rook event loses nothing
    return out


def _faulty_castle_keeps_rights(state, move):
    out = rt.apply(state, move)
    if type(move) is dict and move.get("type") == "castle":
        out["rights"] = state["rights"]  # fault: castle loses nothing
    return out


def _faulty_castle_rook_stays(state, move):
    out = rt.apply(state, move)
    if type(move) is dict and move.get("type") == "castle":
        path = PATHS[move["right"]]
        out["occupied"] = dict(out["occupied"])
        out["occupied"][path["rook_from"]] = out["occupied"].pop(path["rook_to"])
    return out


def _faulty_skip_validation(state, move):
    try:
        return rt.apply(state, move)
    except rt.CastlingError:
        return dict(state) if type(state) is dict else {"echo": True}  # fault


def _faulty_mutates_input(state, move):
    out = rt.apply(copy.deepcopy(state), move)
    if type(state) is dict:
        state["side_to_move"] = "x"  # fault: mutate the caller's state
    return out


FAULTS = {
    "king_keeps_rights": (_faulty_king_keeps_rights,
                          "loss event did not remove exactly the mapped rights"),
    "rook_event_keeps_rights": (_faulty_rook_event_keeps_rights,
                                "loss event did not remove exactly the mapped"
                                " rights"),
    "castle_keeps_rights": (_faulty_castle_keeps_rights,
                            "castle must remove BOTH of the side's rights"),
    "castle_rook_stays": (_faulty_castle_rook_stays,
                          "rook did not relocate to the contract rook_to square"),
    "skip_validation": (_faulty_skip_validation, "expected reject:"),
    "mutates_input": (_faulty_mutates_input, "input mutated"),
}


def test_injected_faults_detected_by_own_check():
    """Every injected fault MUST trip the campaign detector that owns it:
    accept-path invariants catch transition faults, the valid-input
    acceptance check catches swallowed rejections, and the no-mutation
    assert catches input writes. A fault passing its own campaign is a
    fuzz weakness, not a green run."""
    for name, (fn, owning_message) in FAULTS.items():
        with pytest.raises(AssertionError) as ei:
            _campaign(fn, SEED + 17, 300)
        assert owning_message in str(ei.value), (
            f"fault {name} was NOT caught by its owning check"
            f" ({owning_message!r}); got: {ei.value}")


WRONG_CODE = {cls: next(c for c in sorted(ERROR_ENUM) if c != m["error"])
              for cls, m in FAILURE_MAPPING.items()}


def _wrong_code_apply(cls_to_break):
    def apply(state, move):
        try:
            return rt.apply(state, move)
        except rt.CastlingError as err:
            if err.failure_class == cls_to_break:
                err.code = (WRONG_CODE[cls_to_break] if cls_to_break is not None
                            else "illegal_move")  # fault: code swapped
            raise
    return apply


def _trigger_for(cls: str | None):
    """One contract-invalid input per failure class (None = structural)."""
    if cls == "rights_malformed":
        return _castling_state("K") | {"rights": "KK"}, {"type": "castle", "right": "K"}
    if cls == "rights_inconsistent":
        st = _castling_state("K")
        del st["occupied"]["h1"]
        return st, {"type": "castle", "right": "K"}
    if cls == "path_blocked":
        st = _castling_state("K")
        st["occupied"]["f1"] = "wp"
        return st, {"type": "castle", "right": "K"}
    if cls == "king_unmoved_required":
        st = _castling_state("K")
        st["rights"] = _canonical(set(VALUES) - {"K", "Q"})
        return st, {"type": "castle", "right": "K"}
    if cls == "through_check":
        return (_castling_state("K") | {"attacked": ["e1"]},
                {"type": "castle", "right": "K"})
    if cls is None:
        st = _castling_state("K")
        del st["attacked"]
        return st, {"type": "castle", "right": "K"}
    raise AssertionError(f"no trigger pinned for {cls}")


def test_class_to_code_mapping_enforced_per_class():
    """Sibling sweep: for EVERY declared failure class (and the pinned
    structural None class), a runtime that reports the right class with
    a wrong (in-enum) code must fail the campaign's own shape check -
    the mapping is normative, not incidental."""
    assert set(FAILURE_CLASSES) == set(FAILURE_MAPPING), (
        "every declared class needs a trigger and a mapping pin")
    for cls in sorted(FAILURE_CLASSES) + [None]:
        state, move = _trigger_for(cls)
        # baseline: the true runtime rejects with the mapped code
        rec = _classify(rt.apply, state, move)
        want = ("reject:None:malformed_request" if cls is None
                else f"reject:{cls}:{FAILURE_MAPPING[cls]['error']}")
        assert rec == want, cls
        # faulted: right class, wrong code -> _classify must refuse it
        match = ("structural defects must be" if cls is None
                 else "must emit code")
        with pytest.raises(AssertionError, match=match):
            _classify(_wrong_code_apply(cls), state, move)


TE_SEED = SEED + 101
TE_CASES = 400
EXPECTED_TE_COUNTS: dict[str, int] | None = {
    "accept": 193,
    "reject:None:malformed_request": 207,
}


def _te_campaign(seed: int, n: int) -> list[str]:
    """turn_effect fuzz: castle moves accept with the exact
    contract-derived linkage; anything else is the pinned structural
    malformed_request (class None)."""
    rng = random.Random(seed)
    log = []
    for i in range(n):
        right = rng.choice(VALUES)
        state = _castling_state(right)
        if rng.random() < 0.5:
            move = {"type": "castle", "right": right}
            rec = _classify(rt.turn_effect, state, move)
            assert rec.startswith("accept:"), f"te case {i}: castle rejected"
            got = json.loads(rec[len("accept:"):])
            assert got == {
                "halfmove_clock": "increment",
                "fullmove_number": ("increment" if right.islower() else "same"),
            }, f"te case {i}: linkage wrong for {right}"
        else:
            move = rng.choice([
                {"type": "king_move", "side": _side_of(right)},
                {"type": "rook_move_from", "square": sorted(LOSS_ROOK_FROM)[0]},
                {"type": "rook_capture_on", "square": sorted(LOSS_CAPTURE_ON)[0]},
                {"type": "castle", "right": "X"},
                {"type": "castle"},
                "castle", None, 7,
            ])
            rec = _classify(rt.turn_effect, state, move)
            assert rec == "reject:None:malformed_request", (
                f"te case {i}: non-castle move -> {rec}")
        log.append(rec)
    return log


def test_turn_effect_fuzz_pinned():
    log = _te_campaign(TE_SEED, TE_CASES)
    assert log == _te_campaign(TE_SEED, TE_CASES), "turn_effect fuzz unstable"
    counts: dict[str, int] = {}
    for rec in log:
        key = rec if rec.startswith("reject") else "accept"
        counts[key] = counts.get(key, 0) + 1
    if EXPECTED_TE_COUNTS is not None:
        assert counts == EXPECTED_TE_COUNTS, counts
