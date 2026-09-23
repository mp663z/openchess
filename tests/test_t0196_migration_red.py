"""T0196 permanent red battery for store-migration engines.

The battery drives EVERY row of the T0195 conformance fixture
(tests/fixtures/migration/cases.json), a closed set of totality
probes and a pinned canonical-order probe through an engine class
constructed with a target-digest oracle:

- happy/boundary: the pinned receipt exactly (the migrated state
  compared order-sensitively), deterministic over fresh copies,
  exactly one oracle call per source record, and the request and
  source untouched by value AND object identity (exact snapshot)
  with no container shared between the receipt and the inputs;
- malformed: the original input rejects with the pinned failure
  class and its mapped code, inputs untouched, and the declared
  minimal repair is accepted with the receipt the reference
  derives for the repaired input;
- rollback: a rejection leaves the inputs untouched, then the
  valid follow-up (on the SAME engine instance when the oracle is
  unchanged) returns the pinned receipt;
- totality: hostile requests, request keys/values, source
  states, map keys, records, record keys/fields, dict subclasses,
  cyclic/deep/huge values and hostile oracle behavior each reject
  with the pinned failure class, inputs untouched - any other
  BaseException escaping is a failure.

Standalone-red convention (T0151, T0178, T0187): the battery is
permanently GREEN against the contract-derived reference engine
from tests.test_t0194_migration_contract and every black-box
mutant below is RED. The production task switches the binding by
replacing ONLY the two binding lines below with the production
MigrationEngine / MigrationError names; no assertion changes.

Fixture closure: ordered per-section manifests, per-row semantic
pins and closed per-tag edge/defect-locus checks over the
ORIGINAL row data (an unknown tag raises), and a ROW_DIGESTS
whole-row sha256 table whose key set equals the manifests.
test_closure_kills_substitution_mutants proves substitution
mutants are killed with the digest table live AND with digests
neutralized."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0194_migration_contract as _reference  # noqa: E402
from tests.test_t0194_migration_contract import (  # noqa: E402
    _identity,
    _node,
    _step,
    pdv2_oracle,
    state_id,
)
from tests.test_t0195_migration_fixture import (  # noqa: E402
    _assert_malformed_scenario,
)
from tools.migration_contract_lint import (  # noqa: E402
    FAILURE_MAPPING,
)

# -- binding switch: the production task replaces ONLY these two
# lines; the helpers above stay contract-derived.
MigrationEngine = _reference.MigrationEngine
MigrationError = _reference.MigrationError

FIXTURE = (Path(__file__).parent / "fixtures" / "migration"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECORD_FIELDS = frozenset({"variant", "digest", "snapshot_fen"})
REQUEST_FIELDS = ("from_schema", "to_schema", "source_id")
RECEIPT_FIELDS = ("migration_id", "from_schema", "to_schema",
                  "source_id", "target_id", "state")
MMR = "malformed_migration_record"
UM = "unknown_migration"
CS = "conflicting_source"
DVT = "divergent_target"
V1_RE = re.compile(r"^pdv1:[0-9a-f]{64}$")
V2_RE = re.compile(r"^pdv2:[0-9a-f]{64}$")
SID_RE = re.compile(r"^gs1:[0-9a-f]{64}$")
MID_RE = re.compile(r"^mg1:[0-9a-f]{64}$")


def _raising_oracle(variant, fen):
    raise ValueError("untrusted oracle failure")


def _bad_grammar_oracle(variant, fen):
    return "bad"


def _non_str_oracle(variant, fen):
    return None


ORACLES = {"honest": pdv2_oracle, "raising": _raising_oracle,
           "bad-grammar": _bad_grammar_oracle,
           "non-str": _non_str_oracle}

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, edge tag, oracle, pins) where pins =
# (|source|, records with an en-passant square, records with any
# castling right, black-to-move records)
HAPPY_MANIFEST = (
    ("migrate-three-record-source", "three-record", "honest",
     (3, 0, 2, 1)),
    ("migrate-single-record-source", "single-record", "honest",
     (1, 0, 1, 0)),
    ("migrate-two-record-source", "two-record-en-passant", "honest",
     (2, 1, 1, 1)),
)
BOUNDARY_MANIFEST = (
    ("migrate-empty-source", "empty-source", "honest",
     (0, 0, 0, 0)),
    ("migrate-two-record-source-boundary", "two-record-subset",
     "honest", (2, 0, 1, 0)),
)
# malformed: (name, defect-locus tag, failure class, oracle,
# repair form)
MALFORMED_MANIFEST = (
    ("request-missing-field", "missing-request-field", MMR,
     "honest", "replace_request"),
    ("request-extra-field", "extra-request-field", MMR, "honest",
     "replace_request"),
    ("request-bad-schema-grammar", "bad-schema-grammar", MMR,
     "honest", "set_request_field"),
    ("request-unknown-schema", "unregistered-schema", MMR,
     "honest", "set_request_field"),
    ("request-bad-source-id-grammar", "bad-source-id-grammar", MMR,
     "honest", "set_request_field"),
    ("request-non-str-value", "non-str-request-value", MMR,
     "honest", "replace_request"),
    ("request-noop-unregistered", "unregistered-noop-pair", UM,
     "honest", "set_request_field"),
    ("request-downgrade-unregistered",
     "unregistered-downgrade-pair", UM, "honest",
     "replace_request"),
    ("source-id-mismatch", "recomputed-source-id-mismatch", CS,
     "honest", "set_request_field"),
    ("source-not-mapping", "source-not-mapping", MMR, "honest",
     "replace_source"),
    ("source-record-bad-digest", "source-record-bad-digest-grammar",
     MMR, "honest", "replace_source"),
    ("oracle-raises", "oracle-raises", DVT, "raising",
     "set_oracle"),
    ("oracle-bad-grammar-output", "oracle-bad-grammar-output", DVT,
     "bad-grammar", "set_oracle"),
    ("oracle-non-str-output", "oracle-non-str-output", DVT,
     "non-str", "set_oracle"),
)
# rollback: (name, rejection tag, failure class, oracle,
# follow-up oracle)
ROLLBACK_MANIFEST = (
    ("rejected-conflicting-source-then-valid-migrate",
     "tampered-source-id", CS, "honest", "honest"),
    ("rejected-oracle-raising-then-valid-migrate",
     "oracle-raising", DVT, "raising", "honest"),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:migrate-three-record-source":
        "574c0f78736ea77fdb8cd936ebac9abff38fe8157aa75a9bfa1f7d158ade212a",
    "happy:migrate-single-record-source":
        "ae56fdd61fa0fa5ee1bf2d2824d9fa6d95ccf8760fcf4387c541ce33eb304c91",
    "happy:migrate-two-record-source":
        "5d206fbbb3aa1e024a3ed3f1738a2f23a7ed88fa92e6387a5f211d60c62c0016",
    "boundary:migrate-empty-source":
        "310e7b377b06f849c49b751445bbf4bca28c44790c63e349f2b2e28989cc7e3f",
    "boundary:migrate-two-record-source-boundary":
        "359a385ebc341a3a838e7e4dc133baa4cfdad2ccba2cc98ca406ac5454a0fd44",
    "malformed:request-missing-field":
        "0572a3151d3d9e2251d27be322eefe75cbde38dec99d49fd52f9ff5605b45aa1",
    "malformed:request-extra-field":
        "bca76e5a5b939d993c8ae3df7660a641151a8cc87c7b4457e159b1349571b01e",
    "malformed:request-bad-schema-grammar":
        "e1fcda1e075a3a6a9e35ebd7594fb6b2d242d0425d79a79b8d8a75f6c9baa8c3",
    "malformed:request-unknown-schema":
        "1a99194a54e35d2ba8c19aadde47c83da8890f4047e64da30443a64534bf6348",
    "malformed:request-bad-source-id-grammar":
        "874a05a019eb1d1dd145d5d2cbaec7362a6f4f48cb09ba75e9a88173e517f635",
    "malformed:request-non-str-value":
        "5271454846fb3bd5e485f7aa4cf247914fa4fa61308c7f9047920aa0182a7e9a",
    "malformed:request-noop-unregistered":
        "f99e9022fd7e6869441b40d031947e01560b6252b0f45cdea5bfa317ee4f81f4",
    "malformed:request-downgrade-unregistered":
        "fabbaf922391841e69a77ac52f4b1dfe8f9fa7bf6028573e37a2fe486b29cace",
    "malformed:source-id-mismatch":
        "7714848a19381cdb9c8555752c45b2669abdd45ec0a082a6a5d17c0c36f6ab9b",
    "malformed:source-not-mapping":
        "ec252360ff3feae236723ff30f5fca4e12e2e9de499818466030da495d66709b",
    "malformed:source-record-bad-digest":
        "6c0bdb85e71db68bf32805e30b600d53fd893ecf8f48f3d59e8a229009878999",
    "malformed:oracle-raises":
        "9f02c4e209fca845f2cbcd7bd288f88b2c0d7aaed46ef7668b65479aef878b6f",
    "malformed:oracle-bad-grammar-output":
        "2e44f30148738c70f09aec08cc1f3a93082f71c5c81043911ada7237eda90559",
    "malformed:oracle-non-str-output":
        "e4a52cff6cabc75120ac0eb15876af7f305b5f65be512628c3679abd81c8d851",
    "rollback:rejected-conflicting-source-then-valid-migrate":
        "be06400628f0b02195767d1cbb5f61f5dca4940add80a142a76964638db3321b",
    "rollback:rejected-oracle-raising-then-valid-migrate":
        "1fa03e5faced2393566af30a9e9a2dad1f99e3243121d14a895f8d58f98e25bf",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _section_of(row, cases=None):
    for section, rows in (cases or CASES).items():
        if isinstance(rows, list) and any(r is row for r in rows):
            return section
    for section, manifest in MANIFESTS.items():
        if row["name"] in {m[0] for m in manifest}:
            return section
    raise AssertionError(f"row {row.get('name')!r} has no section")


# -- per-tag semantic checks over ORIGINAL row data ---------------------------
def _safe_identity(record):
    try:
        return _identity(record)
    except Exception:  # noqa: BLE001
        return None


def _valid_v1_state(state):
    return type(state) is dict and all(
        type(k) is str and type(v) is dict
        and set(v) == RECORD_FIELDS
        and all(type(v[f]) is str for f in RECORD_FIELDS)
        and V1_RE.fullmatch(v["digest"])
        and _safe_identity(v) == k
        for k, v in state.items())


def _fen_pins(source):
    fields = [rec["snapshot_fen"].split(" ") for rec in
              source.values()]
    return (len(fields),
            sum(f[3] != "-" for f in fields),
            sum(f[2] != "-" for f in fields),
            sum(f[1] == "b" for f in fields))


def _migration_id(req, target_id):
    return "mg1:" + hashlib.sha256(
        f"{req['from_schema']}\n{req['to_schema']}\n"
        f"{req['source_id']}\n{target_id}".encode()).hexdigest()


def _check_receipt(name, request, source, expect):
    """The receipt realizes the contract over the ORIGINAL data:
    ids derived, identities and cardinality preserved, every
    record re-digested by the honest oracle, canonical order."""
    assert list(expect) == list(RECEIPT_FIELDS) or \
        set(expect) == set(RECEIPT_FIELDS), name
    assert expect["from_schema"] == request["from_schema"], name
    assert expect["to_schema"] == request["to_schema"], name
    assert expect["source_id"] == request["source_id"] == \
        state_id(source), name
    state = expect["state"]
    assert list(state) == sorted(source), name
    assert expect["target_id"] == state_id(state), name
    assert expect["migration_id"] == _migration_id(
        request, expect["target_id"]), name
    for key, rec in source.items():
        new = state[key]
        assert set(new) == RECORD_FIELDS, name
        assert new["variant"] == rec["variant"], name
        assert new["snapshot_fen"] == rec["snapshot_fen"], name
        assert V2_RE.fullmatch(new["digest"]), name
        assert new["digest"] == pdv2_oracle(rec["variant"],
                                            rec["snapshot_fen"]), name


def _check_edge(case, tag, oracle, pins):
    """One closed branch per happy/boundary edge tag; an unknown
    tag raises."""
    name = case["name"]
    req, src = case["request"], case["source"]
    assert case["oracle"] == oracle, name
    assert list(req) == sorted(REQUEST_FIELDS) or \
        set(req) == set(REQUEST_FIELDS), name
    assert set(req) == set(REQUEST_FIELDS), name
    assert (req["from_schema"], req["to_schema"]) == \
        ("store-v1", "store-v2"), name
    assert _step(req["from_schema"], req["to_schema"]), name
    assert _valid_v1_state(src), name
    assert _fen_pins(src) == pins, name
    _check_receipt(name, req, src, case["expect"])
    fens = sorted(rec["snapshot_fen"] for rec in src.values())
    three = sorted(rec["snapshot_fen"] for rec in _row(
        "happy", "migrate-three-record-source")["source"].values())
    if tag == "three-record":
        assert len(fens) == 3, name
    elif tag == "single-record":
        assert len(fens) == 1, name
        assert fens[0].split(" ")[0] == \
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR", name
    elif tag == "two-record-en-passant":
        assert len(fens) == 2, name
        assert any(f.split(" ")[3] != "-" for f in fens), name
        assert not set(fens) <= set(three), name
    elif tag == "empty-source":
        assert src == {} and case["expect"]["state"] == {}, name
    elif tag == "two-record-subset":
        assert len(fens) == 2 and set(fens) < set(three), name
    else:
        raise AssertionError(f"unknown edge tag {tag!r}")


def _row(section, name, cases=None):
    for row in (cases or CASES)[section]:
        if row["name"] == name:
            return row
    raise AssertionError(f"{section}:{name} missing")


def _check_rollback(case, tag, failure, oracle, then_oracle):
    name = case["name"]
    assert case["expect_failure"] == failure, name
    assert case["oracle"] == oracle, name
    assert case["then_oracle"] == then_oracle, name
    req, src = case["request"], case["source"]
    then_req, then_src = case["then_request"], case["then_source"]
    assert set(req) == set(REQUEST_FIELDS), name
    assert _valid_v1_state(src) and _valid_v1_state(then_src), name
    assert then_src == src, name
    _check_receipt(name, then_req, then_src, case["expect"])
    if tag == "tampered-source-id":
        assert SID_RE.fullmatch(req["source_id"]), name
        assert req["source_id"] != state_id(src), name
        assert then_req == dict(req, source_id=state_id(src)), name
    elif tag == "oracle-raising":
        assert oracle == "raising" and then_oracle == "honest", name
        assert req == then_req, name
        assert req["source_id"] == state_id(src), name
        assert len(src) >= 1, name
    else:
        raise AssertionError(f"unknown rollback tag {tag!r}")


def _check_row(section, case, meta):
    if section in ("happy", "boundary"):
        _, tag, oracle, pins = meta
        _check_edge(case, tag, oracle, pins)
    elif section == "malformed":
        _, tag, failure, oracle, form = meta
        assert case["expect_failure"] == failure, case["name"]
        assert case["oracle"] == oracle, case["name"]
        assert list(case["minimal_repair"]) == [form], case["name"]
        _assert_malformed_scenario(case, tag)
    else:
        _, tag, failure, oracle, then_oracle = meta
        _check_rollback(case, tag, failure, oracle, then_oracle)


def _validate_closure(cases):
    """Ordered names, whole-row digests and per-tag semantic
    checks for every executed section; the digest table's key set
    equals the manifests."""
    assert set(ROW_DIGESTS) == {
        f"{s}:{m[0]}" for s, man in MANIFESTS.items() for m in man}
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == \
            [m[0] for m in manifest], section
        for row, meta in zip(rows, manifest, strict=True):
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS[label] == _row_digest(row), label
            try:
                _check_row(section, row, meta)
            except AssertionError:
                raise
            except Exception as error:  # noqa: BLE001
                raise AssertionError(
                    f"{label}: {type(error).__name__}") from None


# -- exact snapshot / aliasing -------------------------------------------------
class _KeyMark:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key


def _snap(obj):
    """Exact flat snapshot: type, id, length and key order of every
    container, key identity (text for exact str keys, id otherwise),
    scalar type+value (huge ints by bit length and low bits).
    Iterative, cycle-safe, never hashes, compares or reprs a
    caller-owned key or subclass instance."""
    out, seen, stack = [], set(), [obj]
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is dict or kind is list or kind is tuple:
            if id(node) in seen:
                out.append(("seen", id(node)))
                continue
            seen.add(id(node))
            if kind is dict:
                items = list(dict.items(node))
                out.append(("dict", id(node), len(items)))
                for key, value in reversed(items):
                    stack.append(value)
                    stack.append(_KeyMark(key))
            else:
                values = list(kind.__iter__(node))
                out.append((kind.__name__, id(node), len(values)))
                stack.extend(reversed(values))
        elif kind is _KeyMark:
            key = node.key
            out.append(("key", key if type(key) is str
                        else (type(key).__name__, id(key))))
        elif kind is int:
            out.append(("int", id(node), node.bit_length(),
                        node & 0xFFFF))
        elif kind in (str, bool, float, type(None)):
            out.append((kind.__name__, node))
        else:
            out.append(("object", kind.__name__, id(node)))
    return out


def _not_aliased(result, inputs):
    if not isinstance(result, dict):
        return True
    inner = {e[1] for e in _snap(inputs)
             if e[0] in ("dict", "list", "tuple")}
    return not any(e[0] in ("dict", "list", "tuple") and e[1] in inner
                   for e in _snap(result))


def _same_ordered(result, expect):
    """Order-sensitive receipt equality: the migrated state is also
    compared as an ordered item list."""
    return (result == expect and isinstance(result, dict)
            and isinstance(result.get("state"), dict)
            and list(result["state"].items())
            == list(expect["state"].items()))


class _Counting:
    """Wraps an oracle and counts calls per (variant, fen)."""

    def __init__(self, oracle):
        self.oracle, self.calls = oracle, []

    def __call__(self, variant, fen):
        self.calls.append((variant, fen))
        return self.oracle(variant, fen)


def _checked_migrate(engine, counter, request, source):
    """(receipt, ok) for one successful migrate call: inputs
    untouched exactly, no aliasing, one oracle call per record."""
    inputs = (request, source)
    snap = _snap(inputs)
    n_before = len(counter.calls)
    expected_calls = sorted((r["variant"], r["snapshot_fen"])
                            for r in source.values())
    result = engine.migrate(request, source)
    ok = (_snap(inputs) == snap and _not_aliased(result, inputs)
          and sorted(counter.calls[n_before:]) == expected_calls)
    return result, ok


def _engine(cls, oracle_name):
    counter = _Counting(ORACLES[oracle_name])
    return cls(counter), counter


def _fresh(case, prefix=""):
    return (copy.deepcopy(case[prefix + "request"]),
            copy.deepcopy(case[prefix + "source"]))


def _migrate_ok(cls, case, prefix="", engine=None):
    """Pinned receipt (order-sensitive), determinism, exactly one
    oracle call per record, inputs untouched, no aliasing."""
    oracle = case[prefix + "oracle"] if prefix else case["oracle"]
    if engine is None:
        engine = _engine(cls, oracle)
    results = []
    for eng, counter in (engine, _engine(cls, oracle)):
        result, ok = _checked_migrate(eng, counter,
                                      *_fresh(case, prefix))
        if not (ok and _same_ordered(result, case["expect"])):
            return False
        results.append(result)
    # determinism is by VALUE: two calls never hand back the same
    # receipt object or share any container between receipts
    return (results[1] is not results[0]
            and _not_aliased(results[1], results[0]))


def _rejects(engine, case, failure):
    inputs = _fresh(case)
    snap = _snap(inputs)
    try:
        engine.migrate(*inputs)
    except MigrationError as error:
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and _snap(inputs) == snap)
    return False


def _repaired(case):
    rep = case["minimal_repair"]
    out = {"request": copy.deepcopy(case["request"]),
           "source": copy.deepcopy(case["source"]),
           "oracle": case["oracle"]}
    if "set_request_field" in rep:
        form = rep["set_request_field"]
        out["request"][form["field"]] = form["value"]
    elif "replace_request" in rep:
        out["request"] = copy.deepcopy(rep["replace_request"]["request"])
    elif "replace_source" in rep:
        out["source"] = copy.deepcopy(rep["replace_source"]["source"])
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _malformed_ok(cls, case):
    engine, _ = _engine(cls, case["oracle"])
    if not _rejects(engine, case, case["expect_failure"]):
        return False
    fixed = _repaired(case)
    want = _reference.MigrationEngine(ORACLES[fixed["oracle"]]).migrate(
        copy.deepcopy(fixed["request"]), copy.deepcopy(fixed["source"]))
    eng, counter = _engine(cls, fixed["oracle"])
    result, ok = _checked_migrate(eng, counter, fixed["request"],
                                  fixed["source"])
    return ok and _same_ordered(result, want)


def _rollback_ok(cls, case):
    engine = _engine(cls, case["oracle"])
    if not _rejects(engine[0], case, case["expect_failure"]):
        return False
    follow = engine if case["then_oracle"] == case["oracle"] else None
    return _migrate_ok(cls, case, "then_", engine=follow)


_RUNNERS = {"happy": _migrate_ok, "boundary": _migrate_ok,
            "malformed": _malformed_ok, "rollback": _rollback_ok}


# -- in-file canonical-order probe --------------------------------------------
# Every fixture source is inserted in sorted order, so the receipt
# state's canonical (sorted identity) order is proven here: a source
# inserted in neither canonical nor reversed-canonical order.
_FEN_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
_FEN_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
_FEN_KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"


def _order_inputs():
    source = {}
    for fen in (_FEN_E4, _FEN_START, _FEN_KINGS):
        rec = _node(fen)
        source[_identity(rec)] = rec
    request = {"from_schema": "store-v1", "to_schema": "store-v2",
               "source_id": state_id(source)}
    return request, source


ORDER_EXPECT = {
    "migration_id":
        "mg1:e4d2f34bd6d28ac300151718dee9c7a96ab6f8c9af3faa2be44fdc71b8df059c",
    "from_schema":
        "store-v1",
    "to_schema":
        "store-v2",
    "source_id":
        "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
    "target_id":
        "gs1:d56bd89b356b0b6a7b34f2b69717bff38f5dcbe37cbc7d56a793664ef1e5e7f0",
    "state": {
        "('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')": {
            "variant":
                "standard",
            "digest":
                "pdv2:d3ad9796f3d96516d5c0aaeb2753dff47cb253994cd5b476b95334f28f7209a6",
            "snapshot_fen":
                "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR', 'b', 'KQkq', '-')": {
            "variant":
                "standard",
            "digest":
                "pdv2:01634ebab8aee4037bb79e417c78e9e15f36c28ec9ba9d030b94960add253f09",
            "snapshot_fen":
                "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', 'KQkq', '-')": {
            "variant":
                "standard",
            "digest":
                "pdv2:8fa14e0cdbd23096c8345b18bfadad1f8d46c402f3986c101dbbe66504dbc8f1",
            "snapshot_fen":
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        },
    },
}
ORDER_LABEL = "probe:canonical-order"


def _order_ok(cls):
    engine, counter = _engine(cls, "honest")
    result, ok = _checked_migrate(engine, counter, *_order_inputs())
    return ok and _same_ordered(result, ORDER_EXPECT)


# -- totality probes (closed, ordered) ----------------------------------------
class SK(str):
    """str-subclass key/value whose comparisons raise."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile str __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile str __ne__")


class HK:
    """Non-str key whose hash collides with a real key and whose
    __eq__ raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


class _DictSub(dict):
    """dict subclass whose accessors raise."""

    def items(self):
        raise RuntimeError("hostile items")

    def keys(self):
        raise RuntimeError("hostile keys")

    def __getitem__(self, key):
        raise RuntimeError("hostile __getitem__")


def _probe_inputs():
    """A valid two-record migration; every probe corrupts one
    component."""
    source = {}
    for fen in (_FEN_KINGS, _FEN_START):
        rec = _node(fen)
        source[_identity(rec)] = rec
    request = {"from_schema": "store-v1", "to_schema": "store-v2",
               "source_id": state_id(source)}
    return request, source


def _rekey(mapping, key, new_key):
    return {(new_key if k == key else k): v for k, v in mapping.items()}


def _self_ref():
    loop = []
    loop.append(loop)
    return loop


def _deep():
    node = []
    for _ in range(100_000):
        node = [node]
    return node


_HOSTILE_CONTAINERS = (("none", None), ("true", True), ("zero", 0),
                       ("float", 1.5), ("text", "text"), ("list", []))
_HOSTILE_KEYS = (("none", None), ("true", True), ("zero", 0))
_HOSTILE_VALUES = (("none", None), ("true", True), ("zero", 0),
                   ("float", 1.5), ("list", []), ("dict", {}),
                   ("empty", ""))
_FIELDS = ("variant", "digest", "snapshot_fen")


def _target(source):
    return sorted(source)[-1]


def _src(fn):
    def build():
        request, source = _probe_inputs()
        return request, fn(source), "honest"
    return build


def _req(fn):
    def build():
        request, source = _probe_inputs()
        return fn(request), source, "honest"
    return build


def _with_field(source, field, value):
    out = dict(source)
    rec = dict(source[_target(source)])
    rec[field] = value
    out[_target(source)] = rec
    return out


def _probe_builders():
    """name -> (builder returning (request, source, oracle name),
    pinned failure class)."""
    out = {}
    for label, value in _HOSTILE_CONTAINERS:
        out[f"request-{label}"] = (
            _req(lambda r, v=value: copy.deepcopy(v)), MMR)
    out["request-dict-subclass"] = (_req(_DictSub), MMR)
    for field in REQUEST_FIELDS:
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"request-key-{field}-{label}"] = (
                _req(lambda r, f=field, c=cls: _rekey(r, f, c(f))), MMR)
        for label, value in _HOSTILE_VALUES:
            out[f"request-value-{field}-{label}"] = (
                _req(lambda r, f=field, v=value: dict(
                    r, **{f: copy.deepcopy(v)})), MMR)
        out[f"request-value-{field}-str-subclass"] = (
            _req(lambda r, f=field: dict(r, **{f: SK(r[f])})), MMR)
        out[f"request-value-{field}-huge-int"] = (
            _req(lambda r, f=field: dict(r, **{f: 10 ** 5000})), MMR)
    out["request-key-none"] = (
        _req(lambda r: {**r, None: "x"}), MMR)
    for label, value in _HOSTILE_CONTAINERS:
        out[f"source-{label}"] = (
            _src(lambda s, v=value: copy.deepcopy(v)), MMR)
    out["source-dict-subclass"] = (_src(_DictSub), MMR)
    for label, value in _HOSTILE_KEYS:
        out[f"source-key-{label}"] = (
            _src(lambda s, v=value: _rekey(s, _target(s), v)), MMR)
    out["source-key-str-subclass"] = (
        _src(lambda s: _rekey(s, _target(s), SK(_target(s)))), MMR)
    out["source-key-colliding-hash"] = (
        _src(lambda s: _rekey(s, _target(s), HK(_target(s)))), MMR)
    for label, value in (("none", None), ("zero", 0), ("list", []),
                         ("text", "text")):
        out[f"source-record-{label}"] = (
            _src(lambda s, v=value: dict(
                s, **{_target(s): copy.deepcopy(v)})), MMR)
    out["source-record-dict-subclass"] = (
        _src(lambda s: dict(s, **{_target(s): _DictSub(s[_target(s)])})),
        MMR)
    for field in _FIELDS:
        for label, value in _HOSTILE_VALUES:
            out[f"source-field-{field}-{label}"] = (
                _src(lambda s, f=field, v=value: _with_field(
                    s, f, copy.deepcopy(v))), MMR)
        out[f"source-field-{field}-str-subclass"] = (
            _src(lambda s, f=field: _with_field(
                s, f, SK(s[_target(s)][f]))), MMR)
        out[f"source-field-{field}-missing"] = (
            _src(lambda s, f=field: {**s, _target(s): {
                k: v for k, v in s[_target(s)].items() if k != f}}),
            MMR)
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"source-record-key-{field}-{label}"] = (
                _src(lambda s, f=field, c=cls: {**s, _target(s): _rekey(
                    s[_target(s)], f, c(f))}), MMR)
    out["source-record-key-none"] = (
        _src(lambda s: _with_field(s, None, "x")), MMR)
    out["source-record-extra-field"] = (
        _src(lambda s: _with_field(s, "label", "x")), MMR)
    out["source-field-self-referential"] = (
        _src(lambda s: _with_field(s, "digest", _self_ref())), MMR)
    out["source-field-deep-nested"] = (
        _src(lambda s: _with_field(s, "snapshot_fen", _deep())), MMR)
    out["source-field-huge-int"] = (
        _src(lambda s: _with_field(s, "digest", 10 ** 5000)), MMR)
    for label, oracle in _HOSTILE_ORACLES.items():
        out[f"oracle-{label}"] = (_oracle_probe(oracle), DVT)
    for label in _LIVE_MUTATIONS:
        out[f"oracle-{label}"] = (_live_oracle_probe(label), ACCEPT)
    out["oracle-mutates-then-raises"] = (
        _live_oracle_probe("mutates-then-raises"), DVT)
    return out


# Accept-probes: the engine must SUCCEED with exactly PROBE_EXPECT
# (order-sensitive) and leave the ORIGINAL (request, source) objects
# _snap-identical, although the untrusted oracle mutates them live.
ACCEPT = None
_LIVE_MUTATIONS = ("mutates-source-record", "mutates-source-container",
                   "mutates-request-source-id", "clears-request")
_FORGED_SID = "gs1:" + "f" * 64


def _mutate_live(kind, request, source):
    """One live attack on the caller's own objects (built-in dict
    methods only, so the attack itself never touches a guard)."""
    if kind in ("mutates-source-record", "mutates-then-raises"):
        for rec in list(dict.values(source)):
            dict.__setitem__(rec, "snapshot_fen", "x")
    if kind in ("mutates-source-container", "mutates-then-raises"):
        dict.pop(source, sorted(source)[-1])
        dict.__setitem__(source, "extra", {})
    if kind in ("mutates-request-source-id", "mutates-then-raises"):
        dict.__setitem__(request, "source_id", _FORGED_SID)
    if kind == "clears-request":
        dict.clear(request)


def _live_oracle_probe(kind):
    """Oracle bound to the probe's own live inputs: attacks them on
    call 1; the mutate-then-raise variant also raises on call 2."""
    def build():
        request, source = _probe_inputs()
        calls = [0]

        def oracle(variant, fen):
            calls[0] += 1
            if calls[0] == 1:
                _mutate_live(kind, request, source)
            if kind == "mutates-then-raises" and calls[0] == 2:
                raise ValueError("mutate then explode")
            return pdv2_oracle(variant, fen)
        return request, source, oracle
    return build


def _oracle_probe(oracle):
    def build():
        request, source = _probe_inputs()
        return request, source, oracle
    return build


def _o_raise(exc):
    def oracle(variant, fen):
        raise exc
    return oracle


_HOSTILE_ORACLES = {
    "raises-value-error": _o_raise(ValueError("x")),
    "raises-runtime-error": _o_raise(RuntimeError("x")),
    "raises-keyboard-interrupt": _o_raise(KeyboardInterrupt()),
    "raises-system-exit": _o_raise(SystemExit(1)),
    "raises-generator-exit": _o_raise(GeneratorExit()),
    "returns-none": lambda v, f: None,
    "returns-int": lambda v, f: 7,
    "returns-list": lambda v, f: [],
    "returns-bytes": lambda v, f: b"pdv2:" + b"0" * 64,
    "returns-str-subclass": lambda v, f: SK("pdv2:" + "0" * 64),
    "returns-v1-digest": lambda v, f: "pdv1:" + "0" * 64,
    "returns-bad-grammar": lambda v, f: "bad",
    "returns-uppercase-hex": lambda v, f: "pdv2:" + "A" * 64,
}

# Honest reference receipt for _probe_inputs(): every accept-probe
# must return exactly this, order-sensitively.
PROBE_EXPECT = {
    "migration_id":
        "mg1:e02f82fc2b8c3293349d3cb2efb313e66e2ecfa13e253a334e6457c8100f5806",
    "from_schema":
        "store-v1",
    "to_schema":
        "store-v2",
    "source_id":
        "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
    "target_id":
        "gs1:75a8865beede99faa001e58ef7d7bf692e6880f1e9453956fe102c26b80730a3",
    "state": {
        "('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')": {
            "variant":
                "standard",
            "digest":
                "pdv2:d3ad9796f3d96516d5c0aaeb2753dff47cb253994cd5b476b95334f28f7209a6",
            "snapshot_fen":
                "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
        },
        "('standard', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', 'KQkq', '-')": {
            "variant":
                "standard",
            "digest":
                "pdv2:8fa14e0cdbd23096c8345b18bfadad1f8d46c402f3986c101dbbe66504dbc8f1",
            "snapshot_fen":
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        },
    },
}


PROBES = _probe_builders()
PROBE_MANIFEST = (
    "request-none",
    "request-true",
    "request-zero",
    "request-float",
    "request-text",
    "request-list",
    "request-dict-subclass",
    "request-key-from_schema-str-subclass",
    "request-key-from_schema-colliding-hash",
    "request-value-from_schema-none",
    "request-value-from_schema-true",
    "request-value-from_schema-zero",
    "request-value-from_schema-float",
    "request-value-from_schema-list",
    "request-value-from_schema-dict",
    "request-value-from_schema-empty",
    "request-value-from_schema-str-subclass",
    "request-value-from_schema-huge-int",
    "request-key-to_schema-str-subclass",
    "request-key-to_schema-colliding-hash",
    "request-value-to_schema-none",
    "request-value-to_schema-true",
    "request-value-to_schema-zero",
    "request-value-to_schema-float",
    "request-value-to_schema-list",
    "request-value-to_schema-dict",
    "request-value-to_schema-empty",
    "request-value-to_schema-str-subclass",
    "request-value-to_schema-huge-int",
    "request-key-source_id-str-subclass",
    "request-key-source_id-colliding-hash",
    "request-value-source_id-none",
    "request-value-source_id-true",
    "request-value-source_id-zero",
    "request-value-source_id-float",
    "request-value-source_id-list",
    "request-value-source_id-dict",
    "request-value-source_id-empty",
    "request-value-source_id-str-subclass",
    "request-value-source_id-huge-int",
    "request-key-none",
    "source-none",
    "source-true",
    "source-zero",
    "source-float",
    "source-text",
    "source-list",
    "source-dict-subclass",
    "source-key-none",
    "source-key-true",
    "source-key-zero",
    "source-key-str-subclass",
    "source-key-colliding-hash",
    "source-record-none",
    "source-record-zero",
    "source-record-list",
    "source-record-text",
    "source-record-dict-subclass",
    "source-field-variant-none",
    "source-field-variant-true",
    "source-field-variant-zero",
    "source-field-variant-float",
    "source-field-variant-list",
    "source-field-variant-dict",
    "source-field-variant-empty",
    "source-field-variant-str-subclass",
    "source-field-variant-missing",
    "source-record-key-variant-str-subclass",
    "source-record-key-variant-colliding-hash",
    "source-field-digest-none",
    "source-field-digest-true",
    "source-field-digest-zero",
    "source-field-digest-float",
    "source-field-digest-list",
    "source-field-digest-dict",
    "source-field-digest-empty",
    "source-field-digest-str-subclass",
    "source-field-digest-missing",
    "source-record-key-digest-str-subclass",
    "source-record-key-digest-colliding-hash",
    "source-field-snapshot_fen-none",
    "source-field-snapshot_fen-true",
    "source-field-snapshot_fen-zero",
    "source-field-snapshot_fen-float",
    "source-field-snapshot_fen-list",
    "source-field-snapshot_fen-dict",
    "source-field-snapshot_fen-empty",
    "source-field-snapshot_fen-str-subclass",
    "source-field-snapshot_fen-missing",
    "source-record-key-snapshot_fen-str-subclass",
    "source-record-key-snapshot_fen-colliding-hash",
    "source-record-key-none",
    "source-record-extra-field",
    "source-field-self-referential",
    "source-field-deep-nested",
    "source-field-huge-int",
    "oracle-raises-value-error",
    "oracle-raises-runtime-error",
    "oracle-raises-keyboard-interrupt",
    "oracle-raises-system-exit",
    "oracle-raises-generator-exit",
    "oracle-returns-none",
    "oracle-returns-int",
    "oracle-returns-list",
    "oracle-returns-bytes",
    "oracle-returns-str-subclass",
    "oracle-returns-v1-digest",
    "oracle-returns-bad-grammar",
    "oracle-returns-uppercase-hex",
    "oracle-mutates-source-record",
    "oracle-mutates-source-container",
    "oracle-mutates-request-source-id",
    "oracle-clears-request",
    "oracle-mutates-then-raises",
)


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    request, source, oracle = build()
    if isinstance(oracle, str):
        oracle = ORACLES[oracle]
    inputs = (request, source)
    snap = _snap(inputs)
    if failure is ACCEPT:
        try:
            result = cls(oracle).migrate(request, source)
        except BaseException:  # noqa: BLE001 - any rejection fails
            return False
        return (_snap(inputs) == snap
                and _same_ordered(result, PROBE_EXPECT)
                and _not_aliased(result, inputs))
    try:
        cls(oracle).migrate(request, source)
    except MigrationError as error:
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and _snap(inputs) == snap)
    except BaseException:  # noqa: BLE001 - a raw escape is the defect
        return False
    return False


def _probe(cls, executed=None, first_only=False, only=None):
    """Every manifest row, every totality probe and the order probe
    through CLS; returns failing labels (only the first when
    FIRST_ONLY). Untrusted-component boundary: BaseException is
    caught."""
    failures = []
    jobs = []
    for section, manifest in MANIFESTS.items():
        rows = {r["name"]: r for r in CASES[section]}
        for meta in manifest:
            jobs.append((f"{section}:{meta[0]}",
                         lambda s=section, r=rows[meta[0]]:
                         _RUNNERS[s](cls, r)))
    for name in PROBE_MANIFEST:
        jobs.append((f"totality:{name}",
                     lambda n=name: _totality_ok(cls, n)))
    jobs.append((ORDER_LABEL, lambda: _order_ok(cls)))
    for label, job in jobs:
        if only is not None and label not in only:
            continue
        try:
            ok = job()
        except BaseException as error:  # noqa: BLE001
            failures.append(f"{label}:{type(error).__name__}")
        else:
            if executed is not None:
                executed.append(label)
            if not ok:
                failures.append(label)
        if first_only and failures:
            return failures
    return failures


# -- black-box mutants ---------------------------------------------------------
def _fake_receipt():
    return {"migration_id": "", "from_schema": "", "to_schema": "",
            "source_id": "", "target_id": "", "state": {}}


class AcceptsAll(MigrationEngine):
    def migrate(self, request, source):
        try:
            return super().migrate(request, source)
        except MigrationError:
            return _fake_receipt()


class PartialCommit(MigrationEngine):
    """Rejects with the right class after dropping a caller source
    record."""

    def migrate(self, request, source):
        try:
            return super().migrate(copy.deepcopy(request),
                                   copy.deepcopy(source))
        except MigrationError:
            if type(source) is dict and source:
                source.pop(next(iter(source)))
            raise


class InPlaceMigration(MigrationEngine):
    """Writes the migrated digests into the caller's records."""

    def migrate(self, request, source):
        out = super().migrate(request, source)
        for key, rec in source.items():
            rec["digest"] = out["state"][key]["digest"]
        return out


class WrongCode(MigrationEngine):
    def migrate(self, request, source):
        try:
            return super().migrate(request, source)
        except MigrationError as error:
            raise MigrationError(error.failure_class,
                                 "E_WRONG") from error


def _remap(frm, to):
    class Remap(MigrationEngine):
        def migrate(self, request, source):
            try:
                return super().migrate(request, source)
            except MigrationError as error:
                if error.failure_class == frm:
                    raise MigrationError(
                        to, FAILURE_MAPPING[to]) from error
                raise
    return Remap


class TrustsRecomputedSourceId(MigrationEngine):
    """Replaces the caller's source_id with the recomputed one
    (the conflicting-source check can never fire)."""

    def migrate(self, request, source):
        try:
            fixed = dict(request, source_id=state_id(source))
        except BaseException:  # noqa: BLE001
            fixed = request
        return super().migrate(fixed, source)


class DoubleOracleCall(MigrationEngine):
    def __init__(self, target_oracle):
        def twice(variant, fen):
            target_oracle(variant, fen)
            return target_oracle(variant, fen)
        super().__init__(twice)


class UnguardedOracle(MigrationEngine):
    """Calls the oracle once outside the boundary."""

    def migrate(self, request, source):
        if type(source) is dict:
            for rec in source.values():
                if type(rec) is dict:
                    self.target_oracle(rec.get("variant"),
                                       rec.get("snapshot_fen"))
                    break
        return super().migrate(request, source)


class AcceptsBadOracleOutput(MigrationEngine):
    """Coerces any invalid oracle output into a well-formed digest
    (skips output validation)."""

    def __init__(self, target_oracle):
        def coerce(variant, fen):
            try:
                key = target_oracle(variant, fen)
            except Exception:  # noqa: BLE001
                key = None
            if type(key) is not str or not V2_RE.fullmatch(key):
                key = "pdv2:" + "0" * 64
            return key
        super().__init__(coerce)


class SourceOrderState(MigrationEngine):
    """Receipt state in source insertion order, not canonical."""

    def migrate(self, request, source):
        out = super().migrate(request, source)
        out["state"] = {k: out["state"][k] for k in source}
        return out


class ReverseState(MigrationEngine):
    def migrate(self, request, source):
        out = super().migrate(request, source)
        out["state"] = dict(reversed(list(out["state"].items())))
        return out


class ValueRotate(MigrationEngine):
    def migrate(self, request, source):
        out = super().migrate(request, source)
        keys, values = list(out["state"]), list(out["state"].values())
        out["state"] = dict(zip(keys, values[1:] + values[:1],
                                strict=True))
        return out


class StaleTargetId(MigrationEngine):
    def migrate(self, request, source):
        out = super().migrate(request, source)
        out["target_id"] = out["source_id"]
        return out


class DropsRecord(MigrationEngine):
    def migrate(self, request, source):
        out = super().migrate(request, source)
        if out["state"]:
            out["state"].pop(next(iter(out["state"])))
        return out


def _swap_in_equal_copies(mapping):
    if type(mapping) is dict:
        for key in list(mapping):
            mapping[key] = copy.deepcopy(mapping[key])


class EqualCopySwapOnReject(MigrationEngine):
    def migrate(self, request, source):
        try:
            return super().migrate(copy.deepcopy(request),
                                   copy.deepcopy(source))
        except MigrationError:
            _swap_in_equal_copies(source)
            raise


class EqualCopySwapOnSuccess(MigrationEngine):
    def migrate(self, request, source):
        out = super().migrate(request, source)
        _swap_in_equal_copies(source)
        return out


class ReordersRequestKeys(MigrationEngine):
    """Rewrites the caller's request in reversed key order."""

    def migrate(self, request, source):
        out = super().migrate(request, source)
        items = list(request.items())
        request.clear()
        request.update(reversed(items))
        return out


class RawRecordFieldSet(MigrationEngine):
    """Compares a source record's raw key set before any guard."""

    def migrate(self, request, source):
        if isinstance(source, dict):
            for rec in source.values():
                if isinstance(rec, dict):
                    set(rec.keys()) == set(_FIELDS)  # noqa: B015
        return super().migrate(request, source)


class RawRequestKeySet(MigrationEngine):
    def migrate(self, request, source):
        if isinstance(request, dict):
            set(request.keys()) == set(REQUEST_FIELDS)  # noqa: B015
        return super().migrate(request, source)


class RawOnNonDictSource(MigrationEngine):
    def migrate(self, request, source):
        len(source.items())
        return super().migrate(request, source)


class CachedReceipt(MigrationEngine):
    """Class-level cache: a repeated target returns the SAME
    receipt object."""
    _cache = {}

    def migrate(self, request, source):
        out = super().migrate(request, source)
        return CachedReceipt._cache.setdefault(out["target_id"], out)


def _source_mutant(name, edits):
    """A white-box edit of the reference engine: each OLD must occur
    in the reference source (first occurrence is replaced)."""
    src = inspect.getsource(_reference.MigrationEngine)
    for old, new in edits:
        if old not in src:
            raise AssertionError(f"{name}: edit site missing: {old!r}")
        src = src.replace(old, new, 1)
    namespace = dict(vars(_reference))
    # the mutant raises the BOUND error class, so a correct rejection
    # counts as one under any binding (reference or production)
    namespace["MigrationError"] = MigrationError

    def _bound_fail(cls):
        raise MigrationError(cls, FAILURE_MAPPING[cls])

    namespace["_fail"] = _bound_fail
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return namespace["MigrationEngine"]


_I12 = " " * 12
_SOURCE_RESTORE = (
    f"{_I12}for rec, content in saved_recs.values():\n"
    f"{_I12}    rec.clear()\n{_I12}    rec.update(content)\n"
    f"{_I12}source_state.clear()\n"
    f"{_I12}source_state.update(saved_container)\n")
_REQUEST_RESTORE = (f"{_I12}dict.clear(request)\n"
                    f"{_I12}dict.update(request, saved_req_items)\n")
_FIELD_TYPE_CHECK = (
    'if type(rec["variant"]) is not str or \\\n'
    '                    type(rec["snapshot_fen"]) is not str or \\\n'
    '                    type(rec["digest"]) is not str:')
_FIELD_ISINSTANCE = (
    'if not isinstance(rec["variant"], str) or \\\n'
    '                    not isinstance(rec["snapshot_fen"], str) or \\\n'
    '                    not isinstance(rec["digest"], str):')

NoSourceRestore = _source_mutant(
    "no-source-restore", [(_SOURCE_RESTORE, f"{_I12}pass\n")])
LiveSourceRead = _source_mutant(
    "live-source-read", [("rec = frozen[key]", "rec = source_state[key]")])
ShallowFreeze = _source_mutant(
    "shallow-freeze",
    [("frozen = {key: dict(rec)\n"
      "                  for key, rec in source_state.items()}",
      "frozen = dict(source_state)"),
     (_SOURCE_RESTORE, f"{_I12}pass\n")])
NoRequestRestore = _source_mutant(
    "no-request-restore", [(_REQUEST_RESTORE, f"{_I12}pass\n")])
LiveRequestReread = _source_mutant(
    "live-request-reread",
    [("            target_id = state_id(staged)\n",
      "            target_id = state_id(staged)\n"
      "            frozen_req[\"source_id\"] = request[\"source_id\"]\n")])
IsinstanceRecordFields = _source_mutant(
    "isinstance-record-fields", [(_FIELD_TYPE_CHECK, _FIELD_ISINSTANCE)])

# mutants that only the live-mutation / str-subclass-value probes kill
NEW_PROBE_MUTANTS = {
    "no-source-restore": NoSourceRestore,
    "live-source-read": LiveSourceRead,
    "shallow-freeze": ShallowFreeze,
    "no-request-restore": NoRequestRestore,
    "live-request-reread": LiveRequestReread,
    "isinstance-record-fields": IsinstanceRecordFields,
}

MUTANTS = {
    **NEW_PROBE_MUTANTS,
    "cached-receipt": CachedReceipt,
    "accepts-all": AcceptsAll,
    "partial-commit": PartialCommit,
    "in-place-migration": InPlaceMigration,
    "wrong-code": WrongCode,
    "conflicting-as-malformed": _remap(CS, MMR),
    "unknown-as-malformed": _remap(UM, MMR),
    "divergent-as-malformed": _remap(DVT, MMR),
    "trusts-recomputed-source-id": TrustsRecomputedSourceId,
    "double-oracle-call": DoubleOracleCall,
    "unguarded-oracle": UnguardedOracle,
    "accepts-bad-oracle-output": AcceptsBadOracleOutput,
    "source-order-state": SourceOrderState,
    "reverse-state": ReverseState,
    "value-rotate": ValueRotate,
    "stale-target-id": StaleTargetId,
    "drops-record": DropsRecord,
    "equal-copy-swap-on-reject": EqualCopySwapOnReject,
    "equal-copy-swap-on-success": EqualCopySwapOnSuccess,
    "reorders-request-keys": ReordersRequestKeys,
    "raw-record-field-set": RawRecordFieldSet,
    "raw-request-key-set": RawRequestKeySet,
    "raw-on-non-dict-source": RawOnNonDictSource,
}

MUTANT_TARGETS = {
    "no-source-restore": "totality:oracle-mutates-source-record",
    "live-source-read": "totality:oracle-mutates-source-container",
    "shallow-freeze": "totality:oracle-mutates-source-record",
    "no-request-restore": "totality:oracle-mutates-request-source-id",
    "live-request-reread": "totality:oracle-mutates-request-source-id",
    "isinstance-record-fields":
        "totality:source-field-digest-str-subclass",
    "cached-receipt": "happy:migrate-single-record-source",
    "trusts-recomputed-source-id": "malformed:source-id-mismatch",
    "double-oracle-call": "happy:migrate-three-record-source",
    "unguarded-oracle": "malformed:oracle-raises",
    "accepts-bad-oracle-output": "malformed:oracle-bad-grammar-output",
    "source-order-state": ORDER_LABEL,
    "reverse-state": ORDER_LABEL,
    "value-rotate": ORDER_LABEL,
    "in-place-migration": "happy:migrate-single-record-source",
    "equal-copy-swap-on-reject": "malformed:source-id-mismatch",
    "equal-copy-swap-on-success": "happy:migrate-single-record-source",
    "reorders-request-keys": "happy:migrate-single-record-source",
    "raw-record-field-set":
        "totality:source-record-key-digest-str-subclass",
    "raw-request-key-set":
        "totality:request-key-source_id-str-subclass",
    "raw-on-non-dict-source": "totality:source-none",
    "conflicting-as-malformed":
        "rollback:rejected-conflicting-source-then-valid-migrate",
    "unknown-as-malformed": "malformed:request-noop-unregistered",
    "divergent-as-malformed": "malformed:oracle-non-str-output",
    "stale-target-id": "boundary:migrate-two-record-source-boundary",
    "drops-record": "happy:migrate-two-record-source",
}


# -- kill-proof: substitution mutants vs the closure ---------------------------
def _payload(row):
    return {k: v for k, v in row.items() if k != "name"}


def _regen(row, prefix=""):
    """Recompute the request source id and pinned receipt from the
    reference for a mutated source."""
    req = row[prefix + "request"]
    req["source_id"] = state_id(row[prefix + "source"])
    row["expect"] = _reference.MigrationEngine(pdv2_oracle).migrate(
        copy.deepcopy(req), copy.deepcopy(row[prefix + "source"]))


def _erasures(section, row):
    """Single-edge erasures of ROW, still executable where it
    matters, with the pinned receipt regenerated from the reference
    so only the closure can kill them."""
    out = []
    if section in ("happy", "boundary"):
        for n, key in enumerate(sorted(row["source"])):
            m = copy.deepcopy(row)
            m["source"].pop(key)
            _regen(m)
            out.append((f"drops-{n}", m))
        for label, fen in (("adds-e4", _FEN_E4),
                           ("adds-kings", _FEN_KINGS),
                           ("adds-start", _FEN_START)):
            rec = _node(fen)
            if _identity(rec) in row["source"]:
                continue
            m = copy.deepcopy(row)
            m["source"][_identity(rec)] = rec
            m["source"] = dict(sorted(m["source"].items()))
            _regen(m)
            out.append((label, m))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        rep = _repaired(row)
        fixed.update(request=rep["request"], source=rep["source"],
                     oracle=rep["oracle"])
        out.append(("defect-repaired", fixed))
    else:
        unfixed = copy.deepcopy(row)
        unfixed["then_request"] = copy.deepcopy(row["request"])
        out.append(("follow-up-not-fixed", unfixed))
        drift = copy.deepcopy(row)
        drift["then_source"].pop(sorted(drift["then_source"])[0])
        _regen(drift, "then_")
        out.append(("follow-up-source-drift", drift))
        swapped = copy.deepcopy(row)
        swapped["oracle"], swapped["then_oracle"] = \
            row["then_oracle"], row["oracle"]
        out.append(("oracles-swapped", swapped))
    # An erasure identical to its row is an equivalent mutant (e.g.
    # swapping two identical oracles), not a defect: drop it.
    return [(label, m) for label, m in out if m != row]


def _substitution_mutants():
    out = []
    labels = [(s, r["name"]) for s in MANIFESTS for r in CASES[s]]
    for sa, na in labels:
        for sb, nb in labels:
            if (sa, na) == (sb, nb):
                continue
            m = copy.deepcopy(CASES)
            i = [r["name"] for r in m[sa]].index(na)
            src = next(r for r in CASES[sb] if r["name"] == nb)
            m[sa][i] = dict(copy.deepcopy(src), name=na)
            out.append((f"payload:{sa}:{na}<-{sb}:{nb}", m))
    for section in MANIFESTS:
        rows = CASES[section]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                m = copy.deepcopy(CASES)
                m[section][i]["name"], m[section][j]["name"] = \
                    rows[j]["name"], rows[i]["name"]
                out.append((f"name-swap:{section}:{i}:{j}", m))
        for label, fn in (("reversed", lambda r: r.reverse()),
                          ("dropped", lambda r: r.pop()),
                          ("duplicated",
                           lambda r: r.append(copy.deepcopy(r[0])))):
            m = copy.deepcopy(CASES)
            fn(m[section])
            out.append((f"{label}:{section}", m))
        for i, row in enumerate(CASES[section]):
            for label, mutant in _erasures(section, row):
                m = copy.deepcopy(CASES)
                m[section][i] = mutant
                out.append((f"erased:{section}:{row['name']}:{label}",
                            m))
    return out


# -- tests ---------------------------------------------------------------------
def test_closure():
    _validate_closure(CASES)


def test_row_digest_table_is_closed():
    assert set(ROW_DIGESTS) == {
        f"{s}:{m[0]}" for s, man in MANIFESTS.items() for m in man}
    for section in MANIFESTS:
        for row in CASES[section]:
            assert ROW_DIGESTS[f"{section}:{row['name']}"] == \
                _row_digest(row)


def test_no_equivalent_payload_pairs():
    """No two rows share a payload, so every substitution is a
    real mutant."""
    rows = [_payload(r) for s in MANIFESTS for r in CASES[s]]
    assert all(a != b for i, a in enumerate(rows)
               for b in rows[i + 1:])


def test_probe_manifest_closed_and_ordered():
    assert list(PROBES) == list(PROBE_MANIFEST)
    assert len(PROBE_MANIFEST) == 114


def test_order_probe_is_discriminating():
    request, source = _order_inputs()
    canonical = sorted(source)
    assert list(source) not in (canonical, canonical[::-1])
    assert list(ORDER_EXPECT["state"]) == canonical
    _check_receipt("order-probe", request, source, ORDER_EXPECT)


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(MigrationEngine, executed=executed) == []
    assert executed == [f"{s}:{m[0]}" for s, man in MANIFESTS.items()
                        for m in man] + [
        f"totality:{n}" for n in PROBE_MANIFEST] + [ORDER_LABEL]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_red(name):
    assert _probe(MUTANTS[name], first_only=True), \
        f"{name}: mutant passed battery"


def test_mutants_fail_on_their_target_rows():
    for name, label in MUTANT_TARGETS.items():
        failures = _probe(MUTANTS[name], only={label})
        assert [":".join(f.split(":")[:2]) for f in failures] == \
            [label], (name, failures)


@pytest.mark.parametrize("with_digests", [True, False],
                         ids=["with-digests", "digests-neutralized"])
def test_closure_kills_substitution_mutants(with_digests,
                                            monkeypatch):
    """Every cross-section payload substitution, rename, reorder,
    drop, duplicate and single-edge erasure is killed by
    _validate_closure with the digest table live AND with
    _row_digest monkeypatched to return the table value (digests
    neutralized)."""
    if not with_digests:
        monkeypatch.setattr(
            sys.modules[__name__], "_row_digest",
            lambda row: ROW_DIGESTS.get(
                f"{_section_of(row)}:{row['name']}", ""))
    mutants = _substitution_mutants()
    assert len(mutants) > 450
    survivors = []
    for label, m in mutants:
        try:
            _validate_closure(m)
        except (AssertionError, ValueError, KeyError, TypeError):
            continue
        survivors.append(label)
    assert survivors == [], survivors


def test_every_row_has_a_real_erasure():
    """Dropping equivalent erasures leaves every row with at least
    one real single-edge erasure, each differing from the row."""
    for section in MANIFESTS:
        for row in CASES[section]:
            erasures = _erasures(section, row)
            assert erasures, (section, row["name"])
            assert all(m != row for _, m in erasures)


NEW_PROBE_LABELS = frozenset(
    [f"totality:source-field-{f}-str-subclass" for f in _FIELDS]
    + [f"totality:oracle-{k}" for k in _LIVE_MUTATIONS]
    + ["totality:oracle-mutates-then-raises"])


@pytest.mark.parametrize("name", sorted(NEW_PROBE_MUTANTS))
def test_guard_edit_mutants_die_only_on_new_probes(name):
    """Each one-guard edit of the reference survives every other row
    and probe: only the live-mutation / str-subclass-value probes
    kill it (the reference is green on all of them)."""
    failures = [":".join(f.split(":")[:2])
                for f in _probe(NEW_PROBE_MUTANTS[name])]
    assert failures, name
    assert set(failures) <= NEW_PROBE_LABELS, (name, failures)
    assert _probe(MigrationEngine, only=NEW_PROBE_LABELS) == []
