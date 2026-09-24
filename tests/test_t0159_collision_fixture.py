"""T0159: graph collision conformance fixture.

The fixture must PROVE happy, boundary, malformed and rollback behavior
against the T0158 graph-collision contract. Every row is an operation
script (initial inserts, then one insert, merge or bucket-key lookup)
under a named oracle from a closed registry, executed against the
contract-derived reference table in tests.test_t0158_collision_contract
(nothing is re-implemented here). Pinned tables (records in canonical
identity order, canonical view, bucket sizes) were computed from that
reference at authoring time, so contract or derivation drift breaks this
battery. Every malformed row is discriminating: applying ONLY its
declared single-locus repair (the oracle or the operation) makes the
same script succeed. Rollback rows prove a rejected operation leaves the
table identical by value and by stored-record identity before a valid
follow-up operation succeeds with its pinned table.

DESIGN CAUTION: the reference is derived from the same contract, so this
fixture proves fixture/contract CONSISTENCY, not production behavior. The
later runtime work must execute these same rows against a separately
implemented runtime.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.test_t0158_collision_contract as t0158  # noqa: E402
from tests.test_t0113_position_digest_contract import digest_fen  # noqa: E402
from tests.test_t0158_collision_contract import (  # noqa: E402
    CollisionError,
    CollisionProbe,
    raw_oracle,
    stateful_oracle,
)
from tools.collision_contract_lint import CONTRACT, ERROR_ENUM, FAILURE_MAPPING  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "collision" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = frozenset(_CC["failures"]["classes"])
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
CONSTANT = "pdv1:" + "0" * 64
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
LEGAL_EP = "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
OP_KINDS = {
    "insert": {"kind", "variant", "fen"},
    "merge": {"kind", "records"},
    "lookup": {"kind", "bucket_key"},
}
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}


class _Str(str):
    pass


def _interrupt(variant, fen):
    raise KeyboardInterrupt


def _corrupt(table):
    for record in list(table.identity_index.values()):
        record["digest"] = "x"
    table.buckets.clear()
    table.identity_index.clear()


def _rebind(table):
    """Attribute REPLACEMENT (not in-place mutation): the receiver's
    containers are swapped for fresh empty ones."""
    table.buckets = {}
    table.identity_index = {}


def _oracle(name, holder):
    """The closed oracle registry. HOLDER[0] is the live table, for the
    oracles that close over the receiver; HOLDER[1] is the row state
    (armed once the initial inserts are done, so the hostile oracles act
    on the row's operation only)."""
    if name == "constant":
        return lambda variant, fen: CONSTANT
    if name == "real":
        return digest_fen
    if name == "raw":
        return raw_oracle
    if name == "stateful":
        return stateful_oracle()
    if name == "raising":

        def raising(variant, fen):
            raise ValueError("oracle exploded")

        return raising
    if name == "interrupt":
        return _interrupt
    if name == "invalid-format":
        return lambda variant, fen: "pdv1:" + "g" * 64
    if name == "str-subclass":
        return lambda variant, fen: _Str(CONSTANT)
    if name == "non-str":
        return lambda variant, fen: CONSTANT.encode()
    if name == "reentrant":

        def reentrant(variant, fen):
            # ONE-SHOT: re-enters the receiver exactly once, so a
            # guardless table does not recurse into RecursionError (which
            # would map to the same class) - it succeeds and is caught
            state = holder[1]
            if state["armed"] and not state["reentered"]:
                state["reentered"] = True
                holder[0].insert("standard", KINGS)
            return CONSTANT

        return reentrant
    if name == "corrupt-then-return":

        def corrupt_then_return(variant, fen):
            if holder[1]["armed"]:
                _corrupt(holder[0])
            return CONSTANT

        return corrupt_then_return
    if name == "corrupt-then-raise-on-ep":

        def corrupting(variant, fen):
            if fen == LEGAL_EP:
                _corrupt(holder[0])
                raise RuntimeError("after corrupting the receiver")
            return CONSTANT

        return corrupting
    if name == "rebind-then-return":

        def rebind_then_return(variant, fen):
            if holder[1]["armed"]:
                _rebind(holder[0])
            return CONSTANT

        return rebind_then_return
    if name == "rebind-then-raise":

        def rebind_then_raise(variant, fen):
            # rebinds and raises on the e.p. FEN only, so the rollback
            # row's valid follow-up runs under the same oracle
            if holder[1]["armed"] and fen == LEGAL_EP:
                _rebind(holder[0])
                raise RuntimeError("after rebinding the receiver")
            return CONSTANT

        return rebind_then_raise
    raise KeyError(name)


ORACLES = (
    "constant",
    "real",
    "raw",
    "stateful",
    "raising",
    "interrupt",
    "invalid-format",
    "str-subclass",
    "non-str",
    "reentrant",
    "corrupt-then-raise-on-ep",
    "corrupt-then-return",
    "rebind-then-return",
    "rebind-then-raise",
)


class _Other:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _table_of(oracle_name, initial, cls=CollisionProbe):
    """Build the row's table; the oracle is wrapped in a call counter that
    counts only the row's operation (after the initial inserts)."""
    state = {"armed": False, "reentered": False, "calls": 0}
    holder = []
    inner = _oracle(oracle_name, holder)

    def counted(variant, fen):
        if state["armed"]:
            state["calls"] += 1
        return inner(variant, fen)

    table = cls(counted)
    holder.extend([table, state])
    for variant, fen in initial:
        table.insert(variant, fen)
    state["armed"] = True
    table.fixture_state = state
    return table


def _apply(table, op):
    if op["kind"] == "insert":
        return table.insert(op["variant"], op["fen"])
    if op["kind"] == "merge":
        return table.merge(_Other(copy.deepcopy(op["records"])))
    return table.lookup_by_bucket_key(op["bucket_key"])


def _pinned(table):
    """The observable table: records in canonical identity order, the
    canonical view, and bucket sizes by key."""
    ordered = sorted(table.identity_index.items(), key=lambda item: repr(item[0]))
    return {
        "records": [dict(record) for _, record in ordered],
        "view": table.canonical_view(),
        "buckets": {key: len(table.buckets[key]) for key in sorted(table.buckets)},
    }


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


def _state(table):
    """Value and stored-record identity of both structures."""
    return (
        copy.deepcopy(table.identity_index),
        copy.deepcopy(table.buckets),
        {repr(k): _Pin(v) for k, v in table.identity_index.items()},
        {k: [_Pin(r) for r in v] for k, v in table.buckets.items()},
    )


def _run(row, op=None, oracle=None):
    table = _table_of(oracle or row["oracle"], row["initial"])
    _apply(table, op or row["op"])
    return _pinned(table)


def _rejects(row, op, oracle=None):
    table = _table_of(oracle or row["oracle"], row["initial"])
    before = _state(table)
    with pytest.raises(CollisionError) as exc:
        _apply(table, op)
    return exc.value, before, table


def _rows(section):
    return CASES[section]


def _names(section):
    return [row["name"] for row in CASES[section]]


def _row(section, name):
    return next(row for row in CASES[section] if row["name"] == name)


# -- structure --------------------------------------------------------------------


def _validate_op(op):
    assert type(op) is dict and op.get("kind") in OP_KINDS and set(op) == OP_KINDS[op["kind"]]
    if op["kind"] == "insert":
        assert type(op["variant"]) is str and type(op["fen"]) is str
    elif op["kind"] == "merge":
        assert type(op["records"]) is list


def _validate_structure(cases):
    assert set(cases) == TOP
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == _CC["versioning"]["base_path"]
    assert cases["schema"] == 1
    for section in SECTIONS:
        rows = cases[section]
        assert type(rows) is list and rows, section
        names = [row["name"] for row in rows]
        assert len(names) == len(set(names)), section
        for row in rows:
            assert row["oracle"] in ORACLES, row["name"]
            assert type(row["initial"]) is list
            assert all(type(p) is list and len(p) == 2 for p in row["initial"])
            if section in ("happy", "boundary"):
                assert set(row) == {"name", "why", "oracle", "initial", "op", "expect"}
                _validate_op(row["op"])
            elif section == "malformed":
                assert set(row) == {
                    "name",
                    "defect",
                    "oracle",
                    "initial",
                    "op",
                    "expect_failure",
                    "oracle_calls",
                    "minimal_repair",
                }
                assert type(row["oracle_calls"]) is int and row["oracle_calls"] >= 0
                _validate_op(row["op"])
                assert row["expect_failure"] in FAILURE_CLASSES
                repair = row["minimal_repair"]
                assert type(repair) is dict and len(repair) == 1
                assert set(repair) <= {"oracle", "op"}
                if "op" in repair:
                    _validate_op(repair["op"])
                    assert repair["op"] != row["op"]
                else:
                    assert repair["oracle"] in ORACLES and repair["oracle"] != row["oracle"]
            else:
                assert set(row) == {
                    "name",
                    "why",
                    "oracle",
                    "initial",
                    "bad",
                    "expect_failure",
                    "good",
                    "expect",
                }
                _validate_op(row["bad"])
                _validate_op(row["good"])
                assert row["expect_failure"] in FAILURE_CLASSES
                assert row["initial"], "rollback needs a populated table"


def test_fixture_structure():
    _validate_structure(CASES)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop("notes"),
        lambda c: c.update(contract="graph-collisions"),
        lambda c: c["happy"].append(copy.deepcopy(c["happy"][0])),
        lambda c: c["malformed"][0].update(minimal_repair={"oracle": "constant", "op": {}}),
        lambda c: c["malformed"][0].update(expect_failure="collision"),
        lambda c: c["happy"][0].update(oracle="md5"),
        lambda c: c["happy"][0]["op"].update(extra=1),
        lambda c: c["rollback"][0].update(initial=[]),
    ],
    ids=[
        "drop-notes",
        "wrong-contract",
        "duplicate-name",
        "two-locus-repair",
        "undeclared-class",
        "unknown-oracle",
        "extra-op-field",
        "empty-rollback-table",
    ],
)
def test_structure_mutations_fail(mutate):
    cases = copy.deepcopy(CASES)
    mutate(cases)
    with pytest.raises((AssertionError, KeyError)):
        _validate_structure(cases)


def test_failure_class_coverage():
    covered = {row["expect_failure"] for row in _rows("malformed")}
    assert covered == FAILURE_CLASSES
    assert {row["expect_failure"] for row in _rows("rollback")} >= {
        "malformed_collision_record",
        "accelerator_inconsistent",
    }
    assert {row["oracle"] for s in SECTIONS for row in _rows(s)} | {
        row["minimal_repair"].get("oracle") for row in _rows("malformed")
    } >= set(ORACLES)


# -- happy and boundary -----------------------------------------------------------


@pytest.mark.parametrize(
    "section,name", [(s, n) for s in ("happy", "boundary") for n in _names(s)] if CASES else []
)
def test_pinned_tables(section, name):
    row = _row(section, name)
    assert _run(row) == row["expect"]
    for record in row["expect"]["records"]:
        assert set(record) == RECORD_FIELDS


def test_guarantees_under_total_collision():
    # count-exact and no-merge: N distinct identities in one bucket are N records
    four = _row("happy", "total-collision-four-distinct")["expect"]
    assert list(four["buckets"].values()) == [4] and len(four["records"]) == 4
    # order-insensitivity: reversed arrival order, same observable table
    assert _row("happy", "total-collision-reversed-arrival")["expect"] == four
    # no-fork: one identity with different clocks stays one record
    fork = _row("happy", "total-collision-same-identity-new-clocks")["expect"]
    assert len(fork["records"]) == 1 and list(fork["buckets"].values()) == [1]
    # transparency: collision-free and total-collision tables agree on
    # records' identities and order
    free = _row("happy", "collision-free-baseline")["expect"]
    total = _row("happy", "total-collision-same-set")["expect"]
    assert free["view"] == total["view"]
    assert [r["snapshot_fen"] for r in free["records"]] == [
        r["snapshot_fen"] for r in total["records"]
    ]
    assert len(free["buckets"]) == 3 and list(total["buckets"].values()) == [3]


def test_tampered_pin_is_detected():
    row = copy.deepcopy(_row("happy", "total-collision-four-distinct"))
    row["expect"]["records"] = row["expect"]["records"][::-1]
    assert _run(row) != row["expect"]


# -- malformed --------------------------------------------------------------------


@pytest.mark.parametrize("name", _names("malformed") if CASES else [])
def test_malformed(name):
    row = _row("malformed", name)
    error, before, table = _rejects(row, row["op"])
    assert error.failure_class == row["expect_failure"]
    assert error.code == FAILURE_MAPPING[row["expect_failure"]] and error.code in ERROR_ENUM
    assert _state(table) == before
    # pinned oracle calls: a semantic or shape rejection makes ZERO calls
    assert table.fixture_state["calls"] == row["oracle_calls"]


@pytest.mark.parametrize("name", _names("malformed") if CASES else [])
def test_malformed_minimal_repair_is_discriminating(name):
    row = _row("malformed", name)
    repair = row["minimal_repair"]
    table = _table_of(repair.get("oracle", row["oracle"]), row["initial"])
    _apply(table, repair.get("op", row["op"]))


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(_rows("happy")[0])
    with pytest.raises(pytest.fail.Exception):
        _rejects(row, row["op"])


# -- rollback ---------------------------------------------------------------------


@pytest.mark.parametrize("name", _names("rollback") if CASES else [])
def test_rollback(name):
    row = _row("rollback", name)
    error, before, table = _rejects(row, row["bad"])
    assert error.failure_class == row["expect_failure"]
    assert error.code == FAILURE_MAPPING[row["expect_failure"]]
    assert _state(table) == before
    _apply(table, row["good"])
    assert _pinned(table) == row["expect"]


def test_rollback_expect_matches_a_clean_run():
    for row in _rows("rollback"):
        clean = _table_of(row["oracle"], row["initial"])
        _apply(clean, row["good"])
        assert _pinned(clean) == row["expect"], row["name"]


# -- the rows discriminate: reference mutants each fail a pinned row --------------


class _BucketDedup(CollisionProbe):
    """Bucket membership decides equality: first writer wins."""

    def _staged_insert(self, rec, identity, key):
        if self.buckets.get(key):
            return self.buckets[key][0]
        return super()._staged_insert(rec, identity, key)


class _ArrivalOrder(CollisionProbe):
    """The observable view follows arrival order, not canonical order."""

    def canonical_view(self):
        return [repr(identity) for identity in self.identity_index]


class _NoRestore(CollisionProbe):
    """A rejected operation does not undo receiver corruption."""

    def _restore_live(self, *saved):
        return None


class _TrustKey(CollisionProbe):
    """Equal identity with a divergent key is accepted, not rejected."""

    def _staged_insert(self, rec, identity, key):
        existing = self.identity_index.get(identity)
        if existing is not None:
            return existing
        return super()._staged_insert(rec, identity, key)


def _row_fails(cls, section, name):
    row = _row(section, name)
    try:
        if section == "rollback":
            table = _table_of(row["oracle"], row["initial"], cls)
            before = _state(table)
            try:
                _apply(table, row["bad"])
                return True
            except CollisionError:
                pass
            if _state(table) != before:
                return True
            _apply(table, row["good"])
            return _pinned(table) != row["expect"]
        if section == "malformed":
            table = _table_of(row["oracle"], row["initial"], cls)
            try:
                _apply(table, row["op"])
            except CollisionError as error:
                return (
                    error.failure_class != row["expect_failure"]
                    or table.fixture_state["calls"] != row["oracle_calls"]
                )
            return True
        table = _table_of(row["oracle"], row["initial"], cls)
        _apply(table, row["op"])
        return _pinned(table) != row["expect"]
    except Exception:  # noqa: BLE001 - a raw escape is also a caught mutant
        return True


# One-edit SOURCE mutants of the T0158 reference CollisionProbe: the module
# source with exactly one edit is executed into a fresh namespace that keeps
# the original CollisionError, so the fixture helpers see the same failures.
SOURCE_MUTANTS = {
    "insert-no-prestaging-restore": (
        "            # discard any direct live-receiver corruption by the\n"
        "            # untrusted oracle BEFORE staging reads the structures\n"
        "            self._restore_live(orig, saved_b, saved_i,\n"
        "                                 saved_recs)\n",
        "",
    ),
    "merge-no-failure-restore": (
        "            # corruption by receiver-closing oracle code never\n"
        "            # survives a rejected merge\n"
        "            self._restore_live(orig, saved_b, saved_i,\n"
        "                                 saved_recs)\n",
        "",
    ),
    "merge-no-success-restore": (
        "        self._restore_live(orig, saved_b, saved_i, saved_recs)\n"
        "        self.buckets = staged.buckets\n",
        "        self.buckets = staged.buckets\n",
    ),
    "restore-no-rebind": ("        self.buckets, self.identity_index = orig\n", ""),
    "no-reentrancy-guard": ("        if self._active:\n", "        if False:\n"),
    "shape-len-not-key-set": ("set(rec) != _RECORD_FIELDS", "len(rec) != len(_RECORD_FIELDS)"),
    "no-semantic-phase": ("            self._validate_record_semantics(frozen)\n", ""),
}


def _source_mutant(name):
    before, after = SOURCE_MUTANTS[name]
    source = Path(t0158.__file__).read_text()
    assert source.count(before) == 1, name
    module = types.ModuleType(f"t0158_mutant_{name.replace('-', '_')}")
    module.__file__ = t0158.__file__
    exec(compile(source.replace(before, after), t0158.__file__, "exec"), module.__dict__)
    module.CollisionError = CollisionError
    return module.CollisionProbe


@pytest.mark.parametrize(
    "name,section,row",
    [
        ("insert-no-prestaging-restore", "happy", "insert-under-corrupt-then-return-oracle"),
        ("merge-no-failure-restore", "rollback", "merge-corrupting-oracle-restored"),
        ("merge-no-success-restore", "happy", "merge-under-corrupt-then-return-oracle"),
        ("restore-no-rebind", "happy", "insert-under-rebind-then-return-oracle"),
        ("restore-no-rebind", "rollback", "rebind-oracle-restored"),
        ("restore-no-rebind", "rollback", "merge-rebind-oracle-restored"),
        ("no-reentrancy-guard", "malformed", "reentrant-oracle"),
        ("shape-len-not-key-set", "malformed", "merge-record-renamed-key"),
        ("no-semantic-phase", "malformed", "merge-record-unnormalized-clocks"),
        ("no-semantic-phase", "malformed", "merge-record-bad-snapshot"),
        ("no-semantic-phase", "malformed", "malformed-merge-record-under-raising-oracle"),
    ],
)
def test_reference_source_mutant_fails_a_pinned_row(name, section, row):
    assert _row_fails(_source_mutant(name), section, row)
    assert not _row_fails(CollisionProbe, section, row)


@pytest.mark.parametrize(
    "cls,section,name",
    [
        (_BucketDedup, "happy", "total-collision-four-distinct"),
        (_BucketDedup, "happy", "total-collision-same-set"),
        (_ArrivalOrder, "happy", "total-collision-reversed-arrival"),
        (_NoRestore, "rollback", "corrupting-oracle-restored"),
        (_TrustKey, "malformed", "raw-oracle-clock-divergence"),
        (_TrustKey, "malformed", "stateful-oracle-divergence"),
        (_TrustKey, "rollback", "raw-divergence-then-insert"),
    ],
    ids=[
        "bucket-dedup-four",
        "bucket-dedup-three",
        "arrival-order",
        "no-restore",
        "trust-key-raw",
        "trust-key-stateful",
        "trust-key-rollback",
    ],
)
def test_reference_mutant_fails_a_pinned_row(cls, section, name):
    assert _row_fails(cls, section, name)
    assert not _row_fails(CollisionProbe, section, name)


def test_fixture_digest_is_stable():
    """Row payloads are closed: any edit to the file changes this digest,
    so a silent fixture rewrite fails review here."""
    text = json.dumps(CASES, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(text.encode()).hexdigest() == FIXTURE_SHA256


FIXTURE_SHA256 = "37d285f7065ae33e0cfb804dab1d58043b918254ba90cdc47df2f2bdc2ee7623"


class _PinProbeTable:
    def __init__(self):
        self.identity_index = {"k": {"r": 1}}
        self.buckets = {"b": []}


def test_state_pins_replaced_records():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    table = _PinProbeTable()
    before = _state(table)
    table.identity_index["k"] = {"r": 1}
    table.identity_index["k"] = {"r": 1}
    assert _state(table) != before
