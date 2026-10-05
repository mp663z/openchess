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
from pathlib import Path

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


class Crash:
    """A foreign (non-EnPassantError) exception. It is never an expected outcome and never a
    kill: any test that observes one fails."""

    def __init__(self, exc):
        self.exc = exc

    def __repr__(self):
        return f"Crash({type(self.exc).__name__})"


class CrashError(Exception):
    """Raised for a foreign exception; not an AssertionError, so no assertion-based kill sees it."""

    pass


def _check(module, state, move):
    """t72's property detector, except that a foreign exception from the runtime under test
    raises CrashError instead of being read as a P-total violation (t72._check swallows
    arbitrary exceptions into P-total, which would make a crash look like a semantic kill)."""
    try:
        module.apply(copy.deepcopy(state), copy.deepcopy(move))
    except prod.EnPassantError:
        pass
    except Exception as exc:
        raise CrashError(f"{type(exc).__name__}: {exc}") from exc
    return t72._check(module, state, move)


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
            violations |= _check(prod, case_state, move)
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


FAULTS = {
    "target-kept-after-quiet-move": (_keeps_target, "P-lifetime"),
    "input-mutated": (_mutates_input, "P-atomic"),
    "victim-kept": (_victim_kept, "P-delta"),
    "pin-ignored": (_pin_ignored, "P-capture-sound"),
    "side-not-flipped": (_side_kept, "P-side"),
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
            found |= _check(module, case_state, move)
        if expected in found:
            break
    assert expected in found, (name, found)


# -- move corruptions ---------------------------------------------------------------


class _MoveDict(dict):
    pass


class _MoveStr(str):
    pass


def _move_corruptions():
    def extra_key(m):
        return dict(m, extra="x")

    def missing_key(m):
        return {k: v for k, v in m.items() if k != "to"}

    def dict_subclass(m):
        return _MoveDict(m)

    def wrong_type_from(m):
        return dict(m, **{"from": 12})

    def wrong_type_kind(m):
        return dict(m, type=None)

    def undeclared_square(m):
        return dict(m, to="z9")

    def undeclared_kind(m):
        return dict(m, type="castle")

    def str_subclass_square(m):
        return dict(m, **{"from": _MoveStr(m["from"])})

    def not_a_dict(m):
        return [(k, v) for k, v in m.items()]

    return dict(locals())


MOVE_CORRUPTIONS = {k: v for k, v in _move_corruptions().items() if callable(v)}


def _valid_move(move):
    return (
        type(move) is dict
        and set(move) == {"type", "from", "to"}
        and all(type(v) is str for v in move.values())
        and move["type"] in {"pawn-advance", "ep-capture", "quiet"}
        and move["from"] in SQUARES
        and move["to"] in SQUARES
    )


@pytest.mark.parametrize("name", sorted(MOVE_CORRUPTIONS))
def test_move_corruptions_fail_closed_target_malformed(name):
    rng = random.Random(f"move/{name}")
    for _ in range(60):
        state = t72._state(rng)
        move = next(m for m in t72._moves(rng, state) if isinstance(m, dict))
        assert _valid_move(move)
        bad = MOVE_CORRUPTIONS[name](move)
        assert not _valid_move(bad), name
        before_state, before_move = copy.deepcopy(state), copy.deepcopy(bad)
        got = _classify(state, bad)
        assert got[:2] == ("err", "target_malformed"), (name, got)
        assert state == before_state
        assert bad == before_move and type(bad) is type(before_move)


# -- source mutants ----------------------------------------------------------------

_SRC = Path(prod.__file__).read_text()
MALFORMED = ("err", "target_malformed", prod.FAILURE_MAPPING["target_malformed"])


def _exec_mutant(old, new):
    assert _SRC.count(old) == 1, old
    ns = {"__name__": "mutant_en_passant", "__file__": prod.__file__}
    exec(compile(_SRC.replace(old, new), "mutant", "exec"), ns)
    return ns


def _outcome(ns, fn, *args):
    """('ok', value) | ('err', class, code) | Crash. Callers assert no Crash."""
    try:
        return ("ok", ns[fn](*args))
    except ns["EnPassantError"] as exc:
        return ("err", exc.failure_class, exc.code)
    except Exception as exc:  # recorded as a Crash value, asserted against by every caller
        return Crash(exc)


PROD_NS = vars(prod)

_GUARD = {
    "move-shape": ("if not _exact_dict(move, _MOVE_KEYS):", "if False:", "extra_key"),
    "move-kind": (
        "if type(kind) is not str or kind not in _MOVE_TYPES:",
        "if False:",
        "undeclared_kind",
    ),
    "move-squares": (
        "if type(frm) is not str or frm not in _SQUARES or type(to) is not str"
        " or to not in _SQUARES:",
        "if False:",
        "undeclared_square",
    ),
}


def _non_capture_move(rng, state):
    """A well-formed non-capture move whose from-square holds a piece, so a mutant that lets a
    corrupt move through has a legal-looking continuation (and nothing else is wrong)."""
    for m in t72._moves(rng, state):
        if isinstance(m, dict) and m["type"] != "ep-capture" and m["from"] in state["occupied"]:
            return m
    return None


@pytest.mark.parametrize("name", sorted(_GUARD))
def test_move_guard_mutants_are_killed_semantically(name):
    old, new, corruption = _GUARD[name]
    ns = _exec_mutant(old, new)
    rng = random.Random(4242)
    checked = 0
    for _ in range(40):
        state = t72._state(rng)
        move = _non_capture_move(rng, state)
        if move is None:
            continue
        checked += 1
        bad = MOVE_CORRUPTIONS[corruption](move)
        assert not _valid_move(bad)
        got = _outcome(ns, "apply", state, bad)
        assert not isinstance(got, Crash), (name, got)
        # killed only when the mutant accepts the corrupt move or names a wrong class
        assert got != MALFORMED, (name, got)
        assert _outcome(PROD_NS, "apply", state, bad) == MALFORMED
    assert checked >= 20, checked


def test_unmutated_source_exec_is_clean():
    ns = _exec_mutant(
        "if not _exact_dict(move, _MOVE_KEYS):", "if not _exact_dict(move, _MOVE_KEYS):"
    )
    rng = random.Random(4242)
    for _ in range(30):
        state = t72._state(rng)
        move = next(m for m in t72._moves(rng, state) if isinstance(m, dict))
        for gen in MOVE_CORRUPTIONS.values():
            assert _outcome(ns, "apply", state, gen(move)) == MALFORMED


# -- acceptance rows: one violation at a time, literal expected outcomes -------------


def _board(*pieces):
    return {sq: tok for sq, tok in pieces}


KINGS = (("g1", "wk"), ("g8", "bk"))


def _state_of(target, side, *pieces):
    return {
        "ep_target": target,
        "occupied": _board(*KINGS, *pieces),
        "side_to_move": side,
    }


def _ep(frm, to):
    return {"type": "ep-capture", "from": frm, "to": to}


def _str_target_state():
    return _state_of(_StrSub("d6"), "w", ("c5", "wp"), ("d5", "bp"))


def _str_key_state():
    base = _state_of("d6", "w", ("c5", "wp"), ("d5", "bp"))
    return {_StrSub("ep_target") if k == "ep_target" else k: v for k, v in base.items()}


def _str_key_move():
    return {"type": "ep-capture", _StrSub("from"): "c5", "to": "d6"}


ID = "identity_value"
AP = "apply"
CAP_OK = None  # filled by test via apply on the reference state
ROWS = {
    # identity_value: the capturer stands on the lower file (df=-1) of the target
    "identity-left-capturer": (
        ID,
        (_state_of("d6", "w", ("c5", "wp"), ("d5", "bp")),),
        ("ok", "d6"),
    ),
    "identity-right-capturer": (
        ID,
        (_state_of("d6", "w", ("e5", "wp"), ("d5", "bp")),),
        ("ok", "d6"),
    ),
    "identity-left-capturer-black": (
        ID,
        (_state_of("d3", "b", ("c4", "bp"), ("d4", "wp")),),
        ("ok", "d3"),
    ),
    "identity-right-capturer-black": (
        ID,
        (_state_of("d3", "b", ("e4", "bp"), ("d4", "wp")),),
        ("ok", "d3"),
    ),
    # edge files: the only capturer is on the a-file or the h-file
    "identity-a-file-capturer": (
        ID,
        (_state_of("b6", "w", ("a5", "wp"), ("b5", "bp")),),
        ("ok", "b6"),
    ),
    "identity-h-file-capturer": (
        ID,
        (_state_of("g6", "w", ("h5", "wp"), ("g5", "bp")),),
        ("ok", "g6"),
    ),
    "identity-a-file-target-no-capturer": (ID, (_state_of("a6", "w", ("a5", "bp")),), ("ok", "-")),
    "identity-h-file-target-no-capturer": (ID, (_state_of("h6", "w", ("h5", "bp")),), ("ok", "-")),
    # a str-subclass target is outside the closed domain, nothing else is wrong
    "state-target-str-subclass": (AP, (_str_target_state(), _ep("c5", "d6")), MALFORMED),
    "identity-target-str-subclass": (ID, (_str_target_state(),), MALFORMED),
    # a str-subclass key equal to a required key, nothing else is wrong
    "state-key-str-subclass": (AP, (_str_key_state(), _ep("c5", "d6")), MALFORMED),
    "move-key-str-subclass": (
        AP,
        (_state_of("d6", "w", ("c5", "wp"), ("d5", "bp")), _str_key_move()),
        MALFORMED,
    ),
    # target file outside a-h while every other precondition field is well formed
    "capture-target-bad-file": (
        AP,
        (_state_of("i6", "w", ("h5", "wp")), _ep("h5", "e6")),
        ("err", "target_malformed", prod.FAILURE_MAPPING["target_malformed"]),
    ),
    # a three-character target whose rank digit is right and file is right
    "identity-target-overlong": (
        ID,
        (_state_of("d66", "w", ("c5", "wp"), ("d5", "bp")),),
        ("ok", "-"),
    ),
}


@pytest.mark.parametrize("name", sorted(ROWS))
def test_acceptance_rows_hold_on_production(name):
    fn, args, expected = ROWS[name]
    before = copy.deepcopy(args)
    got = _outcome(PROD_NS, fn, *args)
    assert not isinstance(got, Crash), (name, got)
    assert got == expected, (name, got)
    assert args == before


_CAPTURE_GRAMMAR = (
    '    if not _grammar_ok(target):\n        _fail("target_malformed")\n    if target == NONE:'
)
# (old, new) source edits applied together; rows that must expose the edit
MUTANTS = {
    "identity-skips-lower-file": (
        (("for df in (-1, 1):", "for df in (1,):"),),
        ("identity-left-capturer", "identity-left-capturer-black"),
    ),
    "identity-lower-bound-strict": (
        (("if 0 <= f <= 7:", "if 0 < f <= 7:"),),
        ("identity-a-file-capturer",),
    ),
    "identity-upper-bound-strict": (
        (("if 0 <= f <= 7:", "if 0 <= f < 7:"),),
        ("identity-h-file-capturer",),
    ),
    "target-type-check-dropped": (
        (("type(target) is not str or ", ""),),
        ("state-target-str-subclass", "identity-target-str-subclass"),
    ),
    "capture-grammar-check-dropped": (
        ((_CAPTURE_GRAMMAR, "    if target == NONE:"),),
        ("capture-target-bad-file",),
    ),
    "key-type-check-dropped": (
        (("all(type(k) is str for k in dict.keys(value))", "True"),),
        ("state-key-str-subclass", "move-key-str-subclass"),
    ),
    # identity's own grammar guard is redundant with the one inside _capture, so it is only
    # observable when both are gone
    "identity-and-capture-grammar-checks-dropped": (
        (
            (_CAPTURE_GRAMMAR, "    if target == NONE:"),
            ("if target == NONE or not _grammar_ok(target):", "if target == NONE:"),
        ),
        ("identity-target-overlong",),
    ),
}


def _exec_edits(edits):
    src = _SRC
    for old, new in edits:
        assert src.count(old) == 1, old
        src = src.replace(old, new)
    ns = {"__name__": "mutant_en_passant", "__file__": prod.__file__}
    exec(compile(src, "mutant", "exec"), ns)
    return ns


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_each_source_mutant_is_killed_by_a_semantic_row(name):
    edits, rows = MUTANTS[name]
    ns = _exec_edits(edits)
    for row in rows:
        fn, args, expected = ROWS[row]
        got = _outcome(ns, fn, *copy.deepcopy(args))
        assert not isinstance(got, Crash), (name, row, got)
        assert got != expected, (name, row)
    # a mutant with no edit is not killed: the rows really are the discriminator
    clean = _exec_edits(((MUTANTS[name][0][0][0],) * 2,))
    for row in rows:
        fn, args, expected = ROWS[row]
        assert _outcome(clean, fn, *copy.deepcopy(args)) == expected


def test_a_foreign_exception_is_a_crash_never_a_property_violation():
    def raw_escape(state, move):
        if move["type"] == "quiet":
            raise KeyError(move["from"])
        return prod.apply(state, move)

    module = _wrap(raw_escape)
    rng = random.Random(7373)
    with pytest.raises(CrashError):
        for _ in range(400):
            state = t72._state(rng)
            for item in t72._moves(rng, state):
                case_state, move = item if isinstance(item, tuple) else (state, item)
                _check(module, case_state, move)
