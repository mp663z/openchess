"""T0105: closed, executable UCI conformance fixture."""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
from pathlib import Path

import pytest
import yaml

from tests.test_t0104_uci_contract import (
    FrameReader,
    Session,
    UciError,
    emit,
    parse_engine,
    parse_gui,
)

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/uci/cases.json").read_text())
DOC = yaml.safe_load((ROOT / "data/contracts/uci.yaml").read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP = {"schema_version", "contract", "contract_schema_version", "section_manifests", *SECTIONS}
KEYS = {
    "happy": {"name", "scenario_tag", "direction", "line", "expect"},
    "boundary": {"name", "scenario_tag", "kind", "direction", "line", "expect"},
    "malformed": {
        "name",
        "scenario_tag",
        "kind",
        "direction",
        "line",
        "repair_line",
        "expect_failure",
    },
    "rollback": {"name", "scenario_tag", "prefix", "direction", "line", "expect_failure"},
}
# framing rows replace direction/line/expect forms.
FRAME_BOUNDARY = {"name", "scenario_tag", "kind", "chunks_hex", "expect_frames"}
FRAME_MALFORMED = {
    "name",
    "scenario_tag",
    "kind",
    "chunks_hex",
    "repair_chunks_hex",
    "expect_failure",
}
FAIL = set(DOC["contract"]["failure_classes"])


def _typed_keys(d):
    return sorted((type(k).__name__, repr(k)) for k in d)


def _keys(d, expected, where):
    assert type(d) is dict, f"{where}: object required"
    assert set(d) == expected, (
        f"{where}: {_typed_keys(d)} != {_typed_keys({k: None for k in expected})}"
    )


def _validate(cases):
    _keys(cases, TOP, "root")
    assert type(cases["schema_version"]) is int and cases["schema_version"] == 1
    assert (
        type(cases["contract_schema_version"]) is int
        and cases["contract_schema_version"] == DOC["schema_version"]
    )
    assert cases["contract"] == "data/contracts/uci.yaml"
    _keys(cases["section_manifests"], set(SECTIONS), "manifests")
    names = []
    tags = []
    for section in SECTIONS:
        rows = cases[section]
        assert type(rows) is list and rows
        for i, row in enumerate(rows):
            expected = KEYS[section]
            if row.get("kind") == "framing":
                expected = FRAME_BOUNDARY if section == "boundary" else FRAME_MALFORMED
            _keys(row, expected, f"{section}[{i}]")
            assert type(row["name"]) is str and row["name"]
            assert type(row["scenario_tag"]) is str and ":" in row["scenario_tag"]
            if "direction" in row:
                assert row["direction"] in {"gui", "engine"}
            if "expect_failure" in row:
                assert row["expect_failure"] in FAIL
            names.append(row["name"])
            tags.append(row["scenario_tag"])
        manifest = cases["section_manifests"][section]
        assert type(manifest) is dict
        assert set(manifest) == {row["name"] for row in rows}
        computed = {
            row["name"]: hashlib.sha256(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            for row in rows
        }
        assert manifest == computed
    assert len(names) == len(set(names))
    assert len(tags) == len(set(tags))


def _parse(row, line=None):
    parser = parse_gui if row["direction"] == "gui" else parse_engine
    return parser(DOC["contract"], row["line"] if line is None else line)


def _frames(chunks):
    r = FrameReader(DOC["contract"])
    out = []
    for chunk in chunks:
        out += r.feed(bytes.fromhex(chunk))
    r.finish()
    return out


def test_fixture_structure():
    _validate(CASES)


@pytest.mark.parametrize("section", SECTIONS)
def test_closed_ordered_manifests(section):
    assert [r["name"] for r in CASES[section]] == list(CASES["section_manifests"][section])


def test_happy_executes_and_roundtrips():
    for row in CASES["happy"]:
        got = _parse(row)
        assert got == row["expect"]
        assert emit(got) == row["line"]


def test_boundaries_execute():
    for row in CASES["boundary"]:
        if row["kind"] == "framing":
            assert _frames(row["chunks_hex"]) == row["expect_frames"]
        else:
            got = _parse(row)
            assert got == row["expect"]
            assert emit(got) == row["line"]


def test_malformed_rejects_and_repairs_execute():
    for row in CASES["malformed"]:
        with pytest.raises(UciError) as err:
            _frames(row["chunks_hex"]) if row["kind"] == "framing" else _parse(row)
        assert err.value.failure_class == row["expect_failure"]
        if row["kind"] == "framing":
            assert _frames(row["repair_chunks_hex"])
        else:
            assert _parse(row, row["repair_line"])


def test_rollback_is_bit_identical():
    for row in CASES["rollback"]:
        s = Session(DOC["contract"])
        for direction, line in row["prefix"]:
            getattr(s, "feed_" + direction)(line)
        before = copy.deepcopy(s.__dict__)
        with pytest.raises(UciError) as err:
            getattr(s, "feed_" + row["direction"])(row["line"])
        assert err.value.failure_class == row["expect_failure"]
        assert s.__dict__ == before


def test_exhaustive_ordered_pairwise_scenario_substitution_closure():
    for section in SECTIONS:
        rows = CASES[section]
        for target_index, donor_index in itertools.permutations(range(len(rows)), 2):
            target = rows[target_index]
            donor = rows[donor_index]
            mutant = copy.deepcopy(CASES)
            replacement = copy.deepcopy(donor)
            replacement["name"] = target["name"]
            replacement["scenario_tag"] = target["scenario_tag"]
            mutant[section][target_index] = replacement
            with pytest.raises(AssertionError):
                _validate(mutant)


def test_each_payload_field_and_scenario_tag_is_manifest_bound():
    for section in SECTIONS:
        rows = CASES[section]
        for target_index, donor_index in itertools.permutations(range(len(rows)), 2):
            shared = (set(rows[target_index]) & set(rows[donor_index])) - {"name"}
            for field in shared:
                if rows[target_index][field] == rows[donor_index][field]:
                    continue
                mutant = copy.deepcopy(CASES)
                mutant[section][target_index][field] = copy.deepcopy(rows[donor_index][field])
                with pytest.raises(AssertionError):
                    _validate(mutant)


def test_manifest_guard_is_non_vacuous_for_parse_substitution():
    mutant = copy.deepcopy(CASES)
    target = mutant["happy"][0]
    donor = copy.deepcopy(mutant["happy"][1])
    donor["name"] = target["name"]
    donor["scenario_tag"] = target["scenario_tag"]
    mutant["happy"][0] = donor
    got = _parse(donor)
    assert got == donor["expect"]
    assert emit(got) == donor["line"]
    with pytest.raises(AssertionError):
        _validate(mutant)


def test_manifest_guard_is_non_vacuous_for_frame_substitution():
    mutant = copy.deepcopy(CASES)
    donor = copy.deepcopy(mutant["boundary"][2])
    # Cross-section donor proves a valid framing payload executes; place it
    # in boundary under another boundary identity so only the digest rejects.
    boundary_target = mutant["boundary"][0]
    donor["name"] = boundary_target["name"]
    donor["scenario_tag"] = boundary_target["scenario_tag"]
    mutant["boundary"][0] = donor
    assert _frames(donor["chunks_hex"]) == donor["expect_frames"]
    with pytest.raises(AssertionError):
        _validate(mutant)


def test_schema_and_recursive_shapes_are_closed():
    for bad in (True, 1.0, "1", 0, 2):
        mutant = copy.deepcopy(CASES)
        mutant["schema_version"] = bad
        with pytest.raises(AssertionError):
            _validate(mutant)
    for path in [("happy", 0), ("malformed", 0)]:
        mutant = copy.deepcopy(CASES)
        mutant[path[0]][path[1]]["rogue"] = None
        with pytest.raises(AssertionError):
            _validate(mutant)
