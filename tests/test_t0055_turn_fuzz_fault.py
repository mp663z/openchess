"""T0055: turn fuzz/fault campaign against the T0053 runtime.

Where T0054 pins properties over 400 seeded cases, this task runs a
LARGER classified campaign with three added duties:

1. CLASSIFICATION: every campaign case ends exactly one of accept
   (new state satisfying the contract-derived transition invariants)
   or reject (TurnError with closed-enum code, declared failure class,
   exact-bool retryable, nonempty message). Any other exception type
   is a CRASH and fails the suite with the case recorded - a fuzz that
   cannot tell reject from crash turns real crashes green.
2. FAULT INJECTION: the same campaign detectors are run against
   deliberately faulted apply wrappers (side not flipped, fullmove on
   white, halfmove never resets, validation skipped). Every fault must
   be detected by its OWN campaign check (an invariant violation on
   the accept path or a missing rejection), never a collateral one.
3. DETERMINISM: the campaign is pure in its seed; two runs produce a
   byte-identical classified outcome log, and the log's SHA-256 is
   pinned so an RNG/generator edit cannot silently change coverage.

Every corruption generator GUARANTEES invalidity: a predicate asserts
the produced input actually violates the contract, so a weakened
generator (a "corruption" that yields a legal input) fails the suite.
All semantics are derived from data/contracts/turn.yaml, never
hardcoded: side values, bounds, reset kinds, fullmove rule, thresholds,
termination states, failure classes and the closed error enum.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from pathlib import Path

import pytest
import yaml

from tools import turn_runtime as rt

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "data" / "contracts" / "turn.yaml").read_text())
C = DOC["contract"]
SIDE_VALUES = list(C["state"]["side_values"])
BOUNDS = dict(C["state"]["bounds"])
STATE_FIELDS = list(C["state"]["fields"])
ON_MOVE = dict(C["transition"]["on_move"])
RESET_WHEN = list(ON_MOVE["halfmove_clock"]["reset_when"])
RESET_TO = ON_MOVE["halfmove_clock"]["reset_to"]
FMW = dict(ON_MOVE["fullmove_number"])
HM_OTHERWISE = dict(ON_MOVE["halfmove_clock"]["otherwise"])
TERMINATION_STATES = list(C["termination"]["states"])
FAILURE_MAPPING = dict(C["failure_mapping"])
ERROR_ENUM = set(C["errors"]["closed_enum"])
FAILURE_CLASSES = set(C["failure_classes"])

SEED = 20260919
CAMPAIGN_CASES = 3000
LEGAL_MOVES = sorted(set(RESET_WHEN) | {"quiet"})
# exact campaign distribution pins for SEED (fixed seed -> deterministic;
# a generator change updates these pins in the same change)
EXPECTED_OUTCOME_COUNTS: dict[str, int] = {
    "accept": 1785,
    "reject:bad_counter:malformed_request": 443,
    "reject:illegal_transition:illegal_transition": 449,
    "reject:no_side_to_move:malformed_request": 219,
    "reject:unknown_termination:unknown_termination": 104,
}
EXPECTED_LOG_SHA256 = "255640975a68c2016b7e14172fb33ec54c413be9f56bd8c89733d0e6056105bf"

BOUNDARY_CORPUS: list[tuple[str, dict, object, object, str]] = []  # built below


class Crash(Exception):
    """A non-TurnError escape: the campaign's crash classification."""


def _classify(apply_fn, state, move, terminated) -> str:
    """Run one case; classify accept / reject:<class>; Crash on escape."""
    try:
        out = apply_fn(state, move, terminated)
    except rt.TurnError as err:
        _assert_error_shape(err)
        return f"reject:{err.failure_class}:{err.code}"
    except Exception as exc:  # noqa: BLE001 - classification, not handling
        raise Crash(f"{type(exc).__name__}: {exc}") from exc
    return "accept:" + json.dumps(out, sort_keys=True)


def _assert_error_shape(err: rt.TurnError) -> None:
    assert type(err.code) is str and err.code in ERROR_ENUM
    assert type(err.failure_class) is str and err.failure_class in FAILURE_CLASSES
    assert type(err.retryable) is bool
    assert type(err.args[0]) is str and err.args[0].strip()
    # the contract's class-to-code mapping is normative: the right class
    # with the wrong code is a mapping violation, not a shaped error
    assert err.code == FAILURE_MAPPING[err.failure_class]["error"], (
        f"class {err.failure_class} must emit code"
        f" {FAILURE_MAPPING[err.failure_class]['error']}, got {err.code}")


def _check_accept(before: dict, move: str, after: dict, where: str) -> None:
    """Contract-derived transition invariants; the accept-path detector."""
    assert type(after) is dict, where
    assert set(after) == set(STATE_FIELDS), f"{where}: accept changed the field set"
    mover = before["side_to_move"]
    assert after["side_to_move"] == next(s for s in SIDE_VALUES if s != mover), (
        f"{where}: side did not flip")
    expect_fm = before["fullmove_number"] + (
        FMW["amount"] if (FMW["when"] == "black") == (mover == "b") else 0)
    assert after["fullmove_number"] == expect_fm, f"{where}: fullmove rule broken"
    if move in RESET_WHEN:
        assert after["halfmove_clock"] == RESET_TO, f"{where}: halfmove not reset"
    else:
        assert after["halfmove_clock"] == before["halfmove_clock"] + HM_OTHERWISE["amount"], (
            f"{where}: halfmove did not increment")


def _good_state(rng: random.Random) -> dict:
    return {
        "side_to_move": rng.choice(SIDE_VALUES),
        "halfmove_clock": rng.randint(0, 200),
        "fullmove_number": rng.randint(1, 150),
    }


CATEGORIES = [
    "state_container", "missing_side", "bad_side_value", "bad_counter_type",
    "bad_counter_range", "field_set", "move_type", "move_kind", "null_move",
    "terminated_closed", "unknown_termination",
]


def _corrupt(rng: random.Random, category: str):
    """Produce one GUARANTEED-invalid input for the category."""
    st = _good_state(rng)
    if category == "state_container":
        state = rng.choice([None, [], "w", 7, {"w"}, [1, 2]])
        return state, rng.choice(LEGAL_MOVES), None
    if category == "missing_side":
        del st["side_to_move"]
        return st, rng.choice(LEGAL_MOVES), None
    if category == "bad_side_value":
        st["side_to_move"] = rng.choice(["x", "", "W", "white", 1, None, True, ["w"]])
        return st, rng.choice(LEGAL_MOVES), None
    if category == "bad_counter_type":
        f = rng.choice(["halfmove_clock", "fullmove_number"])
        st[f] = rng.choice([True, False, 1.5, "7", None, [7], {"v": 7}])
        return st, rng.choice(LEGAL_MOVES), None
    if category == "bad_counter_range":
        f = rng.choice(["halfmove_clock", "fullmove_number"])
        st[f] = BOUNDS[f]["min"] - rng.randint(1, 10**6)
        return st, rng.choice(LEGAL_MOVES), None
    if category == "field_set":
        if rng.random() < 0.5:
            st["halfmove_clok"] = st.pop("halfmove_clock")  # typo = missing + extra
        else:
            st["extra"] = 1
        return st, rng.choice(LEGAL_MOVES), None
    if category == "move_type":
        return st, rng.choice([7, None, [], {}, True, 1.5]), None
    if category == "move_kind":
        bad = rng.choice(["castle", "", "quiet ", "QUIET", "Quiet", "null ", "resign"])
        assert bad not in LEGAL_MOVES and bad != "null"
        return st, bad, None
    if category == "null_move":
        return st, "null", None
    if category == "terminated_closed":
        return st, rng.choice(LEGAL_MOVES), rng.choice(TERMINATION_STATES)
    if category == "unknown_termination":
        bad = rng.choice(["weird", "", "DRAW", 7, True])
        return st, rng.choice(LEGAL_MOVES), bad
    raise AssertionError(f"unknown category {category}")


CLASS_FOR_CATEGORY = {
    "state_container": "bad_counter",
    "missing_side": "no_side_to_move",
    "bad_side_value": "no_side_to_move",
    "bad_counter_type": "bad_counter",
    "bad_counter_range": "bad_counter",
    "field_set": "bad_counter",
    "move_type": "illegal_transition",
    "move_kind": "illegal_transition",
    "null_move": "illegal_transition",
    "terminated_closed": "illegal_transition",
    "unknown_termination": "unknown_termination",
}


def _violates_contract(state, move, terminated, category) -> bool:
    """Guaranteed-invalidity predicate: the corruption must REALLY break
    the contract (a 'corruption' yielding a legal input weakens the
    fuzz silently)."""
    if type(state) is not dict:
        return True
    if set(state) != set(STATE_FIELDS):
        return True
    if type(state.get("side_to_move")) is not str or state["side_to_move"] not in SIDE_VALUES:
        return True
    for f, bound in BOUNDS.items():
        v = state.get(f)
        if type(v) is not int or v < bound["min"]:
            return True
    if terminated is not None:
        return True  # closed-after-termination and unknown-termination both reject
    return type(move) is not str or move not in LEGAL_MOVES


def _campaign(apply_fn, seed: int, n: int) -> list[str]:
    rng = random.Random(seed)
    log = []
    for i in range(n):
        if rng.random() < 0.6:
            state, move, terminated = _good_state(rng), rng.choice(LEGAL_MOVES), None
            before = copy.deepcopy(state)
            rec = _classify(apply_fn, state, move, terminated)
            assert rec.startswith("accept:"), f"case {i}: valid input rejected: {rec}"
            out = json.loads(rec[len("accept:"):])
            _check_accept(before, move, out, f"case {i}")
            assert state == before, f"case {i}: input mutated"
        else:
            category = CATEGORIES[rng.randrange(len(CATEGORIES))]
            state, move, terminated = _corrupt(rng, category)
            assert _violates_contract(state, move, terminated, category), (
                f"case {i}: {category} corruption produced a LEGAL input")
            before = copy.deepcopy(state)
            rec = _classify(apply_fn, state, move, terminated)
            cls = CLASS_FOR_CATEGORY[category]
            assert rec == f"reject:{cls}:{FAILURE_MAPPING[cls]['error']}", (
                f"case {i}: {category} -> {rec}, expected"
                f" reject:{cls}:{FAILURE_MAPPING[cls]['error']}")
            assert state == before, f"case {i}: rejected input mutated"
        log.append(rec)
    return log


def test_campaign_clean_run():
    log = _campaign(rt.apply_move, SEED, CAMPAIGN_CASES)
    counts: dict[str, int] = {}
    for rec in log:
        key = rec if rec.startswith("reject") else "accept"
        counts[key] = counts.get(key, 0) + 1
    if EXPECTED_OUTCOME_COUNTS:
        assert counts == EXPECTED_OUTCOME_COUNTS, counts


def test_determinism_pinned_log():
    a = _campaign(rt.apply_move, SEED, CAMPAIGN_CASES)
    b = _campaign(rt.apply_move, SEED, CAMPAIGN_CASES)
    assert a == b, "campaign not deterministic for a fixed seed"
    digest = hashlib.sha256("\n".join(a).encode()).hexdigest()
    if EXPECTED_LOG_SHA256:
        assert digest == EXPECTED_LOG_SHA256, (
            "campaign log changed - generator or runtime edit; update the"
            " pin only with a reviewed generator change")


def _boundary_corpus() -> list[tuple[str, object, object, object, str]]:
    g = {"side_to_move": "w", "halfmove_clock": 7, "fullmove_number": 12}
    rows = []

    def add(name, state, move, terminated, cls):
        rows.append((name, state, move, terminated, cls))

    add("maxint-halfmove", {**g, "halfmove_clock": 10**30}, "quiet", None, "ACCEPT")
    add("minint-halfmove", {**g, "halfmove_clock": -(10**30)}, "quiet", None, "bad_counter")
    add("bool-true-halfmove", {**g, "halfmove_clock": True}, "quiet", None, "bad_counter")
    add("bool-false-fullmove", {**g, "fullmove_number": False}, "quiet", None, "bad_counter")
    add("float-whole-halfmove", {**g, "halfmove_clock": 7.0}, "quiet", None, "bad_counter")
    add("string-counter", {**g, "halfmove_clock": "7"}, "quiet", None, "bad_counter")
    add("none-counter", {**g, "fullmove_number": None}, "quiet", None, "bad_counter")
    add("unicode-side", {**g, "side_to_move": "ｗ"}, "quiet", None, "no_side_to_move")
    add("uppercase-side", {**g, "side_to_move": "W"}, "quiet", None, "no_side_to_move")
    add("int-side", {**g, "side_to_move": 0}, "quiet", None, "no_side_to_move")
    add("empty-move", g, "", None, "illegal_transition")
    add("near-miss-move", g, "quiet\t", None, "illegal_transition")
    add("int-move", g, 0, None, "illegal_transition")
    add("bool-move", g, True, None, "illegal_transition")
    add("list-move", g, ["quiet"], None, "illegal_transition")
    add("dict-move", g, {"kind": "quiet"}, None, "illegal_transition")
    add("none-move", g, None, None, "illegal_transition")
    add("null-move", g, "null", None, "illegal_transition")
    add("null-suffix", g, "null ", None, "illegal_transition")
    add("terminated-each-state", g, "quiet", TERMINATION_STATES[0], "illegal_transition")
    add("terminated-int", g, "quiet", 7, "unknown_termination")
    add("terminated-bool", g, "quiet", True, "unknown_termination")
    add("terminated-empty", g, "quiet", "", "unknown_termination")
    add("state-list", ["w", 7, 12], "quiet", None, "bad_counter")
    add("state-none", None, "quiet", None, "bad_counter")
    add("state-string", "state", "quiet", None, "bad_counter")
    add("extra-field", {**g, "z": 1}, "quiet", None, "bad_counter")
    add("missing-counter", {"side_to_move": "w", "fullmove_number": 12}, "quiet", None,
        "bad_counter")
    return rows


def test_fault_boundary_corpus():
    corpus = _boundary_corpus()
    assert len(corpus) == 28, len(corpus)  # pinned corpus size
    for name, state, move, terminated, cls in corpus:
        before = copy.deepcopy(state)
        rec = _classify(rt.apply_move, state, move, terminated)
        if cls == "ACCEPT":
            assert rec.startswith("accept:"), f"{name}: boundary legal input rejected: {rec}"
            out = json.loads(rec[len("accept:"):])
            _check_accept(state, move, out, name)
        else:
            expect = f"reject:{cls}:{FAILURE_MAPPING[cls]['error']}"
            assert rec == expect, f"{name}: {rec}, expected {expect}"
        assert state == before, f"{name}: input mutated"


def _faulty_no_flip(state, move, terminated=None):
    out = rt.apply_move(state, move, terminated)
    out["side_to_move"] = state["side_to_move"]  # fault: side never flips
    return out


def _faulty_fullmove_white(state, move, terminated=None):
    out = rt.apply_move(state, move, terminated)
    if state["side_to_move"] == "w":
        out["fullmove_number"] = state["fullmove_number"] + 1  # fault
    return out


def _faulty_no_reset(state, move, terminated=None):
    out = rt.apply_move(state, move, terminated)
    if move in RESET_WHEN:
        out["halfmove_clock"] = state["halfmove_clock"] + 1  # fault: no reset
    return out


def _faulty_skip_validation(state, move, terminated=None):
    try:
        return rt.apply_move(state, move, terminated)
    except rt.TurnError:
        return dict(state) if type(state) is dict else {"echo": True}  # fault: accept


WRONG_CODE = {cls: next(c for c in sorted(ERROR_ENUM) if c != m["error"])
              for cls, m in FAILURE_MAPPING.items()}


def _wrong_code_apply(cls_to_break):
    def apply(state, move, terminated=None):
        try:
            return rt.apply_move(state, move, terminated)
        except rt.TurnError as err:
            if err.failure_class == cls_to_break:
                err.code = WRONG_CODE[cls_to_break]  # fault: class kept, code swapped
            raise
    return apply


def _trigger_for(cls: str):
    g = {"side_to_move": "w", "halfmove_clock": 7, "fullmove_number": 12}
    if cls == "no_side_to_move":
        return {**g, "side_to_move": "x"}, "quiet", None
    if cls == "bad_counter":
        return {**g, "halfmove_clock": True}, "quiet", None
    if cls == "illegal_transition":
        return g, "null", None
    if cls == "unknown_termination":
        return g, "quiet", "weird"
    raise AssertionError(f"no trigger pinned for {cls}")


def test_class_to_code_mapping_enforced_per_class():
    """Sibling sweep: for EVERY declared failure class, a runtime that
    reports the right class with a wrong (in-enum) code must fail the
    campaign's own shape check - the mapping is normative, not
    incidental."""
    assert set(FAILURE_CLASSES) == set(FAILURE_MAPPING), (
        "every declared class needs a trigger and a mapping pin")
    for cls in sorted(FAILURE_CLASSES):
        state, move, terminated = _trigger_for(cls)
        # baseline: the true runtime rejects with the mapped code
        rec = _classify(rt.apply_move, state, move, terminated)
        assert rec == f"reject:{cls}:{FAILURE_MAPPING[cls]['error']}", cls
        # faulted: right class, wrong code -> _classify must refuse it
        with pytest.raises(AssertionError, match="must emit code"):
            _classify(_wrong_code_apply(cls), state, move, terminated)


FAULTS = {
    "no_flip": (_faulty_no_flip, "side did not flip"),
    "fullmove_white": (_faulty_fullmove_white, "fullmove rule broken"),
    "no_reset": (_faulty_no_reset, "halfmove not reset"),
    "skip_validation": (_faulty_skip_validation, "expected reject:"),
}


def test_injected_faults_detected_by_own_check():
    """Every injected fault MUST trip the campaign detector that owns it:
    accept-path invariants catch transition faults; the valid-input
    acceptance check catches swallowed rejections. A fault passing its
    own campaign is a fuzz weakness, not a green run."""
    for name, (fn, owning_message) in FAULTS.items():
        with pytest.raises(AssertionError) as ei:
            _campaign(fn, SEED + 17, 300)
        assert owning_message in str(ei.value), (
            f"fault {name} was NOT caught by its owning check"
            f" ({owning_message!r}); got: {ei.value}")
