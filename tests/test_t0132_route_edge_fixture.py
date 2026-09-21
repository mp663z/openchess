"""T0132: route edge conformance fixture.

Executes the pinned cases in tests/fixtures/route_edge/cases.json
against the T0131 contract-derived reference (EdgeTable) -
nothing re-implemented. Every section is CLOSED by a scenario
manifest: the section's actual {name: metadata} map must equal
the manifest exactly, semantic scenario shapes are asserted from
the case data BEFORE execution, and a mutation battery proves no
row can be deleted, renamed, added, substituted, swapped or moved
across sections without failing structure validation.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0131_route_edge_contract import (  # noqa: E402
    EdgeError,
    _table,
)
from tools.route_edge_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "route_edge"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
FAILURE_CLASSES = {"malformed_edge_record", "malformed_position",
                   "unknown_variant", "conflicting_edge"}
REPAIR_FORMS = {"set_variant", "set_move", "set_from_fen",
                "set_to_fen", "replace_records"}

# -- the closed scenario manifests -------------------------------------------
MER = "malformed_edge_record"
MP = "malformed_position"
UV = "unknown_variant"
CE = "conflicting_edge"

# happy/boundary: name -> (kind, scenario tag)
HAPPY_MANIFEST = {
    "insert-single-edge": ("inserts", "single-edge"),
    "insert-same-edge-twice-returns-existing": ("inserts",
                                                "idempotent"),
    "insert-two-distinct-edges-one-node": ("inserts",
                                           "distinct-one-node"),
    "merge-disjoint-tables": ("merge", "merge-disjoint"),
    "merge-idempotent-self": ("merge", "merge-self"),
}
BOUNDARY_MANIFEST = {
    "insert-promotion-move-edge": ("inserts", "promotion"),
    "merge-empty-into-nonempty": ("merge", "merge-empty-right"),
    "merge-nonempty-into-empty": ("merge", "merge-empty-left"),
    "merge-two-empty-tables": ("merge", "merge-both-empty"),
}
# malformed: name -> (failure class, pinned defect, repair
# form, scenario tag) - the tag is asserted semantically over
# the case data, so a same-tuple substituted row can never fill
# another row's slot.
MALFORMED_MANIFEST = {
    "insert-unknown-variant": (
        UV, "variant id is grammar-valid but not registered",
        "set_variant", "unknown-variant"),
    "insert-position-cannot-exist": (
        MP, "from-position has no black king", "set_from_fen",
        "position-cannot-exist"),
    "insert-target-garbage-fen": (
        MP, "to-position text is not a FEN", "set_to_fen",
        "garbage-fen"),
    "insert-move-too-short": (
        MER, "move text is shorter than long-algebraic",
        "set_move", "move-too-short"),
    "insert-move-overlong": (
        MER,
        "move text is longer than long-algebraic with promotion",
        "set_move", "move-overlong"),
    "insert-move-same-squares": (
        MER, "from-square equals to-square", "set_move",
        "move-same-squares"),
    "insert-move-bad-from-file": (
        MER, "from-file is outside a-h", "set_move",
        "move-bad-from-file"),
    "insert-move-bad-promotion": (
        MER, "promotion letter is not in the linked enum",
        "set_move", "move-bad-promotion"),
    "insert-move-uppercase-from": (
        MER, "from-file is uppercase", "set_move",
        "move-uppercase-from"),
    "insert-move-trailing-space": (
        MER, "move text carries trailing whitespace", "set_move",
        "move-trailing-space"),
    "merge-record-extra-field": (
        MER, "stored record carries an undeclared extra field",
        "replace_records", "record-extra-field"),
    "merge-record-clocks-not-normalized": (
        MER, "stored record snapshot clocks are not normalized",
        "replace_records", "clocks-not-normalized"),
    "insert-conflicting-target": (
        CE,
        "same from-identity plus same move reaches a different "
        "target",
        "set_to_fen", "conflicting-target"),
    "merge-batch-internal-conflict": (
        CE,
        "the source batch itself carries two edges with the same "
        "from-identity and move but different targets",
        "replace_records", "batch-internal-conflict"),
}
# rollback: name -> (kind, expected rejection class)
ROLLBACK_MANIFEST = {
    "rejected-conflicting-insert-then-valid-insert": (
        "rollback-insert", CE),
    "rejected-atomic-merge-then-valid-merge": (
        "rollback-merge", CE),
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

INSERT_KEYS = {"name", "kind", "variant", "move",
               "from_snapshot_fen", "to_snapshot_fen",
               "expect_failure", "defect", "minimal_repair"}
INSERT_SETUP_KEYS = INSERT_KEYS | {"setup"}
MERGE_KEYS = {"name", "kind", "left", "right_records",
              "expect_failure", "defect", "minimal_repair"}
RB_INSERT_KEYS = {"name", "kind", "setup", "rejected",
                  "expect_failure", "then", "expect_serialized"}
RB_MERGE_KEYS = {"name", "kind", "setup", "rejected_records",
                 "expect_failure", "then_right",
                 "expect_serialized"}
HAPPY_INSERT_KEYS = {"name", "kind", "inserts",
                     "expect_serialized"}
HAPPY_MERGE_KEYS = {"name", "kind", "left", "right",
                    "expect_serialized"}


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    for case in CASES[section]:
        if case["name"] == name:
            return case
    raise KeyError(name)


def _assert_scenario(case, scenario):
    """The advertised semantic shape is REALLY present in the
    case data - a substituted row can never satisfy the wrong
    scenario."""
    name = case["name"]
    if scenario == "single-edge":
        assert len(case["inserts"]) == 1, name
        assert len(case["expect_serialized"]) == 1, name
    elif scenario == "idempotent":
        assert len(case["inserts"]) == 2, name
        assert case["inserts"][0] == case["inserts"][1], name
        assert len(case["expect_serialized"]) == 1, name
    elif scenario == "distinct-one-node":
        a, b = case["inserts"]
        assert a[2] == b[2] and a[1] != b[1], name
        assert len(case["expect_serialized"]) == 2, name
    elif scenario == "promotion":
        assert len(case["inserts"]) == 1, name
        assert len(case["inserts"][0][1]) == 5, name
        assert len(case["expect_serialized"]) == 1, name
    elif scenario == "merge-disjoint":
        assert case["left"] and case["right"], name
        left_froms = {op[2] for op in case["left"]}
        right_froms = {op[2] for op in case["right"]}
        assert not (left_froms & right_froms), name
        assert len(case["expect_serialized"]) == (
            len(case["left"]) + len(case["right"])), name
    elif scenario == "merge-self":
        assert case["left"] == case["right"], name
        assert len(case["expect_serialized"]) == len(case["left"]), (
            name)
    elif scenario == "merge-empty-right":
        assert case["left"] and not case["right"], name
        assert case["expect_serialized"] == case["left"], name
    elif scenario == "merge-empty-left":
        assert not case["left"] and case["right"], name
        assert case["expect_serialized"] == case["right"], name
    elif scenario == "merge-both-empty":
        assert not case["left"] and not case["right"], name
        assert case["expect_serialized"] == [], name
    else:  # pragma: no cover - manifest typo guard
        raise AssertionError(f"unknown scenario {scenario!r}")


_VARIANT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_REGISTERED_VARIANTS = {"standard"}
_EDGE_FIELDS = {"variant", "move", "from_snapshot_fen",
                "to_snapshot_fen"}
_PROMOTION_LETTERS = {"q", "r", "b", "n"}


def _assert_malformed_scenario(case, tag):
    """The case data REALLY realizes the named defect locus."""
    name = case["name"]
    if tag == "unknown-variant":
        v = case["variant"]
        assert _VARIANT_ID_RE.fullmatch(v), name
        assert v not in _REGISTERED_VARIANTS, name
    elif tag == "position-cannot-exist":
        board = case["from_snapshot_fen"].split()[0]
        assert "k" not in board, name
    elif tag == "garbage-fen":
        board = case["to_snapshot_fen"].split()[0]
        assert re.fullmatch(r"[rnbqkpRNBQKP1-8/]+", \
                            board) is None, name
    elif tag == "move-too-short":
        assert len(case["move"]) < 4, name
    elif tag == "move-overlong":
        assert len(case["move"]) > 5, name
    elif tag == "move-same-squares":
        assert case["move"][:2] == case["move"][2:4], name
    elif tag == "move-bad-from-file":
        f = case["move"][0]
        assert f not in "abcdefgh" and f.islower(), name
    elif tag == "move-bad-promotion":
        assert len(case["move"]) == 5, name
        assert case["move"][4] not in _PROMOTION_LETTERS, name
    elif tag == "move-uppercase-from":
        assert case["move"][0].isupper(), name
    elif tag == "move-trailing-space":
        assert case["move"] != case["move"].strip(), name
    elif tag == "record-extra-field":
        assert any(set(rec) - _EDGE_FIELDS
                   for rec in case["right_records"]), name
    elif tag == "clocks-not-normalized":
        assert any(not rec["from_snapshot_fen"].endswith(" 0 1")
                   for rec in case["right_records"]), name
    elif tag == "conflicting-target":
        assert any(op[0] == case["variant"]
                   and op[1] == case["move"]
                   and op[2] == case["from_snapshot_fen"]
                   and op[3] != case["to_snapshot_fen"]
                   for op in case["setup"]), name
    elif tag == "batch-internal-conflict":
        recs = case["right_records"]
        assert len(recs) == 2, name
        a, b = recs
        assert (a["variant"], a["move"],
                a["from_snapshot_fen"]) == (
                    b["variant"], b["move"],
                    b["from_snapshot_fen"]), name
        assert a["to_snapshot_fen"] != b["to_snapshot_fen"], name
    else:  # pragma: no cover - manifest typo guard
        raise AssertionError(f"unknown scenario {tag!r}")


def _validate_structure(cases):
    assert set(cases) == TOP_KEYS
    # the fixture-format version is pinned exactly: an int equal
    # to FIXTURE_SCHEMA_VERSION, never a string, bool, or other
    # integer silently interpreted under wrong assumptions
    assert type(cases["schema"]) is int, "schema must be an int"
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == "chess-route-edge"
    assert isinstance(cases["notes"], str) and cases["notes"]
    for section in ("happy", "boundary", "malformed",
                    "rollback"):
        assert cases[section], f"{section} must be non-empty"
        names = [case["name"] for case in cases[section]]
        assert len(names) == len(set(names)), (
            f"{section} names must be unique")
        # CLOSED SCENARIO MANIFEST: the section's actual
        # {name: metadata} map must equal the manifest EXACTLY -
        # a missing, substituted, duplicated, renamed or extra
        # row fails HERE, before execution.
        manifest = MANIFESTS[section]
        assert set(names) == set(manifest), (
            f"{section} scenario set drifted: "
            f"missing={set(manifest) - set(names)} "
            f"extra={set(names) - set(manifest)}")
        for case in cases[section]:
            want = manifest[case["name"]]
            if section in ("happy", "boundary"):
                kind, scenario = want
                assert case["kind"] == kind, case["name"]
                keys = (HAPPY_INSERT_KEYS if kind == "inserts"
                        else HAPPY_MERGE_KEYS)
                assert set(case) == keys, case["name"]
                # fail closed: a substituted row lacking the
                # scenario's expected data shape is a
                # STRUCTURAL failure, never a raw escape
                try:
                    _assert_scenario(case, scenario)
                except AssertionError:
                    raise
                except Exception as exc:
                    raise AssertionError(
                        f"{case['name']}: scenario "
                        f"{scenario!r} not realizable "
                        f"({exc!r})") from exc
            elif section == "malformed":
                failure, defect, repair, tag = want
                assert case["expect_failure"] == failure, (
                    case["name"])
                assert case["defect"] == defect, case["name"]
                assert set(case["minimal_repair"]) == {repair}, (
                    case["name"])
                # fail closed: a substituted row lacking the
                # scenario's expected data shape is a
                # STRUCTURAL failure, never a raw escape
                try:
                    _assert_malformed_scenario(case, tag)
                except AssertionError:
                    raise
                except Exception as exc:
                    raise AssertionError(
                        f"{case['name']}: scenario {tag!r} not "
                        f"realizable ({exc!r})") from exc
                if case["kind"] == "insert":
                    assert set(case) == INSERT_KEYS, case["name"]
                elif case["kind"] == "insert-with-setup":
                    assert set(case) == INSERT_SETUP_KEYS, (
                        case["name"])
                else:
                    assert case["kind"] == "merge", case["name"]
                    assert set(case) == MERGE_KEYS, case["name"]
            else:
                kind, failure = want
                assert case["kind"] == kind, case["name"]
                assert case["expect_failure"] == failure, (
                    case["name"])
                keys = (RB_INSERT_KEYS
                        if kind == "rollback-insert"
                        else RB_MERGE_KEYS)
                assert set(case) == keys, case["name"]
    # every declared failure class is exercised by the malformed
    # battery
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared == FAILURE_CLASSES


def test_fixture_structure():
    _validate_structure(CASES)


# -- executors ----------------------------------------------------------------


def _build_table(inserts):
    t = _table()
    for variant, move, frm, to in inserts:
        t.insert(variant, move, frm, to)
    return t


class _StubTable:
    """A merge source carrying exact stored records."""

    def __init__(self, records):
        self._records = records

    def records(self):
        return self._records


def _exec_happy(case):
    if case["kind"] == "inserts":
        t = _table()
        for variant, move, frm, to in case["inserts"]:
            t.insert(variant, move, frm, to)
    else:
        t = _build_table(case["left"])
        t.merge(_build_table(case["right"]))
    assert [list(x) for x in t.serialize()] == \
        case["expect_serialized"]


def _repaired(case):
    """The exact minimal_repair tagged union applied to a deep
    copy - one of set_variant / set_move / set_from_fen /
    set_to_fen / replace_records, nothing else."""
    rep = case["minimal_repair"]
    assert len(rep) == 1, case["name"]
    form, payload = next(iter(rep.items()))
    out = copy.deepcopy(case)
    if form == "set_variant":
        assert set(payload) == {"variant"}, case["name"]
        out["variant"] = payload["variant"]
    elif form == "set_move":
        assert set(payload) == {"move"}, case["name"]
        out["move"] = payload["move"]
    elif form == "set_from_fen":
        assert set(payload) == {"from_snapshot_fen"}, case["name"]
        out["from_snapshot_fen"] = payload["from_snapshot_fen"]
    elif form == "set_to_fen":
        assert set(payload) == {"to_snapshot_fen"}, case["name"]
        out["to_snapshot_fen"] = payload["to_snapshot_fen"]
    else:
        assert form == "replace_records", case["name"]
        assert set(payload) == {"records"}, case["name"]
        out["right_records"] = copy.deepcopy(payload["records"])
    return out


def _exec_malformed(case, table_factory=None):
    """The ORIGINAL input rejects with the pinned failure class.
    The table any case touches is the SAME object inspected
    after the rejection - mutation of the destination during a
    rejected insert or merge is observed."""
    build = table_factory or _build_table
    if case["kind"] == "insert":
        t = build([])
        before = [list(x) for x in t.serialize()]
        try:
            t.insert(case["variant"], case["move"],
                     case["from_snapshot_fen"],
                     case["to_snapshot_fen"])
        except EdgeError as exc:
            assert exc.failure_class == case["expect_failure"]
            assert exc.code == FAILURE_MAPPING[
                case["expect_failure"]]
            assert exc.code in ERROR_ENUM
            assert [list(x) for x in t.serialize()] == before
            return
        raise AssertionError("input unexpectedly accepted")
    if case["kind"] == "insert-with-setup":
        t = build(case["setup"])
        before = [list(x) for x in t.serialize()]
        try:
            t.insert(case["variant"], case["move"],
                     case["from_snapshot_fen"],
                     case["to_snapshot_fen"])
        except EdgeError as exc:
            assert exc.failure_class == case["expect_failure"]
            assert [list(x) for x in t.serialize()] == before
            return
        raise AssertionError("input unexpectedly accepted")
    t = build(case["left"])
    before = [list(x) for x in t.serialize()]
    try:
        t.merge(_StubTable(copy.deepcopy(case["right_records"])))
    except EdgeError as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[case["expect_failure"]]
        assert [list(x) for x in t.serialize()] == before
        return
    raise AssertionError("input unexpectedly accepted")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    if out["kind"] in ("insert", "insert-with-setup"):
        t = _build_table(out.get("setup", []))
        t.insert(out["variant"], out["move"],
                 out["from_snapshot_fen"], out["to_snapshot_fen"])
    else:
        t = _build_table(out["left"])
        t.merge(_StubTable(copy.deepcopy(out["right_records"])))
    return t


def test_happy():
    for name in _names("happy"):
        _exec_happy(_case("happy", name))


def test_boundary():
    for name in _names("boundary"):
        _exec_happy(_case("boundary", name))


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    """Collection guard: test_malformed's parameter IDs are
    exactly the fixture's malformed-name set - a late-added row
    can never evade execution."""
    marks = test_malformed.pytestmark
    param = next(m for m in marks if m.name == "parametrize")
    assert list(param.args[1]) == _names("malformed")


def test_rollback():
    """A rejected insert or merge leaves the destination table
    bit-identical, and a subsequent valid operation returns the
    pinned serialization."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        t = _build_table(case["setup"])
        before = [list(x) for x in t.serialize()]
        if case["kind"] == "rollback-insert":
            rejected = case["rejected"]
            with pytest.raises(EdgeError) as exc:
                t.insert(rejected["variant"], rejected["move"],
                         rejected["from_snapshot_fen"],
                         rejected["to_snapshot_fen"])
            assert exc.value.failure_class == \
                case["expect_failure"]
            assert [list(x) for x in t.serialize()] == before
            for variant, move, frm, to in case["then"]:
                t.insert(variant, move, frm, to)
        else:
            with pytest.raises(EdgeError) as exc:
                t.merge(_StubTable(
                    copy.deepcopy(case["rejected_records"])))
            assert exc.value.failure_class == \
                case["expect_failure"]
            assert [list(x) for x in t.serialize()] == before
            t.merge(_build_table(case["then_right"]))
        assert [list(x) for x in t.serialize()] == \
            case["expect_serialized"]


def test_determinism():
    """Every happy and boundary case executed twice yields the
    identical serialization."""
    for section in ("happy", "boundary"):
        for name in _names(section):
            case = _case(section, name)
            _exec_happy(case)
            _exec_happy(case)


def test_repairs_minimal_locus():
    """Every repair touches ONLY its declared locus."""
    for case in CASES["malformed"]:
        rep = _repaired(case)
        form = next(iter(case["minimal_repair"]))
        for key in case:
            if key in ("minimal_repair", "right_records",
                       "variant", "move", "from_snapshot_fen",
                       "to_snapshot_fen"):
                continue
            assert rep[key] == case[key], case["name"]
        untouched = {"set_variant": ["move", "from_snapshot_fen",
                                     "to_snapshot_fen"],
                     "set_move": ["variant", "from_snapshot_fen",
                                  "to_snapshot_fen"],
                     "set_from_fen": ["variant", "move",
                                      "to_snapshot_fen"],
                     "set_to_fen": ["variant", "move",
                                    "from_snapshot_fen"],
                     "replace_records": []}[form]
        for key in untouched:
            assert rep[key] == case[key], case["name"]


# -- closed scenario coverage -------------------------------------------------


def test_dispatch_sections_match_manifest():
    """Every iterated or parametrized section dispatches EXACTLY
    the manifest's scenario names - a late, replaced or renamed
    row cannot evade or sneak into execution."""
    for section, manifest in MANIFESTS.items():
        assert set(_names(section)) == set(manifest), section


def _pop_first(m, section):
    m[section].pop(0)


def _rename_first(m, section):
    row = copy.deepcopy(m[section][0])
    row["name"] = row["name"] + "-renamed"
    m[section][0] = row


def _add_extra(m, section):
    row = copy.deepcopy(m[section][0])
    row["name"] = "extra-row-witness"
    m[section].append(row)


def _substitute_first(m, section):
    row = copy.deepcopy(m[section][1])
    row["name"] = m[section][0]["name"]
    m[section][0] = row


def _move_row(m, section):
    other = ({"happy", "boundary", "malformed", "rollback"}
             - {section})
    row = copy.deepcopy(m[sorted(other)[0]][0])
    m[section][0] = row


def _swap_failure(m, section):
    a, b = m["malformed"][0], m["malformed"][3]
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _swap_repair(m, section):
    a, b = m["malformed"][0], m["malformed"][1]
    a["minimal_repair"], b["minimal_repair"] = (
        b["minimal_repair"], a["minimal_repair"])


def _swap_defect(m, section):
    a, b = m["malformed"][3], m["malformed"][4]
    a["defect"], b["defect"] = b["defect"], a["defect"]


def _swap_kind(m, section):
    a, b = m["happy"][0], m["happy"][3]
    a["kind"], b["kind"] = b["kind"], a["kind"]


def _section_mutations():
    out = []
    for section in MANIFESTS:
        out.append((f"{section}-delete-row", _pop_first))
        out.append((f"{section}-rename-row", _rename_first))
        out.append((f"{section}-add-row", _add_extra))
        out.append((f"{section}-substitute-row",
                    _substitute_first))
        out.append((f"{section}-moved-row", _move_row))
    out.append(("malformed-swap-failure", _swap_failure))
    out.append(("malformed-swap-repair", _swap_repair))
    out.append(("malformed-swap-defect", _swap_defect))
    out.append(("happy-swap-kind", _swap_kind))
    return out


def test_section_mutations_fail_structure():
    for label, mutate in _section_mutations():
        for section in MANIFESTS:
            m = copy.deepcopy(CASES)
            mutate(m, section)
            try:
                _validate_structure(m)
            except AssertionError:
                continue
            raise AssertionError(
                f"mutation {label!r} on {section!r} passed")


def test_mutant_whole_section_scenario_substitution():
    """Every happy row replaced by a uniquely-named VALID
    single-edge copy, every boundary row by a VALID
    both-empty-merge copy, every rollback row by a VALID
    conflicting-insert copy - must fail structure validation
    BEFORE execution."""
    m = copy.deepcopy(CASES)
    single = copy.deepcopy(_case("happy", "insert-single-edge"))
    both_empty = copy.deepcopy(
        _case("boundary", "merge-two-empty-tables"))
    rejected = copy.deepcopy(
        _case("rollback",
              "rejected-conflicting-insert-then-valid-insert"))
    m["happy"] = [dict(copy.deepcopy(single),
                       name=f"single-copy-{i}")
                  for i in range(len(HAPPY_MANIFEST))]
    m["boundary"] = [dict(copy.deepcopy(both_empty),
                          name=f"empty-copy-{i}")
                     for i in range(len(BOUNDARY_MANIFEST))]
    m["rollback"] = [dict(copy.deepcopy(rejected),
                          name=f"rejected-copy-{i}")
                     for i in range(len(ROLLBACK_MANIFEST))]
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_mutant_intra_failure_class_malformed_substitution():
    """Malformed rows substituted WITHIN one failure class AND
    one repair form must fail the manifest - the pinned defect
    text discriminates."""
    m = copy.deepcopy(CASES)
    donor = copy.deepcopy(
        _case("malformed", "insert-move-too-short"))
    for i, case in enumerate(m["malformed"]):
        if (case["expect_failure"] == donor["expect_failure"]
                and set(case["minimal_repair"])
                == set(donor["minimal_repair"])
                and case["name"] != donor["name"]):
            m["malformed"][i] = dict(copy.deepcopy(donor),
                                     name=case["name"])
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_exhaustive_pairwise_intra_section_substitution():
    """The standing scan: EVERY ordered donor/recipient pair in
    EVERY section, donor content under the recipient's name,
    must fail structure validation."""
    for section in MANIFESTS:
        rows = CASES[section]
        for i, recipient in enumerate(rows):
            for j, donor in enumerate(rows):
                if i == j:
                    continue
                m = copy.deepcopy(CASES)
                m[section][i] = dict(copy.deepcopy(donor),
                                     name=recipient["name"])
                try:
                    _validate_structure(m)
                except AssertionError:
                    continue
                raise AssertionError(
                    f"{section}: {donor['name']!r} substitutes "
                    f"for {recipient['name']!r} undetected")
