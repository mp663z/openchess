"""T0134: production route-edge runtime proof (graph/route_edge.py).

The T0133 red battery is the behavioral proof: it runs against
graph.route_edge with only its binding switched (the import lines plus
the table builder the battery took from the reference fixture). This
file proves the switch is exactly that binding, that production never
imports the test package and links only the shipped graph runtimes,
that production matches the T0131 contract reference on every fixture
case and on a hostile probe corpus, that the untrusted digest oracle
fails closed on every forged error in this module's per-module forge
set (the classes it raises plus the classes its own except clauses
name), and that one-edit source mutants of the production module are
killed.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph.route_edge as prod  # noqa: E402
import graph.transposition_node as node  # noqa: E402
from graph.fen import FenError  # noqa: E402
from graph.position_digest import DigestError, digest_fen  # noqa: E402
from tests import test_t0131_route_edge_contract as ref  # noqa: E402
from tests.test_t0132_route_edge_fixture import (  # noqa: E402
    CASES,
    _repaired,
)

RED = ROOT / "tests" / "test_t0133_route_edge_red.py"
PRODUCTION = ROOT / "graph" / "route_edge.py"
ORACLE_BINDING = (
    "from tests.test_t0131_route_edge_contract import EdgeError, "
    "EdgeTable, _docs\n"
    "from tests.test_t0132_route_edge_fixture import CASES, _build_table, "
    "_StubTable\n")
PRODUCTION_BINDING = (
    "from graph.route_edge import EdgeError, EdgeTable\n"
    "from graph.route_edge import load_docs as _docs\n"
    "from tests.test_t0132_route_edge_fixture import CASES, _StubTable\n"
    "\n\n"
    "def _build_table(inserts):\n"
    "    table = EdgeTable(_docs())\n"
    "    for edge in inserts:\n"
    "        table.insert(*edge)\n"
    "    return table\n")
# sha256 of tests/test_t0133_route_edge_red.py as merged
RED_AS_MERGED_SHA256 = (
    "6dffac60adcf3eeb006eaac30cf22d40c865c96fa678fe193349fbf9e1539b46")

DOCS = ref._docs()
STARTPOS, AFTER_E4, KINGS, LEGAL_EP = (ref.STARTPOS, ref.AFTER_E4,
                                       ref.KINGS, ref.LEGAL_EP)
PAWN_E2, PAWN_E2E4_TO = ref.PAWN_E2, ref.PAWN_E2E4_TO
KINGS_E1E2_TO, KINGS_E1D1_TO = ref.KINGS_E1E2_TO, ref.KINGS_E1D1_TO
PROMO_FROM, PROMO_TO = ref.PROMO_FROM, ref.PROMO_TO
MER = "malformed_edge_record"


# -- R1: surface and links -----------------------------------------------------


def test_r1_production_imports_only_shipped_runtimes():
    tree = ast.parse(PRODUCTION.read_text())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            mods.add(n.module if n.level == 0 else "." + str(n.module))
    assert not any(m.split(".")[0] == "tests" for m in mods)
    assert mods == {"__future__", "pathlib", "yaml", "graph", "graph.fen",
                    "graph.position_digest"}


def test_r1_public_surface():
    assert prod.__all__ == ["FAILURE_MAPPING", "EdgeError", "EdgeTable",
                            "load_docs", "validate_record"]
    assert DOCS[0]["failures"]["mapping"] == prod.FAILURE_MAPPING
    assert prod.load_docs() == DOCS
    assert prod.load_docs()[0] is not prod.load_docs()[0]


def test_r1_red_switch_is_exactly_the_binding():
    text = RED.read_text()
    assert text.count(PRODUCTION_BINDING) == 1
    assert "tests.test_t0131_route_edge_contract" not in text
    back = text.replace(PRODUCTION_BINDING, ORACLE_BINDING)
    assert hashlib.sha256(back.encode()).hexdigest() == RED_AS_MERGED_SHA256


# -- R2: parity with the contract reference ------------------------------------


def _outcome(fn, extra=()):
    try:
        return ("ok", fn())
    except (prod.EdgeError, ref.EdgeError, *extra) as exc:
        return ("err", exc.failure_class, exc.code)


class _S(str):
    pass


class _HK(str):
    def __hash__(self):
        return hash("variant")

    def __eq__(self, other):
        raise RuntimeError("hostile key compare")


INSERT_PROBES = [
    ("standard", "e2e4", STARTPOS, AFTER_E4),
    ("standard", "e2e4", STARTPOS.replace(" 0 1", " 7 42"), AFTER_E4),
    ("standard", "e1e2", KINGS, KINGS_E1E2_TO),
    ("standard", "a7a8q", PROMO_FROM, PROMO_TO),
    ("standard", "a7a8k", PROMO_FROM, PROMO_TO),
    ("standard", "a7a8Q", PROMO_FROM, PROMO_TO),
    ("standard", "e2e2", STARTPOS, AFTER_E4),
    ("standard", "e2e", STARTPOS, AFTER_E4),
    ("standard", "e2e4qq", STARTPOS, AFTER_E4),
    ("standard", "i2e4", STARTPOS, AFTER_E4),
    ("standard", "e9e4", STARTPOS, AFTER_E4),
    ("standard", "e2e4\n", STARTPOS, AFTER_E4),
    ("standard", "\u04352e4", STARTPOS, AFTER_E4),
    ("standard", "", STARTPOS, AFTER_E4),
    ("standard", None, STARTPOS, AFTER_E4),
    ("standard", 7, STARTPOS, AFTER_E4),
    ("standard", "e2e4", "bad", AFTER_E4),
    ("standard", "e2e4", STARTPOS, "bad"),
    ("standard", "e2e4", STARTPOS + " ", AFTER_E4),
    ("standard", "e2e4", STARTPOS, AFTER_E4 + "\n"),
    ("standard", "e2e4", None, AFTER_E4),
    ("standard", "e2e4", STARTPOS, 1),
    ("nope", "e2e4", STARTPOS, AFTER_E4),
    ("standard\n", "e2e4", STARTPOS, AFTER_E4),
    (None, "e2e4", STARTPOS, AFTER_E4),
    (None, None, None, None),
    ("nope", "e2e2", "bad", "bad"),
    ("standard", "e2e2", "bad", "bad"),
] + [tuple(r[:4]) for r in ref.MALFORMED_INSERTS]


def _insert_both(args, digest_fn=digest_fen):
    a = _outcome(lambda: prod.EdgeTable(DOCS, digest_fn).insert(*args))
    b = _outcome(lambda: ref.EdgeTable(DOCS, digest_fn).insert(*args))
    return a, b


@pytest.mark.parametrize("i", range(len(INSERT_PROBES)))
def test_r2_insert_parity(i):
    a, b = _insert_both(INSERT_PROBES[i])
    assert a == b


def test_r2_str_subclass_inputs_fail_closed():
    base = ["standard", "e2e4", STARTPOS, AFTER_E4]
    for pos in range(4):
        args = list(base)
        args[pos] = _S(args[pos])
        with pytest.raises(prod.EdgeError):
            prod.EdgeTable(DOCS).insert(*args)


def _good_record(args=("standard", "e2e4", STARTPOS, AFTER_E4)):
    return ref.EdgeTable(DOCS).insert(*args)


def _record_probes():
    good = _good_record()
    out = [good, _good_record(("standard", "a7a8q", PROMO_FROM, PROMO_TO)),
           _good_record(("standard", "e1e2", KINGS, KINGS_E1E2_TO))]
    for key in list(good):
        r = dict(good)
        del r[key]
        out.append(r)
        out.append({**r, key + "_x": good[key]})
        out.append({**good, key: _S(good[key])})
        out.append({**good, key: 1})
    out += [rec for _n, rec, _c in ref._record_cases()]
    out += [
        {**good, "extra": 1}, {**good, "variant": "nope"},
        {**good, "move": "e2e2"}, {**good, "move": "e2e4\n"},
        {**good, "move": "a7a8k"}, {**good, "move": "E2E4"},
        {**good, "from_snapshot_fen": STARTPOS.replace(" 0 1", " 0 9")},
        {**good, "to_snapshot_fen": AFTER_E4.replace(" 0 1", " 3 1")},
        {**good, "to_snapshot_fen": "bad"},
        {**good, "from_snapshot_fen": STARTPOS + " "},
        {"variant": "standard", "move": "e2e4", "from_snapshot_fen": STARTPOS,
         1: AFTER_E4},
        [good], None, "record",
    ]
    return out


@pytest.mark.parametrize("i", range(len(_record_probes())))
def test_r2_validate_parity(i):
    record = _record_probes()[i]
    a = _outcome(lambda: prod.validate_record(*DOCS, copy.deepcopy(record)))
    b = _outcome(lambda: ref.validate_record(*DOCS, copy.deepcopy(record)))
    assert a == b


def test_r2_hostile_key_fails_closed_without_compare():
    good = _good_record()
    rec = {k: v for k, v in good.items() if k != "variant"}
    rec[_HK("variant")] = "standard"
    with pytest.raises(prod.EdgeError) as err:
        prod.validate_record(*DOCS, rec)
    assert err.value.failure_class == MER


class _Src:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _fixture_outcome(mod, case, value=None):
    data = case if value is None else value
    kind = data["kind"]

    def build(inserts):
        t = mod.EdgeTable(DOCS)
        for edge in inserts:
            t.insert(*edge)
        return t

    def run():
        if kind == "inserts":
            t = build(data["inserts"])
            return t.serialize()
        if kind in ("merge", "merge-left-right") and "right" in data:
            return build(data["left"]).merge(build(data["right"])).serialize()
        if kind in ("insert", "insert-with-setup"):
            t = build(data.get("setup", []))
            rec = t.insert(data["variant"], data["move"],
                           data["from_snapshot_fen"], data["to_snapshot_fen"])
            return rec, t.serialize()
        if kind == "rollback-insert":
            t = build(data["setup"])
            r = data["rejected"]
            got = _outcome(lambda: t.insert(r["variant"], r["move"],
                                            r["from_snapshot_fen"],
                                            r["to_snapshot_fen"]),
                           (mod.EdgeError,))
            for edge in data["then"]:
                t.insert(*edge)
            return got, t.serialize()
        if kind == "rollback-merge":
            t = build(data["setup"])
            got = _outcome(lambda: t.merge(_Src(copy.deepcopy(
                data["rejected_records"]))), (mod.EdgeError,))
            t.merge(build(data["then_right"]))
            return got[:1] + got[1:] if got[0] == "err" else got, \
                t.serialize()
        t = build(data["left"])
        out = _outcome(lambda: t.merge(_Src(copy.deepcopy(
            data["right_records"]))).serialize(), (mod.EdgeError,))
        return out, t.serialize()

    return _outcome(run, (mod.EdgeError,))


def _fixture_rows():
    return [(s, c) for s in ("happy", "boundary", "malformed", "rollback")
            for c in CASES[s]]


def test_r2_fixture_kinds_are_all_dispatched():
    kinds = {c["kind"] for _s, c in _fixture_rows()}
    assert kinds <= {"inserts", "merge", "insert", "insert-with-setup",
                     "rollback-insert", "rollback-merge"}, kinds


def test_r2_fixture_parity():
    for section, case in _fixture_rows():
        got = _fixture_outcome(prod, case)
        assert got == _fixture_outcome(ref, case), case["name"]
        if section in ("happy", "boundary"):
            assert got[0] == "ok"
            assert [list(x) for x in got[1]] == case["expect_serialized"]
        if section == "malformed":
            fixed = _repaired(case)
            fgot = _fixture_outcome(prod, case, fixed)
            assert fgot == _fixture_outcome(ref, case, fixed), case["name"]
            assert "err" not in (fgot[0], fgot[1][0]), case["name"]


def test_r2_rollback_and_conflict():
    t = prod.EdgeTable(DOCS)
    t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    before, buckets = copy.deepcopy(t.buckets), t.buckets
    for args in INSERT_PROBES[4:]:
        with contextlib.suppress(prod.EdgeError):
            t.insert(*args)
    with pytest.raises(prod.EdgeError) as err:
        t.insert("standard", "e2e4", STARTPOS, KINGS)
    assert err.value.failure_class == "conflicting_edge"
    assert t.buckets == before and t.buckets is buckets
    same = t.insert("standard", "e2e4", STARTPOS.replace(" 0 1", " 9 9"),
                    AFTER_E4)
    assert same is t.records()[0] and len(t.records()) == 1


def test_r2_merge_rejects_hostile_source_before_any_commit():
    good = _good_record(("standard", "e1e2", KINGS, KINGS_E1E2_TO))
    for bad in ([good, {**good, "move": "e1e1"}], "records", [good, [good]],
                (good,), [good, None]):
        t = prod.EdgeTable(DOCS)
        t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
        before = t.serialize()
        with pytest.raises(prod.EdgeError) as err:
            t.merge(_Src(bad))
        assert err.value.failure_class == MER
        assert t.serialize() == before


def test_r2_merge_conflicts_reject_both_orders():
    a, b = prod.EdgeTable(DOCS), prod.EdgeTable(DOCS)
    a.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    b.insert("standard", "e1e2", KINGS, ref.KINGS_ALT_TARGET)
    for x, y in ((a, b), (b, a)):
        before = x.serialize()
        with pytest.raises(prod.EdgeError) as err:
            x.merge(y)
        assert err.value.failure_class == "conflicting_edge"
        assert x.serialize() == before


def test_r2_collision_separated_by_field_comparison():
    const = "pdv1:" + "0" * 64
    t = prod.EdgeTable(DOCS, lambda v, f: const)
    x = t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    y = t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
    assert x != y and len(t.records()) == 2 and len(t.buckets) == 1
    assert t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO) is y
    assert t.insert("standard", "e2e4", STARTPOS, AFTER_E4) is x


# -- R3: the untrusted digest oracle boundary ----------------------------------


def _raiser(exc):
    def fn(variant, fen):
        raise exc
    return fn


FORGED = {
    **{f"edge-{c}": prod.EdgeError(c, code)
       for c, code in prod.FAILURE_MAPPING.items()},
    **{f"node-{c}": node.NodeError(c, code)
       for c, code in node.FAILURE_MAPPING.items()},
    "fen-error": FenError("illegal_position", "malformed_request"),
    "value-error": ValueError("x"),
    "digest-malformed_digest": DigestError("malformed_digest",
                                           "malformed_request"),
    "keyboard-interrupt": KeyboardInterrupt(),
    "system-exit": SystemExit(3),
    "generator-exit": GeneratorExit(),
    "recursion": RecursionError(),
    "assertion-error": AssertionError("forged"),
}
BAD_OUTPUTS = {
    "none": None, "bytes": b"pdv1:" + b"0" * 64, "int": 7,
    "str-subclass": _S("pdv1:" + "0" * 64), "empty": "",
    "upper-hex": "PDV1:" + "A" * 64, "short": "pdv1:" + "0" * 63,
    "trailing-newline": "pdv1:" + "0" * 64 + "\n",
}


def _fresh_mer(err, forged, mod=prod):
    return (type(err) is mod.EdgeError and err.failure_class == MER
            and err.code == mod.FAILURE_MAPPING[MER] and err is not forged
            and err.__context__ is None and err.__cause__ is None)


@pytest.mark.parametrize("name", list(FORGED))
def test_r3_forged_oracle_errors_fail_closed(name):
    forged, fn = FORGED[name], _raiser(FORGED[name])
    t = prod.EdgeTable(DOCS, fn)
    with pytest.raises(prod.EdgeError) as err:
        t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    assert _fresh_mer(err.value, forged) and t.buckets == {}
    good = _good_record()
    before = copy.deepcopy(good)
    with pytest.raises(prod.EdgeError) as err:
        prod.validate_record(*DOCS, good, fn)
    assert _fresh_mer(err.value, forged) and good == before
    with pytest.raises(prod.EdgeError) as err:
        t.merge(_Src([good]))
    assert _fresh_mer(err.value, forged) and t.buckets == {}


@pytest.mark.parametrize("name", list(BAD_OUTPUTS))
def test_r3_bad_oracle_outputs_fail_closed(name):
    out = BAD_OUTPUTS[name]
    t = prod.EdgeTable(DOCS, lambda v, f: out)
    with pytest.raises(prod.EdgeError) as err:
        t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    assert err.value.failure_class == MER and t.buckets == {}
    with pytest.raises(prod.EdgeError) as err:
        prod.validate_record(*DOCS, _good_record(), lambda v, f: out)
    assert err.value.failure_class == MER


def test_r3_input_errors_take_precedence_over_the_oracle():
    calls = []

    def spy(v, f):
        calls.append((v, f))
        return digest_fen(v, f)

    t = prod.EdgeTable(DOCS, spy)
    for args, cls in ((("nope", "e2e4", STARTPOS, AFTER_E4), "unknown_variant"),
                      (("standard", "e2e2", STARTPOS, AFTER_E4), MER),
                      (("standard", "e2e4", "bad", AFTER_E4),
                       "malformed_position")):
        with pytest.raises(prod.EdgeError) as err:
            t.insert(*args)
        assert err.value.failure_class == cls
    assert calls == []


# -- hostile containers, live mutation, docs freshness, clocks -----------------


class _HostileDict(dict):
    calls = []

    def __getitem__(self, key):
        _HostileDict.calls.append("getitem")
        return "standard"

    def keys(self):
        _HostileDict.calls.append("keys")
        return list(ref.CONTRACT and prod.load_docs()[0]["record"]["fields"])

    def __iter__(self):
        _HostileDict.calls.append("iter")
        return iter(prod.load_docs()[0]["record"]["fields"])

    def items(self):
        _HostileDict.calls.append("items")
        return []

    def get(self, key, default=None):
        _HostileDict.calls.append("get")
        return "standard"


def _hostile_container_red(mod):
    _HostileDict.calls.clear()
    try:
        mod.validate_record(*DOCS, _HostileDict(_good_record()))
        return True
    except mod.EdgeError as err:
        if err.failure_class != MER:
            return True
    t = mod.EdgeTable(DOCS)
    try:
        t.merge(_Src([_HostileDict(_good_record())]))
        return True
    except mod.EdgeError as err:
        if err.failure_class != MER or t.records():
            return True
    return bool(_HostileDict.calls)


class _CountingOracle:
    def __init__(self, const=None):
        self.calls, self.fail_on, self.const = 0, None, const

    def __call__(self, variant, fen):
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise RuntimeError("oracle fails midway")
        return self.const or digest_fen(variant, fen)


# oracle calls per merged source record: validation makes two per
# endpoint (the node digest, then the node validator's consistency
# check), staging makes one (the bucket key)
CALLS_PER_MERGED_RECORD = 5


def _merge_midway_red(mod):
    """True when a merge the oracle fails AFTER the first source record
    was staged leaves any trace in the destination: once with honest
    digests (record 1 opens a new bucket) and once with one constant
    digest (record 1 is appended to the destination's EXISTING bucket
    list, so a shallow staging copy would leak it)."""
    return any(_merge_midway_red_once(mod, const)
               for const in (None, "pdv1:" + "0" * 64))


def _merge_midway_red_once(mod, const):
    src = mod.EdgeTable(DOCS, _CountingOracle(const))
    src.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
    src.insert("standard", "a7a8q", PROMO_FROM, PROMO_TO)
    oracle = _CountingOracle(const)
    dst = mod.EdgeTable(DOCS, oracle)
    dst.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    buckets = dst.buckets
    lists = {k: (v, list(v)) for k, v in buckets.items()}
    before = dst.serialize()
    # fail on the 2nd record's first validation call, after record 1
    # was validated AND staged
    fail_on = CALLS_PER_MERGED_RECORD + 1
    oracle.calls, oracle.fail_on = 0, fail_on
    try:
        dst.merge(src)
        return True
    except mod.EdgeError as err:
        if err.failure_class != MER:
            return True
    if oracle.calls != fail_on or dst.serialize() != before or \
            dst.buckets is not buckets:
        return True
    return any(dst.buckets[k] is not v or v != snap
               for k, (v, snap) in lists.items())


def _source_append_red(mod):
    """True when merge reads source records appended to the caller's
    list after merge began."""
    records = [_good_record(("standard", "e1e2", KINGS, KINGS_E1E2_TO)),
               _good_record()]
    state = {"n": 0}

    def oracle(v, f):
        state["n"] += 1
        if state["n"] == 1:
            records.append({**records[0], "move": "junk"})
        return digest_fen(v, f)

    dst = mod.EdgeTable(DOCS, oracle)
    try:
        dst.merge(_Src(records))
    except BaseException:  # noqa: BLE001 - any failure here is a kill
        return True
    return len(dst.records()) != 2


def _live_rewrite_red(mod):
    """True when merge inserts anything but the ORIGINAL source: the
    destination oracle rewrites every live source record's target to
    another valid position while returning honest digests."""
    records = [_good_record(("standard", "e1e2", KINGS, KINGS_E1E2_TO))]

    def oracle(v, f):
        for rec in records:
            rec["to_snapshot_fen"] = KINGS_E1D1_TO
        return digest_fen(v, f)

    dst = mod.EdgeTable(DOCS, oracle)
    try:
        dst.merge(_Src(records))
    except BaseException:  # noqa: BLE001 - any failure here is a kill
        return True
    honest = mod.EdgeTable(DOCS)
    honest.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    return dst.serialize() != honest.serialize()


def _docs_shared_red(mod):
    t1 = mod.EdgeTable()
    t1.ec["failures"]["mapping"]["unknown_variant"] = "tampered"
    t1.vc["variants"]["entries"].clear()
    t2 = mod.EdgeTable()
    return (t2.ec is t1.ec or t2.vc is t1.vc
            or t2.ec["failures"]["mapping"] != DOCS[0]["failures"]["mapping"]
            or t2.insert("standard", "e2e4", STARTPOS, AFTER_E4)
            != _good_record())


HUGE = "9" * 5000  # beyond the interpreter's int-string limit (4300)
HUGE_CLOCKS = (STARTPOS.replace(" 0 1", f" {HUGE} 1"),
               STARTPOS.replace(" 0 1", f" 0 {HUGE}"))


def _huge_clock_red(mod):
    good = _good_record()
    for fen in HUGE_CLOCKS:
        calls = [
            (lambda f=fen: mod.EdgeTable(DOCS).insert(
                "standard", "e2e4", f, AFTER_E4), "malformed_position"),
            (lambda f=fen: mod.EdgeTable(DOCS).insert(
                "standard", "e2e4", STARTPOS, f), "malformed_position"),
            (lambda f=fen: mod.validate_record(
                *DOCS, {**good, "from_snapshot_fen": f}), MER),
            (lambda f=fen: mod.validate_record(
                *DOCS, {**good, "to_snapshot_fen": f}), MER),
        ]
        for call, cls in calls:
            try:
                call()
                return True
            except mod.EdgeError as err:
                if err.failure_class != cls or err.__context__ is not None \
                        or err.__cause__ is not None:
                    return True
        t = mod.EdgeTable(DOCS)
        t.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
        before = t.serialize()
        try:
            t.merge(_Src([{**good, "from_snapshot_fen": fen}]))
            return True
        except mod.EdgeError as err:
            if err.failure_class != MER or t.serialize() != before:
                return True
    return False


def _collision_merge_red(mod):
    """True when a non-empty merge under a shared injected oracle whose
    digests differ from digest_fen fails or does not reproduce the
    source: merge validates and stages with the TABLE's oracle."""
    const = "pdv1:" + "7" * 64
    assert const != digest_fen("standard", STARTPOS)
    src = mod.EdgeTable(DOCS, lambda v, f: const)
    src.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    src.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
    dst = mod.EdgeTable(DOCS, lambda v, f: const)
    try:
        dst.merge(src)
    except BaseException:  # noqa: BLE001 - any failure here is a kill
        return True
    if dst.serialize() != src.serialize() or len(dst.records()) != 2:
        return True
    # the merged records sit under the table's own bucket keys: a later
    # insert of the same edge finds them instead of storing a duplicate
    again = dst.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
    return again not in dst.records() or len(dst.records()) != 2 or \
        set(dst.buckets) != set(src.buckets)


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
    _HostileList.calls.clear()
    t = mod.EdgeTable(DOCS)
    t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    before, buckets = t.serialize(), t.buckets
    try:
        t.merge(_Src(_HostileList([_good_record(
            ("standard", "e1e2", KINGS, KINGS_E1E2_TO))])))
        return True
    except mod.EdgeError as err:
        if err.failure_class != MER:
            return True
    return (t.serialize() != before or t.buckets is not buckets
            or bool(_HostileList.calls))


def _flip_flop_rows(mod):
    digests = ["pdv1:" + "1" * 64, "pdv1:" + "2" * 64]
    state = {"n": 0}

    def oracle(v, f):
        state["n"] += 1
        return digests[state["n"] % 2]

    t = mod.EdgeTable(DOCS, oracle)
    t.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    t.insert("standard", "e1e2", KINGS.replace(" 0 1", " 3 9"), KINGS_E1E2_TO)
    return len(t.records())


def test_r2_merge_uses_the_tables_own_oracle():
    assert not _collision_merge_red(prod)


def test_r2_merge_rejects_a_list_subclass_source_without_calls():
    assert not _list_subclass_red(prod)


def test_r2_nondeterministic_oracle_behavior_is_documented():
    """DOCUMENTED, not intended semantics: the oracle is assumed
    deterministic per identity (parked owner question, see T0125.md);
    a flip-flopping oracle stores one edge twice, in reference and
    production alike."""
    assert _flip_flop_rows(prod) == _flip_flop_rows(ref) == 2


def test_r2_dict_subclass_record_fails_closed_without_hostile_calls():
    assert not _hostile_container_red(prod)


def test_r2_merge_midway_oracle_failure_is_rollback_clean():
    assert not _merge_midway_red(prod)


def test_r2_calls_per_merged_record_is_pinned():
    """The midway probe's fail point is only meaningful if one merged
    record costs exactly CALLS_PER_MERGED_RECORD oracle calls."""
    src = prod.EdgeTable(DOCS)
    src.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    oracle = _CountingOracle()
    prod.EdgeTable(DOCS, oracle).merge(src)
    assert oracle.calls == CALLS_PER_MERGED_RECORD


def test_r2_merge_reads_the_source_list_once():
    assert not _source_append_red(prod)


def test_r2_merge_inserts_the_frozen_source_not_live_records():
    assert not _live_rewrite_red(prod)


def test_r1_default_docs_are_fresh_per_table():
    assert not _docs_shared_red(prod)


def test_r2_clock_beyond_int_string_limit_fails_closed_typed():
    assert not _huge_clock_red(prod)


def test_r3_per_module_forge_set_is_covered():
    """Per-module forge scope (coordinator ruling): the classes this
    module raises plus the classes its own except clauses name, each
    forged; AST-enumerated so a new raise site or handler fails here."""
    raised, caught = set(), set()
    for n in ast.walk(ast.parse(PRODUCTION.read_text())):
        if isinstance(n, ast.Raise) and n.exc is not None:
            exc = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            raised.add(ast.unparse(exc))
        elif isinstance(n, ast.ExceptHandler) and n.type is not None:
            elts = n.type.elts if isinstance(n.type, ast.Tuple) else [n.type]
            caught |= {ast.unparse(t) for t in elts}
    assert raised == {"EdgeError"}
    assert caught == {"_node.NodeError", "FenError", "ValueError"}
    forged = {type(e).__name__ for e in FORGED.values()}
    assert {"EdgeError", "NodeError", "FenError", "ValueError"} <= forged
    assert {c for c in prod.FAILURE_MAPPING} == {
        e.failure_class for e in FORGED.values()
        if type(e) is prod.EdgeError}
    assert not {k for k, v in vars(prod).items() if k.startswith("_")
                and isinstance(v, type) and issubclass(v, BaseException)}


# -- R4: source mutants of the production module are killed --------------------

MUTANTS = {
    "oracle-reraises-node-error": (
        "    except _node.NodeError:\n        failed = True\n"
        "    if failed:\n        _fail(ec, \"malformed_edge_record\")\n"
        "    return out\n",
        "    except _node.NodeError:\n        raise\n"
        "    return out\n"),
    "oracle-fails-inside-except-chained": (
        "    except _node.NodeError:\n        failed = True\n"
        "    if failed:\n        _fail(ec, \"malformed_edge_record\")\n"
        "    return out\n",
        "    except _node.NodeError:\n"
        "        _fail(ec, \"malformed_edge_record\")\n    return out\n"),
    "oracle-wrong-class": (
        "    if failed:\n        _fail(ec, \"malformed_edge_record\")\n"
        "    return out\n",
        "    if failed:\n        _fail(ec, \"malformed_position\")\n"
        "    return out\n"),
    "insert-bucket-uses-raw-oracle": (
        "        bucket_key = (_oracle(self.ec, self.nc, self.dc, self.digest_fn,\n"
        "                              variant_id, rec[\"from_snapshot_fen\"]), move)\n",
        "        bucket_key = (self.digest_fn(variant_id,\n"
        "                                     rec[\"from_snapshot_fen\"]), move)\n"),
    "validate-digest-uses-raw-oracle": (
        "                \"digest\": _node._oracle_digest(nc, dc, digest_fn,\n"
        "                                               record[\"variant\"], record[key]),\n",
        "                \"digest\": digest_fn(record[\"variant\"], record[key]),\n"),
    "validate-node-wrong-class": (
        "        if failed:\n            _fail(ec, \"malformed_edge_record\")\n"
        "    return record\n",
        "        if failed:\n            _fail(ec, \"malformed_position\")\n"
        "    return record\n"),
    "validate-node-check-off": (
        "            _node.validate_record(nc, vc, dc, epc, fc, node_record, digest_fn)\n",
        "            pass\n"),
    "move-distinct-off": (
        "if shape[\"from_to_distinct\"] and squares[0] == squares[1]:",
        "if False:"),
    "move-promotion-enum-off": (
        "        move[4] in shape[\"types\"][\"promotion\"][\"enum\"])",
        "        True)"),
    "move-file-check-off": (
        "if square[0] not in grammar[\"files\"] or square[1] not in grammar[\"ranks\"]:",
        "if square[1] not in grammar[\"ranks\"]:"),
    "move-rank-check-off": (
        "if square[0] not in grammar[\"files\"] or square[1] not in grammar[\"ranks\"]:",
        "if square[0] not in grammar[\"files\"]:"),
    "move-length-off": (
        "    if len(move) not in (squares_len, squares_len + 1):\n"
        "        return False\n", "    if len(move) < squares_len:\n"
        "        return False\n"),
    "insert-variant-isinstance": (
        "if type(variant) is not str or variant not in [",
        "if not isinstance(variant, str) or variant not in ["),
    "move-type-guards-both-off": [
        ("    if type(move) is not str:\n"
         "        _fail(ec, \"malformed_edge_record\")\n", ""),
        ("if type(move) is not str or not move.isascii():",
         "if not isinstance(move, str) or not move.isascii():")],
    "insert-fen-isinstance": (
        "if type(from_fen) is not str or type(to_fen) is not str:",
        "if not isinstance(from_fen, str) or not isinstance(to_fen, str):"),
    "insert-move-check-wrong-class": (
        "    if not _move_ok(lc, move):\n"
        "        _fail(ec, \"malformed_edge_record\")\n"
        "    failed = False\n",
        "    if not _move_ok(lc, move):\n"
        "        _fail(ec, \"malformed_position\")\n"
        "    failed = False\n"),
    "parse-catches-fenerror-only": (
        "    except (FenError, ValueError):  # ValueError: clock over int-str limit",
        "    except FenError:"),
    "parse-fails-inside-except-chained": (
        "    except (FenError, ValueError):  # ValueError: clock over int-str limit\n"
        "        failed = True\n",
        "    except (FenError, ValueError):\n"
        "        _fail(ec, \"malformed_position\")\n"),
    "snapshots-swapped": (
        "\"from_snapshot_fen\": snaps[0], \"to_snapshot_fen\": snaps[1]}",
        "\"from_snapshot_fen\": snaps[1], \"to_snapshot_fen\": snaps[0]}"),
    "record-key-set-subset": (
        "if not _exact_dict(record) or set(dict.keys(record)) != set(\n"
        "            ec[\"record\"][\"fields\"]):",
        "if not _exact_dict(record) or not set(dict.keys(record)) >= set(\n"
        "            ec[\"record\"][\"fields\"]):"),
    "record-key-len": (
        "if not _exact_dict(record) or set(dict.keys(record)) != set(\n"
        "            ec[\"record\"][\"fields\"]):",
        "if not _exact_dict(record) or len(record) != len(\n"
        "            ec[\"record\"][\"fields\"]):"),
    "exact-dict-isinstance": (
        "return type(obj) is dict and all(type(k) is str",
        "return isinstance(obj, dict) and all(isinstance(k, str)"),
    "exact-dict-key-check-off": (
        "return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "return type(obj) is dict"),
    "record-field-type-off": (
        "    if not all(type(record[f]) is str for f in _FIELDS):\n"
        "        _fail(ec, \"malformed_edge_record\")\n", ""),
    "record-unknown-variant-class": (
        "    if record[\"variant\"] not in [e[\"id\"] for e in vc[\"variants\"][\"entries\"]]:\n"
        "        _fail(ec, \"unknown_variant\")\n",
        "    if record[\"variant\"] not in [e[\"id\"] for e in vc[\"variants\"][\"entries\"]]:\n"
        "        _fail(ec, \"malformed_edge_record\")\n"),
    "record-move-check-off": (
        "    if not _move_ok(lc, record[\"move\"]):\n"
        "        _fail(ec, \"malformed_edge_record\")\n", ""),
    "insert-conflict-off": (
        "if existing_identity[:3] == new_identity[:3]:", "if False:"),
    "insert-first-writer-wins": (
        "if existing_identity == new_identity:",
        "if existing_identity[:3] == new_identity[:3]:"),
    "insert-second-record": (
        "if existing_identity == new_identity:", "if False:"),
    "bucket-key-uses-target": (
        "variant_id, rec[\"from_snapshot_fen\"]), move)",
        "variant_id, rec[\"to_snapshot_fen\"]), move)"),
    "merge-stages-alias": (
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}\n",
        "        staged.buckets = self.buckets\n"),
    "merge-stages-shallow": (
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}\n",
        "        staged.buckets = dict(self.buckets)\n"),
    "merge-inserts-in-place": (
        "            staged.insert(rec[\"variant\"], rec[\"move\"],",
        "            self.insert(rec[\"variant\"], rec[\"move\"],"),
    "merge-source-type-off": (
        "        if type(source) is not list:\n"
        "            _fail(self.ec, \"malformed_edge_record\")\n", ""),
    "merge-no-source-copy": (
        "        source = list(source)  # the batch is read once\n", ""),
    "merge-inserts-live-records": (
        "            rec = {f: dict.__getitem__(rec, f) for f in _FIELDS}\n", ""),
    "merge-freeze-guard-off": (
        "            if not _exact_dict(rec) or set(dict.keys(rec)) != set(\n"
        "                    self.ec[\"record\"][\"fields\"]):\n"
        "                _fail(self.ec, \"malformed_edge_record\")\n", ""),
    "merge-validates-with-default-oracle": (
        "            validate_record(*self._docs(), rec, self.digest_fn)\n",
        "            validate_record(*self._docs(), rec)\n"),
    "merge-stages-with-default-oracle": (
        "        staged = EdgeTable(self._docs(), self.digest_fn)\n",
        "        staged = EdgeTable(self._docs())\n"),
    "merge-source-isinstance": (
        "        if type(source) is not list:\n",
        "        if not isinstance(source, list):\n"),
    "merge-validate-off": (
        "            validate_record(*self._docs(), rec, self.digest_fn)\n", ""),
    "shared-module-docs": [
        ("FAILURE_MAPPING = dict(load_docs()[0][\"failures\"][\"mapping\"])",
         "_SHARED = load_docs()\n"
         "FAILURE_MAPPING = dict(_SHARED[0][\"failures\"][\"mapping\"])"),
        ("        docs = load_docs() if docs is None else docs\n",
         "        docs = _SHARED if docs is None else docs\n")],
}

# one-guard edits no black-box probe can separate (reason per entry)
EQUIVALENT_EDITS = {
    # a non-ascii character can never be in the linked files/ranks
    # lists, so the grammar check rejects it with the same class
    "move-ascii-off": (
        "if type(move) is not str or not move.isascii():",
        "if type(move) is not str:"),
    # the exact-str move guard runs twice (insert/validate entry, then
    # _move_ok); removing either one alone leaves the other; the
    # both-off mutant above is killed
    "move-exact-str-off-in-move-ok": (
        "if type(move) is not str or not move.isascii():",
        "if not isinstance(move, str) or not move.isascii():"),
    "insert-move-type-off": (
        "    if type(move) is not str:\n"
        "        _fail(ec, \"malformed_edge_record\")\n", ""),
}


def _load_mutant(src):
    mod = types.ModuleType("graph._t0134_mutant")
    mod.__file__ = str(PRODUCTION)
    exec(compile(src, str(PRODUCTION), "exec"), mod.__dict__)  # noqa: S102
    return mod


def _kill_suite_red(mod):
    try:
        for args in INSERT_PROBES:
            a = _outcome(lambda a_=args: mod.EdgeTable(DOCS).insert(*a_),
                         (mod.EdgeError,))
            b = _outcome(lambda a_=args: ref.EdgeTable(DOCS).insert(*a_))
            if a != b:
                return True
        for _s, case in _fixture_rows():
            if _fixture_outcome(mod, case) != _fixture_outcome(ref, case):
                return True
            if _s == "malformed":
                fixed = _repaired(case)
                if _fixture_outcome(mod, case, fixed) != \
                        _fixture_outcome(ref, case, fixed):
                    return True
        base = ["standard", "e2e4", STARTPOS, AFTER_E4]
        for pos in range(4):
            args = list(base)
            args[pos] = _S(args[pos])
            try:
                mod.EdgeTable(DOCS).insert(*args)
                return True
            except mod.EdgeError:
                pass
        for rec in _record_probes():
            a = _outcome(lambda r=rec: mod.validate_record(
                *DOCS, copy.deepcopy(r)), (mod.EdgeError,))
            b = _outcome(lambda r=rec: ref.validate_record(
                *DOCS, copy.deepcopy(r)))
            if a != b:
                return True
        good = _good_record()
        rec = {k: v for k, v in good.items() if k != "variant"}
        rec[_HK("variant")] = "standard"
        try:
            mod.validate_record(*DOCS, rec)
            return True
        except mod.EdgeError:
            pass
        const = "pdv1:" + "0" * 64
        t = mod.EdgeTable(DOCS, lambda v, f: const)
        x = t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
        y = t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
        if len(t.records()) != 2 or \
                t.insert("standard", "e2e4", STARTPOS, AFTER_E4) is not x or \
                t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO) is not y:
            return True
        t = mod.EdgeTable(DOCS)
        t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
        before = copy.deepcopy(t.buckets)
        try:
            t.insert("standard", "e2e4", STARTPOS, KINGS)
            return True
        except mod.EdgeError as err:
            if err.failure_class != "conflicting_edge" or t.buckets != before:
                return True
        a_, b_ = mod.EdgeTable(DOCS), mod.EdgeTable(DOCS)
        a_.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
        b_.insert("standard", "e1e2", KINGS, ref.KINGS_ALT_TARGET)
        for x_, y_ in ((a_, b_), (b_, a_)):
            snap = x_.serialize()
            try:
                x_.merge(y_)
                return True
            except mod.EdgeError as err:
                if err.failure_class != "conflicting_edge" or \
                        x_.serialize() != snap:
                    return True
        kgood = _good_record(("standard", "e1e2", KINGS, KINGS_E1E2_TO))
        for bad in ([kgood, {**kgood, "move": "e1e1"}], "r", (kgood,),
                    [kgood, None]):
            t = mod.EdgeTable(DOCS)
            try:
                t.merge(_Src(bad))
                return True
            except mod.EdgeError as err:
                if err.failure_class != MER or t.records():
                    return True
        own = [mod.EdgeError(c, code) for c, code in mod.FAILURE_MAPPING.items()]
        for exc in [*FORGED.values(), *own]:
            for call in (
                    lambda e=exc: mod.EdgeTable(DOCS, _raiser(e)).insert(
                        "standard", "e2e4", STARTPOS, AFTER_E4),
                    lambda e=exc: mod.validate_record(*DOCS, _good_record(),
                                                      _raiser(e)),
                    lambda e=exc: mod.EdgeTable(DOCS, _raiser(e)).merge(
                        _Src([_good_record()]))):
                try:
                    call()
                    return True
                except BaseException as err:  # noqa: BLE001
                    if not _fresh_mer(err, exc, mod):
                        return True
        for out in BAD_OUTPUTS.values():
            for call in (
                    lambda o=out: mod.EdgeTable(DOCS, lambda v, f: o).insert(
                        "standard", "e2e4", STARTPOS, AFTER_E4),
                    lambda o=out: mod.validate_record(
                        *DOCS, _good_record(), lambda v, f: o)):
                try:
                    call()
                    return True
                except mod.EdgeError as err:
                    if err.failure_class != MER:
                        return True
        if _hostile_container_red(mod) or _merge_midway_red(mod) or \
                _source_append_red(mod) or _live_rewrite_red(mod) or \
                _docs_shared_red(mod) or _huge_clock_red(mod) or \
                _collision_merge_red(mod) or _list_subclass_red(mod):
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
