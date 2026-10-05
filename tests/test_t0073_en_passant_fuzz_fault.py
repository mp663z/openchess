"""T0073: en-passant fuzz/fault campaign against graph.en_passant (T0071).

T0072 pins properties over 700 seeded states. This task runs a larger, independently
seeded campaign with three duties:

1. CLASSIFICATION: every case ends exactly one of accept (the T0072 property detector
   reports no violation) or reject (typed EnPassantError with the contract's class and
   code). Any other exception is a CRASH and fails the suite with the case recorded.
2. CORRUPTION: shape corruptions of the state or move (extra/missing keys, wrong types,
   undeclared squares and tokens, missing or doubled kings, container subclasses) are
   each GUARANTEED invalid by an independent predicate and must fail closed as
   target_malformed, leaving the input bit-identical.
3. FAULT INJECTION: wrappers around the runtime with one planted defect each (target
   kept after a quiet move, input mutated, victim kept, pin ignored, side not flipped,
   raw escape) must be caught by their OWN property, never only a collateral one.
4. DETERMINISM: the campaign is pure in its seed; its classified outcome log hash is pinned.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import types

import pytest

import graph.en_passant as prod
from tests import test_t0072_en_passant_property as t72

SEEDS = (73, 730, 7300)
SQUARES = {t72._sq(f, r) for f in range(8) for r in range(1, 9)}
TOKENS = {c + p for c in "wb" for p in "pnbrqk"}


class _DictSub(dict):
    pass


class _StrSub(str):
    pass


def _valid_state(state):
    """Independent closed-domain predicate for a state."""
    if type(state) is not dict or set(state) != {"ep_target", "occupied", "side_to_move"}:
        return False
    if type(state["side_to_move"]) is not str or state["side_to_move"] not in ("w", "b"):
        return False
    target = state["ep_target"]
    if type(target) is not str:
        return False
    occ = state["occupied"]
    if type(occ) is not dict:
        return False
    if any(type(s) is not str or s not in SQUARES or type(t) is not str or t not in TOKENS
           for s, t in occ.items()):  # fmt: skip
        return False
    return all(sum(1 for t in occ.values() if t == c + "k") == 1 for c in "wb")


def _corruptions():
    """name -> function(state, rng) producing a state that _valid_state refuses."""

    def drop_key(s, rng):
        s = copy.deepcopy(s)
        del s[rng.choice(sorted(s))]
        return s

    def extra_key(s, rng):
        return dict(copy.deepcopy(s), extra=rng.choice([0, "x", None]))

    def retype(s, rng):
        s = copy.deepcopy(s)
        key = rng.choice(sorted(s))
        s[key] = rng.choice([None, 3, 1.5, [], (), b"w", True])
        return s

    def bad_side(s, rng):
        return dict(copy.deepcopy(s), side_to_move=rng.choice(["", "W", "x", "white"]))

    def undeclared_square(s, rng):
        s = copy.deepcopy(s)
        s["occupied"][rng.choice(["i1", "a9", "a0", "zz", "", "e44"])] = "wp"
        return s

    def bad_token(s, rng):
        s = copy.deepcopy(s)
        s["occupied"][rng.choice(sorted(s["occupied"]))] = rng.choice(["xp", "wx", "", "wpp", "WP"])
        return s

    def kingless(s, rng):
        s = copy.deepcopy(s)
        victim = rng.choice("wb") + "k"
        s["occupied"] = {q: t for q, t in s["occupied"].items() if t != victim}
        return s

    def two_kings(s, rng):
        s = copy.deepcopy(s)
        free = sorted(SQUARES - set(s["occupied"]))
        s["occupied"][rng.choice(free)] = rng.choice("wb") + "k"
        return s

    def dict_subclass(s, rng):
        s = copy.deepcopy(s)
        return _DictSub(s) if rng.random() < 0.5 else dict(s, occupied=_DictSub(s["occupied"]))

    def str_subclass(s, rng):
        s = copy.deepcopy(s)
        s["side_to_move"] = _StrSub(s["side_to_move"])
        return s

    return dict(locals())


CORRUPTIONS = {k: v for k, v in _corruptions().items() if callable(v)}


def _classify(state, move):
    """Return ('ok', result) or ('err', class, code); anything else is a crash."""
    try:
        return ("ok", prod.apply(state, move))
    except prod.EnPassantError as exc:
        assert exc.failure_class in t72.CLASSES and exc.code == t72.MAPPING[exc.failure_class]
        return ("err", exc.failure_class, exc.code)


def campaign(seed, n=900):
    rng = random.Random(seed)
    log, violations = [], set()
    for _ in range(n):
        state = t72._state(rng)
        for item in t72._moves(rng, state):
            case_state, move = item if isinstance(item, tuple) else (state, item)
            violations |= t72._check(prod, case_state, move)
            log.append(_classify(case_state, move)[:3])
    return log, violations


def _log_hash(log):
    return hashlib.sha256(json.dumps(log, sort_keys=True, default=repr).encode()).hexdigest()


@pytest.mark.parametrize("seed", SEEDS)
def test_campaign_has_no_property_violation_and_covers_every_class(seed):
    log, violations = campaign(seed)
    assert violations == set()
    kinds = {entry[0] if entry[0] == "ok" else entry[1] for entry in log}
    assert kinds >= {"ok", "target_malformed", "capture_precondition", "pinned_capture"}


def test_campaign_is_deterministic_and_its_log_is_pinned():
    first = _log_hash(campaign(SEEDS[0], 300)[0])
    assert first == _log_hash(campaign(SEEDS[0], 300)[0])
    assert first != _log_hash(campaign(SEEDS[0] + 1, 300)[0])
    assert first == PINNED_LOG_HASH


PINNED_LOG_HASH = "d77a379e412c9c5769dbaf8b57b53ece4e93659a73cca48c4719281512d5a8d9"


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_corruptions_are_invalid_by_predicate_and_fail_closed(seed, name):
    rng = random.Random(f"{seed}/{name}")
    for _ in range(40):
        state = t72._state(rng)
        move = next(m for m in t72._moves(rng, state) if isinstance(m, dict))
        assert _valid_state(state)
        corrupted = CORRUPTIONS[name](state, rng)
        assert not _valid_state(corrupted), name  # the generator really corrupts
        before = copy.deepcopy(corrupted)
        got = _classify(corrupted, move)
        assert got[:2] == ("err", "target_malformed"), (name, got)
        assert corrupted == before
        assert type(corrupted) is type(before)
        identity = None
        try:
            identity = prod.identity_value(corrupted)
        except prod.EnPassantError as exc:
            assert exc.failure_class == "target_malformed"
        assert identity is None


# -- fault injection ---------------------------------------------------------------


def _wrap(apply):
    return types.SimpleNamespace(apply=apply, identity_value=prod.identity_value)


def _keeps_target(state, move):
    out = prod.apply(state, move)
    if move["type"] != "ep-capture":
        out["ep_target"] = (
            state["ep_target"] if state["ep_target"] != t72.NONE else out["ep_target"]
        )
    return out


def _mutates_input(state, move):
    out = prod.apply(state, move)
    state["occupied"].pop(move["from"], None)
    return out


def _victim_kept(state, move):
    out = prod.apply(state, move)
    if move["type"] == "ep-capture":
        victim = move["to"][0] + t72.MOVER[state["side_to_move"]]["captured_rank"]
        out["occupied"][victim] = t72.OTHER[state["side_to_move"]] + "p"
    return out


def _pin_ignored(state, move):
    try:
        return prod.apply(state, move)
    except prod.EnPassantError as exc:
        if exc.failure_class != "pinned_capture":
            raise
        out = copy.deepcopy(state)
        victim = move["to"][0] + t72.MOVER[state["side_to_move"]]["captured_rank"]
        del out["occupied"][move["from"]], out["occupied"][victim]
        out["occupied"][move["to"]] = state["side_to_move"] + "p"
        out["ep_target"] = t72.NONE
        out["side_to_move"] = t72.OTHER[state["side_to_move"]]
        return out


def _side_kept(state, move):
    out = prod.apply(state, move)
    out["side_to_move"] = state["side_to_move"]
    return out


def _raw_escape(state, move):
    if move["type"] == "quiet":
        raise KeyError(move["from"])
    return prod.apply(state, move)


FAULTS = {
    "target-kept-after-quiet-move": (_keeps_target, "P-lifetime"),
    "input-mutated": (_mutates_input, "P-atomic"),
    "victim-kept": (_victim_kept, "P-delta"),
    "pin-ignored": (_pin_ignored, "P-capture-sound"),
    "side-not-flipped": (_side_kept, "P-side"),
    "raw-escape": (_raw_escape, "P-total"),
}


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_each_planted_fault_is_caught_by_its_own_property(name):
    apply, expected = FAULTS[name]
    module = _wrap(apply)
    found = set()
    rng = random.Random(7373)
    for _ in range(400):
        state = t72._state(rng)
        for item in t72._moves(rng, state):
            case_state, move = item if isinstance(item, tuple) else (state, item)
            found |= t72._check(module, case_state, move)
        if expected in found:
            break
    assert expected in found, (name, found)
