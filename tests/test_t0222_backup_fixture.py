"""T0222: backup conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0221 backup contract. The cases execute against the
contract-derived reference in tests.test_t0221_backup_contract
(itself fully derived from data/contracts/backup.yaml plus the
linked WAL, migration, transposition-node, variant,
position-digest and FEN contracts) - nothing is re-implemented
here. Pinned receipts in the fixture were computed from that
reference at authoring time, so any contract or derivation
drift breaks this battery. Every malformed case is
discriminating (repairing ONLY its declared defect locus makes
the case valid) and rollback cases prove a rejected
backup/verify leaves every input bit-identical.

DESIGN CAUTION: the reference engine is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work
must execute these same cases against a separately implemented
runtime."""

from __future__ import annotations

import copy
import hashlib
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
    state_id,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    WalEngine,
    _op_spec,
    canonical_payload,
)
from tests.test_t0221_backup_contract import (  # noqa: E402
    BackupEngine,
    BackupError,
    _fail,
    serialize_bundle,
)
from tools.backup_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    RECORD,
)
from tools.wal_contract_lint import (  # noqa: E402
    CONTRACT as WAL_CONTRACT,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "backup"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_WC = yaml.safe_load(WAL_CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
_BACKUP_RE = re.compile(
    _CC["identifiers"]["backup_id"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["head"]["grammar"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])
_ENTRY_RE = re.compile(
    _WC["identifiers"]["entry_id"]["grammar"])
_PRIOR_RE = re.compile(
    _WC["identifiers"]["prior_entry_id"]["grammar"])
RECEIPT_FIELDS = set(RECORD["fields"])
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}
PAYLOAD_FIELDS = {"identity", "record"}
ENTRY_FIELDS = {"entry_id", "sequence", "op", "payload",
                "prior_entry_id"}


def _raising_serializer(state):
    raise ValueError("untrusted serializer failure")


def _non_str_serializer(state):
    return []


def _surrogate_serializer(state):
    return "\ud800"


ORACLES = {"honest": serialize_bundle,
           "raising": _raising_serializer,
           "non-str": _non_str_serializer,
           "lone-surrogate": _surrogate_serializer}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
HAPPY_KEYS = {"name", "kind", "oracle", "log", "expect"}
MALFORMED_BACKUP_KEYS = {"name", "kind", "oracle", "log",
                         "expect_failure", "defect", "scenario",
                         "minimal_repair"}
MALFORMED_VERIFY_KEYS = {"name", "kind", "oracle", "receipt",
                         "expect_failure", "defect", "scenario",
                         "minimal_repair"}
ROLLBACK_BACKUP_KEYS = {"name", "kind", "oracle", "log",
                        "expect_failure", "then_oracle",
                        "then_log", "expect"}
ROLLBACK_VERIFY_KEYS = {"name", "kind", "oracle", "receipt",
                        "expect_failure", "then_oracle",
                        "then_receipt", "expect"}
REPAIR_FORMS = {"set_receipt_field", "replace_receipt",
                "replace_log", "set_oracle"}

# -- the closed scenario manifests -------------------------------------------
# Every fixture section's actual {name: metadata} map must equal
# its manifest EXACTLY - no missing, substituted, duplicated,
# renamed or extra rows, and every advertised semantic shape is
# asserted before execution.
MBR = "malformed_backup_record"
CS = "corrupt_source"
DS = "divergent_snapshot"
DB = "divergent_backup"

# happy/boundary: name -> (oracle, log cardinality)
HAPPY_MANIFEST = {
    "backup-put-put-delete-chain": ("honest", 3),
    "backup-single-put-after-e4": ("honest", 1),
    "backup-put-delete-put": ("honest", 3),
}
BOUNDARY_MANIFEST = {
    "backup-empty-log": ("honest", 0),
    "backup-delete-missing-identity-noop": ("honest", 1),
    "backup-delete-last-record-empty-state": ("honest", 2),
}
# malformed: name -> (failure class, oracle, repair form,
# pinned defect description, CLOSED SCENARIO TAG)
MALFORMED_MANIFEST = {
    "source-log-not-a-list": (MBR, "honest", "replace_log",
        "the source log is not a list",
        "non-list-source-log"),
    "source-sequence-gap": (CS, "honest", "replace_log",
        "an entry sequence skips the exact 1-based position",
        "sequence-gap"),
    "source-broken-prior-link": (CS, "honest", "replace_log",
        "an entry's prior link does not match the previous "
        "tip", "broken-prior-link"),
    "source-tampered-entry-id": (CS, "honest", "replace_log",
        "an entry id diverges from the recomputed chain",
        "tampered-entry-id"),
    "source-unknown-op": (CS, "honest", "replace_log",
        "an entry op is not a registered operation",
        "unregistered-op"),
    "oracle-raises": (DS, "raising", "set_oracle",
        "the bundle serializer raises during backup",
        "oracle-raises"),
    "oracle-non-str-output": (DS, "non-str", "set_oracle",
        "the bundle serializer returns a non-string",
        "oracle-non-str-output"),
    "oracle-lone-surrogate": (DS, "lone-surrogate", "set_oracle",
        "the bundle serializer returns a non-UTF-8-encodable "
        "string", "oracle-lone-surrogate"),
    "receipt-missing-field": (MBR, "honest", "replace_receipt",
        "the receipt is missing the bundle field",
        "missing-receipt-field"),
    "receipt-extra-field": (MBR, "honest", "replace_receipt",
        "the receipt carries an undeclared extra field",
        "extra-receipt-field"),
    "receipt-bad-backup-id-grammar": (MBR, "honest",
        "set_receipt_field",
        "the backup id fails the backup-id grammar",
        "bad-backup-id-grammar"),
    "receipt-non-int-entry-count": (MBR, "honest",
        "set_receipt_field",
        "the entry count is not an integer",
        "non-int-entry-count"),
    "receipt-unencodable-bundle": (MBR, "honest",
        "set_receipt_field",
        "the bundle is not UTF-8 encodable",
        "unencodable-receipt-field"),
    "receipt-wrong-backup-id": (DB, "honest",
        "set_receipt_field",
        "the backup id is well-formed but diverges from the "
        "recomputed one", "wrong-backup-id"),
    "receipt-head-count-inconsistent": (DB, "honest",
        "set_receipt_field",
        "a non-empty backup pins the genesis head",
        "genesis-head-nonzero-count"),
    "receipt-forged-empty-state": (DB, "honest",
        "set_receipt_field",
        "an empty backup pins a forged non-empty state id",
        "forged-empty-state-id"),
}
# rollback: name -> (failure class, initial oracle, follow-up
# oracle)
ROLLBACK_MANIFEST = {
    "rejected-backup-raising-serializer-then-valid-backup": (
        DS, "raising", "honest"),
    "rejected-backup-corrupt-source-then-valid-backup": (
        CS, "honest", "honest"),
    "rejected-verify-forged-receipt-then-valid-verify": (
        DB, "honest", "honest"),
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


def _validate_receipt_shape(receipt, label):
    """The exact normative five-field backup receipt: exact
    types, pinned grammars, entry_count a non-negative exact
    int (never bool), bundle an exact string."""
    assert isinstance(receipt, dict), label
    assert set(receipt) == RECEIPT_FIELDS, label
    assert type(receipt["backup_id"]) is str and \
        _BACKUP_RE.fullmatch(receipt["backup_id"]), label
    assert type(receipt["head"]) is str and \
        _HEAD_RE.fullmatch(receipt["head"]), label
    assert type(receipt["state_id"]) is str and \
        _STATE_RE.fullmatch(receipt["state_id"]), label
    assert type(receipt["entry_count"]) is int and \
        receipt["entry_count"] >= 0, label
    assert type(receipt["bundle"]) is str, label


def _repaired(case):
    """Apply the declarative minimal repair: it touches ONLY the
    declared locus, everything else byte-identical."""
    rep = case["minimal_repair"]
    assert set(rep) <= REPAIR_FORMS
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure", "scenario",
                        "minimal_repair")}
    if "set_receipt_field" in rep:
        form = rep["set_receipt_field"]
        out["receipt"][form["field"]] = form["value"]
    elif "replace_receipt" in rep:
        out["receipt"] = copy.deepcopy(
            rep["replace_receipt"]["receipt"])
    elif "replace_log" in rep:
        out["log"] = copy.deepcopy(
            rep["replace_log"]["log"])
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of four
    closed forms, never mixed:
    - set_receipt_field: exactly {"field", "value"}; field a
      declared receipt field; value type-correct for it;
    - replace_receipt: exactly {"receipt"}; a shape-valid
      receipt;
    - replace_log: exactly {"log"}; a shape-valid WAL log;
    - set_oracle: exactly {"oracle"}; a declared oracle
      selector."""
    rep = case["minimal_repair"]
    assert len(rep) == 1 and set(rep) <= REPAIR_FORMS, \
        case["name"]
    form = next(iter(rep.values()))
    if "set_receipt_field" in rep:
        assert set(form) == {"field", "value"}, case["name"]
        assert form["field"] in RECEIPT_FIELDS, case["name"]
        if form["field"] == "entry_count":
            assert type(form["value"]) is int, case["name"]
        else:
            assert type(form["value"]) is str, case["name"]
    elif "replace_receipt" in rep:
        assert set(form) == {"receipt"}, case["name"]
        _validate_receipt_shape(form["receipt"], case["name"])
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


def _backup_succeeds(oracle, log):
    """The reference engine accepts the backup end to end under
    the given serializer."""
    try:
        BackupEngine(ORACLES[oracle]).backup(copy.deepcopy(log))
    except BackupError:
        return False
    return True


def _verify_succeeds(receipt):
    try:
        BackupEngine(serialize_bundle).verify(
            copy.deepcopy(receipt))
    except BackupError:
        return False
    return True


def _replay_succeeds(log):
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except Exception:
        return False
    return True


def _derive_backup_id(head, sid, count, bundle):
    return "bck1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{bundle}".encode()
    ).hexdigest()


def _diff_fields(original, repaired, label):
    """The two same-shaped mappings differ at exactly the
    returned field set."""
    assert set(original) == set(repaired), label
    return {k for k in original if original[k] != repaired[k]}


def _check_malformed_scenario(case, tag):
    name = case["name"]
    rep = _repaired(case)
    if tag in _ORACLE_TAG_SELECTOR:
        log = case["log"]
        assert case["oracle"] == _ORACLE_TAG_SELECTOR[tag], name
        assert len(log) == _ORACLE_LOG_CARDINALITY[tag], name
        _validate_log_shape(log, name)
        # the source log is fully well-formed: the ONLY defect
        # is the serializer itself - the honest serializer
        # succeeds
        assert _backup_succeeds("honest", log), name
        form = case["minimal_repair"]["set_oracle"]
        assert form["oracle"] == "honest", name
        assert rep["log"] == log, name
        return
    if tag in ("sequence-gap", "broken-prior-link",
               "tampered-entry-id", "unregistered-op"):
        # source-log defects: the repair log is a VALID log and
        # the original differs from it at exactly ONE entry and
        # exactly ONE field - the declared locus.
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
        if tag == "sequence-gap":
            assert field == "sequence", name
            bad = original[pos]["sequence"]
            assert type(bad) is int, name
            assert bad > pos + 1, name
        elif tag == "broken-prior-link":
            assert field == "prior_entry_id", name
            bad = original[pos]["prior_entry_id"]
            assert type(bad) is str and \
                _PRIOR_RE.fullmatch(bad), name
            prior_tip = (GENESIS if pos == 0 else
                         original[pos - 1]["entry_id"])
            assert bad != prior_tip, name
        elif tag == "tampered-entry-id":
            assert field == "entry_id", name
            bad = original[pos]["entry_id"]
            assert type(bad) is str and \
                _ENTRY_RE.fullmatch(bad), name
            assert bad != repaired_log[pos]["entry_id"], name
        else:
            assert field == "op", name
            bad = original[pos]["op"]
            assert type(bad) is str, name
            assert _op_spec(bad) is None, name
            assert _op_spec(repaired_log[pos]["op"]) is not \
                None, name
        return
    if tag == "non-list-source-log":
        assert not isinstance(case["log"], list), name
        # the repair supplies EXACTLY a valid log, oracle
        # untouched
        assert rep["oracle"] == case["oracle"], name
        assert _backup_succeeds(rep["oracle"], rep["log"]), name
        return
    # verify-kind receipt defects
    rec = case["receipt"]
    rep_rec = rep["receipt"]
    if tag == "missing-receipt-field":
        missing = RECEIPT_FIELDS - set(rec)
        assert len(missing) == 1, name
        # the repair adds EXACTLY the missing field, nothing
        # else
        assert set(rep_rec) == RECEIPT_FIELDS, name
        for key in set(rec):
            assert rep_rec[key] == rec[key], name
        assert _verify_succeeds(rep_rec), name
    elif tag == "extra-receipt-field":
        extra = set(rec) - RECEIPT_FIELDS
        assert len(extra) == 1, name
        assert set(rec) >= RECEIPT_FIELDS, name
        projected = {k: rec[k] for k in RECEIPT_FIELDS}
        # the repair removes EXACTLY the extra key, nothing else
        assert rep_rec == projected, name
        assert _verify_succeeds(rep_rec), name
    elif tag == "bad-backup-id-grammar":
        assert set(rec) == RECEIPT_FIELDS, name
        bad = rec["backup_id"]
        assert type(bad) is str, name
        assert _BACKUP_RE.fullmatch(bad) is None, name
        # the repair corrects EXACTLY the backup id
        assert _diff_fields(rec, rep_rec, name) == \
            {"backup_id"}, name
        assert _verify_succeeds(rep_rec), name
    elif tag == "non-int-entry-count":
        assert set(rec) == RECEIPT_FIELDS, name
        assert type(rec["entry_count"]) is not int, name
        assert _diff_fields(rec, rep_rec, name) == \
            {"entry_count"}, name
        assert type(rep_rec["entry_count"]) is int, name
        assert _verify_succeeds(rep_rec), name
    elif tag == "unencodable-receipt-field":
        assert set(rec) == RECEIPT_FIELDS, name
        bad = rec["bundle"]
        assert type(bad) is str, name
        try:
            bad.encode("utf-8")
            raise AssertionError(name)
        except UnicodeEncodeError:
            pass
        assert _diff_fields(rec, rep_rec, name) == \
            {"bundle"}, name
        assert _verify_succeeds(rep_rec), name
    elif tag == "wrong-backup-id":
        assert set(rec) == RECEIPT_FIELDS, name
        bad = rec["backup_id"]
        assert type(bad) is str and \
            _BACKUP_RE.fullmatch(bad), name
        derived = _derive_backup_id(rec["head"], rec["state_id"],
                                    rec["entry_count"],
                                    rec["bundle"])
        assert bad != derived, name
        # the repair sets EXACTLY the derived id
        assert _diff_fields(rec, rep_rec, name) == \
            {"backup_id"}, name
        assert rep_rec["backup_id"] == derived, name
        assert _verify_succeeds(rep_rec), name
    elif tag == "genesis-head-nonzero-count":
        assert set(rec) == RECEIPT_FIELDS, name
        assert type(rec["entry_count"]) is int and \
            rec["entry_count"] > 0, name
        assert rec["head"] == GENESIS, name
        # the repair sets EXACTLY the real head
        assert _diff_fields(rec, rep_rec, name) == {"head"}, \
            name
        assert rep_rec["head"] != GENESIS, name
        assert _verify_succeeds(rep_rec), name
    elif tag == "forged-empty-state-id":
        assert set(rec) == RECEIPT_FIELDS, name
        assert rec["entry_count"] == 0, name
        assert rec["head"] == GENESIS, name
        bad = rec["state_id"]
        assert type(bad) is str and \
            _STATE_RE.fullmatch(bad), name
        assert bad != state_id({}), name
        # the repair sets EXACTLY the empty-state id
        assert _diff_fields(rec, rep_rec, name) == \
            {"state_id"}, name
        assert rep_rec["state_id"] == state_id({}), name
        assert _verify_succeeds(rep_rec), name
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


def _assert_cardinalities(case, n_entries):
    """The advertised log cardinality is REALLY present, and the
    pinned receipt realizes exactly that cardinality - a
    substituted row can never satisfy the wrong count."""
    name = case["name"]
    assert len(case["log"]) == n_entries, name
    assert case["expect"]["entry_count"] == n_entries, name


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
                oracle, n_entries = want
                assert case["oracle"] == oracle, case["name"]
                _assert_cardinalities(case, n_entries)
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
                assert case["kind"] in ("backup", "verify"), \
                    case["name"]
                assert case["oracle"] in ORACLES and \
                    case["then_oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                if case["kind"] == "backup":
                    assert set(case) == ROLLBACK_BACKUP_KEYS, \
                        case["name"]
                    _validate_receipt_shape(case["expect"],
                                            case["name"])
                else:
                    assert set(case) == ROLLBACK_VERIFY_KEYS, \
                        case["name"]
                    _validate_receipt_shape(case["expect"],
                                            case["name"])
                continue
            if section == "malformed":
                assert case["kind"] in ("backup", "verify"), \
                    case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert isinstance(case["defect"], str) and \
                    case["defect"], case["name"]
                _validate_repair(case)
                # case-owned log/receipt payloads are
                # intentionally shape-defective (that is the
                # defect); validate key-set only, never shape.
                if case["kind"] == "backup":
                    assert set(case) == MALFORMED_BACKUP_KEYS, \
                        case["name"]
                else:
                    assert set(case) == MALFORMED_VERIFY_KEYS, \
                        case["name"]
            else:
                assert case["kind"] == "backup", case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert set(case) == HAPPY_KEYS, case["name"]
                _validate_log_shape(case["log"], case["name"])
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
    """Every repair touches ONLY its declared locus:
    set_receipt_field changes exactly one receipt field;
    replace_receipt / replace_log / set_oracle change exactly
    that one component with every other component
    byte-identical."""
    for case in CASES["malformed"]:
        rep = _repaired(case)
        form_name = next(iter(case["minimal_repair"]))
        if form_name == "set_receipt_field":
            field = case["minimal_repair"]["set_receipt_field"][
                "field"]
            for key in RECEIPT_FIELDS - {field}:
                assert rep["receipt"][key] == \
                    case["receipt"][key], case["name"]
            assert rep["oracle"] == case["oracle"], case["name"]
        elif form_name == "replace_receipt" or form_name == "replace_log":
            assert rep["oracle"] == case["oracle"], case["name"]
        else:
            assert rep["log"] == case["log"], case["name"]


def _run_backup(oracle_name, log, expect):
    """Backup the pinned log; the receipt equals the pinned
    value, it verifies LOCALly, a second run is byte-identical
    and the log is never mutated."""
    engine = BackupEngine(ORACLES[oracle_name])
    log_p = copy.deepcopy(log)
    receipt = engine.backup(log)
    assert receipt == expect
    assert engine.verify(copy.deepcopy(receipt)) == receipt
    again = BackupEngine(ORACLES[oracle_name]).backup(
        copy.deepcopy(log_p))
    assert again == receipt
    assert log == log_p


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        _run_backup(case["oracle"], case["log"], case["expect"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        _run_backup(case["oracle"], case["log"], case["expect"])


def _exec_malformed(case):
    """The ORIGINAL input rejects with the pinned failure class
    and leaves every input byte-identical."""
    engine = BackupEngine(ORACLES[case["oracle"]])
    try:
        if case["kind"] == "backup":
            log_p = copy.deepcopy(case["log"])
            engine.backup(case["log"])
        else:
            log_p = None
            receipt_p = copy.deepcopy(case["receipt"])
            engine.verify(case["receipt"])
    except BackupError as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.code in ERROR_ENUM
        if log_p is not None:
            assert case["log"] == log_p
        else:
            assert case["receipt"] == receipt_p
        return
    raise AssertionError("input unexpectedly accepted")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    engine = BackupEngine(ORACLES[out["oracle"]])
    if case["kind"] == "backup":
        result = engine.backup(out["log"])
        _validate_receipt_shape(result, case["name"])
    else:
        result = engine.verify(out["receipt"])
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
    engine = BackupEngine(ORACLES[valid["oracle"]])
    if original["kind"] == "backup":
        engine.backup(copy.deepcopy(valid["log"]))
    else:
        engine.verify(copy.deepcopy(valid["receipt"]))
    with pytest.raises(AssertionError):
        _exec_malformed(dict(original, **valid))


def _repair_mutation_cases():
    """Every repair-form mutation the tagged union must reject
    STRUCTURALLY, before any execution."""
    # the good control is a row's OWN minimal repair: it proves
    # the mutation-application machinery itself is not what
    # breaks validation (any semantically DIFFERENT repair
    # payload now fails the repair-locus assertions).
    row = next(c for c in CASES["malformed"]
               if "replace_receipt" in c["minimal_repair"])
    good_rec = copy.deepcopy(
        row["minimal_repair"]["replace_receipt"]["receipt"])
    good_rep = CASES["malformed"][0]["minimal_repair"]
    return [
        ("empty-repair", {}),
        ("mixed-forms",
         {"set_oracle": {"oracle": "honest"},
          "set_receipt_field": {"field": "head",
                                "value": GENESIS}}),
        ("unknown-form", {"wat": {"x": 1}}),
        ("set_field-extra-key",
         {"set_receipt_field": {"field": "head",
                                "value": GENESIS, "wat": 1}}),
        ("set_field-bad-field",
         {"set_receipt_field": {"field": "label",
                                "value": GENESIS}}),
        ("set_field-value-wrong-type",
         {"set_receipt_field": {"field": "entry_count",
                                "value": "2"}}),
        ("replace_receipt-extra-key",
         {"replace_receipt": {"receipt": good_rec, "wat": 1}}),
        ("replace_receipt-bad-shape",
         {"replace_receipt": {"receipt": {"wat": 1}}}),
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
    """A rejected backup/verify leaves the EXACT object handed
    to it byte-identical (named working input, snapshotted,
    passed by identity and compared after the call - never a
    throwaway copy), and the subsequent valid follow-up returns
    the pinned receipt."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        engine = BackupEngine(ORACLES[case["oracle"]])
        if case["kind"] == "backup":
            work_log = copy.deepcopy(case["log"])
            snap = _snapshot_work(work_log)
            with pytest.raises(BackupError) as exc:
                engine.backup(work_log)
            _assert_work_atomic(snap, work_log)
        else:
            work_receipt = copy.deepcopy(case["receipt"])
            snap = _snapshot_work(work_receipt)
            with pytest.raises(BackupError) as exc:
                engine.verify(work_receipt)
            _assert_work_atomic(snap, work_receipt)
        assert exc.value.failure_class == \
            case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        follow = BackupEngine(ORACLES[case["then_oracle"]])
        if case["kind"] == "backup":
            result = follow.backup(
                copy.deepcopy(case["then_log"]))
        else:
            result = follow.verify(
                copy.deepcopy(case["then_receipt"]))
        assert result == case["expect"]


class _LogMutatingBackupEngine(BackupEngine):
    """Mutant: corrupts the SUPPLIED source log before raising
    the pinned rejection."""

    def backup(self, log):
        log.append({"junk": True})
        _fail("corrupt_source")


class _ReceiptMutatingVerifyEngine(BackupEngine):
    """Mutant: corrupts the SUPPLIED receipt before raising the
    pinned rejection."""

    def verify(self, receipt):
        receipt.clear()
        _fail("divergent_backup")


class _DeepMutatingBackupEngine(BackupEngine):
    """Mutant: rewrites a NESTED record field of the supplied
    source log before raising the pinned rejection."""

    def backup(self, log):
        log[0]["payload"]["record"]["digest"] = \
            "pdv1:" + "0" * 64
        _fail("corrupt_source")


class _OrderMutatingVerifyEngine(BackupEngine):
    """Mutant: pops and reinserts a receipt field - values
    unchanged, insertion order diverges."""

    def verify(self, receipt):
        receipt["head"] = receipt.pop("head")
        _fail("divergent_backup")


class _AliasBreakingBackupEngine(BackupEngine):
    """Mutant: replaces a nested log object with an EQUAL
    deepcopy - values and bytes unchanged, alias topology
    diverges."""

    def backup(self, log):
        log[1]["payload"] = copy.deepcopy(log[1]["payload"])
        _fail("corrupt_source")


def test_rollback_atomicity_kills_mutants():
    """The atomicity assertions are NOT vacuous. Every mutant
    below raises the pinned failure class after corrupting the
    SUPPLIED object; for each, the test proves the corruption
    is observable at the claimed layer (encoded bytes or alias
    topology) and that _assert_work_atomic KILLS it:
    - value/deep mutations: junk appended to the supplied
      source log, the supplied receipt cleared, a nested log
      record field rewritten;
    - order mutation: a receipt field popped and reinserted
      (== still holds, encoded bytes diverge);
    - alias mutation: a log entry's payload replaced by an
      EQUAL deepcopy while it aliases another entry's payload -
      bytes stay identical, only the joint alias topology
      diverges."""
    backup_case = _case(
        "rollback",
        "rejected-backup-raising-serializer-then-valid-backup")

    # -- value mutants over the backup working log
    for mutant in (_LogMutatingBackupEngine,
                   _DeepMutatingBackupEngine):
        work_log = copy.deepcopy(backup_case["log"])
        snap = _snapshot_work(work_log)
        with pytest.raises(BackupError) as exc:
            mutant(serialize_bundle).backup(work_log)
        assert exc.value.failure_class == "corrupt_source"
        # the mutation really is observable in the encoded
        # bytes - otherwise this kill-test would be vacuous
        assert _encoded_bytes(work_log) != snap[0][0]
        with pytest.raises(AssertionError):
            _assert_work_atomic(snap, work_log)

    # -- receipt mutants: clear (value) and field reorder
    # (== blind, bytes diverge)
    verify_case = _case(
        "rollback",
        "rejected-verify-forged-receipt-then-valid-verify")
    work_receipt = copy.deepcopy(verify_case["receipt"])
    snap = _snapshot_work(work_receipt)
    with pytest.raises(BackupError) as exc:
        _ReceiptMutatingVerifyEngine(
            serialize_bundle).verify(work_receipt)
    assert exc.value.failure_class == "divergent_backup"
    assert _encoded_bytes(work_receipt) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_receipt)
    work_receipt = copy.deepcopy(verify_case["receipt"])
    snap = _snapshot_work(work_receipt)
    with pytest.raises(BackupError) as exc:
        _OrderMutatingVerifyEngine(
            serialize_bundle).verify(work_receipt)
    assert exc.value.failure_class == "divergent_backup"
    assert work_receipt == verify_case["receipt"]  # == is blind
    assert _encoded_bytes(work_receipt) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_receipt)

    # -- alias mutant: bytes survive, only the JOINT alias
    # topology diverges. A synthetic two-entry working log is
    # seeded with a deliberate cross-entry alias (the second
    # entry's payload IS the first's); the mutant swaps it for
    # an equal deepcopy.
    entry = copy.deepcopy(backup_case["log"][0])
    work_log = [entry, copy.deepcopy(entry)]
    work_log[1]["payload"] = work_log[0]["payload"]
    snap = _snapshot_work(work_log)
    with pytest.raises(BackupError) as exc:
        _AliasBreakingBackupEngine(
            serialize_bundle).backup(work_log)
    assert exc.value.failure_class == "corrupt_source"
    # bytes alone CANNOT see it - the witness is the topology
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _graph_signature((work_log,)) != snap[1]
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
    - equal unaliased deepcopy replacement of a nested log
      object (original identity replaced, every byte equal);
    - equal-child swap inside the log list (positions
      exchanged, every byte equal);
    - exact dict replaced by an equal dict SUBCLASS (runtime
      type diverges, bytes equal) - the fail-closed walk
      rejects it by exact type before any hook can run;
    - hostile subclasses whose hooks would raise
      KeyboardInterrupt / SystemExit / GeneratorExit - the
      rejection surfaces as AssertionError with a cause,
      never as the raw BaseException."""
    backup_case = _case(
        "rollback",
        "rejected-backup-raising-serializer-then-valid-backup")

    # -- equal unaliased deepcopy replacement
    work_log = copy.deepcopy(backup_case["log"])
    snap = _snapshot_work(work_log)
    original_payload = work_log[0]["payload"]
    work_log[0]["payload"] = copy.deepcopy(
        work_log[0]["payload"])
    # witness: bytes AND topology unchanged, identity replaced
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _graph_signature((work_log,)) == snap[1]
    assert work_log[0]["payload"] is not original_payload
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log)

    # -- equal-child swap inside a list
    work_log = [copy.deepcopy(backup_case["log"][0]),
                copy.deepcopy(backup_case["log"][0])]
    assert work_log[0] is not work_log[1]
    snap = _snapshot_work(work_log)
    work_log[0], work_log[1] = work_log[1], work_log[0]
    # witness: bytes and topology unchanged, positions swapped
    assert _encoded_bytes(work_log) == snap[0][0]
    assert _graph_signature((work_log,)) == snap[1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log)

    # -- exact dict replaced by an equal subclass
    work_log = copy.deepcopy(backup_case["log"])
    snap = _snapshot_work(work_log)
    work_log[0]["payload"] = _PayloadDict(
        work_log[0]["payload"])
    # witness: bytes unchanged, the exact builtin TYPE diverged
    assert _encoded_bytes(work_log) == snap[0][0]
    assert type(work_log[0]["payload"]) is not dict
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_log)

    # -- hostile traversal/encoding hooks surface as
    # AssertionError with the original as cause, never raw
    for hooked in (_HookedDict({"a": 1}),
                   _HookedList([1])):
        work_log = copy.deepcopy(backup_case["log"])
        snap = _snapshot_work(work_log)
        work_log[0]["payload"]["record"] = hooked
        try:
            _assert_work_atomic(snap, work_log)
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

    work_log = copy.deepcopy(backup_case["log"])
    snap = _snapshot_work(work_log)
    work_log[0]["payload"]["record"] = _SystemExitDict(
        work_log[0]["payload"]["record"])
    try:
        _assert_work_atomic(snap, work_log)
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
    a, b = m["malformed"][5], m["malformed"][6]
    a["oracle"], b["oracle"] = b["oracle"], a["oracle"]


def _swap_repair(m, section):
    a = next(c for c in m["malformed"]
             if "replace_receipt" in c["minimal_repair"])
    b = next(c for c in m["malformed"]
             if "set_receipt_field" in c["minimal_repair"])
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
        "malformed_backup_record", "corrupt_source",
        "divergent_snapshot", "divergent_backup"}
