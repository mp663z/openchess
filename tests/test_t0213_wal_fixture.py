"""T0213: WAL conformance fixture - the fixture must PROVE happy,
boundary, malformed and rollback behavior against the T0212 WAL
contract. The cases execute against the contract-derived
reference in tests.test_t0212_wal_contract (itself fully derived
from data/contracts/wal.yaml plus the linked transposition-node,
variant, position-digest, en-passant and FEN contracts) -
nothing is re-implemented here. Pinned receipts and replay
results in the fixture were computed from that reference at
authoring time, so any contract or derivation drift breaks this
battery. Every malformed case is discriminating (repairing ONLY
its declared defect locus makes the case valid) and rollback
cases prove a rejected append/replay leaves log and request
byte-identical.

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
    GENESIS,
    WalEngine,
    WalError,
    _fail,
    _identity,
    _node,
    _op_spec,
    canonical_payload,
)
from tools.wal_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "wal"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
_ENTRY_RE = re.compile(
    _CC["identifiers"]["entry_id"]["grammar"])
_PRIOR_RE = re.compile(
    _CC["identifiers"]["prior_entry_id"]["grammar"])
_SID_RE = re.compile(
    _CC["identifiers"]["state_id"]["grammar"])
_DIGEST_RE = re.compile(r"pdv1:[0-9a-f]{64}")
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}
PAYLOAD_FIELDS = {"identity", "record"}
REQUEST_FIELDS = {"op", "payload"}
ENTRY_FIELDS = {"entry_id", "sequence", "op", "payload",
                "prior_entry_id"}
REPLAY_FIELDS = {"state", "state_id", "head", "applied"}


def _raising_oracle(identity, record):
    raise ValueError("untrusted canonicalizer failure")


def _non_str_oracle(identity, record):
    return None


def _surrogate_oracle(identity, record):
    return "\ud800"


ORACLES = {"honest": canonical_payload,
           "raising": _raising_oracle,
           "non-str": _non_str_oracle,
           "lone-surrogate": _surrogate_oracle}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
SCRIPT_KEYS = {"name", "kind", "oracle", "appends", "expect"}
MALFORMED_APPEND_KEYS = {"name", "kind", "oracle", "log",
                         "request", "expect_failure", "defect",
                         "minimal_repair"}
MALFORMED_REPLAY_KEYS = {"name", "kind", "oracle", "log",
                         "expect_failure", "defect",
                         "minimal_repair"}
ROLLBACK_APPEND_KEYS = {"name", "kind", "oracle", "log",
                        "request", "expect_failure",
                        "then_oracle", "expect"}
ROLLBACK_REPLAY_KEYS = {"name", "kind", "oracle", "log",
                        "expect_failure", "then_oracle",
                        "then_log", "expect"}
REPAIR_FORMS = {"set_request_field", "replace_request",
                "replace_log", "set_oracle"}

# -- the closed scenario manifests -------------------------------------------
# Every fixture section's actual {name: metadata} map must equal
# its manifest EXACTLY - no missing, substituted, duplicated,
# renamed or extra rows, and every advertised semantic shape is
# asserted before execution.
MWE = "malformed_wal_entry"
UO = "unknown_operation"
SC = "sequence_conflict"
CC = "corrupt_chain"
DC = "divergent_canonicalization"

# happy/boundary: name -> (oracle, append-script cardinality)
HAPPY_MANIFEST = {
    "append-put-put-delete-chain": ("honest", 3),
    "append-single-put-after-e4": ("honest", 1),
    "append-put-delete-put": ("honest", 3),
}
BOUNDARY_MANIFEST = {
    "replay-empty-log": ("honest", 0),
    "delete-missing-identity-noop": ("honest", 1),
    "delete-last-record-empty-state": ("honest", 2),
}
# malformed: name -> (failure class, oracle, repair form,
# pinned defect description, CLOSED SCENARIO TAG)
MALFORMED_MANIFEST = {
    "request-missing-op-field": (MWE, "honest",
        "replace_request",
        "request is missing the op field",
        "missing-request-field"),
    "request-extra-field": (MWE, "honest", "replace_request",
        "request carries an undeclared extra field",
        "extra-request-field"),
    "op-unregistered": (UO, "honest", "set_request_field",
        "op is not a registered operation",
        "unregistered-op"),
    "op-non-str": (MWE, "honest", "set_request_field",
        "op is not a string",
        "non-str-op"),
    "payload-missing-record": (MWE, "honest",
        "replace_request",
        "payload is missing the record field",
        "missing-payload-record"),
    "payload-identity-mismatch": (MWE, "honest",
        "replace_request",
        "payload identity does not match the derived record "
        "identity",
        "identity-mismatch"),
    "record-wrong-digest": (MWE, "honest", "replace_request",
        "record digest is well-formed but belongs to another "
        "record",
        "wrong-record-digest"),
    "record-non-str-field": (MWE, "honest", "replace_request",
        "a record field is not a string",
        "non-str-record-field"),
    "oracle-raises": (DC, "raising", "set_oracle",
        "the canonicalizer raises during append",
        "oracle-raises"),
    "oracle-non-str-output": (DC, "non-str", "set_oracle",
        "the canonicalizer returns a non-string",
        "oracle-non-str-output"),
    "oracle-lone-surrogate": (DC, "lone-surrogate",
        "set_oracle",
        "the canonicalizer returns a non-UTF-8-encodable "
        "string",
        "oracle-lone-surrogate"),
    "log-entry-bad-id-grammar": (MWE, "honest", "replace_log",
        "a log entry id fails the entry-id grammar",
        "bad-entry-id-grammar"),
    "log-sequence-gap": (SC, "honest", "replace_log",
        "an entry sequence skips the exact 1-based position",
        "sequence-gap"),
    "log-sequence-duplicate": (SC, "honest", "replace_log",
        "two entries carry the same sequence",
        "sequence-duplicate"),
    "log-prior-link-broken": (CC, "honest", "replace_log",
        "an entry's prior link does not match the previous "
        "tip",
        "broken-prior-link"),
    "log-entry-id-tampered": (CC, "honest", "replace_log",
        "an entry id diverges from the recomputed chain",
        "tampered-entry-id"),
}
# rollback: name -> (failure class, initial oracle, follow-up
# oracle)
ROLLBACK_MANIFEST = {
    "rejected-append-raising-oracle-then-valid-append": (
        DC, "raising", "honest"),
    "rejected-replay-tampered-chain-then-valid-replay": (
        CC, "honest", "honest"),
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


def _validate_record_shape(record, label):
    assert isinstance(record, dict), label
    assert set(record) == RECORD_FIELDS, label
    for field in RECORD_FIELDS:
        assert isinstance(record[field], str), (label, field)


def _validate_payload_shape(payload, label):
    assert isinstance(payload, dict), label
    assert set(payload) == PAYLOAD_FIELDS, label
    assert isinstance(payload["identity"], str), label
    _validate_record_shape(payload["record"], label)


def _validate_request_shape(request, label):
    assert isinstance(request, dict), label
    assert set(request) == REQUEST_FIELDS, label
    assert isinstance(request["op"], str), label
    _validate_payload_shape(request["payload"], label)


def _validate_entry_shape(entry, label):
    assert isinstance(entry, dict), label
    assert set(entry) == ENTRY_FIELDS, label
    assert _ENTRY_RE.fullmatch(entry["entry_id"]), label
    assert type(entry["sequence"]) is int, label
    assert isinstance(entry["op"], str), label
    assert _PRIOR_RE.fullmatch(entry["prior_entry_id"]), label
    _validate_payload_shape(entry["payload"], label)


def _validate_log_shape(log, label):
    assert isinstance(log, list), label
    for entry in log:
        _validate_entry_shape(entry, label)


def _validate_state_shape(state, label):
    assert isinstance(state, dict), label
    for key, rec in state.items():
        assert isinstance(key, str), label
        _validate_record_shape(rec, label)


def _validate_replay_shape(result, label):
    assert isinstance(result, dict), label
    assert set(result) == REPLAY_FIELDS, label
    _validate_state_shape(result["state"], label)
    assert _SID_RE.fullmatch(result["state_id"]), label
    assert _PRIOR_RE.fullmatch(result["head"]), label
    assert type(result["applied"]) is int, label


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
    elif "replace_log" in rep:
        out["log"] = copy.deepcopy(
            rep["replace_log"]["log"])
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of four
    closed forms, never mixed:
    - set_request_field: exactly {"field", "value"}; field is
      "op" (the only scalar request field); value a string;
    - replace_request: exactly {"request"}; a shape-valid
      request;
    - replace_log: exactly {"log"}; a shape-valid log;
    - set_oracle: exactly {"oracle"}; a declared oracle
      selector."""
    rep = case["minimal_repair"]
    assert len(rep) == 1 and set(rep) <= REPAIR_FORMS, \
        case["name"]
    form = next(iter(rep.values()))
    if "set_request_field" in rep:
        assert set(form) == {"field", "value"}, case["name"]
        assert form["field"] == "op", case["name"]
        assert isinstance(form["value"], str), case["name"]
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

_ORACLE_TAG_SELECTOR = {"oracle-raises": "raising",
                        "oracle-non-str-output": "non-str",
                        "oracle-lone-surrogate": "lone-surrogate"}
# oracle-tag rows are pairwise discriminated by log cardinality
_ORACLE_LOG_CARDINALITY = {"oracle-raises": 1,
                           "oracle-non-str-output": 2,
                           "oracle-lone-surrogate": 0}


def _append_succeeds(log, request):
    """The reference engine accepts the append end to end under
    the HONEST canonicalizer."""
    try:
        WalEngine(canonical_payload).append(
            copy.deepcopy(log), copy.deepcopy(request))
    except WalError:
        return False
    return True


def _replay_succeeds(log):
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return False
    return True


def _valid_declared_request(req):
    """Every declared request field present, op a registered
    exact string, payload identity a string and the record a
    shape-valid exact string record whose identity matches."""
    assert set(req) == REQUEST_FIELDS
    assert type(req["op"]) is str
    assert _op_spec(req["op"]) is not None
    payload = req["payload"]
    assert set(payload) == PAYLOAD_FIELDS
    assert type(payload["identity"]) is str
    _validate_record_shape(payload["record"], "request")
    assert _identity(payload["record"]) == payload["identity"]


def _diff_fields(original, repaired, label):
    """The two same-shaped mappings differ at exactly the
    returned field set."""
    assert set(original) == set(repaired), label
    return {k for k in original if original[k] != repaired[k]}


def _check_malformed_scenario(case, tag):
    name = case["name"]
    rep = _repaired(case)
    if tag in _ORACLE_TAG_SELECTOR:
        req = case["request"]
        log = case["log"]
        assert case["oracle"] == _ORACLE_TAG_SELECTOR[tag], name
        assert len(log) == _ORACLE_LOG_CARDINALITY[tag], name
        _valid_declared_request(req)
        _validate_log_shape(log, name)
        # the payload and log are fully well-formed: the ONLY
        # defect is the canonicalizer itself - the honest
        # canonicalizer succeeds
        assert _append_succeeds(log, req), name
        form = case["minimal_repair"]["set_oracle"]
        assert form["oracle"] == "honest", name
        assert rep["request"] == req and rep["log"] == log, name
        return
    if tag in ("bad-entry-id-grammar", "sequence-gap",
               "sequence-duplicate", "broken-prior-link",
               "tampered-entry-id"):
        # replay-kind log defects: the repair log is a VALID log
        # and the original differs from it at exactly ONE entry
        # and exactly ONE field - the declared locus.
        original = case["log"]
        repaired_log = rep["log"]
        assert _replay_succeeds(repaired_log), name
        assert len(original) == len(repaired_log), name
        diffs = [(i, _diff_fields(original[i], repaired_log[i],
                                  name))
                 for i in range(len(original))
                 if original[i] != repaired_log[i]]
        assert len(diffs) == 1, name
        (pos, fields), = diffs
        assert len(fields) == 1, name
        field = next(iter(fields))
        if tag == "bad-entry-id-grammar":
            assert field == "entry_id", name
            assert type(original[pos]["entry_id"]) is str, name
            assert _ENTRY_RE.fullmatch(
                original[pos]["entry_id"]) is None, name
        elif tag in ("sequence-gap", "sequence-duplicate"):
            assert field == "sequence", name
            bad = original[pos]["sequence"]
            assert type(bad) is int, name
            assert bad != pos + 1, name
            if tag == "sequence-gap":
                assert bad > pos + 1, name
            else:
                sequences = [e["sequence"] for e in original]
                assert len(sequences) != len(set(sequences)), \
                    name
        elif tag == "broken-prior-link":
            assert field == "prior_entry_id", name
            bad = original[pos]["prior_entry_id"]
            assert type(bad) is str and \
                _PRIOR_RE.fullmatch(bad), name
            prior_tip = (GENESIS if pos == 0 else
                         original[pos - 1]["entry_id"])
            assert bad != prior_tip, name
        else:
            assert field == "entry_id", name
            bad = original[pos]["entry_id"]
            assert type(bad) is str and \
                _ENTRY_RE.fullmatch(bad), name
            assert bad != repaired_log[pos]["entry_id"], name
        return
    # append-kind request defects
    req = case["request"]
    rep_req = rep["request"]
    if tag == "missing-request-field":
        assert set(req) == {"payload"}, name
        _validate_payload_shape(req["payload"], name)
        # the repair adds EXACTLY the op field, nothing else
        assert rep_req == dict(req, op=rep_req["op"]), name
        assert _op_spec(rep_req["op"]) is not None, name
        assert _identity(rep_req["payload"]["record"]) == \
            rep_req["payload"]["identity"], name
    elif tag == "extra-request-field":
        extra = set(req) - REQUEST_FIELDS
        assert len(extra) == 1, name
        assert set(req) >= REQUEST_FIELDS, name
        projected = {k: req[k] for k in REQUEST_FIELDS}
        _valid_declared_request(projected)
        # the repair removes EXACTLY the extra key, nothing else
        assert rep_req == projected, name
    elif tag == "unregistered-op":
        assert set(req) == REQUEST_FIELDS, name
        assert type(req["op"]) is str, name
        assert _op_spec(req["op"]) is None, name
        _validate_payload_shape(req["payload"], name)
        form = case["minimal_repair"]["set_request_field"]
        assert form["field"] == "op", name
        assert _op_spec(form["value"]) is not None, name
        assert rep_req["payload"] == req["payload"], name
    elif tag == "non-str-op":
        assert set(req) == REQUEST_FIELDS, name
        assert type(req["op"]) is not str, name
        _validate_payload_shape(req["payload"], name)
        form = case["minimal_repair"]["set_request_field"]
        assert form["field"] == "op", name
        assert _op_spec(form["value"]) is not None, name
        assert rep_req["payload"] == req["payload"], name
    elif tag == "missing-payload-record":
        assert set(req) == REQUEST_FIELDS, name
        assert set(req["payload"]) == {"identity"}, name
        assert type(req["payload"]["identity"]) is str, name
        # the repair supplies EXACTLY the record deriving the
        # declared identity, op and identity untouched
        assert rep_req["op"] == req["op"], name
        assert rep_req["payload"]["identity"] == \
            req["payload"]["identity"], name
        assert _identity(rep_req["payload"]["record"]) == \
            req["payload"]["identity"], name
    elif tag == "identity-mismatch":
        assert set(req) == REQUEST_FIELDS, name
        payload = req["payload"]
        assert type(payload["identity"]) is str, name
        _validate_record_shape(payload["record"], name)
        assert _identity(payload["record"]) != \
            payload["identity"], name
        # the repair corrects EXACTLY the identity - same op,
        # same record
        assert rep_req["op"] == req["op"], name
        assert rep_req["payload"]["record"] == \
            payload["record"], name
        assert rep_req["payload"]["identity"] == \
            _identity(payload["record"]), name
    elif tag == "wrong-record-digest":
        payload = req["payload"]
        record = payload["record"]
        _validate_record_shape(record, name)
        derived = _node(record["snapshot_fen"])
        # grammar-valid digest, well-formed but WRONG: the record
        # equals the derived record except exactly the digest
        assert record == dict(derived, digest=record["digest"]), \
            name
        assert record["digest"] != derived["digest"], name
        assert _DIGEST_RE.fullmatch(record["digest"]), name
        # the repair is EXACTLY the derived record
        assert rep_req["payload"]["record"] == derived, name
        assert rep_req["payload"]["identity"] == \
            payload["identity"], name
        assert rep_req["op"] == req["op"], name
    elif tag == "non-str-record-field":
        payload = req["payload"]
        record = payload["record"]
        assert set(record) == RECORD_FIELDS, name
        bad = [f for f in RECORD_FIELDS
               if type(record[f]) is not str]
        assert len(bad) == 1, name
        # the repair retypes EXACTLY that field - identity, op
        # and every other field untouched
        rep_record = rep_req["payload"]["record"]
        changed = {f for f in RECORD_FIELDS
                   if rep_record[f] != record[f]}
        assert changed == set(bad), name
        assert rep_req["payload"]["identity"] == \
            payload["identity"], name
        assert rep_req["op"] == req["op"], name
        assert _identity(rep_record) == payload["identity"], name
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


def _assert_cardinalities(case, n_appends):
    """The advertised script cardinality is REALLY present, and
    the pinned expectations realize exactly that cardinality -
    a substituted row can never satisfy the wrong count."""
    name = case["name"]
    assert len(case["appends"]) == n_appends, name
    assert len(case["expect"]["receipts"]) == n_appends, name
    assert case["expect"]["replay"]["applied"] == n_appends, \
        name


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
                oracle, n_appends = want
                assert case["oracle"] == oracle, case["name"]
                _assert_cardinalities(case, n_appends)
            elif section == "malformed":
                failure, oracle, repair, defect, tag = want
                assert case["expect_failure"] == failure, (
                    case["name"])
                assert case["oracle"] == oracle, case["name"]
                assert set(case["minimal_repair"]) == {repair}, (
                    case["name"])
                assert case["defect"] == defect, case["name"]
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
                assert case["kind"] in ("append", "replay"), \
                    case["name"]
                assert case["oracle"] in ORACLES and \
                    case["then_oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                if case["kind"] == "append":
                    assert set(case) == ROLLBACK_APPEND_KEYS, \
                        case["name"]
                    _validate_request_shape(case["request"],
                                            case["name"])
                    _validate_log_shape(case["log"],
                                        case["name"])
                    _validate_entry_shape(case["expect"],
                                          case["name"])
                else:
                    assert set(case) == ROLLBACK_REPLAY_KEYS, \
                        case["name"]
                    _validate_log_shape(case["log"],
                                        case["name"])
                    _validate_log_shape(case["then_log"],
                                        case["name"])
                    _validate_replay_shape(case["expect"],
                                           case["name"])
                continue
            if section == "malformed":
                assert case["kind"] in ("append", "replay"), \
                    case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert isinstance(case["defect"], str) and \
                    case["defect"], case["name"]
                _validate_repair(case)
                # case-owned request/log payloads are
                # intentionally shape-defective (that is the
                # defect); validate key-set only, never shape.
                if case["kind"] == "append":
                    assert set(case) == MALFORMED_APPEND_KEYS, \
                        case["name"]
                else:
                    assert set(case) == MALFORMED_REPLAY_KEYS, \
                        case["name"]
            else:
                assert case["kind"] == "script", case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert set(case) == SCRIPT_KEYS, case["name"]
                for request in case["appends"]:
                    _validate_request_shape(request,
                                            case["name"])
                assert set(case["expect"]) == {"receipts",
                                               "replay"}, \
                    case["name"]
                for receipt in case["expect"]["receipts"]:
                    _validate_entry_shape(receipt, case["name"])
                _validate_replay_shape(
                    case["expect"]["replay"], case["name"])
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
    replace_request / replace_log / set_oracle change exactly
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
            assert rep["log"] == case["log"], case["name"]
            assert rep["oracle"] == case["oracle"], case["name"]
        elif form_name == "replace_request":
            assert rep["log"] == case["log"], case["name"]
            assert rep["oracle"] == case["oracle"], case["name"]
        elif form_name == "replace_log":
            assert rep["oracle"] == case["oracle"], case["name"]
        else:
            assert rep.get("request") == case.get("request"), \
                case["name"]
            assert rep["log"] == case["log"], case["name"]


def _run_script(oracle_name, appends, expect):
    """Execute the append script from the empty log; every
    receipt and the final replay result equal the pinned values;
    a second run is byte-identical and no input mutates."""
    engine = WalEngine(ORACLES[oracle_name])
    appends_p = copy.deepcopy(appends)
    log = []
    receipts = []
    for request in appends:
        receipts.append(engine.append(log, request))
    assert receipts == expect["receipts"]
    result = WalEngine(ORACLES[oracle_name]).replay(log)
    assert result == expect["replay"]
    # determinism: fresh log, same receipts and replay result
    again = WalEngine(ORACLES[oracle_name])
    again_log = []
    again_receipts = []
    for request in copy.deepcopy(appends_p):
        again_receipts.append(again.append(again_log, request))
    assert again_receipts == receipts
    assert WalEngine(ORACLES[oracle_name]).replay(
        again_log) == result
    assert appends == appends_p


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        _run_script(case["oracle"], case["appends"],
                    case["expect"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        _run_script(case["oracle"], case["appends"],
                    case["expect"])


def _exec_malformed(case):
    """The ORIGINAL input rejects with the pinned failure class
    and leaves log and request byte-identical."""
    log_p = copy.deepcopy(case["log"])
    engine = WalEngine(ORACLES[case["oracle"]])
    try:
        if case["kind"] == "append":
            req_p = copy.deepcopy(case["request"])
            engine.append(case["log"], case["request"])
        else:
            req_p = None
            engine.replay(case["log"])
    except WalError as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.code in ERROR_ENUM
        assert case["log"] == log_p
        if req_p is not None:
            assert case["request"] == req_p
        return
    raise AssertionError("input unexpectedly accepted")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    engine = WalEngine(ORACLES[out["oracle"]])
    if case["kind"] == "append":
        result = engine.append(out["log"], out["request"])
        _validate_entry_shape(result, case["name"])
    else:
        result = engine.replay(out["log"])
        _validate_replay_shape(result, case["name"])


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
    engine = WalEngine(ORACLES[valid["oracle"]])
    if original["kind"] == "append":
        engine.append(copy.deepcopy(valid["log"]),
                      copy.deepcopy(valid["request"]))
    else:
        engine.replay(copy.deepcopy(valid["log"]))
    with pytest.raises(AssertionError):
        _exec_malformed(dict(original, **valid))


def _repair_mutation_cases():
    """Every repair-form mutation the tagged union must reject
    STRUCTURALLY, before any execution."""
    # the good control is the row's OWN minimal repair: it
    # proves the mutation-application machinery itself is not
    # what breaks validation (any semantically DIFFERENT repair
    # payload now fails the repair-locus assertions).
    good_req = copy.deepcopy(
        CASES["malformed"][0]["minimal_repair"]
        ["replace_request"]["request"])
    good_rep = CASES["malformed"][0]["minimal_repair"]
    return [
        ("empty-repair", {}),
        ("mixed-forms",
         {"set_oracle": {"oracle": "honest"},
          "set_request_field": {"field": "op",
                                "value": "put"}}),
        ("unknown-form", {"wat": {"x": 1}}),
        ("set_field-extra-key",
         {"set_request_field": {"field": "op",
                                "value": "put", "wat": 1}}),
        ("set_field-bad-field",
         {"set_request_field": {"field": "label",
                                "value": "put"}}),
        ("set_field-value-not-str",
         {"set_request_field": {"field": "op",
                                "value": 5}}),
        ("replace_request-extra-key",
         {"replace_request": {"request": good_req, "wat": 1}}),
        ("replace_request-bad-shape",
         {"replace_request": {"request": {"wat": 1}}}),
        ("replace_log-not-list",
         {"replace_log": {"log": {}}}),
        ("replace_log-bad-entry",
         {"replace_log": {"log": [{"wat": 1}]}}),
        ("set_oracle-extra-key",
         {"set_oracle": {"oracle": "honest", "wat": 1}}),
        ("set_oracle-undeclared",
         {"set_oracle": {"oracle": "sneaky"}}),
        # only a same-form, valid-payload control stays valid:
        # the scenario manifest pins each malformed row's repair
        # form, so ANY form swap - even to another valid union
        # member - must now fail structure validation.
        ("good-forms-still-valid-1",
         copy.deepcopy(good_rep)),
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
    """A rejected append/replay leaves the EXACT objects handed
    to it byte-identical (named working inputs, snapshotted,
    passed by identity and compared after the call - never a
    throwaway copy), and the subsequent valid follow-up returns
    the pinned receipt/result."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        engine = WalEngine(ORACLES[case["oracle"]])
        if case["kind"] == "append":
            work_log = copy.deepcopy(case["log"])
            work_req = copy.deepcopy(case["request"])
            snap = _snapshot_work(work_log, work_req)
            with pytest.raises(WalError) as exc:
                engine.append(work_log, work_req)
            _assert_work_atomic(snap, work_log, work_req)
        else:
            work_log = copy.deepcopy(case["log"])
            snap = _snapshot_work(work_log)
            with pytest.raises(WalError) as exc:
                engine.replay(work_log)
            _assert_work_atomic(snap, work_log)
        assert exc.value.failure_class == \
            case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        follow = WalEngine(ORACLES[case["then_oracle"]])
        if case["kind"] == "append":
            result = follow.append(
                copy.deepcopy(case["log"]),
                copy.deepcopy(case["request"]))
        else:
            result = follow.replay(
                copy.deepcopy(case["then_log"]))
        assert result == case["expect"]


class _LogMutatingAppendEngine(WalEngine):
    """Mutant: corrupts the SUPPLIED log before raising the
    pinned rejection."""

    def append(self, log, request):
        log.append({"junk": True})
        _fail("divergent_canonicalization")


class _RequestMutatingAppendEngine(WalEngine):
    """Mutant: corrupts the SUPPLIED request before raising the
    pinned rejection."""

    def append(self, log, request):
        request.clear()
        _fail("divergent_canonicalization")


class _LogMutatingReplayEngine(WalEngine):
    """Mutant: corrupts the SUPPLIED log before raising the
    pinned rejection."""

    def replay(self, log):
        log.clear()
        _fail("corrupt_chain")


class _DeepMutatingAppendEngine(WalEngine):
    """Mutant: rewrites a NESTED field of the supplied request
    before raising the pinned rejection."""

    def append(self, log, request):
        request["payload"]["record"]["digest"] = \
            "pdv1:" + "0" * 64
        _fail("divergent_canonicalization")


class _OrderMutatingAppendEngine(WalEngine):
    """Mutant: pops and reinserts a request field - values
    unchanged, insertion order diverges."""

    def append(self, log, request):
        request["op"] = request.pop("op")
        _fail("divergent_canonicalization")


class _AliasBreakingAppendEngine(WalEngine):
    """Mutant: replaces a nested request object with an EQUAL
    deepcopy - values and bytes unchanged, alias topology
    diverges."""

    def append(self, log, request):
        request["payload"]["record"] = copy.deepcopy(
            request["payload"]["record"])
        _fail("divergent_canonicalization")


class _OrderMutatingReplayEngine(WalEngine):
    """Mutant: pops and reinserts a log entry's first field -
    values unchanged, insertion order diverges."""

    def replay(self, log):
        entry = log[0]
        entry["entry_id"] = entry.pop("entry_id")
        _fail("corrupt_chain")


def test_rollback_atomicity_kills_mutants():
    """The atomicity assertions are NOT vacuous. Every mutant
    below raises the pinned failure class after corrupting the
    SUPPLIED objects; for each, the test proves the corruption
    is observable at the claimed layer (encoded bytes or alias
    topology) and that _assert_work_atomic KILLS it:
    - value/deep mutations: junk appended to the supplied log,
      the supplied request cleared, the replay log cleared, a
      nested record field rewritten;
    - order mutations: a request field popped and reinserted
      (== still holds, encoded bytes diverge), a replay log
      entry's first field popped and reinserted;
    - alias mutation: the request's record replaced by an
      EQUAL deepcopy while it aliases a log entry's record -
      per-root bytes stay identical, only the joint alias
      topology diverges."""
    append_case = _case(
        "rollback",
        "rejected-append-raising-oracle-then-valid-append")

    # -- value mutants over the append working inputs
    for mutant in (_LogMutatingAppendEngine,
                   _RequestMutatingAppendEngine,
                   _DeepMutatingAppendEngine):
        work_log = copy.deepcopy(append_case["log"])
        work_req = copy.deepcopy(append_case["request"])
        snap = _snapshot_work(work_log, work_req)
        with pytest.raises(WalError) as exc:
            mutant(canonical_payload).append(work_log, work_req)
        assert exc.value.failure_class == \
            append_case["expect_failure"]
        # the mutation really is observable in the encoded
        # bytes - otherwise this kill-test would be vacuous
        assert (_encoded_bytes(work_log) != snap[0][0]
                or _encoded_bytes(work_req) != snap[0][1])
        with pytest.raises(AssertionError):
            _assert_work_atomic(snap, work_log, work_req)

    # -- order mutant: == survives, only the bytes diverge
    work_log = copy.deepcopy(append_case["log"])
    work_req = copy.deepcopy(append_case["request"])
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(WalError) as exc:
        _OrderMutatingAppendEngine(
            canonical_payload).append(work_log, work_req)
    assert exc.value.failure_class == \
        append_case["expect_failure"]
    assert work_req == append_case["request"]  # == is blind
    assert _encoded_bytes(work_req) != snap[0][1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # -- alias mutant: per-root bytes survive, only the JOINT
    # alias topology diverges. The working inputs are seeded
    # with a deliberate cross-root alias (the request's record
    # IS a log entry's record); the mutant swaps the request
    # side for an equal deepcopy.
    work_log = copy.deepcopy(append_case["log"]) or [
        copy.deepcopy(entry) for entry in
        _case("rollback",
              "rejected-replay-tampered-chain-then-valid-"
              "replay")["then_log"][:1]]
    work_req = copy.deepcopy(append_case["request"])
    work_req["payload"]["record"] = \
        work_log[0]["payload"]["record"]
    snap = _snapshot_work(work_log, work_req)
    with pytest.raises(WalError) as exc:
        _AliasBreakingAppendEngine(
            canonical_payload).append(work_log, work_req)
    assert exc.value.failure_class == \
        append_case["expect_failure"]
    # bytes alone CANNOT see it - the witness is the topology
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _encoded_bytes(work_req) == snap[0][1]
    assert _graph_signature((work_log, work_req)) != snap[1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # -- replay mutants: clear (value) and field reorder
    # (bytes only)
    replay_case = _case(
        "rollback",
        "rejected-replay-tampered-chain-then-valid-replay")
    work_log = copy.deepcopy(replay_case["log"])
    snap = _snapshot_work(work_log)
    with pytest.raises(WalError) as exc:
        _LogMutatingReplayEngine(
            canonical_payload).replay(work_log)
    assert exc.value.failure_class == \
        replay_case["expect_failure"]
    assert _encoded_bytes(work_log) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log)
    work_log = copy.deepcopy(replay_case["log"])
    snap = _snapshot_work(work_log)
    with pytest.raises(WalError) as exc:
        _OrderMutatingReplayEngine(
            canonical_payload).replay(work_log)
    assert exc.value.failure_class == \
        replay_case["expect_failure"]
    assert work_log == replay_case["log"]  # == is blind
    assert _encoded_bytes(work_log) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log)


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
    append_case = _case(
        "rollback",
        "rejected-append-raising-oracle-then-valid-append")

    # -- equal unaliased deepcopy replacement
    work_log = copy.deepcopy(append_case["log"])
    work_req = copy.deepcopy(append_case["request"])
    snap = _snapshot_work(work_log, work_req)
    original_payload = work_req["payload"]
    work_req["payload"] = copy.deepcopy(work_req["payload"])
    # witness: bytes AND topology unchanged, identity replaced
    assert _encoded_bytes(work_req) == snap[0][1]
    assert _graph_signature((work_log, work_req)) == snap[1]
    assert work_req["payload"] is not original_payload
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # -- equal-child swap inside a list
    work_log = [copy.deepcopy(append_case["request"]),
                copy.deepcopy(append_case["request"])]
    assert work_log[0] is not work_log[1]
    snap = _snapshot_work(work_log)
    work_log[0], work_log[1] = work_log[1], work_log[0]
    # witness: bytes and topology unchanged, positions swapped
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _graph_signature((work_log,)) == snap[1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log)

    # -- exact dict replaced by an equal subclass
    work_log = copy.deepcopy(append_case["log"])
    work_req = copy.deepcopy(append_case["request"])
    snap = _snapshot_work(work_log, work_req)
    work_req["payload"] = _PayloadDict(work_req["payload"])
    # witness: bytes unchanged, the exact builtin TYPE diverged
    assert _encoded_bytes(work_req) == snap[0][1]
    assert type(work_req["payload"]) is not dict
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log, work_req)

    # -- hostile traversal/encoding hooks surface as
    # AssertionError with the original as cause, never raw
    for hooked in (_HookedDict({"a": 1}),
                   _HookedList([1])):
        work_log = copy.deepcopy(append_case["log"])
        work_req = copy.deepcopy(append_case["request"])
        snap = _snapshot_work(work_log, work_req)
        work_req["payload"]["record"] = hooked
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

    work_log = copy.deepcopy(append_case["log"])
    work_req = copy.deepcopy(append_case["request"])
    snap = _snapshot_work(work_log, work_req)
    work_req["payload"]["record"] = _SystemExitDict(
        work_req["payload"]["record"])
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
    a, b = m["malformed"][0], m["malformed"][2]
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _swap_oracle(m, section):
    a, b = m["malformed"][8], m["malformed"][9]
    a["oracle"], b["oracle"] = b["oracle"], a["oracle"]


def _swap_repair(m, section):
    a, b = m["malformed"][0], m["malformed"][3]
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
    uniquely-named VALID single-append copy, every boundary row
    by a VALID empty-script copy, every rollback row by a VALID
    rejected-append copy - must fail structure validation
    BEFORE execution."""
    m = copy.deepcopy(CASES)
    single = copy.deepcopy(
        _case("happy", "append-single-put-after-e4"))
    empty = copy.deepcopy(
        _case("boundary", "replay-empty-log"))
    rejected = copy.deepcopy(
        _case("rollback",
              "rejected-append-raising-oracle-then-valid-append"))
    m["happy"] = [dict(copy.deepcopy(single),
                       name=f"single-copy-{i}")
                  for i in range(len(HAPPY_MANIFEST))]
    m["boundary"] = [dict(copy.deepcopy(empty),
                          name=f"empty-copy-{i}")
                     for i in range(len(BOUNDARY_MANIFEST))]
    m["rollback"] = [dict(copy.deepcopy(rejected),
                          name=f"rejected-copy-{i}")
                     for i in range(len(ROLLBACK_MANIFEST))]
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_mutant_intra_failure_class_malformed_substitution():
    """Malformed rows substituted WITHIN one failure class (all
    classes and repair forms still represented) must fail the
    manifest - the pinned defect and metadata discriminate."""
    m = copy.deepcopy(CASES)
    donor = copy.deepcopy(
        _case("malformed", "request-missing-op-field"))
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


def test_mutant_payload_substitution_retains_target_label():
    """The payload-only attack: the donor's EXECUTABLE payload
    (request + log) under the recipient's COMPLETE manifest
    label - target name, failure class, oracle, repair form,
    defect prose AND scenario tag retained - must fail structure
    validation BEFORE execution, because the payload does not
    realize the recipient's closed scenario tag. Covers every
    same (class, oracle, repair) pair."""
    rows = CASES["malformed"]
    for i, recipient in enumerate(rows):
        for j, donor in enumerate(rows):
            if i == j:
                continue
            m = copy.deepcopy(CASES)
            row = copy.deepcopy(recipient)
            if "request" in donor:
                row["request"] = copy.deepcopy(donor["request"])
            else:
                row.pop("request", None)
            row["log"] = copy.deepcopy(donor["log"])
            m["malformed"][i] = row
            try:
                _validate_structure(m)
            except AssertionError:
                continue
            raise AssertionError(
                f"payload {donor['name']!r} under label "
                f"{recipient['name']!r} passed undetected")
