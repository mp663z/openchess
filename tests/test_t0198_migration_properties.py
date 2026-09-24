"""T0198 deterministic unit/property battery for the production migration.

Seeded properties over store.migration (the shipped T0197 runtime) only:
no tests.* helpers. Source states are built from records made by the
shipped graph.node runtime; receipts are checked against an independent
re-digest model, an independent state-id serialization and an
independent migration-id derivation.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import random
from pathlib import Path

import pytest
import yaml

from graph import position_digest
from graph.node import make_record, record_identity
from store import migration as mig
from tools.variant_runtime import VariantError

FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w Kq - 0 1",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",
    "8/8/8/8/8/8/8/K1k5 w - - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
KEYS = tuple(record_identity(record) for record in RECORDS)
RECEIPT_FIELDS = ("migration_id", "from_schema", "to_schema", "source_id",
                  "target_id", "state")
V1, V2 = "store-v1", "store-v2"
SEEDS = range(40)
DIGEST_CLASSES = sorted(yaml.safe_load(position_digest.CONTRACT.read_text())
                        ["contract"]["failures"]["mapping"])
VARIANT_CODES = ("illegal_position", "internal", "malformed_request", "unknown_variant")


class _Str(str):
    pass


class _Dict(dict):
    pass


class _Colliding:
    """Hashes like a real name; while armed (only around the engine call)
    comparing it raises, so any comparison before an exact-str key guard
    escapes raw."""

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


def _armed(call, *args):
    _Colliding.armed = True
    try:
        return call(*args)
    finally:
        _Colliding.armed = False


def _state_id(state):
    """Independent graph-diff state serialization (data/contracts/migration.yaml)."""
    text = "".join(
        f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key])) + "\n"
        for key in sorted(state))
    return "gs1:" + hashlib.sha256(text.encode()).hexdigest()


def _pdv2(variant, fen):
    return "pdv2:" + hashlib.sha256(f"{variant}\n{fen}".encode()).hexdigest()


def _mid(frm, to, source_id, target_id):
    return "mg1:" + hashlib.sha256(
        f"{frm}\n{to}\n{source_id}\n{target_id}".encode()).hexdigest()


def _source(seed):
    rng = random.Random(seed)
    chosen = rng.sample(range(len(RECORDS)), rng.randint(0, len(RECORDS)))
    return {KEYS[i]: dict(RECORDS[i]) for i in chosen}


def _request(source):
    return {"from_schema": V1, "to_schema": V2, "source_id": _state_id(source)}


def _model(source):
    return {key: {"variant": r["variant"], "digest": _pdv2(r["variant"], r["snapshot_fen"]),
                  "snapshot_fen": r["snapshot_fen"]} for key, r in source.items()}


def _shape(value):
    if type(value) is dict:
        return [(key, _shape(item)) for key, item in value.items()]
    return value


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


def _deep_ids(value):
    if isinstance(value, dict):
        return [_Pin(value)] + [x for k, v in value.items()
                                for x in (_Pin(k), *_deep_ids(v))]
    return [_Pin(value)]


def _snap(*values):
    return [(_shape(v), _deep_ids(v)) for v in values]


def _engine(oracle=mig.pdv2_digest):
    return mig.MigrationEngine(oracle)


def _fails(failure, call, *args):
    with pytest.raises(mig.MigrationError) as exc:
        try:
            _armed(call, *args)
        except mig.MigrationError:
            raise
        else:
            raise AssertionError("accepted")
    assert exc.value.failure_class == failure
    assert exc.value.code == mig.FAILURE_MAPPING[failure]
    return exc.value


def _flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


# -- R2: honest migrations against the model -------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_migration_matches_model_and_derivations(seed):
    source = _source(seed)
    request = _request(source)
    before = _snap(request, source)
    calls = []

    def oracle(variant, fen):
        calls.append((variant, fen))
        return mig.pdv2_digest(variant, fen)

    out = _engine(oracle).migrate(request, source)
    expected_state = _model(source)
    target_id = _state_id(expected_state)
    assert list(out) == list(RECEIPT_FIELDS)
    assert out == {"migration_id": _mid(V1, V2, request["source_id"], target_id),
                   "from_schema": V1, "to_schema": V2,
                   "source_id": request["source_id"], "target_id": target_id,
                   "state": expected_state}
    assert all(type(v) is str for k, v in out.items() if k != "state")
    assert type(out["state"]) is dict
    assert all(type(r) is dict and list(r) == ["variant", "digest", "snapshot_fen"]
               for r in out["state"].values())
    assert calls == [(source[k]["variant"], source[k]["snapshot_fen"]) for k in sorted(source)]
    assert _snap(request, source) == before
    assert not {id(r) for r in out["state"].values()} & {id(r) for r in source.values()}
    assert out["state"] is not source
    assert _engine().migrate(request, source) == out


def test_r2_empty_and_single_record_states():
    out = _engine(lambda v, f: pytest.fail("oracle called")).migrate(_request({}), {})
    assert out["state"] == {} and out["target_id"] == _state_id({}) == out["source_id"]
    assert out["migration_id"] == _mid(V1, V2, _state_id({}), _state_id({}))
    for key, record in zip(KEYS, RECORDS, strict=True):
        source = {key: dict(record)}
        out = _engine().migrate(_request(source), source)
        assert out["state"] == _model(source) and len(out["state"]) == 1
        assert out["target_id"] != out["source_id"]


def test_r2b_distinct_sources_give_distinct_receipts():
    ids = {}
    for seed in SEEDS:
        source = _source(seed)
        out = _engine().migrate(_request(source), source)
        ids.setdefault(out["source_id"], set()).add((out["target_id"], out["migration_id"]))
    assert all(len(v) == 1 for v in ids.values())
    targets = [t for v in ids.values() for t, _ in v]
    assert len(set(targets)) == len(targets)


# -- R3: derivations ---------------------------------------------------------------------

def test_r3_public_derivations_match_independent_ones():
    for seed in SEEDS:
        state = _source(seed)
        before = _snap(state)
        assert mig.state_id(state) == _state_id(state)
        assert mig.state_id(dict(reversed(list(state.items())))) == _state_id(state)
        assert _snap(state) == before
    for record in RECORDS:
        assert mig.pdv2_digest(record["variant"], record["snapshot_fen"]) == \
            _pdv2(record["variant"], record["snapshot_fen"])
    for args in ((V1, V2, "gs1:" + "0" * 64, "gs1:" + "f" * 64),
                 ("store-v9", "store-v10", "gs1:" + "a" * 64, "gs1:" + "b" * 64)):
        assert mig.derive_migration_id(*args) == _mid(*args)
    assert mig.derive_migration_id(V1, V2, "a", "b") != mig.derive_migration_id(V2, V1, "a", "b")


# -- R4: target oracle boundary -----------------------------------------------------------

def _raiser(kind):
    def oracle(variant, fen):
        raise kind()
    return oracle


def _forged_oracle(error):
    def oracle(variant, fen):
        raise error
    return oracle


FORGED_ERRORS = {
    **{f"forged-migration-error-{c}": mig.MigrationError(c, mig.FAILURE_MAPPING[c])
       for c in sorted(mig.FAILURE_MAPPING)},
    **{f"forged-digest-error-{c}": position_digest.DigestError(c, "malformed_request")
       for c in DIGEST_CLASSES},
    **{f"forged-variant-error-{c}": VariantError(code=c, message="forged")
       for c in VARIANT_CODES},
}

HOSTILE_ORACLES = {
    **{name: _forged_oracle(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda v, f: None,
    "bytes": lambda v, f: mig.pdv2_digest(v, f).encode(),
    "int": lambda v, f: 1,
    "str-subclass": lambda v, f: _Str(mig.pdv2_digest(v, f)),
    "source-schema-digest": lambda v, f: make_record(v, f)["digest"],
    "trailing-newline": lambda v, f: mig.pdv2_digest(v, f) + "\n",
    "leading-space": lambda v, f: " " + mig.pdv2_digest(v, f),
    "upper-case": lambda v, f: mig.pdv2_digest(v, f).upper(),
    "short": lambda v, f: mig.pdv2_digest(v, f)[:-1],
    "long": lambda v, f: mig.pdv2_digest(v, f) + "0",
    "surrogate": lambda v, f: mig.pdv2_digest(v, f)[:-1] + "\ud800",
}


@pytest.mark.parametrize("name", sorted(HOSTILE_ORACLES))
@pytest.mark.parametrize("fail_at", [0, -1])
def test_r4_hostile_oracle_fails_closed(name, fail_at):
    source = {k: dict(r) for k, r in zip(KEYS, RECORDS, strict=True)}
    request = _request(source)
    order = sorted(source)
    bad_key = order[fail_at]
    hostile = HOSTILE_ORACLES[name]

    def oracle(variant, fen):
        if (variant, fen) == (source[bad_key]["variant"], source[bad_key]["snapshot_fen"]):
            return hostile(variant, fen)
        return mig.pdv2_digest(variant, fen)

    before = _snap(request, source)
    raised = _fails("divergent_target", _engine(oracle).migrate, request, source)
    assert _snap(request, source) == before
    # a fresh typed error, never the forged object (even a forged divergent_target)
    assert all(raised is not forged for forged in FORGED_ERRORS.values())


def test_r4b_any_grammar_valid_digest_is_accepted_at_the_ceiling():
    source = {k: dict(r) for k, r in zip(KEYS, RECORDS, strict=True)}
    for digest in ("pdv2:" + "0" * 64, "pdv2:" + "f" * 64):
        out = _engine(lambda v, f, d=digest: d).migrate(_request(source), source)
        assert all(r["digest"] == digest for r in out["state"].values())
        assert out["target_id"] == _state_id(out["state"])


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("path", ["ok", "failed"])
def test_r5_oracle_closing_over_live_inputs_is_undone(seed, path):
    source = _source(seed + 100) or {KEYS[0]: dict(RECORDS[0])}
    request = _request(source)
    before = _snap(request, source)
    expected = _engine().migrate(copy.deepcopy(request), copy.deepcopy(source))
    calls = []

    def oracle(variant, fen):
        calls.append(1)
        if len(calls) == 1:
            first = next(iter(source.values()))
            first["digest"] = "pdv1:" + "0" * 64
            first["zz"] = 1
            del first["variant"]
            source["extra"] = {"variant": "x"}
            source.pop(next(iter(source)))
            request["source_id"] = "gs1:" + "0" * 64
            request["zz"] = 1
            del request["from_schema"]
        if path == "failed" and len(calls) == len(expected["state"]):
            raise RuntimeError("late failure")
        return mig.pdv2_digest(variant, fen)

    if path == "failed":
        _fails("divergent_target", _engine(oracle).migrate, request, source)
    else:
        assert _engine(oracle).migrate(request, source) == expected
    assert len(calls) == len(expected["state"])
    assert _snap(request, source) == before


# -- R6: malformed and unknown requests ---------------------------------------------------

def _request_tampers(good):
    return [
        ("none", None), ("list", list(good.items())), ("dict-subclass", _Dict(good)),
        *[(f"renamed-{k}", {(k + "x" if a == k else a): v for a, v in good.items()})
          for k in good],
        *[(f"missing-{k}", {a: v for a, v in good.items() if a != k}) for k in good],
        ("extra-key", {**good, "zz": 1}),
        *[(f"str-subclass-key-{k}", {**{a: v for a, v in good.items() if a != k},
                                      _Str(k): good[k]}) for k in good],
        *[(f"colliding-key-{k}", {**{a: v for a, v in good.items() if a != k},
                                   _Colliding(k): good[k]}) for k in good],
        *[(f"{k}-{n}", {**good, k: v}) for k in ("from_schema", "to_schema")
          for n, v in (("str-subclass", _Str(good[k])), ("none", None),
                       ("bytes", good[k].encode()), ("unregistered", "store-v3"),
                       ("zero", "store-v0"), ("upper", good[k].upper()),
                       ("newline", good[k] + "\n"), ("leading-zero", "store-v01"))],
        *[(f"source-id-{n}", {**good, "source_id": v}) for n, v in (
            ("str-subclass", _Str(good["source_id"])), ("none", None),
            ("upper", good["source_id"].upper()), ("newline", good["source_id"] + "\n"),
            ("short", good["source_id"][:-1]), ("bytes", good["source_id"].encode()),
            ("migration-id", "mg1:" + good["source_id"][4:]))],
    ]


def _rekey(mapping, key, new):
    mapping[new] = mapping.pop(key)
    return mapping


def _first(state):
    return state[next(iter(state))]


CORRUPT_SOURCES = {
    "none": lambda s: s,
    "digest": lambda s: (_first(s).__setitem__("digest", "pdv2:" + "0" * 64), s)[1],
    "key-str-subclass": lambda s: _rekey(s, next(iter(s)), _Str(next(iter(s)))),
    "key-colliding": lambda s: (s.__setitem__(_Colliding("k"), dict(RECORDS[0])), s)[1],
    "field-str-subclass": lambda s: (_rekey(_first(s), "digest", _Str("digest")), s)[1],
    "field-colliding": lambda s: (_first(s).__setitem__(_Colliding("digest"), "x"), s)[1],
    "not-dict": lambda s: list(s.items()),
}


@pytest.mark.parametrize("seed", range(3))
def test_r6_malformed_request_is_rejected_first(seed):
    source = _source(seed) or {KEYS[0]: dict(RECORDS[0])}
    for corrupt in CORRUPT_SOURCES:
        work = CORRUPT_SOURCES[corrupt](copy.deepcopy(source))
        for name, bad in _request_tampers(_request(source)):
            before, calls = _snap(bad, work), []
            try:
                _fails("malformed_migration_record",
                       _engine(lambda v, f, calls=calls: calls.append(v)).migrate, bad, work)
            except AssertionError as err:
                raise AssertionError(f"{name}: {err}") from None
            assert calls == [] and _snap(bad, work) == before, name


@pytest.mark.parametrize("pair", [(V1, V1), (V2, V2), (V2, V1)])
def test_r6b_unregistered_step_is_unknown_before_source(pair):
    good = {KEYS[0]: dict(RECORDS[0])}
    sources = [CORRUPT_SOURCES[c](copy.deepcopy(good)) for c in CORRUPT_SOURCES]
    for source in (*sources, None, _Dict(good)):
        request = {"from_schema": pair[0], "to_schema": pair[1],
                   "source_id": _state_id(good)}
        before = _snap(request, source)
        _fails("unknown_migration", _engine(lambda v, f: pytest.fail("called")).migrate,
               request, source)
        assert _snap(request, source) == before


# -- R7: malformed and conflicting sources ------------------------------------------------

def _source_tampers():
    k, r = KEYS[2], RECORDS[2]
    other = RECORDS[3]

    def rec(**changes):
        return {k: {**r, **changes}}

    return [
        ("none", None), ("list", [(k, dict(r))]), ("dict-subclass", _Dict({k: dict(r)})),
        ("record-none", {k: None}), ("record-dict-subclass", {k: _Dict(r)}),
        ("record-list", {k: list(r.items())}),
        ("key-str-subclass", {_Str(k): dict(r)}), ("key-colliding", {_Colliding(k): dict(r)}),
        ("key-int", {1: dict(r)}), ("key-other-identity", {KEYS[3]: dict(r)}),
        ("key-trailing-space", {k + " ": dict(r)}),
        *[(f"field-renamed-{f}", {k: {(f + "x" if a == f else a): v for a, v in r.items()}})
          for f in r],
        *[(f"field-missing-{f}", {k: {a: v for a, v in r.items() if a != f}}) for f in r],
        ("field-extra", rec(zz="1")),
        *[(f"field-str-subclass-key-{f}", {k: {**{a: v for a, v in r.items() if a != f},
                                               _Str(f): r[f]}}) for f in r],
        *[(f"field-colliding-key-{f}", {k: {**{a: v for a, v in r.items() if a != f},
                                            _Colliding(f): r[f]}}) for f in r],
        *[(f"value-str-subclass-{f}", rec(**{f: _Str(r[f])})) for f in r],
        *[(f"value-none-{f}", rec(**{f: None})) for f in r],
        ("variant-unknown", rec(variant="chess960x")),
        ("variant-upper", rec(variant="Standard")),
        ("fen-clocks", rec(snapshot_fen=r["snapshot_fen"][:-3] + "5 9")),
        ("fen-garbage", rec(snapshot_fen="not a fen")),
        ("fen-other-position", rec(snapshot_fen=other["snapshot_fen"])),
        ("digest-v2-in-v1", rec(digest=_pdv2(r["variant"], r["snapshot_fen"]))),
        ("digest-upper", rec(digest=r["digest"].upper())),
        ("digest-newline", rec(digest=r["digest"] + "\n")),
        ("digest-short", rec(digest=r["digest"][:-1])),
    ]


@pytest.mark.parametrize("name", [n for n, _ in _source_tampers()])
def test_r7_malformed_source_fails_before_the_oracle(name):
    bad = dict(_source_tampers())[name]
    good = {KEYS[0]: dict(RECORDS[0])}
    if type(bad) is dict:
        bad = {**good, **bad}
    try:
        source_id = _state_id(bad)
    except Exception:  # noqa: BLE001 - unserializable tamper: any valid id
        source_id = _state_id(good)
    for sid in (source_id, _flip(source_id)):
        # malformed wins over a conflicting source id
        request = {"from_schema": V1, "to_schema": V2, "source_id": sid}
        before, calls = _snap(request, bad), []
        _fails("malformed_migration_record",
               _engine(lambda v, f, calls=calls: calls.append(v)).migrate, request, bad)
        assert calls == [] and _snap(request, bad) == before


def test_r7b_digest_in_grammar_but_not_derived_is_accepted():
    # the source digest is checked against the schema grammar, not rederived
    k, r = KEYS[2], RECORDS[2]
    source = {k: {**r, "digest": "pdv1:" + "0" * 64}}
    out = _engine().migrate(_request(source), source)
    assert out["state"] == _model(source)


@pytest.mark.parametrize("seed", range(10))
def test_r8_conflicting_source_fails_before_the_oracle(seed):
    source = _source(seed)
    others = [_state_id(_source(s)) for s in range(seed + 1, seed + 60)]
    wrong = [o for o in others if o != _state_id(source)][:3]
    for source_id in [_flip(_state_id(source)), *wrong]:
        request = {"from_schema": V1, "to_schema": V2, "source_id": source_id}
        before, calls = _snap(request, source), []
        _fails("conflicting_source",
               _engine(lambda v, f, calls=calls: calls.append(v)).migrate, request, source)
        assert calls == [] and _snap(request, source) == before


# -- R1: no test helpers ------------------------------------------------------------------

def test_r9_property_file_uses_no_test_helpers():
    tree = ast.parse(Path(__file__).read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)
    assert modules <= {"__future__", "ast", "copy", "hashlib", "random", "pathlib",
                       "pytest", "yaml", "graph", "graph.node", "store",
                       "tools.variant_runtime"}


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    _shape keeps leaves alive but not nested dicts, so the probe swaps an
    equal nested record. The fingerprint pins the original, so the swap
    goes red."""
    value = {"k": {"x": 1}}
    before = _snap(value)
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _snap(value) != before
