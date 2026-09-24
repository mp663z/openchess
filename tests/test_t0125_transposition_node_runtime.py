"""T0125: production transposition-node runtime proof
(graph/transposition_node.py).

The T0124 red battery is the behavioral proof: it runs against
graph.transposition_node with only its binding import switched. This
file proves the switch is exactly that binding, that production never
imports the test package and links only the shipped graph.fen and
graph.position_digest runtimes, that production matches the contract
reference on every fixture case and on a hostile probe corpus, that
the untrusted digest oracle fails closed on every forged typed error
of this module and of each linked module, and that one-edit source
mutants of the production module are killed.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import itertools
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph.transposition_node as prod  # noqa: E402
from graph.fen import FenError  # noqa: E402
from graph.position_digest import DigestError, digest_fen  # noqa: E402
from tests import test_t0086_fen_contract as ref_fen  # noqa: E402
from tests import test_t0113_position_digest_contract as ref_digest  # noqa: E402
from tests import test_t0122_transposition_node_contract as ref  # noqa: E402
from tests.test_t0123_fixture import CASES, _repaired  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402
from tools.variant_runtime import VariantError  # noqa: E402

RED = ROOT / "tests" / "test_t0124_transposition_node_red.py"
PRODUCTION = ROOT / "graph" / "transposition_node.py"
ORACLE_BINDING = (
    "from tests.test_t0122_transposition_node_contract import (\n"
    "    NodeError,\n    NodeTable,\n    _docs,\n    validate_record,\n)\n")
PRODUCTION_BINDING = (
    "from graph.transposition_node import NodeError, NodeTable, "
    "validate_record\n"
    "from graph.transposition_node import load_docs as _docs\n")
# sha256 of tests/test_t0124_transposition_node_red.py as merged
RED_AS_MERGED_SHA256 = (
    "5523f9476680f60789b3b3813d35786e0d7eeae5382ad72d71891f1571d0d24c")

DOCS = ref._docs()
STARTPOS = ref.STARTPOS
AFTER_E4 = ref.AFTER_E4
LEGAL_EP = ref.LEGAL_EP
KINGS = ref.KINGS


# -- R1: surface and links -----------------------------------------------------


def test_r1_production_imports_only_shipped_runtimes():
    tree = ast.parse(PRODUCTION.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module)
    assert not any(m.split(".")[0] == "tests" for m in mods)
    assert mods == {"__future__", "re", "pathlib", "yaml", "graph.fen",
                    "graph.position_digest"}


def test_r1_public_surface():
    assert prod.__all__ == ["FAILURE_MAPPING", "NodeError", "NodeTable",
                            "load_docs", "validate_record"]
    assert DOCS[0]["failures"]["mapping"] == prod.FAILURE_MAPPING
    assert prod.load_docs() == DOCS
    assert prod.load_docs()[0] is not prod.load_docs()[0]


def test_r1_red_switch_is_exactly_the_binding():
    text = RED.read_text()
    assert text.count(PRODUCTION_BINDING) == 1
    assert "tests.test_t0122_transposition_node_contract" not in text
    back = text.replace(PRODUCTION_BINDING, ORACLE_BINDING)
    assert hashlib.sha256(back.encode()).hexdigest() == RED_AS_MERGED_SHA256


# -- R2: parity with the contract reference ------------------------------------


def _outcome(fn, extra=()):
    try:
        return ("ok", fn())
    except (prod.NodeError, ref.NodeError, *extra) as exc:
        return ("err", exc.failure_class, exc.code)


def _insert_both(variant, fen, digest_fn=digest_fen):
    a = _outcome(lambda: prod.NodeTable(DOCS, digest_fn).insert(variant, fen))
    b = _outcome(lambda: ref.NodeTable(DOCS, digest_fn).insert(variant, fen))
    return a, b


def _validate_both(record, digest_fn=digest_fen):
    a = _outcome(lambda: prod.validate_record(*DOCS, copy.deepcopy(record),
                                              digest_fn))
    b = _outcome(lambda: ref.validate_record_bounded(
        *DOCS, copy.deepcopy(record), digest_fn))
    return a, b


class _S(str):
    pass


class _HK(str):
    def __hash__(self):
        return hash("variant")

    def __eq__(self, other):
        raise RuntimeError("hostile key compare")


INSERT_PROBES = [
    ("standard", STARTPOS), ("standard", AFTER_E4), ("standard", LEGAL_EP),
    ("standard", KINGS), ("standard", STARTPOS.replace(" 0 1", " 7 42")),
    ("standard", "not a fen"), ("standard", ""), ("standard", STARTPOS + " "),
    ("standard", " " + STARTPOS), ("standard", STARTPOS + "\n"),
    ("standard", STARTPOS.replace(" w ", " x ")),
    ("nope", STARTPOS), ("Standard", STARTPOS), ("standard\n", STARTPOS),
    ("", STARTPOS), (None, STARTPOS), (1, STARTPOS), ("standard", None),
    ("standard", 7), ("standard", [STARTPOS]),
]


@pytest.mark.parametrize("variant,fen", INSERT_PROBES,
                         ids=lambda v: repr(v)[:24])
def test_r2_insert_parity(variant, fen):
    a, b = _insert_both(variant, fen)
    assert a == b


def test_r2_str_subclass_inputs_fail_closed():
    for variant, fen in ((_S("standard"), STARTPOS),
                         ("standard", _S(STARTPOS))):
        with pytest.raises(prod.NodeError):
            prod.NodeTable(DOCS).insert(variant, fen)

_VARIANT_CALLS = []


class _VarEq(str):
    """str-subclass variant whose compare must never run."""

    def __eq__(self, other):
        _VARIANT_CALLS.append("eq")
        raise RuntimeError("hostile variant compare")

    def __ne__(self, other):
        _VARIANT_CALLS.append("ne")
        raise RuntimeError("hostile variant compare")

    __hash__ = str.__hash__


class _VarHash(str):
    """str-subclass variant that hashes like "standard" and logs every call."""

    def __hash__(self):
        _VARIANT_CALLS.append("hash")
        return hash("standard")

    def __eq__(self, other):
        _VARIANT_CALLS.append("eq")
        raise RuntimeError("hostile variant compare")

    def __ne__(self, other):
        _VARIANT_CALLS.append("ne")
        raise RuntimeError("hostile variant compare")


def _variant_subclass_red(mod):
    """A str-subclass variant ("standard", plain / __eq__-raises / hash-
    colliding) at NodeTable.insert: unknown_variant, no hostile method
    runs, arguments unchanged. The module's own exact-str variant guard
    runs before the registry `in` check, so it is load-bearing."""
    for form in (_S, _VarEq, _VarHash):
        args = [form("standard"), STARTPOS]
        snap = [(type(a), str.__str__(a)) for a in args]
        ids = [id(a) for a in args]
        _VARIANT_CALLS.clear()
        try:
            mod.NodeTable(DOCS).insert(*args)
            return True
        except mod.NodeError as err:
            if err.failure_class != "unknown_variant":
                return True
        if _VARIANT_CALLS or [id(a) for a in args] != ids or \
                [(type(a), str.__str__(a)) for a in args] != snap:
            return True
    return False


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


def test_r2_variant_subclass_is_unknown_variant_without_calls():
    assert not _variant_subclass_red(prod)


def _good_record(fen=STARTPOS):
    return ref.NodeTable(DOCS).insert("standard", fen)


def _record_probes():
    good = _good_record()
    out = [good, _good_record(LEGAL_EP), _good_record(AFTER_E4)]
    for key in list(good):
        r = dict(good)
        del r[key]
        out.append(r)
        # same-arity rename of one field
        out.append({**r, key + "_x": good[key]})
        out.append({**good, key: _S(good[key])})
        out.append({**good, key: 1})
    out += [
        {**good, "extra": 1}, {**good, "digest": good["digest"].upper()},
        {**good, "digest": good["digest"][:-1]},
        {**good, "snapshot_fen": STARTPOS.replace(" 0 1", " 3 9")},
        {**good, "snapshot_fen": STARTPOS.replace(" 0 1", " 3 1")},
        {**good, "snapshot_fen": STARTPOS.replace(" 0 1", " 0 9")},
        {**good, "snapshot_fen": AFTER_E4},
        # uncapturable ep target kept, digest CONSISTENT (identity digest)
        {"variant": "standard", "digest": digest_fen("standard", AFTER_E4),
         "snapshot_fen": AFTER_E4},
        {**good, "variant": "nope"},
        {**good, "digest": _good_record(KINGS)["digest"]},
        {"variant": "standard", "digest": good["digest"], 1: STARTPOS},
        [good], None, "record",
    ]
    return out


@pytest.mark.parametrize("i", range(len(_record_probes())))
def test_r2_validate_parity(i):
    record = _record_probes()[i]
    a, b = _validate_both(record)
    assert a == b


def test_r2_hostile_key_fails_closed_without_compare():
    good = _good_record()
    rec = {"digest": good["digest"], "snapshot_fen": good["snapshot_fen"],
           _HK("variant"): "standard"}
    with pytest.raises(prod.NodeError) as err:
        prod.validate_record(*DOCS, rec)
    assert err.value.failure_class == "malformed_node_record"


def _fixture_outcome(mod, case, value=None):
    data = case if value is None else value
    kind = case["kind"]

    def run():
        if kind == "node-insert":
            return mod.NodeTable(DOCS).insert(data["variant"], data["input_fen"])
        if "input_fens" in data:
            t = mod.NodeTable(DOCS)
            return ([t.insert(data["variant"], f) for f in data["input_fens"]],
                    t.serialize())
        if kind == "merge":
            ta, tb = mod.NodeTable(DOCS), mod.NodeTable(DOCS)
            for f in data["table_a"]:
                ta.insert(data["variant"], f)
            for f in data["table_b"]:
                tb.insert(data["variant"], f)
            return ta.merge(tb).serialize()
        if "record" in data:
            return mod.validate_record(*DOCS, copy.deepcopy(data["record"]))
        raise AssertionError(f"unknown fixture kind {kind}")

    return _outcome(run, (mod.NodeError,))


def _fixture_rows():
    return [(s, c) for s in ("happy", "boundary", "malformed")
            for c in CASES[s]]


def test_r2_fixture_parity():
    for section, case in _fixture_rows():
        assert _fixture_outcome(prod, case) == _fixture_outcome(ref, case), (
            case["name"])
        if section == "malformed":
            fixed = _repaired(case)
            got = _fixture_outcome(prod, case, fixed)
            assert got[0] == "ok" and got == _fixture_outcome(ref, case, fixed)


def test_r2_merge_algebra_and_rollback():
    fens = [STARTPOS, AFTER_E4, LEGAL_EP, KINGS,
            STARTPOS.replace(" 0 1", " 5 30")]
    tables = []
    for order in itertools.permutations(fens):
        t = prod.NodeTable(DOCS)
        for fen in order:
            t.insert("standard", fen)
        tables.append(t.serialize())
    r = ref.NodeTable(DOCS)
    for fen in fens:
        r.insert("standard", fen)
    assert all(s == r.serialize() for s in tables)
    a, b = prod.NodeTable(DOCS), prod.NodeTable(DOCS)
    a.insert("standard", STARTPOS)
    b.insert("standard", KINGS)
    b.insert("standard", STARTPOS)
    assert a.merge(b).serialize() == r.serialize()[:0] + sorted(
        a.serialize())
    assert len(a.records()) == 2
    before = a.serialize()
    for variant, fen in INSERT_PROBES[5:]:
        with contextlib.suppress(prod.NodeError):
            a.insert(variant, fen)
        assert a.serialize() == before


def test_r2_merge_rejects_hostile_source_before_any_insert():
    good = _good_record(KINGS)
    for bad in ([good, {**good, "digest": "x"}], "records", [good, [good]],
                (good,)):
        src = types.SimpleNamespace(records=lambda bad=bad: bad)
        t = prod.NodeTable(DOCS)
        t.insert("standard", STARTPOS)
        before = t.serialize()
        with pytest.raises(prod.NodeError) as err:
            t.merge(src)
        assert err.value.failure_class == "malformed_node_record"
        assert t.serialize() == before


def test_r2_collision_separated_by_field_comparison():
    const = _good_record()["digest"]
    t = prod.NodeTable(DOCS, lambda v, f: const)
    x = t.insert("standard", STARTPOS)
    y = t.insert("standard", KINGS)
    assert x["digest"] == y["digest"] and x != y
    assert len(t.records()) == 2 and len(t.buckets) == 1
    assert t.insert("standard", STARTPOS) is x


# -- R3: the untrusted digest oracle boundary ----------------------------------


def _raiser(exc):
    def fn(variant, fen):
        raise exc
    return fn


FORGED = {
    "node-malformed_node_record": prod.NodeError(
        "malformed_node_record", "malformed_request"),
    "node-malformed_position": prod.NodeError(
        "malformed_position", "malformed_request"),
    "node-unknown_variant": prod.NodeError("unknown_variant",
                                           "unknown_variant"),
    "fen-error": FenError("illegal_position", "malformed_request"),
    "digest-unknown_variant": DigestError("unknown_variant",
                                          "unknown_variant"),
    "digest-malformed_position": DigestError("malformed_position",
                                             "malformed_request"),
    "digest-malformed_digest": DigestError("malformed_digest",
                                           "malformed_request"),
    "variant-error": VariantError(code="unknown_variant", message="x"),
    "contract-error": ContractError("forged"),
    "keyboard-interrupt": KeyboardInterrupt(),
    "system-exit": SystemExit(3),
    "generator-exit": GeneratorExit(),
    "recursion": RecursionError(),
    "value-error": ValueError("x"),
    # builtins the module itself raises (its drift guards)
    "assertion-error": AssertionError("forged drift"),
}
BAD_OUTPUTS = {
    "none": None, "bytes": b"pdv1:" + b"0" * 64, "int": 7,
    "str-subclass": _S("pdv1:" + "0" * 64), "empty": "",
    "upper-hex": "PDV1:" + "A" * 64, "short": "pdv1:" + "0" * 63,
    "trailing-newline": "pdv1:" + "0" * 64 + "\n",
    "leading-space": " pdv1:" + "0" * 64,
}


@pytest.mark.parametrize("name", list(FORGED))
def test_r3_forged_oracle_errors_fail_closed_on_insert_and_validate(name):
    fn = _raiser(FORGED[name])
    t = prod.NodeTable(DOCS, fn)
    with pytest.raises(prod.NodeError) as err:
        t.insert("standard", STARTPOS)
    assert (err.value.failure_class, err.value.code) == (
        "malformed_node_record", "malformed_request")
    # FRESH and unchained: never the forged object, never linked to it
    assert err.value is not FORGED[name]
    assert err.value.__context__ is None and err.value.__cause__ is None
    assert t.buckets == {}
    good = _good_record()
    before = copy.deepcopy(good)
    with pytest.raises(prod.NodeError) as err:
        prod.validate_record(*DOCS, good, fn)
    assert err.value.failure_class == "malformed_node_record"
    assert err.value is not FORGED[name]
    assert err.value.__context__ is None and err.value.__cause__ is None
    assert good == before


@pytest.mark.parametrize("name", list(BAD_OUTPUTS))
def test_r3_bad_oracle_outputs_fail_closed(name):
    out = BAD_OUTPUTS[name]
    t = prod.NodeTable(DOCS, lambda v, f: out)
    with pytest.raises(prod.NodeError) as err:
        t.insert("standard", STARTPOS)
    assert err.value.failure_class == "malformed_node_record"
    assert t.buckets == {}


def test_r3_input_errors_take_precedence_over_the_oracle():
    """unknown_variant / malformed_position are decided before the
    oracle is ever called."""
    calls = []

    def spy(v, f):
        calls.append((v, f))
        return digest_fen(v, f)

    t = prod.NodeTable(DOCS, spy)
    for variant, fen, cls in (("nope", STARTPOS, "unknown_variant"),
                              ("standard", "bad", "malformed_position")):
        with pytest.raises(prod.NodeError) as err:
            t.insert(variant, fen)
        assert err.value.failure_class == cls
    assert calls == []


# -- merge rollback, hostile containers, docs freshness ------------------------


class _HostileDict(dict):
    """A dict subclass that lies through every accessor; any call is
    recorded so the test proves no hostile method ran."""

    calls = []

    def __getitem__(self, key):
        _HostileDict.calls.append("getitem")
        return "standard"

    def keys(self):
        _HostileDict.calls.append("keys")
        return ["variant", "digest", "snapshot_fen"]

    def __iter__(self):
        _HostileDict.calls.append("iter")
        return iter(["variant", "digest", "snapshot_fen"])

    def items(self):
        _HostileDict.calls.append("items")
        return []

    def get(self, key, default=None):
        _HostileDict.calls.append("get")
        return "standard"


def _hostile_record():
    return _HostileDict(_good_record())


class _CountingOracle:
    """Constant-digest oracle that raises on its Nth call (counted
    from the last reset) - a midway failure inside merge's insert
    phase."""

    def __init__(self, const):
        self.const, self.calls, self.fail_on = const, 0, None

    def __call__(self, variant, fen):
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise RuntimeError("oracle fails midway")
        return self.const


def _merge_midway_red(mod):
    """True when a merge that the oracle fails on its SECOND insert
    leaves any trace in the destination (records, bucket identity or
    bucket-list contents)."""
    const = _good_record()["digest"]
    src = mod.NodeTable(DOCS, lambda v, f: const)
    src.insert("standard", KINGS)
    src.insert("standard", AFTER_E4)
    oracle = _CountingOracle(const)
    dst = mod.NodeTable(DOCS, oracle)
    dst.insert("standard", STARTPOS)
    buckets = dst.buckets
    lists = {k: (v, list(v)) for k, v in buckets.items()}
    before = dst.serialize()
    oracle.calls, oracle.fail_on = 0, 4  # 2 validations + 2 inserts
    try:
        dst.merge(src)
        return True
    except mod.NodeError as err:
        if err.failure_class != "malformed_node_record":
            return True
    if dst.serialize() != before or dst.buckets is not buckets:
        return True
    return any(dst.buckets[k] is not v or v != snap
               for k, (v, snap) in lists.items())


def _source_mutation_red(mod):
    """True when merge inserts a record it never validated: the
    oracle appends a malformed record to the source list after the
    validation phase."""
    good_a, good_b = _good_record(KINGS), _good_record(STARTPOS)
    records = [good_a, good_b]
    state = {"n": 0}

    def oracle(v, f):
        state["n"] += 1
        if state["n"] == 2:  # the last validation call
            records.append({"variant": "standard", "digest": "x",
                            "snapshot_fen": "junk"})
        return digest_fen(v, f)

    dst = mod.NodeTable(DOCS, oracle)
    try:
        dst.merge(types.SimpleNamespace(records=lambda: records))
    except BaseException:  # noqa: BLE001 - any failure here is a kill
        return True
    return len(dst.records()) != 2


def _hostile_container_red(mod):
    _HostileDict.calls.clear()
    try:
        mod.validate_record(*DOCS, _hostile_record())
        return True
    except mod.NodeError as err:
        if err.failure_class != "malformed_node_record":
            return True
    t = mod.NodeTable(DOCS)
    try:
        t.merge(types.SimpleNamespace(records=lambda: [_hostile_record()]))
        return True
    except mod.NodeError as err:
        if err.failure_class != "malformed_node_record" or t.records():
            return True
    return bool(_HostileDict.calls)


def _docs_shared_red(mod):
    t1 = mod.NodeTable()
    t1.nc["failures"]["mapping"]["unknown_variant"] = "tampered"
    t1.vc["variants"]["entries"].clear()
    t2 = mod.NodeTable()
    return (t2.nc is t1.nc or t2.vc is t1.vc
            or t2.nc["failures"]["mapping"] != DOCS[0]["failures"]["mapping"]
            or t2.insert("standard", STARTPOS) != _good_record())


def test_r2_merge_midway_oracle_failure_is_rollback_clean():
    assert not _merge_midway_red(prod)


def test_r2_merge_never_inserts_an_unvalidated_source_record():
    assert not _source_mutation_red(prod)


def test_r2_dict_subclass_record_fails_closed_without_hostile_calls():
    assert not _hostile_container_red(prod)


def test_r1_default_docs_are_fresh_per_table():
    assert not _docs_shared_red(prod)


HUGE = "9" * 5000  # beyond the interpreter's int-string limit (4300)
HUGE_CLOCKS = (STARTPOS.replace(" 0 1", f" {HUGE} 1"),
               STARTPOS.replace(" 0 1", f" 0 {HUGE}"))


def _huge_clock_red(mod):
    """True when a clock beyond the int-string limit escapes as anything
    but a FRESH typed rejection on insert, validate or merge, or when a
    rejected merge leaves a trace."""
    good = _good_record()
    for fen in HUGE_CLOCKS:
        calls = [
            (lambda f=fen: mod.NodeTable(DOCS).insert("standard", f),
             "malformed_position"),
            (lambda f=fen: mod.validate_record(
                *DOCS, {**good, "snapshot_fen": f}), "malformed_node_record"),
        ]
        for call, cls in calls:
            try:
                call()
                return True
            except mod.NodeError as err:
                if err.failure_class != cls or err.__context__ is not None \
                        or err.__cause__ is not None:
                    return True
        t = mod.NodeTable(DOCS)
        t.insert("standard", KINGS)
        before = t.serialize()
        try:
            t.merge(types.SimpleNamespace(
                records=lambda f=fen: [{**good, "snapshot_fen": f}]))
            return True
        except mod.NodeError as err:
            if err.failure_class != "malformed_node_record" or \
                    t.serialize() != before:
                return True
    return False


def _live_rewrite_red(mod):
    """True when merge inserts anything but the ORIGINAL source: the
    destination's oracle rewrites every live source record's snapshot
    to K-vs-K mid-merge while returning honest digests."""
    records = [_good_record(STARTPOS)]

    def oracle(v, f):
        for rec in records:
            rec["snapshot_fen"] = KINGS
        return digest_fen(v, f)

    dst = mod.NodeTable(DOCS, oracle)
    try:
        dst.merge(types.SimpleNamespace(records=lambda: records))
    except BaseException:  # noqa: BLE001 - any failure here is a kill
        return True
    honest = mod.NodeTable(DOCS)
    honest.insert("standard", STARTPOS)
    return dst.serialize() != honest.serialize()


def _collision_merge_red(mod):
    """True when a non-empty merge under a shared injected oracle whose
    digests differ from digest_fen fails, or does not reproduce the
    source: merge must validate with the TABLE's oracle."""
    const = "pdv1:" + "7" * 64
    assert const != digest_fen("standard", STARTPOS)
    src = mod.NodeTable(DOCS, lambda v, f: const)
    src.insert("standard", STARTPOS)
    src.insert("standard", KINGS)
    dst = mod.NodeTable(DOCS, lambda v, f: const)
    try:
        dst.merge(src)
    except BaseException:  # noqa: BLE001 - any failure here is a kill
        return True
    return dst.serialize() != src.serialize() or len(dst.records()) != 2


class _HostileList(list):
    calls = []

    def __iter__(self):
        _HostileList.calls.append("iter")
        return iter([])

    def __len__(self):
        _HostileList.calls.append("len")
        return 0

    def __getitem__(self, i):
        _HostileList.calls.append("getitem")
        raise IndexError(i)


def _list_subclass_red(mod):
    """True when a list-subclass records() is not rejected as
    malformed_node_record, changes the table, or has any method run."""
    _HostileList.calls.clear()
    t = mod.NodeTable(DOCS)
    t.insert("standard", STARTPOS)
    before, buckets = t.serialize(), t.buckets
    try:
        t.merge(types.SimpleNamespace(
            records=lambda: _HostileList([_good_record(KINGS)])))
        return True
    except mod.NodeError as err:
        if err.failure_class != "malformed_node_record":
            return True
    return (t.serialize() != before or t.buckets is not buckets
            or bool(_HostileList.calls))


def test_r2_merge_validates_with_the_tables_own_oracle():
    assert not _collision_merge_red(prod)


def test_r2_merge_rejects_a_list_subclass_source_without_calls():
    assert not _list_subclass_red(prod)


def _flip_flop_rows(mod):
    """A D/D' oracle (two valid digests, alternating per call) inserting
    the same K-vs-K identity twice with different clocks."""
    digests = ["pdv1:" + "1" * 64, "pdv1:" + "2" * 64]
    state = {"n": 0}

    def oracle(v, f):
        state["n"] += 1
        return digests[state["n"] % 2]

    t = mod.NodeTable(DOCS, oracle)
    t.insert("standard", KINGS)
    t.insert("standard", KINGS.replace(" 0 1", " 3 9"))
    return len(t.records())


def test_r2_nondeterministic_oracle_behavior_is_documented():
    """DOCUMENTED, not intended semantics: the injected oracle is assumed
    deterministic per identity. A flip-flopping oracle puts one identity
    in two buckets, giving two rows; the reference and production share
    this boundary. Parked owner question (see T0125.md)."""
    assert _flip_flop_rows(prod) == _flip_flop_rows(ref) == 2


def test_r2_clock_beyond_int_string_limit_fails_closed_typed():
    assert not _huge_clock_red(prod)


def test_r2_merge_inserts_the_frozen_source_not_live_records():
    assert not _live_rewrite_red(prod)


# -- R2: reference totality parity (T0122 reference follow-up) ----------------

# The reference module's forge set: its own NodeError per class, the
# classes in its own except clauses (its linked FenError, ValueError,
# BaseException subclasses), the assertion drift guards, its linked
# DigestError per class, and the production-side forges above.
REF_FORGED = dict(FORGED, **{
    "ref-node-malformed_node_record": ref.NodeError(
        "malformed_node_record", "malformed_request"),
    "ref-node-malformed_position": ref.NodeError(
        "malformed_position", "malformed_request"),
    "ref-node-unknown_variant": ref.NodeError("unknown_variant",
                                              "unknown_variant"),
    "ref-fen-error": ref_fen.FenError("illegal_position",
                                      "malformed_request"),
    "ref-digest-malformed_digest": ref_digest.DigestError(
        "malformed_digest", "malformed_request"),
    "ref-digest-unknown_variant": ref_digest.DigestError(
        "unknown_variant", "unknown_variant"),
    "runtime-error": RuntimeError("x"),
    "type-error": TypeError("x"),
})
MALFORMED_NODE_RECORD = ("err", "malformed_node_record", "malformed_request")


@pytest.mark.parametrize("name", list(REF_FORGED))
def test_r2_raising_oracle_parity_fails_closed(name):
    """A raising digest oracle fails closed as a FRESH malformed_node_record
    in the reference exactly as in production, on insert and validate."""
    forged = REF_FORGED[name]
    fn = _raiser(forged)
    assert _insert_both("standard", STARTPOS, fn) == (MALFORMED_NODE_RECORD,
                                                      MALFORMED_NODE_RECORD)
    assert _validate_both(_good_record(), fn) == (MALFORMED_NODE_RECORD,
                                                  MALFORMED_NODE_RECORD)
    t = ref.NodeTable(DOCS, fn)
    for call in (lambda: t.insert("standard", STARTPOS),
                 lambda: ref.validate_record_bounded(*DOCS, _good_record(), fn)):
        with pytest.raises(ref.NodeError) as err:
            call()
        assert err.value is not forged and err.value.__cause__ is None
    assert t.records() == []


@pytest.mark.parametrize("name", list(BAD_OUTPUTS))
def test_r2_bad_oracle_output_parity_fails_closed(name):
    out = BAD_OUTPUTS[name]
    fn = lambda v, f: out  # noqa: E731
    assert _insert_both("standard", STARTPOS, fn) == (MALFORMED_NODE_RECORD,
                                                      MALFORMED_NODE_RECORD)
    assert _validate_both(_good_record(), fn) == (MALFORMED_NODE_RECORD,
                                                  MALFORMED_NODE_RECORD)


@pytest.mark.parametrize("fen", HUGE_CLOCKS, ids=["halfmove", "fullmove"])
def test_r2_huge_clock_parity_is_typed(fen):
    """A clock beyond the int-string limit is a typed rejection in the
    reference exactly as in production (never a raw ValueError)."""
    a, b = _insert_both("standard", fen)
    assert a == b == ("err", "malformed_position", "malformed_request")
    rec = {**_good_record(), "snapshot_fen": fen}
    assert _validate_both(rec) == (MALFORMED_NODE_RECORD,
                                   MALFORMED_NODE_RECORD)
    assert not _huge_clock_red(ref)


def test_r2_frozen_source_parity():
    """Both merges take frozen copies of the source records before any
    oracle call: an oracle rewriting live source records changes nothing."""
    assert not _live_rewrite_red(ref)

    def run(mod, snapshot, digest):
        records = [_good_record(STARTPOS), _good_record(LEGAL_EP)]

        def oracle(v, f):
            for rec in records:
                rec["snapshot_fen"] = snapshot
                rec["digest"] = digest
            return digest_fen(v, f)

        dst = mod.NodeTable(DOCS, oracle)
        dst.merge(types.SimpleNamespace(records=lambda: records))
        return dst.serialize()

    honest = ref.NodeTable(DOCS)
    honest.insert("standard", STARTPOS)
    honest.insert("standard", LEGAL_EP)
    # a valid rewrite (would change what is inserted) and a malformed
    # rewrite (would change what is validated): both are ignored
    for snapshot, digest in ((KINGS, digest_fen("standard", KINGS)),
                             ("not a fen", "junk")):
        assert run(prod, snapshot, digest) == run(ref, snapshot, digest) \
            == honest.serialize()



class _PlainDictSubclass(dict):
    """A dict subclass with honest contents and no overrides."""


REF_FREEZE_GATE_OFF_MUTANT = (
    "            if not _exact_dict(rec):\n"
    "                _fail(self.nc, \"malformed_node_record\")\n"
    "            frozen.append(dict(rec))\n",
    "            frozen.append(dict(rec))\n")

FREEZE_GATE_RECORDS = {
    # dict(rec) would turn these pairs into a valid exact dict
    "list-of-pairs": lambda: list(_good_record().items()),
    # dict(rec) would copy a valid record out of the subclass
    "plain-dict-subclass": lambda: _PlainDictSubclass(_good_record()),
    # dict(rec) would call the lying accessors before any check
    "lying-dict-subclass": _hostile_record,
}


def _contents(rec):
    """Record contents read without calling any overridden accessor."""
    return (type(rec), list(dict.items(rec)) if isinstance(rec, dict)
            else list(rec))


def _freeze_gate_outcome(mod, make):
    """Merge a one-record source whose record is not an exact dict into
    a one-node table: (outcome, oracle calls, hostile calls, table
    unchanged, source unchanged)."""
    calls = []

    def oracle(v, f):
        calls.append((v, f))
        return digest_fen(v, f)

    dst = mod.NodeTable(DOCS, oracle)
    dst.insert("standard", KINGS)
    before = dst.serialize()
    calls.clear()
    rec = make()
    snapshot = _contents(rec)
    source = [rec]
    _HostileDict.calls.clear()
    out = _outcome(lambda: dst.merge(types.SimpleNamespace(
        records=lambda: source)) and "merged")
    hostile = list(_HostileDict.calls)
    _HostileDict.calls.clear()
    same_source = (len(source) == 1 and source[0] is rec
                   and _contents(rec) == snapshot)
    return out, len(calls), hostile, dst.serialize() == before, same_source


@pytest.mark.parametrize("name", sorted(FREEZE_GATE_RECORDS))
def test_r2_freeze_gate_rejects_non_exact_dict_records(name):
    """The freeze step's exact-dict gate: a source record given as a list
    of (field, value) pairs or as a dict subclass fails closed as
    malformed_node_record in both modules, before any oracle call or
    hostile accessor, with table and source unchanged."""
    make = FREEZE_GATE_RECORDS[name]
    got = _freeze_gate_outcome(prod, make)
    assert got == _freeze_gate_outcome(ref, make) == (
        MALFORMED_NODE_RECORD, 0, [], True, True)


@pytest.mark.parametrize("name", ["list-of-pairs", "plain-dict-subclass"])
def test_r2_reference_freeze_gate_off_mutant_is_red(name):
    """One-edit reference mutant: the freeze loop's exact-dict gate
    deleted, so dict(rec) launders the record and the merge accepts it."""
    make = FREEZE_GATE_RECORDS[name]
    got = _freeze_gate_outcome(_ref_mutant(*REF_FREEZE_GATE_OFF_MUTANT), make)
    assert got[0] == ("ok", "merged")
    assert got != _freeze_gate_outcome(ref, make)


KINGS_B = "4k3/8/8/8/8/8/8/3K4 w - - 0 1"
REF_LIVE_INSERT_MUTANT = (
    "        for rec in frozen:\n"
    "            staged.insert(rec[\"variant\"], rec[\"snapshot_fen\"])\n",
    "        for rec in frozen:\n"
    "            self.insert(rec[\"variant\"], rec[\"snapshot_fen\"])\n")


def _ref_mutant(before, after):
    """The reference module source with exactly one edit, executed into a
    fresh namespace that keeps the reference NodeError."""
    source = Path(ref.__file__).read_text()
    assert source.count(before) == 1
    module = types.ModuleType("t0122_reference_mutant")
    module.__file__ = ref.__file__
    exec(compile(source.replace(before, after), ref.__file__, "exec"),
         module.__dict__)
    module.NodeError = ref.NodeError
    return module


SWEEP_CONSTANT = "pdv1:" + "5" * 64
SWEEP_DIGESTS = {"honest": digest_fen,
                 # total collision: every node shares ONE bucket, so a
                 # staged copy that shares the live bucket lists is caught
                 "constant": lambda v, f: SWEEP_CONSTANT}


def _merge_fault_at(mod, fault_at, digest="honest"):
    """Destination holds START; the source is two king-only nodes; the
    destination's oracle is honest (or a total collision) but raises on
    its FAULT_AT-th call during the merge (validation and insert phases
    alike)."""
    base = SWEEP_DIGESTS[digest]
    src = ref.NodeTable(DOCS, base)
    source = [src.insert("standard", KINGS), src.insert("standard", KINGS_B)]
    state = {"armed": False, "n": 0}

    def oracle(v, f):
        if state["armed"]:
            state["n"] += 1
            if state["n"] - 1 == fault_at:
                raise RuntimeError("oracle fault")
        return base(v, f)

    dst = mod.NodeTable(DOCS, oracle)
    dst.insert("standard", STARTPOS)
    before = (copy.deepcopy(dst.buckets), dst.buckets,
              {k: (_Pin(v), [_Pin(r) for r in v]) for k, v in dst.buckets.items()})
    state["armed"] = True
    out = _outcome(lambda: dst.merge(types.SimpleNamespace(
        records=lambda: copy.deepcopy(source))) and "merged")
    after = (copy.deepcopy(dst.buckets), dst.buckets,
             {k: (_Pin(v), [_Pin(r) for r in v]) for k, v in dst.buckets.items()})
    unchanged = before[0] == after[0] and before[2] == after[2]
    return out, unchanged, dst.serialize(), state["n"]


# an honest merge of the two-record source makes 4 oracle calls (2 in
# validation, 2 in the insert phase); index 4 is the no-fault control
MERGE_FAULT_INDEXES = range(5)


@pytest.mark.parametrize("digest", list(SWEEP_DIGESTS))
@pytest.mark.parametrize("fault_at", MERGE_FAULT_INDEXES)
def test_r2_merge_oracle_fault_sweep_parity(fault_at, digest):
    """An oracle fault at ANY call of the merge - validation or insert
    phase - rejects as malformed_node_record and leaves the destination
    bit-identical, in the reference exactly as in production."""
    a = _merge_fault_at(prod, fault_at, digest)
    b = _merge_fault_at(ref, fault_at, digest)
    assert a == b
    out, unchanged, _, calls = a
    if fault_at < 4:
        assert out == MALFORMED_NODE_RECORD and unchanged
    else:
        assert out == ("ok", "merged") and calls == 4


REF_SHARED_BUCKET_LISTS_MUTANT = (
    "        staged.buckets = {key: list(bucket)\n"
    "                          for key, bucket in self.buckets.items()}\n"
    "        for rec in frozen:\n",
    "        staged.buckets = dict(self.buckets)\n"
    "        for rec in frozen:\n")


@pytest.mark.parametrize("mutation,digest", [
    (REF_LIVE_INSERT_MUTANT, "honest"),
    (REF_LIVE_INSERT_MUTANT, "constant"),
    (REF_SHARED_BUCKET_LISTS_MUTANT, "constant"),
], ids=["live-insert-honest", "live-insert-constant", "shared-bucket-lists"])
def test_r2_reference_staging_mutants_are_red(mutation, digest):
    """One-edit reference mutants (merge inserting into the LIVE table;
    staged buckets sharing the live bucket lists) fail the sweep at an
    insert-phase fault; the reference passes the same row."""
    mutant = _ref_mutant(*mutation)
    assert _merge_fault_at(mutant, 3, digest)[1] is False
    assert _merge_fault_at(mutant, 3, digest) != _merge_fault_at(prod, 3, digest)
    assert _merge_fault_at(ref, 3, digest)[1] is True


def test_r3_every_raise_site_and_except_clause_is_forged():
    """Enumerate the module's raise sites and except clauses by AST:
    its own NodeError (forged per class), FenError (forged), and the
    builtins it raises (AssertionError, forged)."""
    raised, caught = set(), set()
    for node in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            raised.add(ast.unparse(exc))
        elif isinstance(node, ast.ExceptHandler) and node.type is not None:
            types_ = (node.type.elts if isinstance(node.type, ast.Tuple)
                      else [node.type])
            caught |= {ast.unparse(t) for t in types_}
    assert raised == {"NodeError", "AssertionError"}
    assert caught == {"BaseException", "FenError", "ValueError"}
    forged = {type(e).__name__ for e in FORGED.values()}
    assert {"NodeError", "AssertionError", "FenError", "ValueError"} <= forged
    assert not {n for n, v in vars(prod).items() if n.startswith("_")
                and isinstance(v, type) and issubclass(v, BaseException)}


# -- R4: source mutants of the production module are killed --------------------

MUTANTS = {
    "oracle-boundary-reraises": (
        "        failed = True\n    if failed:\n        _fail(nc, \"malformed_node_record\")\n",
        "        raise\n    if failed:\n        _fail(nc, \"malformed_node_record\")\n"),
    "oracle-reraises-forged-node-error": (
        "    except BaseException:  # noqa: BLE001 - untrusted oracle boundary\n",
        "    except NodeError as forged:\n"
        "        if forged.failure_class == \"malformed_node_record\":\n"
        "            raise forged\n"
        "        failed = True\n"
        "    except BaseException:  # noqa: BLE001 - untrusted oracle boundary\n"),
    "oracle-fails-inside-except-chained": (
        "        failed = True\n    if failed:\n        _fail(nc, \"malformed_node_record\")\n",
        "        _fail(nc, \"malformed_node_record\")\n    if failed:\n"
        "        _fail(nc, \"malformed_node_record\")\n"),
    "oracle-raise-wrong-class": (
        "    if failed:\n        _fail(nc, \"malformed_node_record\")\n",
        "    if failed:\n        _fail(nc, \"malformed_position\")\n"),
    "oracle-output-wrong-class": (
        "            dc[\"digest\"][\"format\"][\"regex\"], out) is None:\n"
        "        _fail(nc, \"malformed_node_record\")\n",
        "            dc[\"digest\"][\"format\"][\"regex\"], out) is None:\n"
        "        _fail(nc, \"unknown_variant\")\n"),
    "oracle-catches-exception-only": (
        "except BaseException:  # noqa",
        "except Exception:  # noqa"),
    "oracle-output-type-check-off": (
        "if type(out) is not str or re.fullmatch(",
        "if not isinstance(out, str) or re.fullmatch("),
    "oracle-regex-match-not-fullmatch": (
        "if type(out) is not str or re.fullmatch(\n"
        "            dc[\"digest\"][\"format\"][\"regex\"], out) is None:",
        "if type(out) is not str or re.match(\n"
        "            dc[\"digest\"][\"format\"][\"regex\"], out) is None:"),
    "exact-dict-isinstance": (
        "return type(obj) is dict and all(type(k) is str",
        "return isinstance(obj, dict) and all(isinstance(k, str)"),
    # killed by _variant_subclass_red (the registry `in` check runs
    # before digest_fen, so a hostile __eq__ would run)
    "variant-isinstance": (
        "if type(variant_id) is not str or variant_id not in",
        "if not isinstance(variant_id, str) or variant_id not in"),
    "record-key-set-subset": (
        "if not _exact_dict(record) or set(dict.keys(record)) != set(",
        "if not _exact_dict(record) or not set(dict.keys(record)) >= set("),
    "record-field-type-off": (
        "if type(variant) is not str or type(digest) is not str or \\\n"
        "            type(snapshot) is not str:",
        "if False:"),
    "record-unknown-variant-class": (
        "    if variant not in _variant_ids(vc):\n"
        "        _fail(nc, \"unknown_variant\")\n",
        "    if variant not in _variant_ids(vc):\n"
        "        _fail(nc, \"malformed_node_record\")\n"),
    "record-halfmove-check-off": (
        "if fen_named[\"halfmove_clock\"] != \"0\" or \\\n"
        "            fen_named[\"fullmove_number\"] != \"1\":",
        "if fen_named[\"fullmove_number\"] != \"1\":"),
    "record-fullmove-check-off": (
        "if fen_named[\"halfmove_clock\"] != \"0\" or \\\n"
        "            fen_named[\"fullmove_number\"] != \"1\":",
        "if fen_named[\"halfmove_clock\"] != \"0\":"),
    "exact-dict-container-isinstance": (
        "return type(obj) is dict and all(",
        "return isinstance(obj, dict) and all("),
    "record-key-len": (
        "set(dict.keys(record)) != set(\n            nc[\"record\"][\"fields\"])",
        "len(record) != len(\n            nc[\"record\"][\"fields\"])"),
    "merge-stages-alias": (
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}\n",
        "        staged.buckets = self.buckets\n"),
    "merge-stages-shallow": (
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}\n",
        "        staged.buckets = dict(self.buckets)\n"),
    "merge-inserts-in-place": (
        "            staged.insert(rec[\"variant\"], rec[\"snapshot_fen\"])\n"
        "        self.buckets = staged.buckets  # commit\n",
        "            self.insert(rec[\"variant\"], rec[\"snapshot_fen\"])\n"),
    "merge-validates-with-default-oracle": (
        "            validate_record(self.nc, self.vc, self.dc, self.ec, self.fc,\n"
        "                            rec, self.digest_fn)\n",
        "            validate_record(self.nc, self.vc, self.dc, self.ec, self.fc,\n"
        "                            rec)\n"),
    "merge-source-isinstance": (
        "        if type(source) is not list:\n",
        "        if not isinstance(source, list):\n"),
    "merge-inserts-live-records": (
        "            frozen.append(dict(rec))\n",
        "            frozen.append(rec)\n"),
    "merge-freeze-exact-dict-off": (
        "            if not _exact_dict(rec):\n"
        "                _fail(self.nc, \"malformed_node_record\")\n"
        "            frozen.append(dict(rec))\n",
        "            frozen.append(dict(rec))\n"),
    "parse-fails-inside-except-chained": (
        "    except (FenError, ValueError):\n        failed = True\n",
        "    except (FenError, ValueError):\n        _fail(nc, cls)\n"),
    "shared-module-docs": [
        ("FAILURE_MAPPING = dict(load_docs()[0][\"failures\"][\"mapping\"])",
         "_SHARED = load_docs()\n"
         "FAILURE_MAPPING = dict(_SHARED[0][\"failures\"][\"mapping\"])"),
        ("        docs = load_docs() if docs is None else docs\n",
         "        docs = _SHARED if docs is None else docs\n")],
    "record-clock-check-off": (
        "if fen_named[\"halfmove_clock\"] != \"0\" or \\\n"
        "            fen_named[\"fullmove_number\"] != \"1\":",
        "if False:"),
    "record-ep-check-off": (
        "if fen_named[\"en_passant\"] != named[\"en_passant\"]:",
        "if False:"),
    "record-digest-consistency-off": (
        "if digest != _oracle_digest(nc, dc, digest_fn, variant, snapshot):",
        "if False:"),
    "insert-first-writer-wins": (
        "if self._identity(existing) == new_identity:",
        "if True:"),
    "insert-second-node": (
        "if self._identity(existing) == new_identity:",
        "if False:"),
    "snapshot-keeps-clocks": (
        "f\"{named['castling_rights']} {named['en_passant']} 0 1\")",
        "f\"{named['castling_rights']} {named['en_passant']} 0 2\")"),
    "merge-inserts-before-validate": (
        "        for rec in frozen:\n"
        "            validate_record(self.nc, self.vc, self.dc, self.ec, self.fc,\n"
        "                            rec, self.digest_fn)\n", ""),
    "merge-source-type-off": (
        "        if type(source) is not list:\n"
        "            _fail(self.nc, \"malformed_node_record\")\n", ""),
    "castling-sentinel-dropped": (
        "\"castling_rights\": rights or fc[\"castling\"][\"none_sentinel\"],",
        "\"castling_rights\": rights,"),
}


# one-guard edits no black-box probe can separate: a stored digest that
# fails the linked format can never equal the oracle output, which is
# itself format-checked, so digest consistency rejects it with the same
# class (malformed_node_record)
EQUIVALENT_EDITS = {
    # linked parse_fen is total since the T0089 'graph.fen: total parse_fen'
    # commit (exact str, over-limit counters as FenError); the downstream
    # guard is defense in depth
    "fen-type-check-off": (
        "    if type(fen_text) is not str:\n"
        "        _fail(nc, \"malformed_position\")\n", ""),
    # linked parse_fen is total since the T0089 'graph.fen: total parse_fen'
    # commit (exact str, over-limit counters as FenError); the downstream
    # guard is defense in depth
    "parse-catches-fenerror-only": (
        "    except (FenError, ValueError):\n",
        "    except FenError:\n"),
    # the freeze loop reads the whole source list before any oracle
    # call, so iterating the caller's list directly is unobservable
    "merge-freeze-iterates-source-directly": (
        "        for rec in list(source):\n",
        "        for rec in source:\n"),
    "record-format-check-off": (
        "if re.fullmatch(dc[\"digest\"][\"format\"][\"regex\"], digest) is None:",
        "if False:"),
}


def _load_mutant(src):
    mod = types.ModuleType("graph._t0125_mutant")
    mod.__file__ = str(PRODUCTION)
    exec(compile(src, str(PRODUCTION), "exec"), mod.__dict__)  # noqa: S102
    return mod


def _kill_suite_red(mod):
    """True when the mutant disagrees with the reference anywhere the
    battery looks: insert/validate parity, merge, oracle boundary."""
    try:
        for variant, fen in INSERT_PROBES:
            a = _outcome(lambda v=variant, f=fen: mod.NodeTable(DOCS).insert(v, f),
                         (mod.NodeError,))
            b = _outcome(lambda v=variant, f=fen: ref.NodeTable(DOCS).insert(v, f))
            if a != b:
                return True
        for _section, case in _fixture_rows():
            if _fixture_outcome(mod, case) != _fixture_outcome(ref, case):
                return True
        for variant, fen in ((_S("standard"), STARTPOS),
                             ("standard", _S(STARTPOS))):
            try:
                mod.NodeTable(DOCS).insert(variant, fen)
                return True
            except mod.NodeError:
                pass
        for rec in _record_probes():
            a = _outcome(lambda r=rec: mod.validate_record(*DOCS, copy.deepcopy(r)),
                         (mod.NodeError,))
            b = _outcome(lambda r=rec: ref.validate_record(*DOCS, copy.deepcopy(r)))
            if a != b:
                return True
        good = _good_record()
        try:
            mod.validate_record(*DOCS, {"digest": good["digest"],
                                        "snapshot_fen": good["snapshot_fen"],
                                        _HK("variant"): "standard"})
            return True
        except mod.NodeError:
            pass
        const = good["digest"]
        t = mod.NodeTable(DOCS, lambda v, f: const)
        t.insert("standard", STARTPOS)
        t.insert("standard", KINGS)
        if len(t.records()) != 2 or t.insert("standard", STARTPOS) != good:
            return True
        t = mod.NodeTable(DOCS)
        t.insert("standard", STARTPOS)
        t.insert("standard", STARTPOS.replace(" 0 1", " 4 9"))
        if len(t.records()) != 1:
            return True
        for bad in ([_good_record(KINGS), {**good, "digest": "x"}], "r",
                    (_good_record(KINGS),)):
            t = mod.NodeTable(DOCS)
            try:
                t.merge(types.SimpleNamespace(records=lambda b=bad: b))
                return True
            except mod.NodeError:
                if t.records():
                    return True
        # forge the BOUND module's own NodeError per class too (a mutant
        # loaded from source has its own class object)
        own = [mod.NodeError(c, code) for c, code in mod.FAILURE_MAPPING.items()]
        for exc in [*FORGED.values(), *own]:
            for call in (
                    lambda e=exc: mod.NodeTable(DOCS, _raiser(e)).insert(
                        "standard", STARTPOS),
                    lambda e=exc: mod.validate_record(*DOCS, _good_record(),
                                                      _raiser(e))):
                try:
                    call()
                    return True
                except BaseException as err:  # noqa: BLE001
                    if type(err) is not mod.NodeError or \
                            err.failure_class != "malformed_node_record" or \
                            err is exc or err.__context__ is not None or \
                            err.__cause__ is not None:
                        return True
        if _merge_midway_red(mod) or _source_mutation_red(mod) or \
                _hostile_container_red(mod) or _docs_shared_red(mod) or \
                _huge_clock_red(mod) or _live_rewrite_red(mod) or \
                _collision_merge_red(mod) or _list_subclass_red(mod) or \
                _variant_subclass_red(mod):
            return True
        for out in BAD_OUTPUTS.values():
            try:
                mod.NodeTable(DOCS, lambda v, f, o=out: o).insert(
                    "standard", STARTPOS)
                return True
            except mod.NodeError as err:
                if err.failure_class != "malformed_node_record":
                    return True
    except BaseException:  # noqa: BLE001 - any raw escape is a kill
        return True
    return False


def test_r4_production_is_green_under_the_kill_suite():
    assert not _kill_suite_red(_load_mutant(PRODUCTION.read_text()))


@pytest.mark.parametrize("name", list(MUTANTS))
def test_r4_source_mutants_are_killed(name):
    edits = MUTANTS[name]
    edits = [edits] if type(edits) is tuple else edits
    src = PRODUCTION.read_text()
    for old, new in edits:
        assert src.count(old) == 1, (name, old)
        src = src.replace(old, new)
    assert _kill_suite_red(_load_mutant(src)), name


@pytest.mark.parametrize("name", list(EQUIVALENT_EDITS))
def test_r4_equivalent_edits_stay_green(name):
    old, new = EQUIVALENT_EDITS[name]
    src = PRODUCTION.read_text()
    assert src.count(old) == 1, name
    assert not _kill_suite_red(_load_mutant(src.replace(old, new))), name
