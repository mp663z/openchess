"""T0137 Graph/route edge/integration/restart: the production edge table
(graph/route_edge.py) driven end to end and across a REAL process restart.

Integration: the two move orders of a transposition (hand-written FEN
sequences and move texts, never produced by the runtime) are inserted as edges.
Both orders end on ONE node, so the table holds exactly the hand-counted edges,
re-inserting an edge (even with different clocks on its FENs) returns the stored
one, and one from-identity plus one move never reaches two targets
(conflicting_edge, with nothing committed). A colliding digest oracle must not
merge or conflict distinct edges.

Restart: the table is persisted as JSON, a FRESH process rebuilds it through the
public merge, continues the second order, and the final table is bit-identical
to the uninterrupted one at every cut point. Corrupted persisted records and
conflicting unions are refused IN THE FRESH PROCESS with the declared class and
mapped code; the rejected load leaves the table empty. One-edit source mutants
run in the fresh process and each must be killed by a semantic deviation (a
crash is an error, never a kill). Test-only: no production change.
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
from graph.route_edge import EdgeError, EdgeTable  # noqa: E402

SOURCE = (ROOT / "graph" / "route_edge.py").read_text()
CONTRACT = yaml.safe_load((ROOT / "data" / "contracts" / "route_edge.yaml").read_text())["contract"]
MAPPING = dict(CONTRACT["failures"]["mapping"])

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
A1 = "rnbqkbnr/pppppppp/8/8/8/5N2/PPPPPPPP/RNBQKB1R b KQkq - 1 1"
A2 = "rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2"
A3 = "rnbqkb1r/pppppppp/5n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R b KQkq - 3 2"
FINAL_A = "r1bqkb1r/pppppppp/2n2n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R w KQkq - 4 3"
B1 = "rnbqkbnr/pppppppp/8/8/8/2N5/PPPPPPPP/R1BQKBNR b KQkq - 1 1"
B2 = "r1bqkbnr/pppppppp/2n5/8/8/2N5/PPPPPPPP/R1BQKBNR w KQkq - 2 2"
B3 = "r1bqkbnr/pppppppp/2n5/8/8/2N2N2/PPPPPPPP/R1BQKB1R b KQkq - 3 2"
FINAL_B = "r1bqkb1r/pppppppp/2n2n2/8/8/2N2N2/PPPPPPPP/R1BQKB1R w KQkq - 0 9"

EDGES_A = [
    ("g1f3", START, A1),
    ("g8f6", A1, A2),
    ("b1c3", A2, A3),
    ("b8c6", A3, FINAL_A),
]
EDGES_B = [
    ("b1c3", START, B1),
    ("b8c6", B1, B2),
    ("g1f3", B2, B3),
    ("g8f6", B3, FINAL_B),
]
EXPECTED_EDGES = 8


def snap(fen):
    parts = fen.split(" ")
    return " ".join([*parts[:4], "0", "1"])


def args(edges):
    return [("standard", m, a, b) for m, a, b in edges]


def expected_rows(edges):
    return sorted({("standard", m, snap(a), snap(b)) for m, a, b in edges})


CHILD = r"""
import json, sys, types
root = sys.argv[1]
sys.path.insert(0, root)
payload = json.load(sys.stdin)
if payload.get("source"):
    mod = types.ModuleType("mutant_edge")
    mod.__file__ = root + "/graph/route_edge.py"
    exec(compile(payload["source"], mod.__file__, "exec"), mod.__dict__)
else:
    from graph import route_edge as mod
from graph import position_digest


def const_digest(variant, fen):
    return position_digest.digest_fen("standard", "4k3/8/8/8/8/8/8/4K3 w - - 0 1")


class Source:
    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


table = mod.EdgeTable(digest_fn=const_digest) if payload["oracle"] == "const" else mod.EdgeTable()
out = {"results": []}
try:
    if payload["load"] is not None:
        table.merge(Source(payload["load"]))
    for op in payload["ops"]:
        rec = table.insert(*op)
        out["results"].append({"record": rec, "count": len(table.records())})
    out["ok"] = True
except mod.EdgeError as err:
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


def jrows(rows):
    return json.loads(json.dumps(rows))


def test_two_move_orders_share_the_final_node_and_edges_are_counted():
    table = EdgeTable()
    stored = [table.insert(*a) for a in args(EDGES_A + EDGES_B)]
    assert len(table.records()) == EXPECTED_EDGES
    assert table.serialize() == expected_rows(EDGES_A + EDGES_B)
    assert stored[3]["to_snapshot_fen"] == stored[7]["to_snapshot_fen"] == snap(FINAL_A)
    for rec in table.records():
        assert set(rec) == {"variant", "move", "from_snapshot_fen", "to_snapshot_fen"}


def test_reinserting_an_edge_with_other_clocks_returns_the_stored_edge():
    table = EdgeTable()
    first = table.insert("standard", "g1f3", START, A1)
    again = table.insert("standard", "g1f3", START.replace(" 0 1", " 40 21"), A1)
    assert again is first and len(table.records()) == 1


def test_one_from_identity_and_move_never_reaches_two_targets():
    table = EdgeTable()
    table.insert("standard", "g1f3", START, A1)
    before = table.serialize()
    with pytest.raises(EdgeError) as err:
        table.insert("standard", "g1f3", START, B1)
    assert (err.value.failure_class, err.value.code) == ("conflicting_edge", "conflicting_edge")
    assert table.serialize() == before
    # a different move from the same node to B1 is a different edge
    table.insert("standard", "b1c3", START, B1)
    assert len(table.records()) == 2


@pytest.mark.parametrize("cut", range(len(EDGES_A + EDGES_B) + 1))
def test_restart_continuity_bit_identical(cut):
    edges = EDGES_A + EDGES_B
    mid = EdgeTable()
    for a in args(edges[:cut]):
        mid.insert(*a)
    persisted = json.loads(json.dumps(mid.records()))
    child = child_run(args(edges[cut:]), load=persisted)
    assert child["ok"], child
    assert child["rows"] == jrows(expected_rows(edges))
    if cut == 7:  # the last edge arrives at the already stored final node
        assert child["results"][-1]["count"] == EXPECTED_EDGES


def test_restart_restores_conflict_detection():
    mid = EdgeTable()
    mid.insert("standard", "g1f3", START, A1)
    persisted = json.loads(json.dumps(mid.records()))
    child = child_run([("standard", "g1f3", START, B1)], load=persisted)
    assert child["ok"] is False and "crash" not in child
    assert (child["failure_class"], child["code"]) == ("conflicting_edge", "conflicting_edge")
    assert child["rows"] == jrows(expected_rows([("g1f3", START, A1)]))


def test_colliding_oracle_never_merges_or_conflicts_distinct_edges():
    child = child_run(args(EDGES_A), oracle="const")
    assert child["ok"], child
    assert [r["count"] for r in child["results"]] == [1, 2, 3, 4]
    again = child_run(args(EDGES_A + EDGES_A), oracle="const")
    assert [r["count"] for r in again["results"]] == [1, 2, 3, 4, 4, 4, 4, 4]


def _good(move, a, b):
    return {
        "variant": "standard",
        "move": move,
        "from_snapshot_fen": snap(a),
        "to_snapshot_fen": snap(b),
    }


GOOD = _good("g1f3", START, A1)
OTHER = _good("g8f6", A1, A2)
CORRUPT = {
    "missing_field": ({k: v for k, v in GOOD.items() if k != "move"}, "malformed_edge_record"),
    "extra_field": ({**GOOD, "z": 1}, "malformed_edge_record"),
    "non_str_move": ({**GOOD, "move": 7}, "malformed_edge_record"),
    "move_same_squares": ({**GOOD, "move": "e2e2"}, "malformed_edge_record"),
    "move_off_board": ({**GOOD, "move": "e2e9"}, "malformed_edge_record"),
    "move_bad_promotion": ({**GOOD, "move": "a7a8x"}, "malformed_edge_record"),
    "move_too_long": ({**GOOD, "move": "a7a8qq"}, "malformed_edge_record"),
    "from_clock_not_normalized": (
        {**GOOD, "from_snapshot_fen": START.replace(" 0 1", " 3 4")},
        "malformed_edge_record",
    ),
    "to_unparseable": ({**GOOD, "to_snapshot_fen": "garbage"}, "malformed_edge_record"),
    "unknown_variant": ({**GOOD, "variant": "nope"}, "unknown_variant"),
}


@pytest.mark.parametrize("name", sorted(CORRUPT))
def test_corrupted_persisted_record_rejected_in_fresh_process(name):
    record, cls = CORRUPT[name]
    child = child_run([], load=[OTHER, record])
    assert child["ok"] is False and "crash" not in child, child
    assert child["failure_class"] == cls
    assert child["code"] == MAPPING[cls]
    assert child["rows"] == [], "a rejected load must adopt nothing"


def test_conflicting_union_in_one_load_is_refused_atomically():
    clash = _good("g1f3", START, B1)
    child = child_run([], load=[OTHER, GOOD, clash])
    assert child["ok"] is False and "crash" not in child
    assert (child["failure_class"], child["code"]) == ("conflicting_edge", "conflicting_edge")
    assert child["rows"] == []


def test_malformed_inserts_after_restart():
    base = [GOOD]
    cases = (
        (("standard", "g1f3", "garbage", A1), "malformed_position"),
        (("standard", "g1f3", START, 5), "malformed_position"),
        (("standard", "g1f3x", START, A1), "malformed_edge_record"),
        (("nope", "g1f3", START, A1), "unknown_variant"),
    )
    for op, cls in cases:
        child = child_run([op], load=base)
        assert child["ok"] is False and "crash" not in child, child
        assert (child["failure_class"], child["code"]) == (cls, MAPPING[cls])
        assert child["rows"] == jrows(expected_rows([("g1f3", START, A1)]))


# -- one-edit mutants of the runtime, executed in the fresh process ----------------


def _once(old, new):
    assert SOURCE.count(old) == 1, old
    return SOURCE.replace(old, new)


HIT = "            if existing_identity == new_identity:\n                return existing"
EDITS = {
    "conflict-check-dropped": _once('_fail(self.ec, "conflicting_edge")', "pass"),
    "duplicate-edge-on-hit": _once(HIT, HIT.replace("return existing", "break")),
    "equality-by-bucket-only": _once(HIT, HIT.replace("existing_identity == new_identity", "True")),
    "merge-skips-validation": _once(
        "            validate_record(*self._docs(), rec, self.digest_fn)\n", "            pass\n"
    ),
    "promotion-letter-unchecked": _once('move[4] in shape["types"]["promotion"]["enum"])', "True)"),
    "same-square-move-accepted": _once(
        'if shape["from_to_distinct"] and squares[0] == squares[1]:', "if False:"
    ),
}
EXPECTED_KILL = {
    "conflict-check-dropped": "conflict",
    "duplicate-edge-on-hit": "duplicate",
    "equality-by-bucket-only": "collision",
    "merge-skips-validation": "load-clock",
    "promotion-letter-unchecked": "load-promotion",
    "same-square-move-accepted": "load-same-square",
}


def _refused(run, cls):
    return run["ok"] is False and run.get("failure_class") == cls and run["rows"] == []


def _deviations(source):
    runs = {
        "conflict": child_run(
            [("standard", "g1f3", START, A1), ("standard", "g1f3", START, B1)], source=source
        ),
        "transposition": child_run(args(EDGES_A + EDGES_B), source=source),
        "collision": child_run(args(EDGES_A + EDGES_B), source=source, oracle="const"),
        "duplicate": child_run(args(EDGES_A + EDGES_A), source=source),
        "load-clock": child_run(
            [], load=[OTHER, CORRUPT["from_clock_not_normalized"][0]], source=source
        ),
        "load-promotion": child_run(
            [], load=[OTHER, CORRUPT["move_bad_promotion"][0]], source=source
        ),
        "load-same-square": child_run(
            [], load=[OTHER, CORRUPT["move_same_squares"][0]], source=source
        ),
    }
    for name, run in runs.items():
        assert "crash" not in run, f"{name}: mutant crashed instead of deviating: {run}"
    found = set()
    c = runs["conflict"]
    if not (
        c["ok"] is False
        and c.get("failure_class") == "conflicting_edge"
        and c["rows"] == jrows(expected_rows([("g1f3", START, A1)]))
    ):
        found.add("conflict")
    if runs["transposition"].get("rows") != jrows(expected_rows(EDGES_A + EDGES_B)):
        found.add("transposition")
    if [r["count"] for r in runs["collision"].get("results", [])] != [1, 2, 3, 4, 5, 6, 7, 8]:
        found.add("collision")
    if [r["count"] for r in runs["duplicate"].get("results", [])] != [1, 2, 3, 4, 4, 4, 4, 4]:
        found.add("duplicate")
    for tag in ("load-clock", "load-promotion", "load-same-square"):
        if not _refused(runs[tag], "malformed_edge_record"):
            found.add(tag)
    return found


def test_unmutated_runtime_has_no_deviation():
    assert _deviations(None) == set()


@pytest.mark.parametrize("label", sorted(EDITS))
def test_runtime_mutant_is_killed_semantically(label):
    killed_by = _deviations(EDITS[label])
    assert EXPECTED_KILL[label] in killed_by, (label, killed_by)


def test_pinned_digest_helper_is_the_shipped_oracle():
    assert position_digest.digest_fen("standard", START)
