"""T0123: transposition-node conformance fixture - the fixture must
PROVE happy, boundary, malformed and rollback behavior against the
T0122 transposition-node contract. The cases execute against the
contract-derived reference in tests.test_t0122_transposition_node_
contract (itself fully derived from data/contracts/
transposition_node.yaml plus the linked variant, position-digest,
en-passant and FEN contracts) - nothing is re-implemented here.
Pinned records, digests and serializations in the fixture were
computed from that reference at authoring time, so any contract or
derivation drift breaks this battery. Every malformed case is
discriminating (repairing ONLY its declared defect makes the case
valid) and rollback cases prove a rejection leaves no trace.

DESIGN CAUTION: the reference interpreter is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work must
execute these same cases against a separately implemented runtime."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    NodeError,
    NodeTable,
    _docs,
    validate_record,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "transposition_node"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
DOCS = _docs()
NC, VC, DC, EC, FC = DOCS
FAILURE_CLASSES = set(NC["failures"]["classes"])

TOP_KEYS = {"schema", "contract", "contract_schema_version", "notes",
            "happy", "boundary", "malformed", "rollback"}
KIND_KEYS = {
    "node-insert": {"name", "kind", "variant", "input_fen",
                    "expect_record"},
    "transposition": {"name", "kind", "variant", "input_fens",
                      "expect_node_count"},
    "merge": {"name", "kind", "variant", "table_a", "table_b",
              "expect_serialized"},
    "record-validate": {"name", "kind", "record"},
    "rollback-insert": {"name", "kind", "variant", "setup_fens",
                        "reject_variant", "reject_fen",
                        "expect_failure", "then_fen", "expect_record"},
    "rollback-validate": {"name", "kind", "reject_record",
                          "expect_failure", "then_record"},
}
TRANSPOSITION_ONE = {"expect_record"}      # exactly one node expected
TRANSPOSITION_MANY = {"expect_records"}    # distinct nodes expected
REPAIR_KEYS = {"set", "remove", "find", "replace"}
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}

SECTION_KINDS = {
    "happy": {"node-insert", "transposition"},
    "boundary": {"node-insert", "transposition", "merge"},
    "malformed": {"node-insert", "record-validate"},
    "rollback": {"rollback-insert", "rollback-validate"},
}


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    for case in CASES[section]:
        if case["name"] == name:
            return case
    raise AssertionError(f"case {name} not found in {section}")


def _repaired(case):
    """Apply the declarative minimal repair: it touches ONLY the
    declared keys, everything else byte-identical."""
    rep = case["minimal_repair"]
    assert set(rep) <= REPAIR_KEYS
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure", "minimal_repair")}
    if "find" in rep:
        # span-local text repair on the input FEN: the find span is
        # unique, everything outside it byte-identical
        assert case["kind"] == "node-insert"
        text = case["input_fen"]
        assert text.count(rep["find"]) == 1, (
            f"{case['name']}: repair find not unique")
        out["input_fen"] = text.replace(rep["find"], rep["replace"])
        return out
    target_key = "record" if case["kind"] == "record-validate" else None
    for key in rep.get("remove", []):
        if target_key:
            assert key in out[target_key]
            del out[target_key][key]
        else:
            assert key in out
            del out[key]
    for key, value in rep.get("set", {}).items():
        if target_key:
            out[target_key][key] = value
        else:
            assert key in ("variant", "input_fen")
            out[key] = value
    return out


def _validate_structure(cases):
    assert set(cases) == TOP_KEYS
    # the fixture-format version is pinned exactly: an int equal to
    # FIXTURE_SCHEMA_VERSION, never a string, bool, or older/newer
    # integer silently interpreted under v1 assumptions
    assert type(cases["schema"]) is int, "schema must be an int"
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == NC["id"]
    doc = yaml.safe_load(
        (ROOT / "data" / "contracts" / "transposition_node.yaml")
        .read_text())
    assert cases["contract_schema_version"] == doc["schema_version"]
    for section in ("happy", "boundary", "malformed", "rollback"):
        assert cases[section], f"{section} must be non-empty"
        names = [case["name"] for case in cases[section]]
        assert len(names) == len(set(names)), (
            f"{section} names must be unique")
        for case in cases[section]:
            assert case["kind"] in SECTION_KINDS[section], case["name"]
            base = KIND_KEYS[case["kind"]]
            keys = set(case)
            if case["kind"] == "transposition":
                assert (keys == base | TRANSPOSITION_ONE
                        or keys == base | TRANSPOSITION_MANY), (
                    case["name"])
            elif case["kind"] in ("node-insert", "record-validate"):
                if section == "malformed":
                    base = (base - {"expect_record"}
                            | {"defect", "expect_failure",
                               "minimal_repair"})
                assert keys == base, case["name"]
            else:
                assert keys == base, case["name"]
    # every declared failure class is exercised by the malformed
    # battery, and every malformed failure is contract-declared
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared == FAILURE_CLASSES
    # node-insert malformed covers the input-facing classes;
    # record-validate covers the record shape class
    insert_failures = {c["expect_failure"] for c in cases["malformed"]
                       if c["kind"] == "node-insert"}
    assert insert_failures == {"unknown_variant", "malformed_position"}
    record_failures = {c["expect_failure"] for c in cases["malformed"]
                       if c["kind"] == "record-validate"}
    assert record_failures == {"malformed_node_record"}
    # repairs stay declarative: only set/remove of declared keys
    for case in cases["malformed"]:
        assert set(case["minimal_repair"]) <= REPAIR_KEYS
        assert case["minimal_repair"], case["name"]


def test_fixture_structure():
    _validate_structure(CASES)


def test_fixture_schema_version_mutations_fail():
    """The pinned fixture-format version is closed: older, newer,
    string, boolean, and missing schema values all fail structure
    validation."""
    for mutate in (
            lambda m: m.__setitem__("schema", 0),
            lambda m: m.__setitem__("schema", 2),
            lambda m: m.__setitem__("schema", "1"),
            lambda m: m.__setitem__("schema", True),
            lambda m: m.__delitem__("schema")):
        m = copy.deepcopy(CASES)
        mutate(m)
        with pytest.raises(AssertionError):
            _validate_structure(m)


def _run_insert(case):
    table = NodeTable(DOCS)
    record = table.insert(case["variant"], case["input_fen"])
    assert record == case["expect_record"]
    validate_record(NC, VC, DC, EC, FC, record)
    assert table.records() == [record]


def _run_transposition(case):
    table = NodeTable(DOCS)
    returned = [table.insert(case["variant"], fen)
                for fen in case["input_fens"]]
    assert len(table.records()) == case["expect_node_count"]
    if "expect_record" in case:
        for record in returned:
            assert record == case["expect_record"]
    else:
        assert sorted(r["snapshot_fen"] for r in table.records()) == \
            sorted(r["snapshot_fen"] for r in case["expect_records"])
        for record in table.records():
            validate_record(NC, VC, DC, EC, FC, record)
    # order-free: the reversed insertion reaches the same table
    rev = NodeTable(DOCS)
    for fen in reversed(case["input_fens"]):
        rev.insert(case["variant"], fen)
    assert rev.serialize() == table.serialize()


def _run_merge(case):
    def build(fens):
        table = NodeTable(DOCS)
        for fen in fens:
            table.insert(case["variant"], fen)
        return table

    a, b = build(case["table_a"]), build(case["table_b"])
    expect = [tuple(x) for x in case["expect_serialized"]]
    assert a.merge(b).serialize() == expect
    # commutativity and idempotence: merge order is irrelevant and a
    # repeated merge adds nothing
    a2, b2 = build(case["table_a"]), build(case["table_b"])
    assert b2.merge(a2).serialize() == expect
    a3, b3 = build(case["table_a"]), build(case["table_b"])
    a3.merge(b3).merge(build(case["table_b"]))
    assert a3.serialize() == expect


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        if case["kind"] == "node-insert":
            _run_insert(case)
        else:
            _run_transposition(case)


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        if case["kind"] == "node-insert":
            _run_insert(case)
        elif case["kind"] == "transposition":
            _run_transposition(case)
        else:
            _run_merge(case)


def _exec_malformed(case):
    """The ORIGINAL input rejects with the pinned failure class."""
    if case["kind"] == "node-insert":
        table = NodeTable(DOCS)
        try:
            table.insert(case["variant"], case["input_fen"])
        except NodeError as exc:
            assert exc.failure_class == case["expect_failure"]
            assert table.records() == []  # rejection leaves no trace
            return
        raise AssertionError("input unexpectedly accepted")
    try:
        validate_record(NC, VC, DC, EC, FC, case["record"])
    except NodeError as exc:
        assert exc.failure_class == case["expect_failure"]
        return
    raise AssertionError("record unexpectedly validated")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    if case["kind"] == "node-insert":
        table = NodeTable(DOCS)
        record = table.insert(out["variant"], out["input_fen"])
        validate_record(NC, VC, DC, EC, FC, record)
    else:
        validate_record(NC, VC, DC, EC, FC, out["record"])


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    """Collection guard: test_malformed's parameter IDs are exactly
    the fixture's malformed-name set - read from the actual
    parametrize mark, never recomputed from the same expression."""
    marks = [m for m in test_malformed.pytestmark
             if m.name == "parametrize"]
    assert len(marks) == 1
    assert set(marks[0].args[1]) == set(_names("malformed"))


def test_collection_guard_detects_late_fixture_row():
    """A fixture row appended AFTER decorator collection is caught:
    the parametrize mark froze at import, so the guard must fail
    while the row is present."""
    late = copy.deepcopy(CASES["malformed"][0])
    late["name"] = "late-row-witness"
    CASES["malformed"].append(late)
    try:
        with pytest.raises(AssertionError):
            test_malformed_param_ids_equal_fixture_names()
    finally:
        CASES["malformed"].pop()


def test_injected_valid_malformed_row_fails_rejection():
    """An in-memory VALID row labelled malformed must fail the
    original-rejection step - the malformed battery is not
    vacuous."""
    valid = copy.deepcopy(CASES["malformed"][0])
    valid["variant"] = "standard"      # repairs the defect in memory
    table = NodeTable(DOCS)
    record = table.insert(valid["variant"], valid["input_fen"])
    validate_record(NC, VC, DC, EC, FC, record)
    with pytest.raises(AssertionError):
        _exec_malformed(valid)


def test_extra_white_king_repair_removes_only_one_white_king():
    """Narrow controls for the two-white-kings case: the repair
    removes exactly one white king inside its span, adds no black
    king anywhere, and leaves every other field byte-identical;
    the span-local repaired position succeeds on its own."""
    case = _case("malformed", "fen-impossible-two-white-kings")
    rep = case["minimal_repair"]
    assert set(rep) == {"find", "replace"}
    # the span removes exactly one white king, no black king touched
    assert rep["find"].count("K") - rep["replace"].count("K") == 1
    assert rep["find"].count("k") == rep["replace"].count("k") == 0
    # everything outside the span is byte-identical (black king on
    # e8 and all unrelated fields preserved)
    text = case["input_fen"]
    i = text.index(rep["find"])
    repaired = text.replace(rep["find"], rep["replace"])
    assert repaired[:i] == text[:i]
    assert repaired[i + len(rep["replace"]):] == text[i + len(rep["find"]):]
    assert "k" in repaired.split(" ")[0]  # black king still present
    # remove-extra-white-only succeeds end to end
    table = NodeTable(DOCS)
    record = table.insert(case["variant"], repaired)
    validate_record(NC, VC, DC, EC, FC, record)


def test_rollback():
    for name in _names("rollback"):
        case = _case("rollback", name)
        if case["kind"] == "rollback-insert":
            table = NodeTable(DOCS)
            for fen in case["setup_fens"]:
                table.insert(case["variant"], fen)
            before = table.serialize()
            with pytest.raises(NodeError) as exc:
                table.insert(case["reject_variant"],
                             case["reject_fen"])
            assert exc.value.failure_class == case["expect_failure"]
            assert table.serialize() == before
            record = table.insert(case["variant"], case["then_fen"])
            assert record == case["expect_record"]
            validate_record(NC, VC, DC, EC, FC, record)
        else:
            with pytest.raises(NodeError) as exc:
                validate_record(NC, VC, DC, EC, FC,
                                case["reject_record"])
            assert exc.value.failure_class == case["expect_failure"]
            validate_record(NC, VC, DC, EC, FC, case["then_record"])
