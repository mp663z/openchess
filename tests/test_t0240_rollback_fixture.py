"""T0240: rollback conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0239 rollback contract. The cases execute against the
contract-derived reference in
tests.test_t0239_rollback_contract (itself fully derived from
data/contracts/rollback.yaml plus the linked WAL, migration,
transposition-node, variant, position-digest and FEN
contracts) - nothing is re-implemented here. Pinned rollback
receipts in the fixture were computed from that reference at
authoring time, so any contract or derivation drift breaks
this battery. Every malformed case is discriminating
(repairing ONLY its declared defect locus makes the case
valid) and rollback cases prove a rejected rollback leaves the
exact supplied log and request bit-identical before the valid
follow-up commits.

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

from tests.test_t0212_wal_contract import (  # noqa: E402
    WalEngine,
    WalError,
    canonical_payload,
)
from tests.test_t0239_rollback_contract import (  # noqa: E402
    RollbackEngine,
    RollbackError,
    _fail,
    archive_tail,
)
from tools.rollback_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    RECORD,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "rollback"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
_ROLLBACK_RE = re.compile(
    _CC["identifiers"]["rollback_id"]["grammar"])
_TOKEN_RE = re.compile(
    _CC["identifiers"]["archive_token"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["head"]["grammar"])
ROLLBACK_FIELDS = set(RECORD["fields"])


def _raising_archiver(tail):
    raise ValueError("untrusted archiver failure")


def _non_str_archiver(tail):
    return []


def _bad_grammar_archiver(tail):
    return "arc1:zz"


def _unbound_token_archiver(tail):
    """Grammar-valid but NOT bound to the local tail
    derivation."""
    return "arc1:" + "f" * 64


def _surrogate_archiver(tail):
    return "\ud800"


ORACLES = {"honest": archive_tail,
           "raising": _raising_archiver,
           "non_str_output": _non_str_archiver,
           "bad_grammar": _bad_grammar_archiver,
           "unbound_token": _unbound_token_archiver,
           "lone_surrogate": _surrogate_archiver}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
HAPPY_KEYS = {"name", "kind", "oracle", "log", "request",
              "expect"}
MALFORMED_KEYS = {"name", "kind", "oracle", "log", "request",
                  "expect_failure", "defect", "scenario",
                  "minimal_repair"}
ROLLBACK_KEYS = {"name", "kind", "oracle", "log", "request",
                 "expect_failure", "then_oracle", "then_log",
                 "then_request", "expect"}
REPAIR_FORMS = {"set_request_field", "replace_request",
                "replace_log", "set_oracle"}

# -- the closed scenario manifests -------------------------------------------
# Every fixture section's actual {name: metadata} map must equal
# its manifest EXACTLY - no missing, substituted, duplicated,
# renamed or extra rows, and every advertised semantic shape is
# asserted before execution.
MRR = "malformed_rollback_record"
UT = "unknown_target"
CS = "corrupt_source"
DA = "divergent_archive"

# happy/boundary: name -> (oracle, truncated_count)
HAPPY_MANIFEST = {
    "rollback-single-entry-tail": ("honest", 1),
    "rollback-partial-tail": ("honest", 2),
    "rollback-put-delete-put": ("honest", 2),
}
BOUNDARY_MANIFEST = {
    "rollback-entire-log": ("honest", 3),
    "rollback-empty-log": ("honest", 0),
    "rollback-zero-tail-noop": ("honest", 0),
}
# malformed: name -> (failure class, oracle, repair form,
# pinned defect description, CLOSED SCENARIO TAG)
MALFORMED_MANIFEST = {
    "log-not-a-list": (MRR, "honest", "replace_log",
        "the source log is a dict, not a list",
        "non-list-source-log"),
    "request-missing-field": (MRR, "honest", "replace_request",
        "the request is missing the target_sequence field",
        "missing-request-field"),
    "request-extra-field": (MRR, "honest", "replace_request",
        "the request carries a stray field",
        "extra-request-field"),
    "target-non-int": (MRR, "honest", "set_request_field",
        "target_sequence is a string, not an int",
        "non-int-target"),
    "target-bool": (MRR, "honest", "set_request_field",
        "target_sequence is a bool, not an exact int",
        "bool-target"),
    "target-negative": (UT, "honest", "set_request_field",
        "target_sequence is below zero", "target-negative"),
    "target-beyond-length": (UT, "honest", "set_request_field",
        "target_sequence is past the end of the log",
        "target-beyond-length"),
    "sequence-gap": (CS, "honest", "replace_log",
        "an entry sequence skips a position", "sequence-gap"),
    "tampered-entry-id": (CS, "honest", "replace_log",
        "an entry id does not re-derive from its content",
        "tampered-entry-id"),
    "unregistered-op": (CS, "honest", "replace_log",
        "an entry carries an op outside the wal enum",
        "unregistered-op"),
    "oracle-raises": (DA, "raising", "set_oracle",
        "the archiver raises instead of returning a token",
        "oracle-raises"),
    "oracle-non-str-output": (DA, "non_str_output",
        "set_oracle",
        "the archiver returns a list, not a string",
        "oracle-non-str-output"),
    "oracle-bad-grammar": (DA, "bad_grammar", "set_oracle",
        "the archiver token violates the pinned grammar",
        "oracle-bad-grammar"),
    "oracle-unbound-token": (DA, "unbound_token", "set_oracle",
        "the archiver token is grammar-valid but not bound to "
        "the local tail derivation", "oracle-unbound-token"),
    "oracle-lone-surrogate": (DA, "lone_surrogate",
        "set_oracle",
        "the archiver token is not UTF-8 encodable",
        "oracle-lone-surrogate"),
}
# rollback: name -> (failure class, initial oracle, follow-up
# oracle)
ROLLBACK_MANIFEST = {
    "rejected-rollback-raising-archiver-then-valid-rollback": (
        DA, "raising", "honest"),
    "rejected-rollback-unknown-target-then-valid-rollback": (
        UT, "honest", "honest"),
    "rejected-rollback-corrupt-source-then-valid-rollback": (
        CS, "honest", "honest"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    return next(case for case in CASES[section]
                if case["name"] == name)


def _validate_log_shape(log, label):
    assert type(log) is list, label
    for entry in log:
        assert type(entry) is dict, label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert set(request) == {"target_sequence"}, label
    assert type(request["target_sequence"]) is int, label


def _validate_receipt_shape(receipt, label):
    assert type(receipt) is dict, label
    assert set(receipt) == ROLLBACK_FIELDS, label
    assert _ROLLBACK_RE.fullmatch(receipt["rollback_id"]), label
    assert _TOKEN_RE.fullmatch(receipt["archive_token"]), label
    assert _HEAD_RE.fullmatch(receipt["from_head"]), label
    assert _HEAD_RE.fullmatch(receipt["to_head"]), label
    assert type(receipt["truncated_count"]) is int, label
    assert receipt["truncated_count"] >= 0, label


def _repaired(case):
    """Apply the declarative minimal repair: it touches ONLY the
    declared locus, everything else byte-identical."""
    rep = case["minimal_repair"]
    assert set(rep) <= REPAIR_FORMS
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure", "scenario",
                        "minimal_repair")}
    if "set_request_field" in rep:
        form = rep["set_request_field"]
        out["request"][form["field"]] = form["value"]
    elif "replace_request" in rep:
        out["request"] = copy.deepcopy(
            rep["replace_request"]["request"])
    elif "replace_log" in rep:
        out["log"] = copy.deepcopy(rep["replace_log"]["log"])
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of four
    closed forms, never mixed:
    - set_request_field: exactly {"field", "value"}; field is
      target_sequence; value an exact int;
    - replace_request: exactly {"request"}; a shape-valid
      rollback request;
    - replace_log: exactly {"log"}; a shape-valid wal log;
    - set_oracle: exactly {"oracle"}; a declared oracle
      selector."""
    rep = case["minimal_repair"]
    assert len(rep) == 1 and set(rep) <= REPAIR_FORMS, \
        case["name"]
    form = next(iter(rep.values()))
    if "set_request_field" in rep:
        assert set(form) == {"field", "value"}, case["name"]
        assert form["field"] == "target_sequence", case["name"]
        assert type(form["value"]) is int, case["name"]
    elif "replace_request" in rep:
        assert set(form) == {"request"}, case["name"]
        _validate_request_shape(form["request"], case["name"])
    elif "replace_log" in rep:
        assert set(form) == {"log"}, case["name"]
        _validate_log_shape(form["log"], case["name"])
    else:
        assert set(form) == {"oracle"}, case["name"]
        assert form["oracle"] in ORACLES, case["name"]


# -- closed scenario tags with semantic defect-locus assertions --------------
# The defect prose stays as documentation, but it is NOT the
# proof: each malformed row carries a closed machine scenario
# tag, and _assert_malformed_scenario verifies over the ORIGINAL
# executable data that (a) the data realizes exactly the defect
# the tag names, and (b) the minimal repair changes exactly that
# locus and no other semantic locus. Every check fails CLOSED:
# an unexpected shape raises AssertionError, never escapes raw.

_ORACLE_TAG_SELECTOR = {
    "oracle-raises": "raising",
    "oracle-non-str-output": "non_str_output",
    "oracle-bad-grammar": "bad_grammar",
    "oracle-unbound-token": "unbound_token",
    "oracle-lone-surrogate": "lone_surrogate"}
# oracle-tag rows are pairwise discriminated by (log length,
# target sequence) - a substituted oracle row can never satisfy
# the wrong cardinality
_ORACLE_CARDINALITY = {
    "oracle-raises": (3, 2),
    "oracle-non-str-output": (3, 1),
    "oracle-bad-grammar": (2, 1),
    "oracle-unbound-token": (4, 2),
    "oracle-lone-surrogate": (1, 0)}


def _rollback_succeeds(oracle, log, request):
    """The reference engine accepts the rollback end to end
    under the given archiver."""
    try:
        RollbackEngine(ORACLES[oracle]).rollback(
            copy.deepcopy(log), copy.deepcopy(request))
    except RollbackError:
        return False
    return True


def _wal_verifies(log):
    """The LINKED wal machinery accepts the log."""
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return False
    return True


def _diff_fields(original, repaired, label):
    """The two same-shaped mappings differ at exactly the
    returned field set."""
    assert set(original) == set(repaired), label
    return {key for key in original
            if original[key] != repaired[key]}


def _check_malformed_scenario(case, tag):
    name = case["name"]
    rep = _repaired(case)
    if tag in _ORACLE_TAG_SELECTOR:
        log = case["log"]
        request = case["request"]
        assert case["oracle"] == _ORACLE_TAG_SELECTOR[tag], name
        assert (len(log), request["target_sequence"]) == \
            _ORACLE_CARDINALITY[tag], name
        _validate_log_shape(log, name)
        _validate_request_shape(request, name)
        # the log validates through the linked wal and the
        # request is well-formed: the ONLY defect is the
        # archiver itself - the honest archiver succeeds
        assert _wal_verifies(log), name
        assert _rollback_succeeds("honest", log, request), name
        form = case["minimal_repair"]["set_oracle"]
        assert form["oracle"] == "honest", name
        assert rep["log"] == log, name
        assert rep["request"] == request, name
        # the semantic locus of each archiver defect is
        # realized
        if tag == "oracle-raises":
            try:
                ORACLES[case["oracle"]]([])
                raise AssertionError(name)
            except ValueError:
                return
        out = ORACLES[case["oracle"]](
            log[request["target_sequence"]:])
        if tag == "oracle-non-str-output":
            assert type(out) is not str, name
        elif tag == "oracle-bad-grammar":
            assert type(out) is str, name
            assert _TOKEN_RE.fullmatch(out) is None, name
        elif tag == "oracle-unbound-token":
            assert _TOKEN_RE.fullmatch(out) is not None, name
            assert out != archive_tail(
                log[request["target_sequence"]:]), name
        elif tag == "oracle-lone-surrogate":
            assert type(out) is str, name
            try:
                out.encode("utf-8")
                raise AssertionError(name)
            except UnicodeEncodeError:
                pass
        return
    log = case["log"]
    request = case["request"]
    rep_log = rep["log"]
    rep_req = rep["request"]
    if tag == "non-list-source-log":
        assert type(log) is not list, name
        assert "replace_log" in case["minimal_repair"], name
        _validate_log_shape(rep_log, name)
        assert rep_req == request, name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "missing-request-field":
        assert "target_sequence" not in request, name
        assert rep_log == log, name
        _validate_request_shape(rep_req, name)
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "extra-request-field":
        assert "target_sequence" in request, name
        assert set(request) != {"target_sequence"}, name
        assert rep_req == {
            "target_sequence": request["target_sequence"]}, name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "non-int-target":
        assert type(request["target_sequence"]) is str, name
        assert _diff_fields(request, rep_req, name) == \
            {"target_sequence"}, name
        assert type(rep_req["target_sequence"]) is int, name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "bool-target":
        assert type(request["target_sequence"]) is bool, name
        assert _diff_fields(request, rep_req, name) == \
            {"target_sequence"}, name
        assert type(rep_req["target_sequence"]) is int, name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "target-negative":
        target = request["target_sequence"]
        assert type(target) is int and target < 0, name
        assert _diff_fields(request, rep_req, name) == \
            {"target_sequence"}, name
        assert 0 <= rep_req["target_sequence"] <= len(log), name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "target-beyond-length":
        target = request["target_sequence"]
        assert type(target) is int and target > len(log), name
        assert _diff_fields(request, rep_req, name) == \
            {"target_sequence"}, name
        assert 0 <= rep_req["target_sequence"] <= len(log), name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "sequence-gap":
        _validate_log_shape(log, name)
        assert [entry["sequence"] for entry in log] != \
            list(range(1, len(log) + 1)), name
        assert not _wal_verifies(log), name
        assert "replace_log" in case["minimal_repair"], name
        assert rep_req == request, name
        assert _wal_verifies(rep_log), name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "tampered-entry-id":
        _validate_log_shape(log, name)
        assert [entry["sequence"] for entry in log] == \
            list(range(1, len(log) + 1)), name
        assert all(entry["op"] in {"put", "delete"}
                   for entry in log), name
        assert not _wal_verifies(log), name
        assert "replace_log" in case["minimal_repair"], name
        assert rep_req == request, name
        assert _wal_verifies(rep_log), name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    elif tag == "unregistered-op":
        _validate_log_shape(log, name)
        assert any(entry["op"] not in {"put", "delete"}
                   for entry in log), name
        assert not _wal_verifies(log), name
        assert "replace_log" in case["minimal_repair"], name
        assert rep_req == request, name
        assert _wal_verifies(rep_log), name
        assert _rollback_succeeds(rep["oracle"], rep_log,
                                  rep_req), name
    else:
        raise AssertionError(
            f"{name}: unknown scenario tag {tag!r}")


def _assert_malformed_scenario(case, tag):
    """Fail-closed wrapper: scenario checks raise AssertionError
    on EVERY mismatch or unexpected shape - a raw exception is
    converted, never allowed to escape as itself."""
    try:
        _check_malformed_scenario(case, tag)
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(
            f"{case.get('name', '?')}: scenario {tag!r} check "
            f"raised unexpected {type(exc).__name__}") from None


def _assert_cardinalities(case, count):
    """The advertised truncated count is REALLY present, and the
    pinned receipt re-derives from the reference engine - a
    substituted row can never satisfy the wrong count."""
    name = case["name"]
    assert case["expect"]["truncated_count"] == count, name
    derived = RollbackEngine(archive_tail).rollback(
        copy.deepcopy(case["log"]),
        copy.deepcopy(case["request"]))
    assert derived == case["expect"], name


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
    for section in ("happy", "boundary", "malformed",
                    "rollback"):
        manifest = MANIFESTS[section]
        names = [case["name"] for case in cases[section]]
        assert len(names) == len(set(names)), (
            f"{section} has duplicate names")
        assert set(names) == set(manifest), (
            f"{section} scenario set drifted: "
            f"missing={set(manifest) - set(names)} "
            f"extra={set(names) - set(manifest)}")
        for case in cases[section]:
            want = manifest[case["name"]]
            if section in ("happy", "boundary"):
                oracle, count = want
                assert case["oracle"] == oracle, case["name"]
                _assert_cardinalities(case, count)
            elif section == "malformed":
                failure, oracle, repair, defect, tag = want
                assert case["expect_failure"] == failure, (
                    case["name"])
                assert case["oracle"] == oracle, case["name"]
                assert set(case["minimal_repair"]) == {repair}, (
                    case["name"])
                assert case["defect"] == defect, case["name"]
                assert case["scenario"] == tag, case["name"]
                # SEMANTIC PIN: the original executable data must
                # REALIZE the closed scenario tag - the defect
                # prose alone is never the proof.
                _assert_malformed_scenario(case, tag)
            else:
                failure, oracle, then_oracle = want
                assert case["expect_failure"] == failure, (
                    case["name"])
                assert case["oracle"] == oracle, case["name"]
                assert case["then_oracle"] == then_oracle, (
                    case["name"])
        for case in cases[section]:
            if section == "rollback":
                assert case["kind"] == "rollback", case["name"]
                assert case["oracle"] in ORACLES and \
                    case["then_oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert set(case) == ROLLBACK_KEYS, case["name"]
                _validate_log_shape(case["then_log"],
                                    case["name"])
                _validate_request_shape(case["then_request"],
                                        case["name"])
                _validate_receipt_shape(case["expect"],
                                        case["name"])
                derived = RollbackEngine(
                    ORACLES[case["then_oracle"]]).rollback(
                        copy.deepcopy(case["then_log"]),
                        copy.deepcopy(case["then_request"]))
                assert derived == case["expect"], case["name"]
                continue
            if section == "malformed":
                assert case["kind"] == "rollback", case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert isinstance(case["defect"], str) and \
                    case["defect"], case["name"]
                _validate_repair(case)
                # the case-owned log/request payloads are
                # intentionally shape-defective on some rows
                # (that is the defect); validate key-set only,
                # never shape.
                assert set(case) == MALFORMED_KEYS, case["name"]
            else:
                assert case["kind"] == "rollback", case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert set(case) == HAPPY_KEYS, case["name"]
                _validate_log_shape(case["log"], case["name"])
                _validate_request_shape(case["request"],
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
    """The pinned fixture-format version rejects every drifted
    representation before any semantic validation."""
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
    """Every declarative minimal repair makes its malformed case
    fully valid end to end, changing ONLY the declared locus."""
    for name in _names("malformed"):
        case = _case("malformed", name)
        rep = _repaired(case)
        if "set_oracle" not in case["minimal_repair"]:
            assert rep["oracle"] == case["oracle"], name
        if "replace_log" not in case["minimal_repair"]:
            assert rep["log"] == case["log"], name
        if "replace_request" not in case["minimal_repair"] and \
                "set_request_field" not in \
                case["minimal_repair"]:
            assert rep["request"] == case["request"], name
        receipt = RollbackEngine(ORACLES[rep["oracle"]]).rollback(
            copy.deepcopy(rep["log"]),
            copy.deepcopy(rep["request"]))
        _validate_receipt_shape(receipt, name)


def _run_rollback(oracle_name, log, request, expect):
    work_log = copy.deepcopy(log)
    work_req = copy.deepcopy(request)
    receipt = RollbackEngine(ORACLES[oracle_name]).rollback(
        work_log, work_req)
    assert receipt == expect
    _validate_receipt_shape(receipt, "execution")
    # the commit removed EXACTLY the tail, nothing else
    target = request["target_sequence"]
    assert len(work_log) == target
    assert work_log == log[:target]
    # the request is untouched
    assert work_req == request
    # the surviving prefix still validates through the wal
    WalEngine(canonical_payload).replay(work_log)
    # deterministic: a fresh run over fresh copies re-derives
    # the same receipt
    again = RollbackEngine(ORACLES[oracle_name]).rollback(
        copy.deepcopy(log), copy.deepcopy(request))
    assert again == expect


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        _run_rollback(case["oracle"], case["log"],
                      case["request"], case["expect"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        _run_rollback(case["oracle"], case["log"],
                      case["request"], case["expect"])


def _exec_malformed(case):
    engine = RollbackEngine(ORACLES[case["oracle"]])
    with pytest.raises(RollbackError) as exc:
        engine.rollback(copy.deepcopy(case["log"]),
                        copy.deepcopy(case["request"]))
    assert exc.value.failure_class == \
        case["expect_failure"], case["name"]
    assert exc.value.code == FAILURE_MAPPING[
        case["expect_failure"]], case["name"]
    assert exc.value.code in ERROR_ENUM, case["name"]


def _exec_repaired(case):
    rep = _repaired(case)
    receipt = RollbackEngine(ORACLES[rep["oracle"]]).rollback(
        copy.deepcopy(rep["log"]), copy.deepcopy(rep["request"]))
    _validate_receipt_shape(receipt, case["name"])


@pytest.mark.parametrize("name", list(MALFORMED_MANIFEST))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    """The parametrized malformed dispatch can never drift from
    the fixture's own row set."""
    assert list(MALFORMED_MANIFEST) == _names("malformed")


def test_collection_guard_detects_late_fixture_row():
    """A row spliced into the loaded fixture AFTER import is
    caught: dispatch iterates exactly the manifest names and
    structure validation rejects the drifted collection."""
    injected = copy.deepcopy(CASES)
    row = copy.deepcopy(injected["happy"][0])
    row["name"] = "late-injected-row"
    injected["happy"].append(row)
    with pytest.raises(AssertionError):
        _validate_structure(injected)
    assert set(_names("happy")) == set(HAPPY_MANIFEST)


def test_injected_valid_malformed_row_fails_rejection():
    """If a malformed row were silently swapped for its repaired
    (fully valid) form, the rejection test itself fails - the
    battery cannot pass on valid data."""
    case = _case("malformed", "target-negative")
    rep = _repaired(case)
    receipt = RollbackEngine(ORACLES[rep["oracle"]]).rollback(
        copy.deepcopy(rep["log"]), copy.deepcopy(rep["request"]))
    _validate_receipt_shape(receipt, "injected")


def _repair_mutations():
    def bad_form_name(c):
        c["minimal_repair"] = {"set_field": {
            "field": "target_sequence", "value": 2}}

    def two_forms(c):
        c["minimal_repair"] = {
            "set_request_field": {"field": "target_sequence",
                                  "value": 2},
            "set_oracle": {"oracle": "honest"}}

    def missing_value(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "target_sequence"}}

    def bad_field(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "bogus", "value": 2}}

    def bad_value_type(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "target_sequence", "value": "2"}}

    def bad_oracle(c):
        c["minimal_repair"] = {"set_oracle": {"oracle": "bogus"}}

    def replace_log_extra_key(c):
        c["minimal_repair"] = {"replace_log": {
            "log": [], "extra": True}}

    def replace_request_bad_shape(c):
        c["minimal_repair"] = {"replace_request": {
            "request": {"target_sequence": "2"}}}

    return [bad_form_name, two_forms, missing_value, bad_field,
            bad_value_type, bad_oracle, replace_log_extra_key,
            replace_request_bad_shape]


def test_repair_form_mutations_fail_structure():
    """Every drift from the closed repair tagged union is caught
    by structure validation."""
    for mutate in _repair_mutations():
        m = copy.deepcopy(CASES)
        mutate(m["malformed"][3])  # the target-non-int row
        with pytest.raises(AssertionError):
            _validate_structure(m)


def _encoded_bytes(obj):
    """Deterministic encoded bytes of one working input:
    insertion-order-preserving compact JSON, so reordering ANY
    mapping changes the bytes even when == still holds. Called
    ONLY after the joint graph is proven closed over exact
    builtin containers (see _snapshot_work)."""
    return json.dumps(obj, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _graph_signature(roots):
    """Canonical alias-topology signature of the JOINT object
    graph spanning every working input: each distinct container
    gets a first-encounter index, so cross-root shared
    references are pinned; dict entries are captured in
    insertion order; leaves pin exact type and value. Only
    EXACT builtin dict/list containers are traversed, and only
    through base operations (dict.items / list.__iter__) -
    never isinstance-driven dispatch, so a subclass's items()/
    __iter__ hooks can never run here. Two graphs with equal
    values but different sharing (a created or broken alias)
    have different signatures."""
    memo = {}

    def sig(obj):
        obj_type = type(obj)
        if obj_type is dict or obj_type is list:
            oid = id(obj)
            if oid in memo:
                return ("ref", memo[oid])
            idx = len(memo)
            memo[oid] = idx
            if obj_type is dict:
                return ("dict", idx, tuple(
                    (key, sig(value))
                    for key, value in dict.items(obj)))
            return ("list", idx, tuple(
                sig(value) for value in list.__iter__(obj)))
        return ("leaf", obj_type.__name__, repr(obj))

    return tuple(sig(root) for root in roots)


class _NonExactContainer(Exception):
    """A dict/list SUBCLASS reached a path the atomicity walk
    was about to traverse; raised before any user-defined hook
    on that object can run."""


def _walk_identity(roots):
    """Identity map of the JOINT object graph - the FIRST and
    FAIL-CLOSED pass of every snapshot and verification. Only
    exact builtin dict/list containers are traversed, and only
    through base operations (dict.items / list.__iter__): a
    dict/list subclass at any root or nested path is rejected
    by its exact type BEFORE json.dumps or any
    isinstance-driven traversal could run its user-defined
    items()/iteration hooks - a snapshot or check that mutates
    what it observes is no proof. Every reached path records
    the EXACT builtin type and the ORIGINAL object itself
    (kept alive so its identity cannot be recycled); a path
    whose container was already reached records an alias edge
    to the first path where it appeared. Any other object pins
    its exact type and repr as a leaf."""
    seen = {}
    entries = {}
    keepalive = []

    def walk(obj, path):
        obj_type = type(obj)
        if obj_type is dict or obj_type is list:
            oid = id(obj)
            if oid in seen:
                entries[path] = ("alias", seen[oid])
                return
            seen[oid] = path
            keepalive.append(obj)
            entries[path] = ("obj", obj_type, obj)
            if obj_type is dict:
                for key, value in dict.items(obj):
                    walk(value, path + (("k", key),))
            else:
                for index, value in enumerate(
                        list.__iter__(obj)):
                    walk(value, path + (("i", index),))
        elif isinstance(obj, (dict, list)):
            raise _NonExactContainer(
                f"non-exact container "
                f"{obj_type.__name__!r} at {path!r}")
        else:
            entries[path] = ("leaf", obj_type, repr(obj))

    for rindex, root in enumerate(roots):
        walk(root, (("r", rindex),))
    return entries, keepalive


def _snapshot_work(*roots):
    """THE atomicity invariant, captured BEFORE the call over
    the exact objects that will be handed to the operation.
    Capture order is fail-closed first: (a) the identity walk
    proves the graph closed over exact builtin containers via
    base operations - a dict/list subclass at any root or
    nested path is rejected BEFORE any encoding or signature
    traversal, so a hostile snapshot input cannot run its
    hooks or mutate itself while being observed; only then
    (b) the joint alias-topology signature - equal-valued
    graphs with different sharing diverge; and (c) the
    deterministic encoded bytes per root - order-sensitive,
    NaN rejected, so a value-preserving reorder still
    diverges. Any failure while capturing is an AssertionError
    with the original as cause, never a raw escape."""
    try:
        walked = _walk_identity(roots)
        signature = _graph_signature(roots)
        return ([_encoded_bytes(root) for root in roots],
                signature,
                walked)
    except BaseException as exc:
        raise AssertionError(
            f"atomicity snapshot failed: "
            f"{type(exc).__name__}") from exc


def _assert_work_atomic(snap, *roots):
    """INPUT ATOMICITY: the exact objects handed to the rejected
    operation still match the pre-call snapshot - same original
    object and exact builtin type at every path, same alias
    topology, same bytes. Verification replays the capture's
    fail-closed order: the identity walk first (a subclass
    swapped in by the operation is rejected before its hooks
    can run), then the alias topology, then the encoded bytes.
    EVERY failure while verifying - hostile encoding hooks,
    hostile traversal hooks, any BaseException - fails as
    AssertionError with the original as cause, never escapes
    raw."""
    want_bytes, want_signature, (want_entries, _keep) = snap
    try:
        got_entries, _ = _walk_identity(roots)
        assert set(got_entries) == set(want_entries)
        for path, wanted in want_entries.items():
            got = got_entries[path]
            if wanted[0] == "alias":
                assert got == wanted
            elif wanted[0] == "obj":
                assert got[0] == "obj"
                assert got[1] is wanted[1]
                # the ORIGINAL object survives at this path -
                # an equal-valued replacement is a divergence
                assert got[2] is wanted[2]
            else:
                assert got == wanted
        assert _graph_signature(roots) == want_signature
        assert len(roots) == len(want_bytes)
        for root, want in zip(roots, want_bytes, strict=True):
            assert _encoded_bytes(root) == want
    except AssertionError:
        raise
    except BaseException as exc:
        raise AssertionError(
            f"atomicity verification failed: "
            f"{type(exc).__name__}") from exc

def test_rollback():
    """A rejected rollback leaves the EXACT objects handed to it
    bit-identical (named working inputs, snapshotted, passed by
    identity and compared after the call - never a throwaway
    copy), and the subsequent valid follow-up returns the
    pinned receipt."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        engine = RollbackEngine(ORACLES[case["oracle"]])
        work_log = copy.deepcopy(case["log"])
        work_req = copy.deepcopy(case["request"])
        snap = _snapshot_work(work_log, work_req)
        with pytest.raises(RollbackError) as exc:
            engine.rollback(work_log, work_req)
        _assert_work_atomic(snap, work_log, work_req)
        assert exc.value.failure_class == \
            case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        follow = RollbackEngine(ORACLES[case["then_oracle"]])
        result = follow.rollback(
            copy.deepcopy(case["then_log"]),
            copy.deepcopy(case["then_request"]))
        assert result == case["expect"]


class _LogMutatingRollbackEngine(RollbackEngine):
    """Mutant: corrupts the SUPPLIED log before raising the
    pinned rejection."""

    def rollback(self, log, request):
        log.append({"junk": True})
        _fail("divergent_archive")


class _RequestMutatingRollbackEngine(RollbackEngine):
    """Mutant: corrupts the SUPPLIED request before raising the
    pinned rejection."""

    def rollback(self, log, request):
        request.clear()
        _fail("divergent_archive")


class _DeepMutatingRollbackEngine(RollbackEngine):
    """Mutant: rewrites a NESTED field of the supplied log
    before raising the pinned rejection."""

    def rollback(self, log, request):
        log[0]["payload"]["record"]["digest"] = \
            "pdv1:" + "0" * 64
        _fail("divergent_archive")


class _OrderMutatingRollbackEngine(RollbackEngine):
    """Mutant: pops and reinserts a log entry's first field -
    values unchanged, insertion order diverges."""

    def rollback(self, log, request):
        entry = log[0]
        entry["entry_id"] = entry.pop("entry_id")
        _fail("divergent_archive")


class _AliasBreakingRollbackEngine(RollbackEngine):
    """Mutant: replaces a nested log object with an EQUAL
    deepcopy - values and bytes unchanged, alias topology
    diverges."""

    def rollback(self, log, request):
        log[3]["payload"] = copy.deepcopy(log[3]["payload"])
        _fail("divergent_archive")


def test_rollback_atomicity_kills_mutants():
    """The atomicity assertions are NOT vacuous. Every mutant
    below raises the pinned failure class after corrupting the
    SUPPLIED objects; for each, the test proves the corruption
    is observable at the claimed layer (encoded bytes or alias
    topology) and that _assert_work_atomic KILLS it:
    - value/deep mutations: junk appended to the supplied log,
      the supplied request cleared, a nested record field
      rewritten;
    - order mutation: a log entry's first field popped and
      reinserted (== still holds, encoded bytes diverge);
    - alias mutation: a nested payload replaced by an EQUAL
      deepcopy while it aliases another entry's payload -
      per-root bytes stay identical, only the joint alias
      topology diverges."""
    case = _case(
        "rollback",
        "rejected-rollback-raising-archiver-then-valid-"
        "rollback")

    # -- value mutants over the working inputs
    for mutant in (_LogMutatingRollbackEngine,
                   _RequestMutatingRollbackEngine,
                   _DeepMutatingRollbackEngine):
        work_log = copy.deepcopy(case["log"])
        work_req = copy.deepcopy(case["request"])
        snap = _snapshot_work(work_log, work_req)
        with pytest.raises(RollbackError) as exc:
            mutant(archive_tail).rollback(work_log, work_req)
        assert exc.value.failure_class == \
            case["expect_failure"]
        # the mutation really is observable in the encoded
        # bytes - otherwise this kill-test would be vacuous
        assert (_encoded_bytes(work_log) != snap[0][0]
                or _encoded_bytes(work_req) != snap[0][1])
        with pytest.raises(AssertionError):
            _assert_work_atomic(snap, work_log, work_req)

    # -- order mutant: == survives, only the bytes diverge
    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(RollbackError) as exc:
        _OrderMutatingRollbackEngine(
            archive_tail).rollback(work_log, work_req)
    assert exc.value.failure_class == case["expect_failure"]
    assert work_log == case["log"]  # == is blind
    assert _encoded_bytes(work_log) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # -- alias mutant: per-root bytes survive, only the JOINT
    # alias topology diverges. The working log is seeded with a
    # deliberate cross-entry alias (two equal payloads ARE one
    # object); the mutant swaps one side for an equal deepcopy.
    alias_source = _case(
        "malformed", "oracle-unbound-token")["log"]
    work_log = copy.deepcopy(alias_source)
    work_req = copy.deepcopy(case["request"])
    # entries 0 and 3 have EQUAL payloads; make them one object
    work_log[3]["payload"] = work_log[0]["payload"]
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(RollbackError) as exc:
        _AliasBreakingRollbackEngine(
            archive_tail).rollback(work_log, work_req)
    assert exc.value.failure_class == case["expect_failure"]
    # bytes alone CANNOT see it - the witness is the topology
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _encoded_bytes(work_req) == snap[0][1]
    assert _graph_signature((work_log, work_req)) != snap[1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)


class _PayloadDict(dict):
    """An equal-valued dict SUBCLASS: json bytes and first-
    encounter topology are unchanged; the exact builtin type
    diverges."""


class _HookedDict(dict):
    """A dict subclass whose traversal hooks raise
    BaseExceptions - the fail-closed identity walk rejects it
    by exact type BEFORE the hook can run; the rejection still
    surfaces as AssertionError with a cause, never a raw
    escape."""

    def items(self):
        raise GeneratorExit("evil")


class _HookedList(list):
    def __iter__(self):
        raise KeyboardInterrupt("evil")


def test_rollback_atomicity_kills_identity_mutants():
    """Identity- and type-level mutations that survive bytes,
    == and first-encounter topology are each KILLED:
    - equal unaliased deepcopy replacement of a nested object
      (original identity replaced, every byte equal);
    - equal-child swap inside a list (positions exchanged,
      every byte equal);
    - exact dict replaced by an equal dict SUBCLASS (runtime
      type diverges, bytes equal) - the fail-closed walk
      rejects it by exact type before any hook can run;
    - hostile subclasses whose hooks would raise
      KeyboardInterrupt / SystemExit / GeneratorExit - the
      rejection surfaces as AssertionError with a cause,
      never as the raw BaseException."""
    case = _case(
        "rollback",
        "rejected-rollback-raising-archiver-then-valid-"
        "rollback")

    # -- equal unaliased deepcopy replacement
    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)
    original_payload = work_log[0]["payload"]
    work_log[0]["payload"] = copy.deepcopy(work_log[0]["payload"])
    # witness: bytes AND topology unchanged, identity replaced
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _graph_signature((work_log, work_req)) == snap[1]
    assert work_log[0]["payload"] is not original_payload
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # -- equal-child swap inside a list
    work_log = [copy.deepcopy(case["log"][0]),
                copy.deepcopy(case["log"][0])]
    assert work_log[0] is not work_log[1]
    snap = _snapshot_work(work_log)
    work_log[0], work_log[1] = work_log[1], work_log[0]
    # witness: bytes and topology unchanged, positions swapped
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _graph_signature((work_log,)) == snap[1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log)

    # -- exact dict replaced by an equal subclass
    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)
    work_log[0]["payload"] = _PayloadDict(work_log[0]["payload"])
    # witness: bytes unchanged, the exact builtin TYPE diverged
    assert _encoded_bytes(work_log) == snap[0][0]
    assert type(work_log[0]["payload"]) is not dict
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # -- hostile traversal/encoding hooks surface as
    # AssertionError with the original as cause, never raw
    for hooked in (_HookedDict({"a": 1}),
                   _HookedList([1])):
        work_log = copy.deepcopy(case["log"])
        work_req = copy.deepcopy(case["request"])
        snap = _snapshot_work(work_log, work_req)
        work_log[0]["payload"]["record"] = hooked
        try:
            _assert_work_atomic(snap, work_log, work_req)
            raise AssertionError("hostile hooks accepted")
        except AssertionError as err:
            assert err.__cause__ is None or isinstance(
                err.__cause__, BaseException)
        except BaseException:
            raise AssertionError(
                "a raw BaseException escaped the atomicity "
                "boundary") from None

    class _SystemExitDict(dict):
        def __repr__(self):
            raise SystemExit("evil")

    work_log = copy.deepcopy(case["log"])
    work_req = copy.deepcopy(case["request"])
    snap = _snapshot_work(work_log, work_req)
    work_log[0]["payload"]["record"] = _SystemExitDict(
        work_log[0]["payload"]["record"])
    try:
        _assert_work_atomic(snap, work_log, work_req)
        raise AssertionError("hostile repr accepted")
    except AssertionError:
        pass
    except BaseException:
        raise AssertionError(
            "a raw BaseException escaped the atomicity "
            "boundary") from None


class _SnapshotMutatingDict(dict):
    """A dict subclass whose items() hook MUTATES the object
    without raising: the pre-fix snapshot traversed subclasses
    through json.dumps and isinstance-driven helpers, so a
    hostile input could mutate itself during capture and have
    the snapshot bless the already-mutated state."""

    hook_runs = 0

    def items(self):
        type(self).hook_runs += 1
        self["injected"] = "snapshot-mutated"
        return dict.items(self)


class _SnapshotMutatingList(list):
    hook_runs = 0

    def __iter__(self):
        type(self).hook_runs += 1
        self.append("snapshot-mutated")
        return list.__iter__(self)


class _SnapshotRaisingDict(dict):
    hook_runs = 0

    def items(self):
        type(self).hook_runs += 1
        raise KeyboardInterrupt("evil")


class _SnapshotRaisingList(list):
    hook_runs = 0

    def __iter__(self):
        type(self).hook_runs += 1
        raise SystemExit("evil")


def test_snapshot_rejects_hooked_containers_without_mutation():
    """The snapshot's FIRST pass is the fail-closed exact-type
    identity walk: a dict/list SUBCLASS at any root or nested
    position is rejected by its exact type via base operations
    BEFORE json.dumps or any traversal, so its items()/
    __iter__ hook never runs and the object is left untouched -
    a snapshot must never mutate what it observes. Proven at
    root and nested positions, for dict and list, for hooks
    that mutate WITHOUT raising (the injected field never
    lands, the hook count stays zero, the original content
    captured via base operations is intact) and for hooks that
    raise KeyboardInterrupt/SystemExit (rejected the same way,
    AssertionError with a cause, hook never run). A hostile
    LEAF that is not a container subclass - a raising __repr__
    - still reaches traversal and surfaces as AssertionError
    with the original BaseException as its cause."""
    for hooked_class in (_SnapshotMutatingDict,
                         _SnapshotRaisingDict):
        for nested in (False, True):
            hooked_class.hook_runs = 0
            bad = hooked_class({"a": 1})
            work = [{"outer": [bad]}] if nested else [bad]
            before = list(dict.items(bad))
            with pytest.raises(AssertionError) as err:
                _snapshot_work(*work)
            # fail-closed BEFORE the hook: never ran, no
            # mutation, original content intact via base ops
            assert hooked_class.hook_runs == 0
            assert list(dict.items(bad)) == before
            assert isinstance(
                err.value.__cause__, _NonExactContainer)

    for hooked_class in (_SnapshotMutatingList,
                         _SnapshotRaisingList):
        for nested in (False, True):
            hooked_class.hook_runs = 0
            bad = hooked_class([1])
            work = [{"outer": bad}] if nested else [bad]
            before = list(list.__iter__(bad))
            with pytest.raises(AssertionError) as err:
                _snapshot_work(*work)
            assert hooked_class.hook_runs == 0
            assert list(list.__iter__(bad)) == before
            assert isinstance(
                err.value.__cause__, _NonExactContainer)

    class _RaisingReprLeaf:
        def __repr__(self):
            raise GeneratorExit("evil")

    for work in ([_RaisingReprLeaf()],
                 [{"outer": [_RaisingReprLeaf()]}]):
        with pytest.raises(AssertionError) as err:
            _snapshot_work(*work)
        assert isinstance(err.value.__cause__, GeneratorExit)


# -- closed scenario coverage ---------------------------------------------------


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
    a, b = m["malformed"][0], m["malformed"][5]
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _swap_oracle(m, section):
    a, b = m["malformed"][10], m["malformed"][11]
    a["oracle"], b["oracle"] = b["oracle"], a["oracle"]


def _swap_repair(m, section):
    a = next(c for c in m["malformed"]
             if "replace_log" in c["minimal_repair"])
    b = next(c for c in m["malformed"]
             if "set_request_field" in c["minimal_repair"])
    a["minimal_repair"], b["minimal_repair"] = (
        b["minimal_repair"], a["minimal_repair"])


def _swap_scenario(m, section):
    a, b = m["malformed"][1], m["malformed"][2]
    a["scenario"], b["scenario"] = b["scenario"], a["scenario"]


def _section_mutation_cases():
    cases = []
    for section in ("happy", "boundary", "malformed",
                    "rollback"):
        cases.append((f"{section}-pop", section, _pop_first))
        cases.append((f"{section}-rename", section,
                      _rename_first))
        cases.append((f"{section}-add-extra", section,
                      _add_extra))
        cases.append((f"{section}-substitute", section,
                      _substitute_first))
        cases.append((f"{section}-move", section, _move_row))
    cases.append(("malformed-swap-failure", "malformed",
                  _swap_failure))
    cases.append(("malformed-swap-oracle", "malformed",
                  _swap_oracle))
    cases.append(("malformed-swap-repair", "malformed",
                  _swap_repair))
    cases.append(("malformed-swap-scenario", "malformed",
                  _swap_scenario))
    return cases


def test_section_mutations_fail_structure():
    """Pop, rename, add, substitute, move and swap mutations of
    ANY section are caught by structure validation - the
    manifest is the closed proof of the scenario set."""
    for _label, section, mutate in _section_mutation_cases():
        m = copy.deepcopy(CASES)
        mutate(m, section)
        with pytest.raises(AssertionError):
            _validate_structure(m)


def test_failure_class_coverage():
    """The malformed battery exercises EVERY contract-declared
    failure class - a class silently dropped from the fixture
    is caught here and in structure validation."""
    declared = {c["expect_failure"]
                for c in CASES["malformed"]}
    assert declared == FAILURE_CLASSES
    assert declared == {
        "malformed_rollback_record", "unknown_target",
        "corrupt_source", "divergent_archive"}
