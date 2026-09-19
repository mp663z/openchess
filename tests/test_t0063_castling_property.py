"""T0063: castling unit/property battery for the T0062 runtime.

Where the T0060 fixture pins REVIEWED cases, this battery pins
PROPERTIES over seeded deterministic generated inputs (no new
dependencies):

- transition invariants: loss events remove exactly the mapped right(s)
  and never regain (irrevocability); castles relocate king+rook to the
  contract path squares, lose BOTH of the side's rights, and never
  mutate attacked/side_to_move; apply NEVER mutates its input.
- fail-closed fuzz across every attack surface (state shape, rights
  grammar, occupancy, move shape/domain, attack points) with exact
  per-category count pins and per-case failure-class discrimination
  incl. the normative class-to-code mapping. v2: structural VALUE
  families added - wrong containers/types for every state field,
  malformed occupied keys/tokens, malformed attacked containers/
  entries/duplicates/off-domain squares, and opposite-turn castling -
  all the pinned structural choice (malformed_request, class None).
  A deterministic sibling matrix sweeps each family exhaustively.
- exact field-set sibling class: every missing/extra state field and
  move key-set violation is the pinned structural choice
  (malformed_request, failure_class None) with no mutation.
- identity properties: rights changes always change identity;
  occupancy/attacked/side changes never do; the projection is exactly
  {"rights": ...}.
- turn_effect: castle-side matrix over all four rights; non-castle
  moves are the pinned structural choice.
"""

from __future__ import annotations

import contextlib
import copy
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

SEED = 20260919
FUZZ_CASES = 600
CATEGORIES = [
    "rights_grammar",
    "right_not_held",
    "home_occupancy",
    "path_blocked",
    "through_check",
    "state_field_set",
    "move_shape",
    "move_domain",
    "state_container",
    "rights_type",
    "occupied_container",
    "occupied_key",
    "occupied_token",
    "attacked_shape",
    "side_to_move_shape",
    "wrong_turn",
]
EXPECTED_CATEGORY_COUNTS_PIN = {
    "rights_grammar": 34,
    "right_not_held": 32,
    "home_occupancy": 45,
    "path_blocked": 41,
    "through_check": 38,
    "state_field_set": 30,
    "move_shape": 35,
    "move_domain": 35,
    "state_container": 31,
    "rights_type": 41,
    "occupied_container": 42,
    "occupied_key": 29,
    "occupied_token": 42,
    "attacked_shape": 41,
    "side_to_move_shape": 36,
    "wrong_turn": 48,
}
CLASS_FOR_CATEGORY = {
    "rights_grammar": "rights_malformed",
    "right_not_held": "king_unmoved_required",
    "home_occupancy": "rights_inconsistent",
    "path_blocked": "path_blocked",
    "through_check": "through_check",
    "state_field_set": None,      # pinned structural choice: malformed_request, class None
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


def _side_of(right: str) -> str:
    return "w" if right.isupper() else "b"


def _canonical(held: set) -> str:
    if not held:
        return NONE
    return "".join(c for c in ORDERING if c in held)


def _home_pieces(right: str) -> dict:
    side = _side_of(right)
    return {HOME[right]["king"]: side + "k", HOME[right]["rook"]: side + "r"}


def _good_state(rng: random.Random, rights: str | None = None) -> dict:
    if rights is None:
        rights = _canonical({v for v in VALUES if rng.random() < 0.7})
    occ = {}
    for r in VALUES:
        if r in rights:
            occ.update(_home_pieces(r))
    side = rng.choice(["w", "b"])
    return {"rights": rights, "occupied": occ, "attacked": [], "side_to_move": side}


def _castling_state(right: str, extra_rights: set | None = None) -> dict:
    held = {right} | (extra_rights or set())
    return {"rights": _canonical(held), "occupied": _home_pieces(right),
            "attacked": [], "side_to_move": _side_of(right)}


def _assert_error_shape(err: rt.CastlingError) -> None:
    assert type(err.code) is str and err.code in ERROR_ENUM
    assert err.failure_class is None or (
        type(err.failure_class) is str and err.failure_class in FAILURE_CLASSES
    )
    assert type(err.retryable) is bool
    assert type(err.args[0]) is str and err.args[0].strip()
    if err.failure_class is not None:
        assert err.code == FAILURE_MAPPING[err.failure_class]["error"]


def test_loss_event_invariants_seeded():
    rng = random.Random(SEED + 1)
    for _ in range(200):
        rights = _canonical({v for v in VALUES if rng.random() < 0.8})
        state = _good_state(rng, rights)
        held = set() if rights == NONE else set(rights)
        kind = rng.choice(["king_move", "rook_move_from", "rook_capture_on"])
        if kind == "king_move":
            side = rng.choice(["w", "b"])
            move = {"type": "king_move", "side": side}
            loss = set(LOSS_KING[side])
        elif kind == "rook_move_from":
            sq = rng.choice(sorted(LOSS_ROOK_FROM))
            move = {"type": "rook_move_from", "square": sq}
            loss = {LOSS_ROOK_FROM[sq]}
        else:
            sq = rng.choice(sorted(LOSS_CAPTURE_ON))
            move = {"type": "rook_capture_on", "square": sq}
            loss = {LOSS_CAPTURE_ON[sq]}
        before = copy.deepcopy(state)
        got = rt.apply(state, move)
        assert state == before, "loss event mutated input"
        assert got is not state
        # exactly the mapped right(s) go; nothing else changes
        assert got["rights"] == _canonical(held - loss)
        assert got["occupied"] == state["occupied"]
        assert got["attacked"] == state["attacked"]
        assert got["side_to_move"] == state["side_to_move"]
        # irrevocability: a right once absent never reappears
        assert set(got["rights"]) - {NONE} <= held
        got2 = rt.apply(got, move)
        assert set(got2["rights"]) - {NONE} <= (set() if got["rights"] == NONE
                                                else set(got["rights"]))


def test_castle_success_invariants_all_rights():
    for right in VALUES:
        side = _side_of(right)
        state = _castling_state(right, extra_rights=set(VALUES) - {right})
        before = copy.deepcopy(state)
        got = rt.apply(state, {"type": "castle", "right": right})
        assert state == before, f"{right}: castle mutated input"
        assert got is not state
        path = PATHS[right]
        # king and rook relocated to the contract path squares
        assert got["occupied"].get(path["king_to"]) == side + "k", right
        assert got["occupied"].get(path["rook_to"]) == side + "r", right
        assert HOME[right]["king"] not in got["occupied"], right
        assert HOME[right]["rook"] not in got["occupied"], right
        # a castle is a king move: BOTH of the side's rights go
        assert got["rights"] == _canonical(set(VALUES) - set(LOSS_KING[side])), right
        # attacked and side_to_move never change
        assert got["attacked"] == state["attacked"], right
        assert got["side_to_move"] == state["side_to_move"], right
        # exactly the four declared fields out
        assert set(got) == {"rights", "occupied", "attacked", "side_to_move"}, right


def _corruptions(rng: random.Random):
    for _ in range(FUZZ_CASES):
        category = CATEGORIES[rng.randrange(len(CATEGORIES))]
        right = rng.choice(VALUES)
        state = _castling_state(right)
        move = {"type": "castle", "right": right}
        if category == "rights_grammar":
            bad = rng.choice(["", "KK", "QK", "KX", "-K", "kK", "KQkqKQ"])
            state["rights"] = bad
        elif category == "right_not_held":
            state["rights"] = _canonical(set(VALUES) - {right, *_side_rights(right)})
        elif category == "home_occupancy":
            home = HOME[right]
            sq = rng.choice([home["king"], home["rook"]])
            if rng.random() < 0.5:
                del state["occupied"][sq]
            else:
                state["occupied"][sq] = rng.choice(["wn", "bn", "wr", "bq"])
                if state["occupied"][sq] in (_side_of(right) + "k", _side_of(right) + "r"):
                    state["occupied"][sq] = "wn"
        elif category == "path_blocked":
            sq = rng.choice(PATHS[right]["empty_required"])
            state["occupied"][sq] = rng.choice(["wn", "bn", "wp", "bp"])
        elif category == "through_check":
            home, path = HOME[right], PATHS[right]
            sq = rng.choice([home["king"], path["king_to"], *path["king_transit"]])
            state["attacked"] = [sq]
        elif category == "state_field_set":
            f = rng.choice(["rights", "occupied", "attacked", "side_to_move"])
            if rng.random() < 0.5:
                del state[f]
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
                {"type": "king_move", "side": "x"},
                {"type": "rook_move_from", "square": "e4"},
                "castle", 7, None,
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
                                                "WK", "xk", "wz", ["wk"], ""])
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
            state["side_to_move"] = ("b" if _side_of(right) == "w" else "w")
        yield category, state, move


def _side_rights(right: str) -> list:
    return LOSS_KING[_side_of(right)]


def test_fail_closed_fuzz():
    rng = random.Random(SEED + 2)
    counts: dict[str, int] = {}
    for category, state, move in _corruptions(rng):
        counts[category] = counts.get(category, 0) + 1
        before = copy.deepcopy(state)
        with pytest.raises(rt.CastlingError) as ei:
            rt.apply(state, move)
        _assert_error_shape(ei.value)
        assert ei.value.failure_class == CLASS_FOR_CATEGORY[category], (
            f"{category}: failed as {ei.value.failure_class}")
        assert state == before, f"{category}: rejected input mutated"
    if EXPECTED_CATEGORY_COUNTS_PIN is not None:
        assert counts == EXPECTED_CATEGORY_COUNTS_PIN, counts


def test_state_and_move_field_set_exact():
    """The pinned structural choice: every missing/extra state field and
    every move key-set violation is malformed_request with failure_class
    None - never a raw KeyError/TypeError/traceback."""
    right = "K"
    base = _castling_state(right)
    for field in ("rights", "occupied", "attacked", "side_to_move"):
        st = {k: v for k, v in base.items() if k != field}
        with pytest.raises(rt.CastlingError) as ei:
            rt.apply(st, {"type": "castle", "right": right})
        _assert_error_shape(ei.value)
        assert ei.value.failure_class is None and ei.value.code == "malformed_request"
    for extra in ("x", "right ", "Rights"):
        st = dict(base)
        st[extra] = 1
        with pytest.raises(rt.CastlingError) as ei:
            rt.apply(st, {"type": "castle", "right": right})
        assert ei.value.failure_class is None and ei.value.code == "malformed_request"
    for bad_move in ({"type": "castle"},
                     {"type": "castle", "right": right, "x": 1},
                     {"type": "king_move"},
                     {"type": "rook_move_from"},
                     {"type": "rook_capture_on", "square": "h1", "y": 2}):
        with pytest.raises(rt.CastlingError) as ei:
            rt.apply(base, bad_move)
        assert ei.value.failure_class is None and ei.value.code == "malformed_request"


def test_identity_properties_seeded():
    rng = random.Random(SEED + 3)
    for _ in range(100):
        state = _good_state(rng)
        ident = rt.identity(state)
        expected = ({"rights": state["rights"]}
                    if C["identity"]["rights_participate"] else {})
        assert ident == expected  # EXACT projection, no extra keys
        other = copy.deepcopy(state)
        other["occupied"] = {}
        other["attacked"] = ["e1"] if state["attacked"] != ["e1"] else []
        other["side_to_move"] = "b" if state["side_to_move"] == "w" else "w"
        assert rt.identity(other) == ident, (
            "occupancy/attacked/side changes must never change identity")
        if state["rights"] != "K":
            other2 = copy.deepcopy(state)
            other2["rights"] = "K"
            assert rt.identity(other2) != ident, (
                "rights changes must always change identity")


def test_turn_effect_matrix():
    for right in VALUES:
        state = _castling_state(right)
        got = rt.turn_effect(state, {"type": "castle", "right": right})
        assert got == {
            "halfmove_clock": "increment",
            "fullmove_number": "increment" if right.islower() else "same",
        }, right
    # non-castle moves are the pinned structural choice
    state = _castling_state("K")
    for move in ({"type": "king_move", "side": "w"},
                 {"type": "rook_move_from", "square": "h1"},
                 {"type": "rook_capture_on", "square": "h1"}):
        with pytest.raises(rt.CastlingError) as ei:
            rt.turn_effect(state, move)
        assert ei.value.failure_class is None and ei.value.code == "malformed_request"


def test_state_value_structural_matrix():
    """Deterministic sibling sweep (v2 remediation): every wrong
    container/type/off-domain family for each state field, malformed
    occupied keys and tokens, malformed attacked entries/duplicates,
    and opposite-turn castling - each is exactly the pinned structural
    choice: CastlingError, code malformed_request, failure_class None,
    exact-bool retryable, nonempty message, input never mutated. This
    matrix independently kills runtime weakening of _validate_state
    (token validation, duplicate rejection) and the opposite-side
    castle rejection."""
    right = "K"
    base = _castling_state(right)
    move = {"type": "castle", "right": right}
    cases = []

    def add(name, state, mv=move):
        cases.append((name, state, mv))

    # state containers
    for bad in (None, [], "w", 7, {"w"}, [1, 2], True):
        add(f"state-container-{bad!r}", bad)
    # rights wrong container/type (grammar-valid content, wrong type)
    for bad in (7, None, True, ["K"], b"K", {"r": "K"}, 1.5):
        st = dict(base)
        st["rights"] = bad
        add(f"rights-type-{bad!r}", st)
    # occupied wrong container
    for bad in (None, [], "e1", 7, True, ["e1"], {("e1",)}):
        st = dict(base)
        st["occupied"] = bad
        add(f"occupied-container-{bad!r}", st)
    # occupied keys: wrong type / off-domain / near-miss
    for bad in (7, None, True, "e4", "E1", "e1 ", ("e1",), 1.5):
        st = dict(base)
        st["occupied"] = {**base["occupied"], bad: "wp"}
        add(f"occupied-key-{bad!r}", st)
    # occupied tokens: wrong type / wrong length / bad color / bad piece
    for bad in (7, None, True, ["wk"], "w", "wkk", "", "WK", "xk", "wz",
                " wk", "wk "):
        st = dict(base)
        st["occupied"] = {**base["occupied"], "e1": bad}
        add(f"occupied-token-{bad!r}", st)
    # attacked wrong container
    for bad in (None, "e1", 7, True, {"e1": True}, {"e1"}):
        st = dict(base)
        st["attacked"] = bad
        add(f"attacked-container-{bad!r}", st)
    # attacked entries: wrong type / unhashable / off-domain / near-miss
    for bad in ([7], [None], [True], [["e1"]], [{"e1": 1}], ["e4"],
                ["E1"], ["e1 ", "f1"], ["e4", "e4"]):
        st = dict(base)
        st["attacked"] = bad
        add(f"attacked-entry-{bad!r}", st)
    # attacked duplicates (valid squares, duplicated)
    for bad in (["e1", "e1"], ["f1", "f1", "g1"], ["h1"] * 3):
        st = dict(base)
        st["attacked"] = bad
        add(f"attacked-dup-{bad!r}", st)
    # side_to_move wrong container/type/value
    for bad in (7, None, True, "W", "B", "", "ｗ", ["w"], "white"):
        st = dict(base)
        st["side_to_move"] = bad
        add(f"side-{bad!r}", st)
    # opposite-turn castling, every right
    for r in VALUES:
        st = _castling_state(r)
        st["side_to_move"] = "b" if _side_of(r) == "w" else "w"
        add(f"wrong-turn-{r}", st, {"type": "castle", "right": r})

    assert len(cases) == 72, len(cases)  # pinned matrix size
    for name, state, mv in cases:
        before = copy.deepcopy(state)
        with pytest.raises(rt.CastlingError) as ei:
            rt.apply(state, mv)
        _assert_error_shape(ei.value)
        assert ei.value.failure_class is None, (
            f"{name}: structural defect classified {ei.value.failure_class}")
        assert ei.value.code == "malformed_request", name
        assert state == before, f"{name}: input mutated"


def test_rollback_never_mutates_across_fuzz():
    rng = random.Random(SEED + 4)
    for category, state, move in _corruptions(rng):
        before = copy.deepcopy(state)
        with contextlib.suppress(rt.CastlingError):
            rt.apply(state, move)
        assert state == before, f"{category}: apply mutated a rejected input"
