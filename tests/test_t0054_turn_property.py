"""T0054: turn unit/property battery for the T0053 runtime.

Where the T0051 fixture pins REVIEWED cases, this battery pins
PROPERTIES over seeded deterministic generated inputs (no new
dependencies):

- transition invariants: side always flips; fullmove +1 exactly when
  black moves; halfmove resets exactly on pawn_move/capture, else +1.
- fail-closed fuzz across every attack surface (state shape, side,
  counters incl. bool/int conflation, move domain incl. the PUBLIC API
  CHOICE pinned per the T0053 verifier note: undeclared and non-string
  move kinds are illegal_transition via the null-move policy,
  termination domain) with exact per-category count pins and per-case
  failure-class discrimination.
- rollback: a rejected transition never mutates its input, over seeded
  invalid inputs.
- termination_for: seeded halfmove sweep across both thresholds, exact
  automatic flags, None below 100.
- identity: seeded counter changes never change identity; side flips
  always do.
"""

from __future__ import annotations

import copy
import random
from pathlib import Path

import pytest
import yaml

from tools import turn_runtime as rt

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "turn.yaml").read_text())
C = DOC["contract"]
TERMINATION_STATES = list(C["termination"]["states"])
FAILURE_MAPPING = dict(C["failure_mapping"])
ERROR_ENUM = set(C["errors"]["closed_enum"])
FAILURE_CLASSES = set(C["failure_classes"])
CLAIM = C["termination"]["fifty_move"]["claim"]
AUTO = C["termination"]["fifty_move"]["automatic"]

SEED = 20260919
FUZZ_CASES = 400
CATEGORIES = [
    "state_not_mapping",
    "no_side_to_move",
    "bad_counter",
    "move_domain",      # the pinned public API choice: -> illegal_transition
    "null_move",
    "terminated_closed",
    "unknown_termination",
]
# exact per-category fuzz distribution for SEED (fixed seed ->
# deterministic; a generator change updates this pin in the same change)
EXPECTED_CATEGORY_COUNTS = {
    "state_not_mapping": 44,
    "no_side_to_move": 63,
    "bad_counter": 44,
    "move_domain": 79,
    "null_move": 57,
    "terminated_closed": 48,
    "unknown_termination": 65,
}
# category -> expected failure class (discrimination pin)
CLASS_FOR_CATEGORY = {
    "state_not_mapping": "bad_counter",
    "no_side_to_move": "no_side_to_move",
    "bad_counter": "bad_counter",
    "move_domain": "illegal_transition",
    "null_move": "illegal_transition",
    "terminated_closed": "illegal_transition",
    "unknown_termination": "unknown_termination",
}

GOOD = {"side_to_move": "w", "halfmove_clock": 7, "fullmove_number": 12}
LEGAL_MOVES = ["quiet", "pawn_move", "capture"]


def _assert_error_shape(err: rt.TurnError) -> None:
    assert type(err.code) is str and err.code in ERROR_ENUM
    assert err.failure_class is None or (
        type(err.failure_class) is str and err.failure_class in FAILURE_CLASSES
    )
    assert type(err.retryable) is bool
    assert type(err.args[0]) is str and err.args[0].strip()


def _good_state(rng: random.Random) -> dict:
    return {
        "side_to_move": rng.choice(["w", "b"]),
        "halfmove_clock": rng.randint(0, 160),
        "fullmove_number": rng.randint(1, 120),
    }


def _corruptions(rng: random.Random):
    for _ in range(FUZZ_CASES):
        category = CATEGORIES[rng.randrange(len(CATEGORIES))]
        state = _good_state(rng)
        if category == "state_not_mapping":
            yield category, rng.choice([None, 7, "x", [1], (1, 2)]), "quiet", None
        elif category == "no_side_to_move":
            bad = dict(state)
            which = rng.randrange(3)
            if which == 0:
                del bad["side_to_move"]
            elif which == 1:
                bad["side_to_move"] = rng.choice(["white", "W", "", "wb"])
            else:
                bad["side_to_move"] = rng.choice([None, 1, True, ["w"]])
            yield category, bad, "quiet", None
        elif category == "bad_counter":
            bad = dict(state)
            field = rng.choice(["halfmove_clock", "fullmove_number"])
            bad[field] = rng.choice([-1, True, False, 1.5, "3", None, [0]])
            if field == "fullmove_number" and bad[field] is False:
                bad[field] = 0  # False == 0 already covered; keep 0 explicit
            yield category, bad, "quiet", None
        elif category == "move_domain":
            yield category, state, rng.choice(
                ["garbage", "castle", "PAWN_MOVE", "", "null_move", "resign"]), None
        elif category == "null_move":
            yield category, state, "null", None
        elif category == "terminated_closed":
            yield category, state, rng.choice(LEGAL_MOVES), rng.choice(TERMINATION_STATES)
        else:
            # None is NOT a corruption here: it is apply_move's default
            # (unterminated) and must stay accepted
            yield category, state, "quiet", rng.choice(
                ["draw_by_mutual_confusion", "CHECKMATE", "", "draw", 42, 3.5])


def test_transition_invariants_seeded():
    rng = random.Random(SEED + 1)
    for _ in range(200):
        state = _good_state(rng)
        move = rng.choice(LEGAL_MOVES)
        out = rt.apply_move(state, move)
        assert out["side_to_move"] != state["side_to_move"]
        if state["side_to_move"] == "b":
            assert out["fullmove_number"] == state["fullmove_number"] + 1
        else:
            assert out["fullmove_number"] == state["fullmove_number"]
        if move in ("pawn_move", "capture"):
            assert out["halfmove_clock"] == 0
        else:
            assert out["halfmove_clock"] == state["halfmove_clock"] + 1
        assert set(out) == {"side_to_move", "halfmove_clock", "fullmove_number"}


def test_fail_closed_fuzz():
    rng = random.Random(SEED + 2)
    counts = dict.fromkeys(CATEGORIES, 0)
    for category, state, move, terminated in _corruptions(rng):
        counts[category] += 1
        before = copy.deepcopy(state)
        try:
            rt.apply_move(state, move, terminated)
        except rt.TurnError as err:
            _assert_error_shape(err)
            assert err.failure_class == CLASS_FOR_CATEGORY[category], (
                f"{category} case failed as {err.failure_class}")
            assert err.code == FAILURE_MAPPING[err.failure_class]["error"]
        except Exception as other:  # noqa: BLE001 - the property IS the exception type
            raise AssertionError(
                f"non-TurnError escape: {type(other).__name__}: {other}") from other
        else:
            raise AssertionError(
                f"corrupted input accepted: {category} {state!r} {move!r} {terminated!r}")
        assert state == before, f"{category}: rejected call mutated its input"
    assert counts == EXPECTED_CATEGORY_COUNTS


def test_move_domain_public_api_choice_pinned():
    """T0053 verifier note: undeclared/non-string move kinds map to
    illegal_transition via the null-move policy. Pin it explicitly."""
    state = dict(GOOD)
    for bad_move in ("garbage", "castle", "", 7, None, ["quiet"], True):
        with pytest.raises(rt.TurnError) as ei:
            rt.apply_move(state, bad_move)
        assert ei.value.failure_class == "illegal_transition"
        assert ei.value.code == FAILURE_MAPPING["illegal_transition"]["error"]
    assert state == GOOD


def test_termination_threshold_sweep():
    for hm in range(95, 156):
        state = {"side_to_move": "w", "halfmove_clock": hm, "fullmove_number": 50}
        status = rt.termination_for(state)
        if hm >= AUTO["threshold"]:
            assert status == {"state": AUTO["state"], "automatic": True}
        elif hm >= CLAIM["threshold"]:
            assert status == {"state": CLAIM["state"], "automatic": False}
        else:
            assert status is None


def test_identity_properties_seeded():
    rng = random.Random(SEED + 3)
    for _ in range(100):
        state = _good_state(rng)
        base = rt.identity(state)
        changed_counters = dict(state)
        changed_counters["halfmove_clock"] = rng.randint(0, 200)
        changed_counters["fullmove_number"] = rng.randint(1, 300)
        assert rt.identity(changed_counters) == base
        flipped = dict(state)
        flipped["side_to_move"] = "b" if state["side_to_move"] == "w" else "w"
        assert rt.identity(flipped) != base


def test_state_field_set_exact():
    """The exact three-field state set: every missing required key and
    representative extra keys (single, multiple, unknown, known-shaped)
    are bad_counter with exact shape and no input mutation. Closes the
    sibling-class hole where only non-mapping/missing-side shapes were
    generated."""
    # missing side_to_move is its own declared class; missing counters
    # are bad_counter - both per the contract's failure_mapping
    for missing, expected_class in (
        ("side_to_move", "no_side_to_move"),
        ("halfmove_clock", "bad_counter"),
        ("fullmove_number", "bad_counter"),
    ):
        state = {k: v for k, v in GOOD.items() if k != missing}
        before = dict(state)
        with pytest.raises(rt.TurnError) as ei:
            rt.apply_move(state, "quiet")
        _assert_error_shape(ei.value)
        assert ei.value.failure_class == expected_class, missing
        assert ei.value.code == FAILURE_MAPPING[expected_class]["error"]
        assert state == before
    extra_variants = [
        {"en_passant": "-"},                                  # single unknown
        {"en_passant": "-", "captured": []},                  # multiple unknown
        {"halfmove": 0},                                      # near-miss typo key
        {"side_to_move_alt": "b", "clock": 0, "n": 1},        # three unknown
    ]
    for extra in extra_variants:
        state = {**GOOD, **extra}
        before = dict(state)
        with pytest.raises(rt.TurnError) as ei:
            rt.apply_move(state, "quiet")
        _assert_error_shape(ei.value)
        assert ei.value.failure_class == "bad_counter", extra
        assert state == before
    # sanity: the exact three-field set is accepted
    assert rt.apply_move(dict(GOOD), "quiet")["halfmove_clock"] == GOOD["halfmove_clock"] + 1


def test_rollback_never_mutates_across_fixture_and_fuzz():
    import json

    cases = json.loads((ROOT / "tests" / "fixtures" / "turn" / "cases.json").read_text())
    for case in cases["rollback"] + cases["malformed"]:
        before = copy.deepcopy(case["state"])
        with pytest.raises(rt.TurnError):
            rt.apply_move(case["state"], case["move"], case.get("terminated"))
        assert case["state"] == before
