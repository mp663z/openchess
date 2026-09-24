"""T0181: deterministic fuzz/fault battery for the production diff (graph/diff.py).

Seeded generators build graph states and diffs from an independent model of
data/contracts/diff.yaml (the state-id derivation and the diff sections are
restated here, never taken from production) and then damage them:

- compute: two states, one of them hostile (non-dict and dict-subclass
  containers, one tampered entry: str-subclass, colliding, int and
  mismatched keys, extra, missing, non-str and str-subclass fields, bad and
  foreign digests, clocks, bad FENs, unknown variants, dict-subclass and
  non-dict records);
- validate: a model diff with one tamper (fields, ids, sections, entries,
  changed witnesses, overlaps, empty/non-empty id agreement), through
  validate_diff and apply;
- apply: a model diff over a damaged base (extra, missing, swapped records,
  added identity already present, forged base id over an absent removed
  identity, lying target id, hostile base);
- state_id: model states and hostile states;
- linked: a linked call (parse_position, identity, digest_fen, or the
  entry listing) raising every BaseException kind or a forged typed error
  of every class the module or its linked modules raise.

Every case must satisfy, against production: no raw escape (only a typed
DiffError from the closed class set with its mapped code, never the forged
object, and no explicit __cause__; __context__ is not asserted); the same
outcome as the contract reference engine (tests.test_t0176_diff_contract)
where the reference applies, and where the
outcome is independently known, that outcome; every input identical by
value, exact type, key order and object identity after every call; outputs
equal the model in canonical key order and share no object with any input;
no poisoning; determinism.

The T0176 reference checks only the digest grammar, not that a digest
belongs to its record, so the foreign-digest cases (a well-formed digest of
another position) skip the reference comparison and are checked against the
independently known outcome only.

Mutation check: _probe runs a fixed slice of every generator against a
source mutant of graph/diff.py. Every entry in MUTANTS must turn the probe
red; every entry in _EQUIVALENT_EDITS must keep it green.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import random
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph import diff  # noqa: E402
from graph import position_digest as _digest  # noqa: E402
from graph.node import make_record, record_identity  # noqa: E402
from tests import test_t0176_diff_contract as _reference  # noqa: E402
from tools import variant_runtime as _variant  # noqa: E402
from tools.diff_contract_lint import FAILURE_MAPPING  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402

DIFF_SOURCE = (ROOT / "graph" / "diff.py").read_text()
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
KEYS = tuple(record_identity(record) for record in RECORDS)
CLASSES = frozenset(FAILURE_MAPPING)
MDR, CB, UI, DT = ("malformed_diff_record", "conflicting_base", "unknown_identity",
                   "divergent_target")
GENERATORS = ("compute", "validate", "apply", "state_id", "linked")
FUZZ_SEEDS = {"compute": 256, "validate": 280, "apply": 192, "state_id": 96, "linked": 0}
PROBE_SEEDS = 48


class _Str(str):
    pass


class _Dict(dict):
    pass


class _Colliding:
    """Hashes like a real name; while armed (only around the call) comparing
    it raises, so any comparison before an exact-str key guard escapes raw."""

    armed = False

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __deepcopy__(self, memo):
        return self

    def __eq__(self, other):
        if _Colliding.armed:
            raise RuntimeError("hostile __eq__")
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)

    def __lt__(self, other):
        if _Colliding.armed:
            raise RuntimeError("hostile __lt__")
        return False

    __gt__ = __lt__


# -- the independent model --------------------------------------------------------

def _state(indices):
    return {KEYS[i]: dict(RECORDS[i]) for i in indices}


def _state_id(state):
    text = "".join(f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key]))
                   + "\n" for key in sorted(state))
    return "gs1:" + hashlib.sha256(text.encode()).hexdigest()


def _model_diff(base, target):
    return {"base_id": _state_id(base), "target_id": _state_id(target),
            "added": {k: dict(target[k]) for k in sorted(target) if k not in base},
            "removed": {k: dict(base[k]) for k in sorted(base) if k not in target},
            "changed": {}}


def _subset(rng, low=0):
    n = rng.randint(low, len(RECORDS))
    return sorted(rng.sample(range(len(RECORDS)), n))


def _case_of(label, op, args, expect=None, model=None, patch=None):
    return {"label": label, "op": op, "args": args, "expect": expect, "model": model,
            "patch": patch}


# -- entry and container damage ---------------------------------------------------

ENTRY_TAMPERS = ("strsub-key", "colliding-key", "int-key", "key-mismatch", "extra-field",
                 "missing-field", "nonstr-field", "strsub-field", "digest-grammar",
                 "foreign-digest", "clocks", "bad-fen", "unknown-variant", "record-subclass",
                 "record-not-dict", "short-fen", "record-colliding-key",
                 "record-strsub-field-key", "strsub-digest", "nonstr-digest")
CONTAINERS = ("none", "list", "subclass", "tuple", "str", "int")


def _damage_entry(state, kind, rng):
    """One damaged entry in STATE (adding one when it is empty)."""
    if not state:
        state[KEYS[0]] = dict(RECORDS[0])
    key = rng.choice(sorted(state))
    rec = state[key]
    other = RECORDS[(KEYS.index(key) + 1) % len(RECORDS)]
    if kind == "strsub-key":
        state[_Str(key)] = state.pop(key)
    elif kind == "colliding-key":
        state[_Colliding(key)] = dict(rec)
    elif kind == "int-key":
        state[1] = state.pop(key)
    elif kind == "key-mismatch":
        free = [k for k in KEYS if k not in state]
        if free:
            state[free[0]] = state.pop(key)
        else:
            other_key = next(k for k in sorted(state) if k != key)
            state[key], state[other_key] = state[other_key], rec
    elif kind == "extra-field":
        rec["zz"] = "1"
    elif kind == "missing-field":
        del rec["digest"]
    elif kind == "nonstr-field":
        rec["variant"] = None
    elif kind == "strsub-field":
        rec["snapshot_fen"] = _Str(rec["snapshot_fen"])
    elif kind == "digest-grammar":
        rec["digest"] = rec["digest"].upper()
    elif kind == "foreign-digest":
        rec["digest"] = other["digest"]
    elif kind == "clocks":
        rec["snapshot_fen"] = rec["snapshot_fen"][:-3] + "5 9"
    elif kind == "bad-fen":
        rec["snapshot_fen"] = "8/8/8/8/8/8/8 w - - 0 1"
    elif kind == "unknown-variant":
        rec["variant"] = "atomic960"
    elif kind == "record-subclass":
        state[key] = _Dict(rec)
    elif kind == "record-not-dict":
        state[key] = list(rec.items())
    elif kind == "strsub-digest":
        rec["digest"] = _Str(rec["digest"])
    elif kind == "nonstr-digest":
        rec["digest"] = None
    elif kind == "record-colliding-key":
        rec[_Colliding("digest")] = "x"
    elif kind == "record-strsub-field-key":
        rec[_Str("digest")] = rec.pop("digest")
    else:
        rec["snapshot_fen"] = " ".join(rec["snapshot_fen"].split(" ")[:5])
    return state


def _container(kind, state):
    return {"none": None, "list": list(state.items()), "subclass": _Dict(state),
            "tuple": tuple(state), "str": "", "int": 0}[kind]


# -- generators ------------------------------------------------------------------------

def _gen_compute(seed):
    rng = random.Random(f"compute-{seed}")
    base, target = _state(_subset(rng)), _state(_subset(rng))
    kinds = ("valid",) * 2 + ENTRY_TAMPERS + CONTAINERS
    kind = kinds[seed % len(kinds)]
    side = seed // len(kinds) % 2
    if kind == "valid":
        return _case_of("compute:valid", "compute", (base, target), ("ok",),
                        _model_diff(base, target))
    pair = [base, target]
    if kind in CONTAINERS:
        pair[side] = _container(kind, pair[side])
    else:
        pair[side] = _damage_entry(pair[side], kind, rng)
    return _case_of(f"compute:{kind}/{('base', 'target')[side]}", "compute", tuple(pair),
                    ("err", MDR))


DIFF_TAMPERS = ("valid", "drop-field", "extra-field", "strsub-field-key", "colliding-field",
                "id-upper", "id-short", "id-strsub", "id-none", "section-list",
                "section-subclass", "added-entry", "removed-entry", "changed-not-dict",
                "changed-missing-target", "changed-equal", "changed-other-identity",
                "changed-bad-record", "changed-colliding-key", "changed-strsub-key",
                "overlap-added-removed", "empty-unequal-ids", "nonempty-equal-ids",
                "diff-subclass", "diff-none", "changed-witness-subclass",
                "changed-extra-key", "witness-strsub-key", "witness-colliding-key",
                "changed-section-list", "changed-section-subclass", "ids-swapped")


def _valid_diff(rng):
    base = _state(_subset(rng))
    target = _state(_subset(rng))
    if base == target:
        target = _state([i for i in range(len(RECORDS)) if KEYS[i] not in base][:1]) or {}
    return base, target, _model_diff(base, target)


def _tamper_diff(d, kind, rng, base, target):
    """(diff, expected class or None for 'ok')."""
    k0, k1 = KEYS[0], KEYS[1]
    witness = {"base": dict(RECORDS[0]), "target": dict(RECORDS[0])}
    if kind == "valid":
        return d, None
    if kind == "drop-field":
        del d[rng.choice(sorted(d))]
    elif kind == "extra-field":
        d["zz"] = {}
    elif kind == "strsub-field-key":
        d[_Str("changed")] = d.pop("changed")
    elif kind == "colliding-field":
        d[_Colliding("added")] = {}
    elif kind == "id-upper":
        d["base_id"] = d["base_id"].upper()
    elif kind == "id-short":
        d["target_id"] = d["target_id"][:-1]
    elif kind == "id-strsub":
        d["base_id"] = _Str(d["base_id"])
    elif kind == "id-none":
        d["target_id"] = None
    elif kind == "section-list":
        d["added"] = list(d["added"].items())
    elif kind == "section-subclass":
        d["removed"] = _Dict(d["removed"])
    elif kind == "added-entry":
        d["added"] = _damage_entry(d["added"], ENTRY_TAMPERS[rng.randrange(len(ENTRY_TAMPERS))],
                                   rng)
    elif kind == "removed-entry":
        d["removed"] = _damage_entry(d["removed"],
                                     ENTRY_TAMPERS[rng.randrange(len(ENTRY_TAMPERS))], rng)
    elif kind == "changed-not-dict":
        d["changed"] = {k0: [dict(RECORDS[0]), dict(RECORDS[0])]}
    elif kind == "changed-missing-target":
        d["changed"] = {k0: {"base": dict(RECORDS[0])}}
    elif kind == "changed-equal":
        d["changed"] = {k0: witness}
    elif kind == "changed-other-identity":
        d["changed"] = {k0: {"base": dict(RECORDS[0]), "target": dict(RECORDS[1])}}
    elif kind == "changed-bad-record":
        d["changed"] = {k0: {"base": dict(RECORDS[0]),
                             "target": dict(RECORDS[0], digest=RECORDS[1]["digest"])}}
    elif kind == "changed-colliding-key":
        d["changed"] = {_Colliding(k0): witness}
    elif kind == "changed-strsub-key":
        d["changed"] = {_Str(k0): witness}
    elif kind == "changed-witness-subclass":
        d["changed"] = {k0: _Dict(witness)}
    elif kind == "changed-extra-key":
        d["changed"] = {k0: dict(witness, zz=dict(RECORDS[0]))}
    elif kind == "witness-strsub-key":
        d["changed"] = {k0: {_Str("base"): dict(RECORDS[0]),
                             "target": dict(RECORDS[0], zz="1")}}
    elif kind == "witness-colliding-key":
        d["changed"] = {k0: {"base": dict(RECORDS[0]), _Colliding("target"): {},
                             "target": dict(RECORDS[0], zz="1")}}
    elif kind == "changed-section-list":
        d["changed"] = []
    elif kind == "changed-section-subclass":
        d["changed"] = _Dict()
    elif kind == "overlap-added-removed":
        d["added"] = {k1: dict(RECORDS[1])}
        d["removed"] = {k1: dict(RECORDS[1])}
    elif kind == "empty-unequal-ids":
        d.update(added={}, removed={}, changed={})
        return d, DT
    elif kind == "nonempty-equal-ids":
        d["target_id"] = d["base_id"]
    elif kind == "diff-subclass":
        d = _Dict(d)
    elif kind == "diff-none":
        d = None
    else:
        d["base_id"], d["target_id"] = d["target_id"], d["base_id"]
        return d, "swap"
    return d, MDR


def _gen_validate(seed):
    rng = random.Random(f"validate-{seed}")
    base, target, d = _valid_diff(rng)
    kind = DIFF_TAMPERS[seed % len(DIFF_TAMPERS)]
    through = ("validate", "apply")[seed // len(DIFF_TAMPERS) % 2]
    d, cls = _tamper_diff(d, kind, rng, base, target)
    if cls == "swap":
        # a consistent-looking diff whose ids are swapped: valid shape; over
        # the real base the base id no longer matches
        expect = ("ok",) if through == "validate" else ("err", CB)
    elif cls is None:
        expect = ("ok",)
    else:
        expect = ("err", cls)
    if through == "validate":
        return _case_of(f"validate:{kind}", "validate", (d,), expect)
    model = dict(target) if expect == ("ok",) else None
    return _case_of(f"apply-diff:{kind}", "apply", (d, base), expect, model)


BASE_TAMPERS = ("valid", "extra-record", "missing-record", "added-present",
                "forged-removed-absent", "target-lies", "base-none", "base-subclass",
                "base-entry", "base-is-target", "forged-changed-absent", "empty-to-empty")


def _gen_apply(seed):
    rng = random.Random(f"apply-{seed}")
    kind = BASE_TAMPERS[seed % len(BASE_TAMPERS)]
    base, target, d = _valid_diff(rng)
    expect, model = ("err", CB), None
    if kind == "valid":
        expect, model = ("ok",), dict(target)
    elif kind == "extra-record":
        extra = [i for i in range(len(RECORDS)) if KEYS[i] not in base and KEYS[i] not in target]
        if extra:
            base[KEYS[extra[0]]] = dict(RECORDS[extra[0]])
        else:
            base.pop(next(iter(base)), None) if base else base.update(_state([0]))
    elif kind == "missing-record":
        if base:
            base.pop(sorted(base)[-1])
        else:
            base.update(_state([0]))
    elif kind == "added-present":
        # the base id is forged to match the base; an added identity is present
        base = _state([0, 1])
        d = {"base_id": _state_id(base), "target_id": _state_id(_state([0, 1, 2])),
             "added": _state([1, 2]), "removed": {}, "changed": {}}
    elif kind == "forged-removed-absent":
        base = _state([0])
        d = {"base_id": _state_id(base), "target_id": _state_id({}),
             "added": {}, "removed": _state([3]), "changed": {}}
        expect = ("err", UI)
    elif kind == "forged-changed-absent":
        base = _state([0])
        witness = {"base": dict(RECORDS[3]), "target": dict(RECORDS[3], zz="1")}
        d = {"base_id": _state_id(base), "target_id": _state_id(_state([0, 3])),
             "added": {}, "removed": {}, "changed": {KEYS[3]: witness}}
        expect = ("err", MDR)  # every exact record is identity-derived: no valid witness
    elif kind == "target-lies":
        others = [i for i in range(len(RECORDS)) if KEYS[i] not in target]
        lie = _state_id(_state(others[:1])) if others else _state_id({})
        if lie in (d["base_id"], d["target_id"]):
            lie = _state_id(_state([0, 1, 2, 3, 4, 5, 6]))
        d["target_id"] = lie
        expect = ("err", DT) if lie != d["base_id"] else ("err", MDR)
    elif kind == "base-none":
        base, expect = None, ("err", MDR)
    elif kind == "base-subclass":
        base, expect = _Dict(base), ("err", MDR)
    elif kind == "base-entry":
        base = _damage_entry(base, ENTRY_TAMPERS[seed // len(BASE_TAMPERS)
                                                 % len(ENTRY_TAMPERS)], rng)
        expect = ("err", MDR)
    elif kind == "base-is-target":
        base = dict(target)
    else:
        base = {}
        d = _model_diff({}, {})
        expect, model = ("ok",), {}
    return _case_of(f"apply:{kind}", "apply", (d, base), expect, model)


def _gen_state_id(seed):
    rng = random.Random(f"state-{seed}")
    state = _state(_subset(rng))
    kinds = ("valid",) * 4 + ENTRY_TAMPERS + CONTAINERS
    kind = kinds[seed % len(kinds)]
    if kind == "valid":
        return _case_of("state_id:valid", "state_id", (state,), ("ok",), _state_id(state))
    if kind in CONTAINERS:
        return _case_of(f"state_id:{kind}", "state_id", (_container(kind, state),),
                        ("err", MDR))
    return _case_of(f"state_id:{kind}", "state_id", (_damage_entry(state, kind, rng),),
                    ("err", MDR))


_DIGEST_CLASSES = yaml.safe_load(_digest.CONTRACT.read_text())["contract"]["failures"]["mapping"]
FORGED = {
    **{f"forged-diff-{c}": diff.DiffError(c) for c in sorted(FAILURE_MAPPING)},
    **{f"forged-digest-{c}": _digest.DigestError(c, _DIGEST_CLASSES[c])
       for c in sorted(_DIGEST_CLASSES)},
    **{f"forged-variant-{c}": _variant.VariantError(
        "illegal_position" if c == "illegal_position" else "malformed_request", c, c)
       for c in sorted(_variant.FAILURE_CLASSES)},
    "forged-contract": ContractError("forged"),
}
_KINDS = {f"raise-{k.__name__}": k for k in (ValueError, KeyError, TypeError, RuntimeError,
                                              KeyboardInterrupt, SystemExit, GeneratorExit,
                                              MemoryError, RecursionError)}
LINK_FAULTS = (*_KINDS, *sorted(FORGED))
# the linked calls behind the two boundaries: the record boundary
# (parse_position, identity, digest_fen) and the entry-listing boundary (list)
LINKS = ("parse_position", "identity", "digest_fen", "list")
FUZZ_SEEDS["linked"] = len(LINK_FAULTS) * len(LINKS) * 2


def _gen_linked(seed):
    rng = random.Random(f"linked-{seed}")
    fault = LINK_FAULTS[seed % len(LINK_FAULTS)]
    link = LINKS[seed // len(LINK_FAULTS) % len(LINKS)]
    op = ("compute", "apply")[seed // (len(LINK_FAULTS) * len(LINKS)) % 2]
    error = FORGED[fault] if fault in FORGED else _KINDS[fault]
    base = _state(_subset(rng, 1))
    target = _state(_subset(rng, 1))
    args = (base, target) if op == "compute" else (_model_diff(base, target), base)
    return _case_of(f"linked:{fault}@{link}/{op}", op, args, ("err", MDR),
                    patch=(link, error))


GEN = {"compute": _gen_compute, "validate": _gen_validate, "apply": _gen_apply,
       "state_id": _gen_state_id, "linked": _gen_linked}


@functools.cache
def _case(gen, seed):
    return GEN[gen](seed)


# -- running and checking ---------------------------------------------------------


class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _deep(value):
    """Value, exact type, key order and object identity at every level."""
    if isinstance(value, dict):
        return ("d", type(value).__name__, _Pin(value),
                tuple((type(k).__name__, _Pin(k) if type(k) is _Colliding else k, _deep(v))
                      for k, v in dict.items(value)))
    if isinstance(value, (list, tuple)):
        return ("l", type(value).__name__, _Pin(value),
                tuple(_deep(v) for v in list.__iter__(value)) if isinstance(value, list)
                else tuple(_deep(v) for v in value))
    if type(value) is float and value != value:
        return ("nan",)
    return ("v", type(value).__name__, _Pin(value) if type(value) is frozenset else value)


def _call(module, op, args):
    if module is _reference:
        engine = _reference.DiffEngine()
        return {"compute": engine.compute, "validate": engine.validate_diff,
                "apply": engine.apply}[op](*args)
    return {"compute": module.compute, "validate": module.validate_diff,
            "apply": module.apply, "state_id": module.state_id}[op](*args)


def _raiser(error):
    def stub(*args, **kwargs):
        if isinstance(error, BaseException):
            raise error
        raise error()
    return stub


def _run(module, case):
    """(outcome, before, after, args, raised)."""
    args = copy.deepcopy(case["args"])
    before = _deep(args)
    patch = case["patch"] if module is not _reference else None
    saved = None
    if patch is not None:
        saved = module.__dict__.get(patch[0], _MISSING)
        module.__dict__[patch[0]] = _raiser(patch[1])
    raised = None
    _Colliding.armed = True
    try:
        outcome = ("ok", _call(module, case["op"], args))
    except (diff.DiffError, _reference.DiffError) as error:
        outcome = ("err", error.failure_class, error.code)
        raised = error
    except BaseException as error:  # noqa: BLE001 - raw escape is a defect
        outcome = ("raw", type(error).__name__)
    finally:
        _Colliding.armed = False
        if patch is not None:
            if saved is _MISSING:
                del module.__dict__[patch[0]]
            else:
                module.__dict__[patch[0]] = saved
    return outcome, before, _deep(args), args, raised


_MISSING = object()


def _foreign_digest(value):
    """A record whose variant and FEN are valid but whose digest is another
    record's. The T0176 reference checks the digest grammar only; production
    binds the digest to the linked derivation (linked node validity), so the
    reference does not apply to these cases and the expected class stands."""
    if isinstance(value, dict):
        if set(value) == {"variant", "digest", "snapshot_fen"} and \
                all(type(value[k]) is str for k in value):
            try:
                if make_record(value["variant"], value["snapshot_fen"]) != value:
                    return make_record(value["variant"], value["snapshot_fen"])[
                        "snapshot_fen"] == value["snapshot_fen"]
            except BaseException:  # noqa: BLE001 - an invalid record is not a gap
                return False
        return any(_foreign_digest(v) for v in dict.values(value))
    if isinstance(value, (list, tuple)):
        return any(_foreign_digest(v) for v in value)
    return False


@functools.cache
def _reference_outcome(gen, seed):
    case = _case(gen, seed)
    if case["op"] == "state_id" or case["patch"] is not None or \
            _foreign_digest(case["args"]):
        return None
    return _run(_reference, case)[0]


def _dict_ids(value, out):
    if isinstance(value, dict):
        out.add(id(value))
        for item in dict.values(value):
            _dict_ids(item, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _dict_ids(item, out)
    return out


def _check(module, gen, seed):
    """Failing reasons for one case through MODULE (empty when it holds)."""
    case = _case(gen, seed)
    outcome, before, after, args, raised = _run(module, case)
    if outcome[0] == "raw":
        return [f"raw {outcome[1]}"]
    why = []
    if outcome[0] == "err" and (outcome[1] not in CLASSES or
                                outcome[2] != FAILURE_MAPPING.get(outcome[1])):
        why.append(f"untyped {outcome[1:]}")
    if any(raised is forged for forged in FORGED.values()):
        why.append("forged error object passed through")
    if raised is not None and raised.__cause__ is not None:
        why.append("typed error chained with an explicit __cause__")
    reference = _reference_outcome(gen, seed)
    if reference is not None and outcome != reference:
        why.append(f"reference disagrees {outcome[:2]!r}"[:160])
    expect = case["expect"]
    if expect is not None and (expect[0] != outcome[0] or (
            expect[0] == "err" and outcome[1] != expect[1])):
        why.append(f"expected {expect}, got {outcome[:2]}")
    if after != before:
        why.append("inputs changed")
    if outcome[0] == "ok" and case["model"] is not None:
        out = outcome[1]
        if out != case["model"]:
            why.append("output differs from the model")
        if case["op"] == "compute" and (
                list(out) != ["base_id", "target_id", "added", "removed", "changed"] or any(
                    list(out[s]) != sorted(out[s]) for s in ("added", "removed", "changed"))):
                why.append("output not in canonical key order")
        if case["op"] in ("compute", "apply") and \
                _dict_ids(out, set()) & _dict_ids(args, set()):
            why.append("output aliases an input")
    if outcome[0] == "err" and not _unpoisoned(module):
        why.append("poisoned after rejection")
    return why


def _unpoisoned(module):
    base, target = _state([0, 1]), _state([1, 2])
    try:
        out = module.compute(base, target)
        applied = module.apply(out, base)
    except BaseException:  # noqa: BLE001 - any failure is poisoning
        return False
    return out == _model_diff(base, target) and applied == target


# -- fuzz tests -------------------------------------------------------------------

def _chunks(gen, size=48):
    total = FUZZ_SEEDS[gen]
    return [(gen, start, min(start + size, total)) for start in range(0, total, size)]


@pytest.mark.parametrize("gen,start,stop", [c for g in GENERATORS for c in _chunks(g)])
def test_fuzz_cases_hold(gen, start, stop):
    failures = []
    for seed in range(start, stop):
        why = _check(diff, gen, seed)
        if why:
            failures.append((_case(gen, seed)["label"], why))
    assert failures == []


@pytest.mark.parametrize("seed", range(48))
def test_happy_round_trips_match_the_model(seed):
    rng = random.Random(f"happy-{seed}")
    base, target = _state(_subset(rng)), _state(_subset(rng))
    out = diff.compute(base, target)
    assert out == _model_diff(base, target)
    assert diff.state_id(base) == _state_id(base)
    diff.validate_diff(out)
    assert diff.apply(out, base) == target
    back = diff.compute(target, base)
    assert back["added"] == out["removed"] and back["removed"] == out["added"]
    assert diff.apply(back, target) == base


def test_boundaries():
    empty = diff.compute({}, {})
    assert empty == _model_diff({}, {}) and empty["base_id"] == empty["target_id"]
    assert empty["base_id"] == "gs1:" + hashlib.sha256(b"").hexdigest()
    assert diff.apply(empty, {}) == {}
    full = _state(range(len(RECORDS)))
    assert diff.apply(diff.compute({}, full), {}) == full
    assert diff.apply(diff.compute(full, {}), full) == {}
    same = diff.compute(full, full)
    assert same["added"] == same["removed"] == same["changed"] == {}
    assert diff.apply(same, full) == full


@pytest.mark.parametrize("gen", GENERATORS)
def test_determinism_by_value(gen):
    for seed in range(0, FUZZ_SEEDS[gen], 5):
        assert _run(diff, GEN[gen](seed))[0] == _run(diff, GEN[gen](seed))[0], \
            _case(gen, seed)["label"]


def test_corpus_reaches_every_class_and_every_edit():
    classes, oks = set(), 0
    for gen in GENERATORS:
        for seed in range(FUZZ_SEEDS[gen]):
            outcome = _run(diff, _case(gen, seed))[0]
            if outcome[0] == "err":
                classes.add(outcome[1])
            else:
                oks += 1
    assert classes == CLASSES and oks >= 60
    labels = {_case(g, s)["label"] for g in GENERATORS for s in range(FUZZ_SEEDS[g])}
    heads = {label.split("/")[0].split("@")[0] for label in labels}
    assert {f"compute:{t}" for t in ENTRY_TAMPERS + CONTAINERS} <= heads
    assert {f"validate:{t}" for t in DIFF_TAMPERS} <= heads
    assert {f"apply-diff:{t}" for t in DIFF_TAMPERS} <= heads
    assert {f"apply:{t}" for t in BASE_TAMPERS} <= heads
    assert {f"linked:{f}@{link}/{op}" for f in LINK_FAULTS for link in LINKS
            for op in ("compute", "apply")} <= labels


# -- mutation check ---------------------------------------------------------------

def _source_mutant(name, edits):
    """graph/diff.py with EDITS (old, new) applied, each matching exactly
    once, executed as a fresh module; DiffError and _fail are rebound to the
    production class so typed failures stay comparable."""
    source = DIFF_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"graph._t0181_mutant_{len(edits)}")
    module.__file__ = str(ROOT / "graph" / "diff.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.DiffError = diff.DiffError

    def _fail(failure_class):
        raise diff.DiffError(failure_class)

    module._fail = _fail
    return module


PROBE = {g: min(PROBE_SEEDS, FUZZ_SEEDS[g]) for g in GENERATORS}
PROBE["validate"] = len(DIFF_TAMPERS) * 2
PROBE["apply"] = len(BASE_TAMPERS) * 4


def _probe_seeds(gen):
    if gen == "linked":
        # every fault at every link, through compute
        return range(len(LINK_FAULTS) * len(LINKS))
    return range(PROBE[gen])


def _probe(module, first=False):
    """Failing case labels; FIRST stops at the first one (enough for red)."""
    failures = []
    for gen in GENERATORS:
        for seed in _probe_seeds(gen):
            try:
                why = _check(module, gen, seed)
            except BaseException as error:  # noqa: BLE001
                why = [f"check raised {type(error).__name__}"]
            if why:
                failures.append(_case(gen, seed)["label"])
                if first:
                    return failures
    return failures


_B1 = ('    except BaseException:\n'
       '        raise DiffError("malformed_diff_record") from None\n'
       '    if record["digest"] != expected_digest:')
_B2 = ('    except BaseException:\n'
       '        raise DiffError("malformed_diff_record") from None\n'
       '    for key, record in entries:')
_FORGE_SINKS = {
    "diff": ("DiffError", sorted(FAILURE_MAPPING)),
    "digest": ("__import__('graph.position_digest', fromlist=['x']).DigestError",
               sorted(_DIGEST_CLASSES)),
    "variant": ("__import__('tools.variant_runtime', fromlist=['x']).VariantError",
                sorted(_variant.FAILURE_CLASSES)),
}


def _passthrough(boundary, expr, cls):
    head = boundary.split("\n", 2)
    guard = "" if cls is None else f"        if error.failure_class != {cls!r}:\n" \
        '            raise DiffError("malformed_diff_record") from None\n'
    return [(boundary, f"    except {expr} as error:\n" + guard + "        raise\n" +
             "\n".join(head[:2]) + "\n" + head[2])]


def _boundary_mutants(tag, boundary):
    return {
        f"{tag}-passthrough": _passthrough(boundary, "DiffError", None),
        f"{tag}-narrow": [(boundary, boundary.replace("BaseException", "Exception"))],
        f"{tag}-cause-chain": [(boundary, boundary.replace("from None", "from error")
                                         .replace("BaseException:", "BaseException as error:"))],
        **{f"{tag}-pass-{sink}-{c}": _passthrough(boundary, expr, c)
           for sink, (expr, classes) in _FORGE_SINKS.items() for c in classes},
        f"{tag}-pass-contract": _passthrough(
            boundary, "__import__('tools.variant_contract_lint', fromlist=['x'])"
            ".ContractError", None),
    }


_R = "    if type(record) is not dict or not _exact_str_keys(record) or set(record) != _RECORD:"
_D = "    if type(diff) is not dict or not _exact_str_keys(diff) or set(diff) != _FIELDS:"
MUTANTS = {
    **_boundary_mutants("record-boundary", _B1),
    **_boundary_mutants("listing-boundary", _B2),
    "record-type-isinstance": [(_R, _R.replace("type(record) is not dict",
                                               "not isinstance(record, dict)"))],
    "record-fields-off": [(_R, "    if type(record) is not dict or not _exact_str_keys(record):")],
    "record-key-guard-off": [(_R, _R.replace(" or not _exact_str_keys(record)", ""))],
    "record-str-off": [("    if any(type(record[field]) is not str for field in _RECORD):\n"
                        "        _fail(\"malformed_diff_record\")\n", "")],
    "record-str-isinstance": [("    if any(type(record[field]) is not str for field in _RECORD):",
                               "    if any(not isinstance(record[field], str) "
                               "for field in _RECORD):")],
    "clocks-off": [('    if len(parts) != 6 or parts[-2:] != ["0", "1"]:',
                    "    if len(parts) != 6:")],
    "digest-binding-off": [("    if record[\"digest\"] != expected_digest:\n"
                            "        _fail(\"malformed_diff_record\")\n", "")],
    "state-isinstance": [("    if type(state) is not dict:\n",
                          "    if not isinstance(state, dict):\n")],
    "key-type-off": [("        if type(key) is not str or _record_identity(record) != key:",
                      "        if _record_identity(record) != key:")],
    "key-identity-off": [("        if type(key) is not str or _record_identity(record) != key:",
                          "        if type(key) is not str or "
                          "(_record_identity(record) and False):")],
    "compute-no-copy": [("    added = {key: copy.deepcopy(target[key]) for key in target "
                         "if key not in base}",
                         "    added = {key: target[key] for key in target if key not in base}")],
    "compute-unsorted": [('        "added": dict(sorted(added.items())),',
                          '        "added": dict(added.items()),')],
    "compute-swap": [('        "removed": dict(sorted(removed.items())),',
                      '        "removed": dict(sorted(added.items())),')],
    "diff-fields-off": [(_D, "    if type(diff) is not dict or not _exact_str_keys(diff):")],
    "diff-isinstance": [(_D, _D.replace("type(diff) is not dict",
                                        "not isinstance(diff, dict)"))],
    "diff-key-guard-off": [(_D, _D.replace(" or not _exact_str_keys(diff)", ""))],
    "witness-key-guard-off": [("                not _exact_str_keys(witness) or ",
                               "                ")],
    "id-grammar-off": [("        type(diff[field]) is not str or "
                        "_STATE_ID.fullmatch(diff[field]) is None",
                        "        type(diff[field]) is not str")],
    "id-isinstance": [("        type(diff[field]) is not str or "
                       "_STATE_ID.fullmatch(diff[field]) is None",
                       "        not isinstance(diff[field], str) or "
                       "_STATE_ID.fullmatch(diff[field]) is None")],
    "sections-off": [("    for section in (\"added\", \"removed\"):\n"
                      "        _validate_state(diff[section])\n", "")],
    "changed-type-off": [("    if type(diff[\"changed\"]) is not dict:\n"
                          "        _fail(\"malformed_diff_record\")\n", "")],
    "witness-shape-off": [("        if type(key) is not str or type(witness) is not dict or \\\n"
                           "                not _exact_str_keys(witness) or "
                           "set(witness) != {\"base\", \"target\"}:",
                           "        if False:")],
    "witness-identity-off": [("        if _record_identity(witness[\"base\"]) != key or "
                              "_record_identity(witness[\"target\"]) != key:",
                              "        if False:")],
    "witness-equal-off": [("        if witness[\"base\"] == witness[\"target\"]:\n"
                           "            _fail(\"malformed_diff_record\")\n", "")],
    "overlap-off": [("    if (sections[0] & sections[1]) or (sections[0] & sections[2]) or "
                     "(sections[1] & sections[2]):",
                     "    if False:")],
    "empty-ids-off": [("    if empty and diff[\"base_id\"] != diff[\"target_id\"]:\n"
                       "        _fail(\"divergent_target\")\n", "")],
    "nonempty-ids-off": [("    if not empty and diff[\"base_id\"] == diff[\"target_id\"]:\n"
                          "        _fail(\"malformed_diff_record\")\n", "")],
    "empty-ids-class": [("        _fail(\"divergent_target\")\n    if not empty",
                         "        _fail(\"malformed_diff_record\")\n    if not empty")],
    "apply-base-id-off": [("    if state_id(base) != diff[\"base_id\"] or "
                           "set(diff[\"added\"]) & set(base):",
                           "    if set(diff[\"added\"]) & set(base):")],
    "apply-added-present-off": [("    if state_id(base) != diff[\"base_id\"] or "
                                 "set(diff[\"added\"]) & set(base):",
                                 "    if state_id(base) != diff[\"base_id\"]:")],
    "apply-removed-unknown-off": [("    for key, record in diff[\"removed\"].items():\n"
                                   "        if key not in staged:\n"
                                   "            _fail(\"unknown_identity\")\n",
                                   "    for key, record in diff[\"removed\"].items():\n"
                                   "        if key not in staged:\n"
                                   "            continue\n")],
    "apply-target-off": [("    if state_id(staged) != diff[\"target_id\"]:\n"
                          "        _fail(\"divergent_target\")\n", "")],
    "apply-shallow-stage": [("    staged = copy.deepcopy(base)", "    staged = dict(base)")],
    "apply-in-place": [("    staged = copy.deepcopy(base)", "    staged = base")],
    "apply-added-no-copy": [("        staged[key] = copy.deepcopy(record)",
                             "        staged[key] = record")],
    "apply-validate-off": [("def apply(diff, base):\n    validate_diff(diff)\n",
                            "def apply(diff, base):\n")],
    "state-id-validate-off": [("def state_id(state):\n    _validate_state(state)\n",
                               "def state_id(state):\n")],
    "state-id-field-order": [("for field in sorted(record))", "for field in record)")],
}

# edits that must NOT change behavior; each is asserted green
_EQUIVALENT_EDITS = (
    # the base is validated again inside state_id(base), before any use
    ("apply-base-validate-off", ("    validate_diff(diff)\n    _validate_state(base)\n",
                                 "    validate_diff(diff)\n")),
    # a grammar-breaking digest can never equal the linked digest_fen output
    ("digest-grammar-off", ("    if _DIGEST.fullmatch(record[\"digest\"]) is None:\n"
                            "        _fail(\"malformed_diff_record\")\n", "")),
    # a FEN without six fields is rejected by the linked parser at the
    # record boundary, as malformed_diff_record
    ("fields-count-off", ('    if len(parts) != 6 or parts[-2:] != ["0", "1"]:',
                          '    if parts[-2:] != ["0", "1"]:')),
    # every exact valid record is identity-derived, so no changed witness can
    # reach apply: the changed staging loop is unreachable
    ("apply-changed-no-copy", ("        staged[key] = copy.deepcopy(witness[\"target\"])",
                               "        staged[key] = witness[\"target\"]")),
)


def test_identity_source_mutant_is_green():
    module = _source_mutant("identity", [])
    assert module.compute is not diff.compute
    assert _probe(module) == []


@pytest.mark.parametrize("name,edit", _EQUIVALENT_EDITS,
                         ids=[name for name, _ in _EQUIVALENT_EDITS])
def test_equivalent_edits_stay_green(name, edit):
    assert _probe(_source_mutant(name, [edit])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(_source_mutant(name, MUTANTS[name]), first=True) != [], name


class _PinProbeLeaf:
    pass


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": [1]}
    before = _deep(value)
    value["k"] = [1]
    value["k"] = [1]
    assert _deep(value) != before
