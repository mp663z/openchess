"""T0195: migration conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0194 migration contract. The cases execute against the
contract-derived reference in tests.test_t0194_migration_
contract (itself fully derived from data/contracts/
migration.yaml plus the linked transposition-node, variant,
position-digest, en-passant and FEN contracts) - nothing is
re-implemented here. Pinned states, ids and receipts in the
fixture were computed from that reference at authoring time, so
any contract or derivation drift breaks this battery. Every
malformed case is discriminating (repairing ONLY its declared
defect locus makes the case valid) and rollback cases prove a
rejected migration leaves request and source byte-identical.

DESIGN CAUTION: the reference engine is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work
must execute these same cases against a separately implemented
runtime."""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0194_migration_contract import (  # noqa: E402
    MigrationEngine,
    MigrationError,
    pdv2_oracle,
)
from tools.migration_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "migration"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
_SID_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
_MID_RE = re.compile(_CC["identifiers"]["migration_id"]
                    ["grammar"])
_SCHEMA_RE = re.compile(_CC["identifiers"]["schema_id"]
                        ["grammar"])
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}
REQUEST_FIELDS = {"from_schema", "to_schema", "source_id"}
RECEIPT_FIELDS = {"migration_id", "from_schema", "to_schema",
                  "source_id", "target_id", "state"}


def _raising_oracle(variant, fen):
    raise ValueError("untrusted oracle failure")


def _bad_grammar_oracle(variant, fen):
    return "bad"


def _non_str_oracle(variant, fen):
    return None


ORACLES = {"honest": pdv2_oracle, "raising": _raising_oracle,
           "bad-grammar": _bad_grammar_oracle,
           "non-str": _non_str_oracle}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
MIGRATE_KEYS = {"name", "kind", "oracle", "request", "source",
                "expect"}
MALFORMED_KEYS = {"name", "kind", "oracle", "request", "source",
                  "expect_failure", "defect", "minimal_repair"}
ROLLBACK_KEYS = {"name", "kind", "oracle", "request", "source",
                 "expect_failure", "then_oracle", "then_request",
                 "then_source", "expect"}
REPAIR_FORMS = {"set_request_field", "replace_request",
                "replace_source", "set_oracle"}

# -- the closed scenario manifests -------------------------------------------
# Every fixture section's actual {name: metadata} map must equal
# its manifest EXACTLY - no missing, substituted, duplicated,
# renamed or extra rows, and every advertised semantic shape is
# asserted before execution.
MMR = "malformed_migration_record"
UM = "unknown_migration"
CS = "conflicting_source"
DVT = "divergent_target"

# happy/boundary: name -> (oracle, source-record cardinality)
HAPPY_MANIFEST = {
    "migrate-three-record-source": ("honest", 3),
    "migrate-single-record-source": ("honest", 1),
    "migrate-two-record-source": ("honest", 2),
}
BOUNDARY_MANIFEST = {
    "migrate-empty-source": ("honest", 0),
    "migrate-two-record-source-boundary": ("honest", 2),
}
# malformed: name -> (failure class, oracle, repair form,
# pinned defect description)
MALFORMED_MANIFEST = {
    "request-missing-field": (MMR, "honest", "replace_request",
        "request is missing the to_schema field"),
    "request-extra-field": (MMR, "honest", "replace_request",
        "request carries an undeclared extra field"),
    "request-bad-schema-grammar": (MMR, "honest",
        "set_request_field",
        "from_schema fails the schema-id grammar"),
    "request-unknown-schema": (MMR, "honest",
        "set_request_field",
        "from_schema is not a registered schema"),
    "request-bad-source-id-grammar": (MMR, "honest",
        "set_request_field",
        "source_id fails the state-id grammar"),
    "request-non-str-value": (MMR, "honest", "replace_request",
        "to_schema is not a string"),
    "request-noop-unregistered": (UM, "honest",
        "set_request_field",
        "a noop migration pair is not registered"),
    "request-downgrade-unregistered": (UM, "honest",
        "replace_request",
        "a downgrade migration pair is not registered"),
    "source-id-mismatch": (CS, "honest", "set_request_field",
        "recomputed source id differs from the request's "
        "source_id"),
    "source-not-mapping": (MMR, "honest", "replace_source",
        "the source state is not a mapping"),
    "source-record-bad-digest": (MMR, "honest",
        "replace_source",
        "a source record fails the source-schema digest "
        "grammar"),
    "oracle-raises": (DVT, "raising", "set_oracle",
        "the target oracle raises during evaluation"),
    "oracle-bad-grammar-output": (DVT, "bad-grammar",
        "set_oracle",
        "the target oracle returns text failing the target "
        "grammar"),
    "oracle-non-str-output": (DVT, "non-str", "set_oracle",
        "the target oracle returns a non-string"),
}
# rollback: name -> (failure class, initial oracle, follow-up
# oracle)
ROLLBACK_MANIFEST = {
    "rejected-conflicting-source-then-valid-migrate": (
        CS, "honest", "honest"),
    "rejected-oracle-raising-then-valid-migrate": (
        DVT, "raising", "honest"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    for case in CASES[section]:
        if case["name"] == name:
            return case
    raise AssertionError(f"case {name} not found in {section}")


def _validate_state_shape(state, label):
    """Structure-level state shape: a mapping of exact-str keys
    to record mappings with exactly the node record fields and
    string values. Semantic validity is proven by EXECUTION."""
    assert isinstance(state, dict), label
    for key, rec in state.items():
        assert isinstance(key, str), label
        assert isinstance(rec, dict), label
        assert set(rec) == RECORD_FIELDS, (label, key)
        for field in RECORD_FIELDS:
            assert isinstance(rec[field], str), (label, key,
                                                 field)


def _validate_request_shape(request, label):
    assert isinstance(request, dict), label
    assert set(request) == REQUEST_FIELDS, label
    for field in REQUEST_FIELDS:
        assert isinstance(request[field], str), (label, field)
    assert _SID_RE.fullmatch(request["source_id"]), label


def _validate_receipt_shape(receipt, label):
    assert set(receipt) == RECEIPT_FIELDS, label
    assert _MID_RE.fullmatch(receipt["migration_id"]), label
    assert _SCHEMA_RE.fullmatch(receipt["from_schema"]), label
    assert _SCHEMA_RE.fullmatch(receipt["to_schema"]), label
    assert _SID_RE.fullmatch(receipt["source_id"]), label
    assert _SID_RE.fullmatch(receipt["target_id"]), label
    _validate_state_shape(receipt["state"], label)


def _repaired(case):
    """Apply the declarative minimal repair: it touches ONLY the
    declared locus, everything else byte-identical."""
    rep = case["minimal_repair"]
    assert set(rep) <= REPAIR_FORMS
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure",
                        "minimal_repair")}
    if "set_request_field" in rep:
        form = rep["set_request_field"]
        out["request"][form["field"]] = form["value"]
    elif "replace_request" in rep:
        out["request"] = copy.deepcopy(
            rep["replace_request"]["request"])
    elif "replace_source" in rep:
        out["source"] = copy.deepcopy(
            rep["replace_source"]["source"])
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of four
    closed forms, never mixed:
    - set_request_field: exactly {"field", "value"}; field is a
      request field; value a string;
    - replace_request: exactly {"request"}; a shape-valid
      request;
    - replace_source: exactly {"source"}; a shape-valid state;
    - set_oracle: exactly {"oracle"}; a declared oracle
      selector."""
    rep = case["minimal_repair"]
    assert len(rep) == 1 and set(rep) <= REPAIR_FORMS, \
        case["name"]
    form = next(iter(rep.values()))
    if "set_request_field" in rep:
        assert set(form) == {"field", "value"}, case["name"]
        assert form["field"] in REQUEST_FIELDS, case["name"]
        assert isinstance(form["value"], str), case["name"]
    elif "replace_request" in rep:
        assert set(form) == {"request"}, case["name"]
        _validate_request_shape(form["request"], case["name"])
    elif "replace_source" in rep:
        assert set(form) == {"source"}, case["name"]
        _validate_state_shape(form["source"], case["name"])
    else:
        assert set(form) == {"oracle"}, case["name"]
        assert form["oracle"] in ORACLES, case["name"]


def _assert_cardinalities(case, n_source):
    """The advertised source cardinality is REALLY present, and
    the expected receipt realizes exactly that cardinality -
    a substituted row can never satisfy the wrong count."""
    name = case["name"]
    assert len(case["source"]) == n_source, name
    state = case["expect"]["state"]
    assert len(state) == n_source, name


def _validate_structure(cases):
    assert set(cases) == TOP_KEYS
    # the fixture-format version is pinned exactly: an int equal
    # to FIXTURE_SCHEMA_VERSION, never a string, bool, or other
    # integer silently interpreted under wrong assumptions
    assert type(cases["schema"]) is int, "schema must be an int"
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == \
        _CC["versioning"]["base_path"]
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
                oracle, n_source = want
                assert case["oracle"] == oracle, case["name"]
                _assert_cardinalities(case, n_source)
            elif section == "malformed":
                failure, oracle, repair, defect = want
                assert case["expect_failure"] == failure, (
                    case["name"])
                assert case["oracle"] == oracle, case["name"]
                assert set(case["minimal_repair"]) == {repair}, (
                    case["name"])
                assert case["defect"] == defect, case["name"]
            else:
                failure, oracle, then_oracle = want
                assert case["expect_failure"] == failure, (
                    case["name"])
                assert case["oracle"] == oracle, case["name"]
                assert case["then_oracle"] == then_oracle, (
                    case["name"])
        for case in cases[section]:
            if section == "rollback":
                assert case["kind"] == "rollback-migrate", \
                    case["name"]
                assert set(case) == ROLLBACK_KEYS, case["name"]
                assert case["oracle"] in ORACLES and \
                    case["then_oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                _validate_request_shape(case["request"],
                                        case["name"])
                _validate_request_shape(case["then_request"],
                                        case["name"])
                _validate_state_shape(case["source"],
                                      case["name"])
                _validate_state_shape(case["then_source"],
                                      case["name"])
                _validate_receipt_shape(case["expect"],
                                        case["name"])
                continue
            assert case["kind"] == "migrate", case["name"]
            assert case["oracle"] in ORACLES, case["name"]
            if section == "malformed":
                assert set(case) == MALFORMED_KEYS, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert isinstance(case["defect"], str) and \
                    case["defect"], case["name"]
                _validate_repair(case)
            else:
                assert set(case) == MIGRATE_KEYS, case["name"]
                _validate_request_shape(case["request"],
                                        case["name"])
                _validate_state_shape(case["source"],
                                      case["name"])
                _validate_receipt_shape(case["expect"],
                                        case["name"])
    # every declared failure class is exercised by the malformed
    # battery, and every malformed failure is contract-declared
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared == FAILURE_CLASSES


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


def test_repairs_minimal_locus():
    """Every repair touches ONLY its declared locus:
    set_request_field changes exactly one request field;
    replace_request / replace_source / set_oracle change exactly
    that one component with every other component
    byte-identical."""
    for case in CASES["malformed"]:
        rep = _repaired(case)
        form_name = next(iter(case["minimal_repair"]))
        if form_name == "set_request_field":
            field = case["minimal_repair"]["set_request_field"][
                "field"]
            for key in REQUEST_FIELDS - {field}:
                assert rep["request"][key] == \
                    case["request"][key], case["name"]
            assert rep["source"] == case["source"], case["name"]
            assert rep["oracle"] == case["oracle"], case["name"]
        elif form_name == "replace_request":
            assert rep["source"] == case["source"], case["name"]
            assert rep["oracle"] == case["oracle"], case["name"]
        elif form_name == "replace_source":
            assert rep["request"] == case["request"], \
                case["name"]
            assert rep["oracle"] == case["oracle"], case["name"]
        else:
            assert rep["request"] == case["request"], \
                case["name"]
            assert rep["source"] == case["source"], case["name"]


def _run_migrate(oracle_name, request, source, expect):
    engine = MigrationEngine(ORACLES[oracle_name])
    req_p = copy.deepcopy(request)
    src_p = copy.deepcopy(source)
    result = engine.migrate(request, source)
    assert result == expect
    # determinism: fresh copies, same receipt; inputs unmutated
    again = MigrationEngine(ORACLES[oracle_name]).migrate(
        copy.deepcopy(req_p), copy.deepcopy(src_p))
    assert again == result
    assert request == req_p
    assert source == src_p


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        _run_migrate(case["oracle"], case["request"],
                     case["source"], case["expect"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        _run_migrate(case["oracle"], case["request"],
                     case["source"], case["expect"])


def _exec_malformed(case):
    """The ORIGINAL input rejects with the pinned failure class
    and leaves request and source byte-identical."""
    req_p = copy.deepcopy(case["request"])
    src_p = copy.deepcopy(case["source"])
    engine = MigrationEngine(ORACLES[case["oracle"]])
    try:
        engine.migrate(case["request"], case["source"])
    except MigrationError as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.code in ERROR_ENUM
        assert case["request"] == req_p
        assert case["source"] == src_p
        return
    raise AssertionError("input unexpectedly accepted")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    result = MigrationEngine(ORACLES[out["oracle"]]).migrate(
        out["request"], out["source"])
    _validate_receipt_shape(result, case["name"])


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    """Collection guard: test_malformed's parameter IDs are
    exactly the fixture's malformed-name set - read from the
    actual parametrize mark, never recomputed from the same
    expression."""
    marks = [m for m in test_malformed.pytestmark
             if m.name == "parametrize"]
    assert len(marks) == 1
    assert set(marks[0].args[1]) == set(_names("malformed"))


def test_collection_guard_detects_late_fixture_row():
    """A fixture row appended AFTER decorator collection is
    caught: the parametrize mark froze at import, so the guard
    must fail while the row is present."""
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
    original = CASES["malformed"][0]
    valid = _repaired(original)
    valid["name"] = original["name"]
    MigrationEngine(ORACLES[valid["oracle"]]).migrate(
        copy.deepcopy(valid["request"]),
        copy.deepcopy(valid["source"]))
    with pytest.raises(AssertionError):
        _exec_malformed(dict(original, **valid))


def _repair_mutation_cases():
    """Every repair-form mutation the tagged union must reject
    STRUCTURALLY, before any execution."""
    good_req = CASES["happy"][0]["request"]
    return [
        ("empty-repair", {}),
        ("mixed-forms",
         {"set_oracle": {"oracle": "honest"},
          "set_request_field": {"field": "to_schema",
                                "value": "store-v2"}}),
        ("unknown-form", {"wat": {"x": 1}}),
        ("set_field-extra-key",
         {"set_request_field": {"field": "to_schema",
                                "value": "store-v2", "wat": 1}}),
        ("set_field-bad-field",
         {"set_request_field": {"field": "label",
                                "value": "store-v2"}}),
        ("set_field-value-not-str",
         {"set_request_field": {"field": "to_schema",
                                "value": 5}}),
        ("replace_request-extra-key",
         {"replace_request": {"request": good_req, "wat": 1}}),
        ("replace_request-bad-shape",
         {"replace_request": {"request": {"wat": 1}}}),
        ("replace_source-not-mapping",
         {"replace_source": {"source": []}}),
        ("replace_source-bad-record",
         {"replace_source": {"source": {"k": {"wat": 1}}}}),
        ("set_oracle-extra-key",
         {"set_oracle": {"oracle": "honest", "wat": 1}}),
        ("set_oracle-undeclared",
         {"set_oracle": {"oracle": "sneaky"}}),
        # only a same-form, valid-payload control stays valid:
        # the scenario manifest pins each malformed row's repair
        # form, so ANY form swap - even to another valid union
        # member - must now fail structure validation.
        ("good-forms-still-valid-1",
         {"replace_request": {"request": good_req}}),
    ]


def test_repair_form_mutations_fail_structure():
    """Every repair-form mutation fails _validate_structure
    before execution (the same-form good control must PASS -
    it proves the mutation application itself is not what
    breaks validation; cross-form swaps are pinned out by the
    scenario manifest)."""
    for label, rep in _repair_mutation_cases():
        m = copy.deepcopy(CASES)
        m["malformed"][0]["minimal_repair"] = copy.deepcopy(rep)
        try:
            _validate_structure(m)
            if label.startswith("good-forms"):
                continue
        except AssertionError:
            if label.startswith("good-forms"):
                raise AssertionError(
                    f"good form {label!r} failed") from None
            continue
        raise AssertionError(
            f"repair mutation {label!r} passed")


def test_rollback():
    """A rejected migration leaves request and source
    byte-identical, and a subsequent valid migration returns the
    pinned receipt."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        req_p = copy.deepcopy(case["request"])
        src_p = copy.deepcopy(case["source"])
        with pytest.raises(MigrationError) as exc:
            MigrationEngine(ORACLES[case["oracle"]]).migrate(
                copy.deepcopy(case["request"]),
                copy.deepcopy(case["source"]))
        assert exc.value.failure_class == \
            case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        assert case["request"] == req_p
        assert case["source"] == src_p
        _run_migrate(case["then_oracle"], case["then_request"],
                     case["then_source"], case["expect"])


# -- v2: closed scenario coverage ---------------------------------------------


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
    a, b = m["malformed"][0], m["malformed"][6]
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _swap_oracle(m, section):
    a, b = m["malformed"][0], m["malformed"][11]
    a["oracle"], b["oracle"] = b["oracle"], a["oracle"]


def _swap_repair(m, section):
    a, b = m["malformed"][0], m["malformed"][2]
    a["minimal_repair"], b["minimal_repair"] = (
        b["minimal_repair"], a["minimal_repair"])


def _swap_rollback_oracle(m, section):
    a, b = m["rollback"][0], m["rollback"][1]
    a["oracle"], b["oracle"] = b["oracle"], a["oracle"]


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
    out.append(("malformed-swap-oracle", _swap_oracle))
    out.append(("malformed-swap-repair", _swap_repair))
    out.append(("rollback-swap-oracle", _swap_rollback_oracle))
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
    """The verifier's replay: every happy row replaced by a
    uniquely-named VALID single-record copy, every boundary row
    by a VALID empty-source copy, every rollback row by a VALID
    conflicting-source copy - must fail structure validation
    BEFORE execution."""
    m = copy.deepcopy(CASES)
    single = copy.deepcopy(
        _case("happy", "migrate-single-record-source"))
    empty = copy.deepcopy(
        _case("boundary", "migrate-empty-source"))
    conflict = copy.deepcopy(
        _case("rollback",
              "rejected-conflicting-source-then-valid-migrate"))
    m["happy"] = [dict(copy.deepcopy(single),
                       name=f"single-copy-{i}")
                  for i in range(len(HAPPY_MANIFEST))]
    m["boundary"] = [dict(copy.deepcopy(empty),
                          name=f"empty-copy-{i}")
                     for i in range(len(BOUNDARY_MANIFEST))]
    m["rollback"] = [dict(copy.deepcopy(conflict),
                          name=f"conflict-copy-{i}")
                     for i in range(len(ROLLBACK_MANIFEST))]
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_mutant_intra_failure_class_malformed_substitution():
    """Malformed rows substituted WITHIN one failure class (all
    classes and repair forms still represented) must fail the
    manifest - the pinned defect and metadata discriminate."""
    m = copy.deepcopy(CASES)
    donor = copy.deepcopy(
        _case("malformed", "request-missing-field"))
    for i, case in enumerate(m["malformed"]):
        if (case["expect_failure"] == donor["expect_failure"]
                and case["oracle"] == donor["oracle"]
                and set(case["minimal_repair"])
                == set(donor["minimal_repair"])
                and case["name"] != donor["name"]):
            m["malformed"][i] = dict(copy.deepcopy(donor),
                                     name=case["name"])
    with pytest.raises(AssertionError):
        _validate_structure(m)
