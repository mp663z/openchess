"""T0190: conflict-detector fuzz/fault campaign against graph.conflict (T0188).

T0189 pins the independent model on 300 seeded plus enumerated triples. This task
adds a larger, independently seeded campaign with these duties:

1. CLASSIFICATION: every case ends exactly one of a model-equal report or a typed
   ConflictError of a contract class with its mapped code and no chained exception.
2. CORRUPTION: ten tampers of one state entry (retype, bad digest, wrong key,
   extra/missing field, bad FEN, unknown variant, non-dict record, dict subclass,
   str-subclass key) in each of base, left and right are GUARANTEED invalid by an
   independent predicate and must fail closed as malformed_conflict_record, the first
   invalid state in base, left, right order deciding, with every input untouched.
3. LINKED FAULTS: the contract's closed linked errors (VariantError, DigestError,
   ValueError) map to a typed refusal with no chained exception; any other exception
   from a linked call propagates unchanged and never becomes a verdict.
4. FAULT INJECTION: wrappers with one planted defect (conflict dropped, sides swapped,
   input mutated, witness order reversed, divergent base ignored, raw escape) must be
   caught by the T0189 checker on their own category.
5. DETERMINISM: outcome log hash is pinned.
"""

# ruff: noqa: E501
from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import types
from pathlib import Path

import pytest

import graph.conflict as cf
from graph.position_digest import DigestError
from tests import test_t0189_conflict_unit_property as t189
from tools.variant_runtime import VariantError

SEEDS = (190, 1900, 19000)
POSITIONS = (0, 1, 2)
DIGEST = re.compile(r"pdv1:[0-9a-f]{64}")


def _entry_valid(key, rec):
    """Independent predicate: is this single state entry well formed?"""
    if type(key) is not str or type(rec) is not dict:
        return False
    if set(map(type, rec)) != {str} or set(rec) != {"variant", "digest", "snapshot_fen"}:
        return False
    if any(type(v) is not str for v in rec.values()):
        return False
    return (
        key in t189.KEYS
        and DIGEST.fullmatch(rec["digest"]) is not None
        and any(
            rec["snapshot_fen"] == r["snapshot_fen"] and rec["variant"] == r["variant"]
            for r in t189.BASE_RECS
        )
    )


def _state_valid(state):
    return type(state) is dict and all(_entry_valid(k, v) for k, v in dict.items(state))


class _StrSub(str):
    pass


class _DictSub(dict):
    pass


def _tampers():
    def pick(state, rng):
        return rng.choice(sorted(state))

    def retype(state, rng):
        state = copy.deepcopy(state)
        key = pick(state, rng)
        state[key][rng.choice(["variant", "digest", "snapshot_fen"])] = rng.choice(
            [None, 5, b"x", ["a"]]
        )
        return state

    def bad_digest(state, rng):
        state = copy.deepcopy(state)
        state[pick(state, rng)]["digest"] = rng.choice(["", "pdv1:xyz", "PDV1:" + "0" * 64,
                                                        "pdv1:" + "A" * 64, "pdv1:" + "0" * 63])  # fmt: skip
        return state

    def wrong_key(state, rng):
        state = copy.deepcopy(state)
        key = pick(state, rng)
        state[key + "x"] = state.pop(key)
        return state

    def extra_field(state, rng):
        state = copy.deepcopy(state)
        state[pick(state, rng)]["note"] = "x"
        return state

    def missing_field(state, rng):
        state = copy.deepcopy(state)
        del state[pick(state, rng)][rng.choice(["variant", "digest", "snapshot_fen"])]
        return state

    def bad_fen(state, rng):
        state = copy.deepcopy(state)
        state[pick(state, rng)]["snapshot_fen"] = rng.choice(["", "not a fen", "8/8/8/8/8/8/8/8 w - - 0 1 x"])  # fmt: skip
        return state

    def unknown_variant(state, rng):
        state = copy.deepcopy(state)
        state[pick(state, rng)]["variant"] = rng.choice(["chess960", "", "Standard"])
        return state

    def non_dict_record(state, rng):
        state = copy.deepcopy(state)
        state[pick(state, rng)] = rng.choice([None, [], "rec", 3])
        return state

    def dict_subclass(state, rng):
        state = copy.deepcopy(state)
        if rng.random() < 0.5:
            return _DictSub(state)
        key = pick(state, rng)
        state[key] = _DictSub(state[key])
        return state

    def str_subclass_key(state, rng):
        state = copy.deepcopy(state)
        key = pick(state, rng)
        state[_StrSub(key)] = state.pop(key)
        return state

    return {k: v for k, v in locals().items() if callable(v) and k != "pick"}


TAMPERS = _tampers()


def _outcome(module, base, left, right):
    got = t189._detect(module, base, left, right)
    assert not ({"raw", "chained", "wrong-code"} & set(got)), got
    return got


def campaign(seed, n=150):
    triples = t189._triples(seed=seed, n=n)
    log = []
    for specs in triples:
        states = [t189._state(s) for s in specs]
        got = _outcome(cf, *states)
        log.append(got.get("err") or sorted(got["conflicts"]))
    return triples, log


def _hash(log):
    return hashlib.sha256(json.dumps(log, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("seed", SEEDS)
def test_campaign_matches_the_model_and_covers_every_conflict_kind(seed):
    triples, _log = campaign(seed)
    assert t189._problems(cf, triples) == []
    kinds = set()
    for specs in triples:
        got = _outcome(cf, *[t189._state(s) for s in specs])
        kinds |= {w["kind"] for w in got.get("conflicts", {}).values()}
    assert kinds >= {"added_differently", "both_changed_differently", "changed_vs_removed"}


def test_divergent_base_is_typed_in_every_side_pairing():
    base = t189._state({3: 0, 4: 1})
    other = t189._state({3: 2})
    for left, right in ((base, other), (other, base), (base, base)):
        assert _outcome(cf, copy.deepcopy(base), copy.deepcopy(left), copy.deepcopy(right)) == {
            "err": t189.DB
        }


def test_campaign_is_deterministic_and_its_log_is_pinned():
    first = _hash(campaign(SEEDS[0], 100)[1])
    assert first == _hash(campaign(SEEDS[0], 100)[1])
    assert first != _hash(campaign(SEEDS[0] + 1, 100)[1])
    assert first == PINNED_LOG_HASH


PINNED_LOG_HASH = "0189dda66361003c628e42247f1bef58befa0fc48ad04bc54666c2184a3cee03"


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("name", sorted(TAMPERS))
def test_every_tamper_is_invalid_by_predicate_and_fails_closed_in_every_position(name, position):
    rng = random.Random(f"{name}/{position}")
    for _ in range(25):
        specs = [{i: rng.choice((0, 1)) for i in range(3, 6)} for _ in range(3)]
        for spec in specs:
            spec.setdefault(3, 0)
        states = [t189._state(s) for s in specs]
        assert all(_state_valid(s) for s in states)
        states[position] = TAMPERS[name](states[position], rng)
        assert not _state_valid(states[position]), name  # the generator really corrupts
        before = [copy.deepcopy(s) for s in states]
        got = _outcome(cf, *states)
        assert got == {"err": t189.MCR}, (name, got)
        assert [type(s) for s in states] == [type(s) for s in before]
        assert states == before


def test_first_invalid_state_in_base_left_right_order_decides():
    rng = random.Random(1)
    good = t189._state({3: 0})
    bad_a = TAMPERS["retype"](good, rng)
    bad_b = TAMPERS["bad_fen"](good, rng)
    assert _outcome(cf, bad_a, bad_b, good) == {"err": t189.MCR}
    assert _outcome(cf, good, bad_b, bad_a) == {"err": t189.MCR}
    same = t189._state({3: 0})
    assert _outcome(cf, good, same, bad_a) == {"err": t189.MCR}  # validation precedes divergence


# -- linked faults -----------------------------------------------------------------


@pytest.mark.parametrize("exc_type", [VariantError, DigestError, ValueError])
@pytest.mark.parametrize("target", ["make_record", "parse_position"])
def test_closed_linked_errors_become_one_typed_unchained_refusal(monkeypatch, exc_type, target):
    def boom(*args, **kwargs):
        if exc_type is VariantError:
            raise VariantError("malformed_request", "forged")
        if exc_type is DigestError:
            raise DigestError("malformed_position", "malformed_request")
        raise exc_type("forged")

    states = [t189._state({3: 0}), t189._state({3: 1}), t189._state({3: 2})]
    before = copy.deepcopy(states)
    monkeypatch.setattr(cf, target, boom)
    got = t189._detect(cf, *states)
    assert got == {"err": t189.MCR}, got
    assert states == before


@pytest.mark.parametrize("exc_type", [RuntimeError, KeyError, KeyboardInterrupt, SystemExit])
def test_foreign_linked_exceptions_propagate_and_never_become_a_verdict(monkeypatch, exc_type):
    states = [t189._state({3: 0}), t189._state({3: 1}), t189._state({3: 2})]
    before = copy.deepcopy(states)

    def boom(*args, **kwargs):
        raise exc_type("foreign")

    monkeypatch.setattr(cf, "parse_position", boom)
    assert t189._detect(cf, *states) == {"raw": exc_type.__name__}
    assert states == before
    monkeypatch.undo()
    assert t189._detect(cf, *states) == t189._model(*before)  # no poisoning


# -- planted faults ----------------------------------------------------------------


def _wrap(detect):
    return types.SimpleNamespace(
        ConflictDetector=lambda: types.SimpleNamespace(detect=detect),
        ConflictError=cf.ConflictError,
    )


def _real(base, left, right):
    return cf.ConflictDetector().detect(base, left, right)


def _drops_conflict(base, left, right):
    out = _real(base, left, right)
    if out.get("conflicts"):
        out["conflicts"].pop(sorted(out["conflicts"])[0])
    return out


def _swaps_sides(base, left, right):
    return _real(base, right, left)


def _mutates_input(base, left, right):
    out = _real(base, left, right)
    if left:
        left.pop(sorted(left)[0])
    return out


def _reversed_order(base, left, right):
    out = _real(base, left, right)
    out["conflicts"] = dict(reversed(list(out["conflicts"].items())))
    return out


def _ignores_divergent_base(base, left, right):
    try:
        return _real(base, left, right)
    except cf.ConflictError as exc:
        if exc.failure_class == t189.DB:
            return {"base_id": "gs1:" + "0" * 64, "left_id": "", "right_id": "", "conflicts": {}}
        raise


def _raw_escape(base, left, right):
    if not base:
        raise KeyError("empty")
    return _real(base, left, right)


class CrashError(Exception):
    """A foreign exception from the detector under test. Not an AssertionError, and never a
    catch: t189._detect folds foreign exceptions into {"raw": name}, which t189._problems then
    reads as a violation, so a crash would pass for a semantic kill."""


def _strict_problems(module, triples):
    for specs in triples:
        states = [t189._state(s) for s in specs]
        try:
            module.ConflictDetector().detect(*states)
        except cf.ConflictError:
            pass
        except Exception as exc:
            raise CrashError(f"{type(exc).__name__}: {exc}") from exc
    return t189._problems(module, triples)


FAULTS = {
    "conflict-dropped": _drops_conflict,
    "sides-swapped": _swaps_sides,
    "input-mutated": _mutates_input,
    "witness-order-reversed": _reversed_order,
    "divergent-base-ignored": _ignores_divergent_base,
}


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_each_planted_fault_is_caught_by_the_checker(name):
    triples = t189._triples(seed=SEEDS[0], n=120) + t189._enumerated()[:120]
    assert _strict_problems(_wrap(FAULTS[name]), triples) != [], name


# -- left == right and digest-only rows: literal expectations ------------------------------

_SRC = Path(cf.__file__).read_text()


def _mutant_cf(old, new):
    assert _SRC.count(old) == 1, old
    ns = {"__name__": "mutant_conflict", "__file__": cf.__file__}
    exec(compile(_SRC.replace(old, new), "mutant_conflict", "exec"), ns)
    return ns


def _report(ns, base, left, right):
    """The detector's report; ConflictError is a typed outcome, anything else propagates and
    fails the test (a foreign exception is never a kill)."""
    try:
        return ns["ConflictDetector"]().detect(base, left, right)
    except ns["ConflictError"] as exc:
        return {"err": exc.failure_class}


def _k(i):
    return t189.KEYS[i]


# name -> (base spec, left spec, right spec, expected conflicts as {key index: (kind, left, right)})
ROWS = {
    # left == right != base: an identical outcome, never divergent_base, never a conflict
    "identical-sides-digest-change": ({3: 0}, {3: 1}, {3: 1}, {}),
    "identical-sides-added": ({3: 0}, {3: 0, 4: 0}, {3: 0, 4: 0}, {}),
    "identical-sides-removed": ({3: 0, 4: 0}, {3: 0}, {3: 0}, {}),
    # same identity, same snapshot: only the digest differs
    "digest-only-both-changed": ({3: 0}, {3: 1}, {3: 2}, {3: ("both_changed_differently", 1, 2)}),
    "digest-only-both-added": (
        {4: 0},
        {3: 1, 4: 0},
        {3: 2, 4: 0},
        {3: ("added_differently", 1, 2)},
    ),
    "digest-only-one-side-changed": ({3: 0}, {3: 1}, {3: 0, 4: 0}, {}),
}


def _expected(spec):
    return {
        _k(i): {"kind": kind, "left": t189._version(i, lv), "right": t189._version(i, rv)}
        for i, (kind, lv, rv) in spec.items()
    }


@pytest.mark.parametrize("name", sorted(ROWS))
def test_acceptance_rows_hold_on_production(name):
    base, left, right, conflicts = ROWS[name]
    states = [t189._state(s) for s in (base, left, right)]
    before = copy.deepcopy(states)
    got = _report(vars(cf), *states)
    assert "err" not in got, got
    assert got["conflicts"] == _expected(conflicts)
    assert got["left_id"] == t189._model_id(states[1]) and got["right_id"] == t189._model_id(
        states[2]
    )
    assert (got["left_id"] == got["right_id"]) == (left == right)
    assert states == before


MUTANTS_ROWS = {
    "equal-sides-treated-as-divergent": (
        ("if base_id in (left_id, right_id):", "if base_id in (left_id, right_id) or left_id == right_id:"),
        ("identical-sides-digest-change", "identical-sides-added", "identical-sides-removed"),
    ),
    "digest-ignored-in-change-derivation": (
        ("elif base[key] != rec:", 'elif {k: v for k, v in base[key].items() if k != "digest"} != {k: v for k, v in rec.items() if k != "digest"}:'),
        ("digest-only-both-changed",),
    ),
    "digest-ignored-in-witness-comparison": (
        ("if lrec != rrec:", 'if {k: v for k, v in lrec.items() if k != "digest"} != {k: v for k, v in rrec.items() if k != "digest"}:'),
        ("digest-only-both-changed", "digest-only-both-added"),
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(MUTANTS_ROWS))
def test_each_row_mutant_is_killed_by_a_semantic_row(name):
    (old, new), rows = MUTANTS_ROWS[name]
    ns = _mutant_cf(old, new)
    for row in rows:
        base, left, right, conflicts = ROWS[row]
        states = [t189._state(s) for s in (base, left, right)]
        got = _report(ns, *states)
        assert got.get("conflicts") != _expected(conflicts), (name, row, got)


def test_a_raw_escape_is_a_crash_never_a_catch():
    triples = t189._triples(seed=SEEDS[0], n=120) + t189._enumerated()[:120]
    with pytest.raises(CrashError):
        _strict_problems(_wrap(_raw_escape), triples)


# -- validation rows: one malformed field at a time, literal typed refusal ----------------


def _tamper_snapshot_clocks(rec):
    # same identity (counters are not identity), digest well formed, not the canonical text
    return dict(rec, snapshot_fen=rec["snapshot_fen"].rsplit(" ", 2)[0] + " 5 9")


def _tamper_digest_trailing_newline(rec):
    return dict(rec, digest=rec["digest"] + "\n")


def _tamper_digest_trailing_text(rec):
    return dict(rec, digest=rec["digest"] + "x")


def _tamper_renamed_field(rec):
    # same field count as a record, one field renamed
    return {("note" if k == "digest" else k): v for k, v in rec.items()}


VALIDATION_ROWS = {
    "snapshot-not-canonical": _tamper_snapshot_clocks,
    "digest-trailing-newline": _tamper_digest_trailing_newline,
    "digest-trailing-text": _tamper_digest_trailing_text,
    "renamed-field-same-count": _tamper_renamed_field,
}


def _tampered_triple(name):
    states = [t189._state({3: 0}), t189._state({3: 1}), t189._state({3: 2})]
    states[1] = {k: VALIDATION_ROWS[name](v) for k, v in states[1].items()}
    return states


@pytest.mark.parametrize("name", sorted(VALIDATION_ROWS))
def test_validation_rows_are_typed_refusals_on_production(name):
    states = _tampered_triple(name)
    before = copy.deepcopy(states)
    assert _report(vars(cf), *states) == {"err": t189.MCR}
    assert states == before


VALIDATION_MUTANTS = {
    "snapshot-text-not-compared": (
        ('if variant != derived["variant"] or snapshot != derived["snapshot_fen"]:', 'if variant != derived["variant"]:'),
        "snapshot-not-canonical",
    ),
    "digest-prefix-match-only": (
        ("_DIGEST_RE.fullmatch(digest)", "_DIGEST_RE.match(digest)"),
        "digest-trailing-newline",
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(VALIDATION_MUTANTS))
def test_each_validation_mutant_accepts_what_production_refuses(name):
    (old, new), row = VALIDATION_MUTANTS[name]
    got = _report(_mutant_cf(old, new), *_tampered_triple(row))
    assert got != {"err": t189.MCR}, (name, got)


# -- state_id: called directly, literal expectations ---------------------------------------

STATE_ID_RE = re.compile(r"gs1:[0-9a-f]{64}")


def _sid(ns, state):
    try:
        return ns["state_id"](state)
    except ns["ConflictError"] as exc:
        return {"err": exc.failure_class}


def _state_pair():
    return t189._state({3: 0, 4: 0}), t189._state({3: 1, 4: 0})


def test_state_id_is_the_model_id_stable_and_content_sensitive():
    a, b = _state_pair()
    before = copy.deepcopy(a)
    got = _sid(vars(cf), a)
    assert STATE_ID_RE.fullmatch(got)
    assert got == t189._model_id(a)
    assert got == _sid(vars(cf), copy.deepcopy(a))
    assert a == before
    assert _sid(vars(cf), b) == t189._model_id(b) != got


def test_state_id_refuses_a_malformed_state_as_a_typed_error():
    a, _ = _state_pair()
    key = next(iter(a))
    bad = copy.deepcopy(a)
    bad[key]["digest"] = "pdv1:xyz"
    for state in (bad, [], None, _DictSub(a)):
        got = _sid(vars(cf), state)
        assert got == {"err": "malformed_conflict_record"}, got


STATE_ID_MUTANTS = {
    "state-id-skips-validation": (
        ("    frozen = _validated_state(state)\n    return _state_id(frozen)", "    return _state_id(state)"),
        "refusal",
    ),
    "state-id-constant": (
        ("    return _state_id(frozen)\n", '    return "gs1:" + "0" * 64\n'),
        "value",
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(STATE_ID_MUTANTS))
def test_each_state_id_mutant_is_killed(name):
    (old, new), how = STATE_ID_MUTANTS[name]
    ns = _mutant_cf(old, new)
    a, _ = _state_pair()
    if how == "value":
        assert _sid(ns, a) != t189._model_id(a)
    else:
        bad = copy.deepcopy(a)
        bad[next(iter(a))]["digest"] = "pdv1:xyz"
        assert _sid(ns, bad) != {"err": "malformed_conflict_record"}


def test_conflict_error_args_carry_only_the_failure_class():
    exc = cf.ConflictError("malformed_conflict_record", "code-x")
    assert exc.args == ("malformed_conflict_record",)
    assert str(exc) == "malformed_conflict_record"
    assert (exc.failure_class, exc.code) == ("malformed_conflict_record", "code-x")


def test_conflict_error_without_super_init_is_killed_by_args():
    ns = _mutant_cf("        super().__init__(failure_class)\n", "")
    exc = ns["ConflictError"]("malformed_conflict_record", "code-x")
    assert exc.args != ("malformed_conflict_record",)
