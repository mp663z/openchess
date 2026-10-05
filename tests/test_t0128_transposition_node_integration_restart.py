"""T0128 Graph/transposition node/integration/restart: the production
node table (graph/transposition_node.py) driven end to end and across a REAL
process restart.

Integration: two different move orders (hand-written FEN sequences, never
produced by the runtime) reach one position. Every arrival must land on ONE
node whose snapshot is the hand-pinned canonical one (clocks 0 1, en-passant
identity value), a legal en-passant target stays in the identity and an
unusable one normalizes away, and the table holds exactly the hand-counted
nodes. A colliding digest oracle must never merge distinct identities.

Restart: the table is persisted as JSON records, a FRESH process rebuilds it
through the public merge, continues the second path, and the final table
must be bit-identical to the uninterrupted one. Corrupted persisted records are
refused IN THE FRESH PROCESS with the declared class and mapped code, and a
rejected load leaves the table unchanged. One-edit source mutants of the
runtime run in the fresh process; each must be killed by a semantic deviation
from the hand-pinned expectation (a crash is an error, never a kill).
Test-only: no production change.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph import position_digest  # noqa: E402
from graph.transposition_node import NodeError, NodeTable  # noqa: E402

SOURCE = (ROOT / "graph" / "transposition_node.py").read_text()
CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "transposition_node.yaml").read_text())[
    "contract"
]
MAPPING = dict(CONTRACT["failures"]["mapping"])

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PATH_A = [
    "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1",
    "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2",
    "rnbqkb1r/pppppppp/5n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R b KQkq - 3 2",
    "r1bqkb1r/pppppppp/2n2n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R w KQkq - 4 3",
]
PATH_B = [
    "rnbqkbnr/pppppppp/8/8/8/2N5/PPPPPPPP/R1BQKBNR b KQkq - 1 1",
    "r1bqkbnr/pppppppp/2n5/8/8/2N5/PPPPPPPP/R1BQKBNR w KQkq - 2 2",
    "r1bqkbnr/pppppppp/2n5/8/8/2N2N2/PPPPPPPP/R1BQKB1R b KQkq - 3 2",
    # same position as the end of PATH_A, reached with different clocks
    "r1bqkb1r/pppppppp/2n2n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R w KQkq - 0 9",
]
FINAL = "r1bqkb1r/pppppppp/2n2n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R w KQkq - 0 1"
EXPECTED_NODES = 8  # start + 4 (A) + 3 (B before the shared final)


def snap(fen):
    """Hand-pinned canonical snapshot: clocks normalized to 0 1."""
    parts = fen.split(" ")
    return " ".join([*parts[:4], "0", "1"])


E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
E4_SNAP = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
LEGAL_EP = "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
KINGS_B = "4k3/8/8/8/8/8/8/4K3 b - - 0 1"


CHILD = r"""
import json, sys, types
root = sys.argv[1]
sys.path.insert(0, root)
payload = json.load(sys.stdin)
if payload.get("source"):
    mod = types.ModuleType("mutant_node")
    mod.__file__ = root + "/graph/transposition_node.py"
    exec(compile(payload["source"], mod.__file__, "exec"), mod.__dict__)
else:
    from graph import transposition_node as mod
from graph import position_digest


def const_digest(variant, fen):
    return position_digest.digest_fen("standard", "4k3/8/8/8/8/8/8/4K3 w - - 0 1")


class Source:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


table = mod.NodeTable(digest_fn=const_digest) if payload["oracle"] == "const" else mod.NodeTable()
out = {"results": []}
try:
    if payload["load"] is not None:
        table.merge(Source(payload["load"]))
    for op in payload["ops"]:
        rec = table.insert(op[0], op[1])
        out["results"].append({"record": rec, "count": len(table.records())})
    out["ok"] = True
except mod.NodeError as err:
    out.update(ok=False, failure_class=err.failure_class, code=err.code)
except Exception as exc:
    out.update(ok=False, crash=type(exc).__name__ + ": " + str(exc))
out["rows"] = table.serialize()
print(json.dumps(out))
"""


def child_run(ops=(), load=None, source=None, oracle="real"):
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(ROOT)],
        input=json.dumps(
            {"ops": [list(o) for o in ops], "load": load, "source": source, "oracle": oracle}
        ),
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    assert out.returncode == 0, f"child crashed: {out.stderr[-400:]}"
    return json.loads(out.stdout)


def std(fens):
    return [("standard", f) for f in fens]


def expected_rows(fens):
    return sorted(
        {("standard", position_digest.digest_fen("standard", snap(f)), snap(f)) for f in fens}
    )


def test_two_move_orders_reach_one_node_with_pinned_snapshot():
    table = NodeTable()
    first = [table.insert("standard", f) for f in [START, *PATH_A]]
    second = [table.insert("standard", f) for f in PATH_B]
    assert second[-1] is first[-1]
    assert second[-1]["snapshot_fen"] == FINAL
    assert len(table.records()) == EXPECTED_NODES
    assert table.serialize() == expected_rows([START, *PATH_A, *PATH_B])
    for rec in table.records():
        assert set(rec) == {"variant", "digest", "snapshot_fen"}
        assert rec["snapshot_fen"].endswith(" 0 1")


def test_en_passant_identity_and_clock_normalization():
    table = NodeTable()
    assert table.insert("standard", E4)["snapshot_fen"] == E4_SNAP
    assert table.insert("standard", LEGAL_EP)["snapshot_fen"] == snap(LEGAL_EP)
    assert snap(LEGAL_EP).split(" ")[3] == "e3"
    assert table.insert("standard", E4_SNAP.replace(" 0 1", " 57 31")) is table.insert(
        "standard", E4
    )
    assert table.insert("standard", KINGS) is not table.insert("standard", KINGS_B)


def test_restart_continuity_bit_identical():
    ref = NodeTable()
    for f in [START, *PATH_A, *PATH_B]:
        ref.insert("standard", f)
    mid = NodeTable()
    for f in [START, *PATH_A]:
        mid.insert("standard", f)
    persisted = json.loads(json.dumps(mid.records()))
    child = child_run(std(PATH_B), load=persisted)
    assert child["ok"], child
    assert child["rows"] == json.loads(json.dumps(ref.serialize()))
    counts = [r["count"] for r in child["results"]]
    assert counts == [6, 7, 8, 8], counts  # the shared final adds no node
    assert child["results"][-1]["record"]["snapshot_fen"] == FINAL
    # the restored final node IS the one persisted before the restart
    assert child["results"][-1]["record"] in persisted


def test_restart_between_every_pair_of_arrivals():
    allf = [START, *PATH_A, *PATH_B]
    ref = expected_rows(allf)
    for cut in range(len(allf) + 1):
        t = NodeTable()
        for f in allf[:cut]:
            t.insert("standard", f)
        child = child_run(std(allf[cut:]), load=json.loads(json.dumps(t.records())))
        assert child["ok"], (cut, child)
        assert child["rows"] == json.loads(json.dumps(ref)), cut


def test_colliding_oracle_never_merges_distinct_identities_across_restart():
    fens = [START, *PATH_A]
    child = child_run(std(fens), oracle="const")
    assert child["ok"], child
    assert len({r["record"]["digest"] for r in child["results"]}) == 1
    assert [r["count"] for r in child["results"]] == [1, 2, 3, 4, 5]
    again = child_run(std(fens + fens), oracle="const")
    assert [r["count"] for r in again["results"]] == [1, 2, 3, 4, 5, 5, 5, 5, 5, 5]


def _good(fen):
    return {
        "variant": "standard",
        "digest": position_digest.digest_fen("standard", snap(fen)),
        "snapshot_fen": snap(fen),
    }


BASE = _good(START)
CORRUPT = {
    "missing_field": ({k: v for k, v in BASE.items() if k != "digest"}, "malformed_node_record"),
    "extra_field": ({**BASE, "z": 1}, "malformed_node_record"),
    "digest_wrong": ({**BASE, "digest": _good(KINGS)["digest"]}, "malformed_node_record"),
    "digest_bad_format": ({**BASE, "digest": "nope"}, "malformed_node_record"),
    "clock_not_normalized": (
        {**BASE, "snapshot_fen": START.replace(" 0 1", " 3 4")},
        "malformed_node_record",
    ),
    "snapshot_unparseable": ({**BASE, "snapshot_fen": "garbage"}, "malformed_node_record"),
    "non_str_digest": ({**BASE, "digest": 7}, "malformed_node_record"),
    "unknown_variant": ({**BASE, "variant": "nope"}, "unknown_variant"),
}


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_corrupted_persisted_record_rejected_in_fresh_process(name):
    record, cls = CORRUPT[name]
    good = _good(KINGS)
    child = child_run([], load=[good, record])
    assert child["ok"] is False and "crash" not in child, child
    assert child["failure_class"] == cls
    assert child["code"] == MAPPING[cls]
    # rollback: the valid record loaded before the bad one is NOT adopted
    assert child["rows"] == []


def test_malformed_position_and_unknown_variant_inserts_after_restart():
    for ops, cls in (
        ([("standard", "garbage")], "malformed_position"),
        ([("standard", START[:-2])], "malformed_position"),
        ([("nope", START)], "unknown_variant"),
    ):
        child = child_run(ops, load=[_good(KINGS)])
        assert child["ok"] is False and "crash" not in child, child
        assert (child["failure_class"], child["code"]) == (cls, MAPPING[cls])
        assert child["rows"] == [list(_row(_good(KINGS)))]


def _row(rec):
    return (rec["variant"], rec["digest"], rec["snapshot_fen"])


def test_rejected_insert_leaves_table_unchanged_in_process():
    table = NodeTable()
    table.insert("standard", START)
    before = table.serialize()
    for args in (("standard", "garbage"), ("nope", START), ("standard", 5)):
        with pytest.raises(NodeError):
            table.insert(*args)
        assert table.serialize() == before


# -- one-edit mutants of the runtime, executed in the fresh process ----------------


def _once(old, new):
    assert SOURCE.count(old) == 1, old
    return SOURCE.replace(old, new)


EDITS = {
    "equality-by-digest-only": _once(
        "            if self._identity(existing) == new_identity:\n                return existing",
        "            if True:\n                return existing",
    ),
    "duplicate-node-on-hit": _once(
        "            if self._identity(existing) == new_identity:\n                return existing",
        "            if self._identity(existing) == new_identity:\n                break",
    ),
    "clock-not-normalized-to-one": _once(
        "{named['castling_rights']} {named['en_passant']} 0 1\")",
        "{named['castling_rights']} {named['en_passant']} 0 2\")",
    ),
    "en-passant-dropped-from-identity": _once(
        '"en_passant": _ep_identity(dc, ec, fc, position),', '"en_passant": "-",'
    ),
    "merge-skips-validation": _once(
        "        for rec in frozen:\n            validate_record(",
        "        for rec in []:\n            validate_record(",
    ),
}
EXPECTED_KILL = {
    "equality-by-digest-only": "collision",
    "duplicate-node-on-hit": "transposition",
    "clock-not-normalized-to-one": "snapshot",
    "en-passant-dropped-from-identity": "en-passant",
    "merge-skips-validation": "corrupt-load",
}


def _deviations(source):
    found = set()
    runs = {
        "collision": child_run(std([START, *PATH_A]), source=source, oracle="const"),
        "transposition": child_run(std([START, *PATH_A, *PATH_B]), source=source),
        "snapshot": child_run(std([E4]), source=source),
        "en-passant": child_run(std([LEGAL_EP]), source=source),
        "corrupt-load": child_run(
            [], load=[_good(KINGS), CORRUPT["digest_wrong"][0]], source=source
        ),
    }
    for name, run in runs.items():
        assert "crash" not in run, f"{name}: mutant crashed instead of deviating: {run}"
    if [r["count"] for r in runs["collision"].get("results", [])] != [1, 2, 3, 4, 5]:
        found.add("collision")
    if runs["transposition"].get("rows") != json.loads(
        json.dumps(expected_rows([START, *PATH_A, *PATH_B]))
    ):
        found.add("transposition")
    if [r["record"]["snapshot_fen"] for r in runs["snapshot"].get("results", [])] != [E4_SNAP]:
        found.add("snapshot")
    if [r["record"]["snapshot_fen"] for r in runs["en-passant"].get("results", [])] != [
        snap(LEGAL_EP)
    ]:
        found.add("en-passant")
    bad = runs["corrupt-load"]
    if not (
        bad["ok"] is False
        and bad.get("failure_class") == "malformed_node_record"
        and bad["rows"] == []
    ):
        found.add("corrupt-load")
    return found


def test_unmutated_runtime_has_no_deviation():
    assert _deviations(None) == set()


@pytest.mark.parametrize("label", sorted(EDITS))
def test_runtime_mutant_is_killed_semantically(label):
    killed_by = _deviations(EDITS[label])
    assert EXPECTED_KILL[label] in killed_by, (label, killed_by)
