"""T0231: restore conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0230 restore contract. The cases execute against the
contract-derived reference in tests.test_t0230_restore_contract
(itself fully derived from data/contracts/restore.yaml plus the
linked backup, WAL, migration, transposition-node, variant,
position-digest and FEN contracts) - nothing is re-implemented
here. Pinned restore receipts in the fixture were computed from
that reference at authoring time, so any contract or derivation
drift breaks this battery. Every malformed case is
discriminating (repairing ONLY its declared defect locus makes
the case valid) and rollback cases prove a rejected restore
leaves the input receipt bit-identical.

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

from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    KINGS,
    STARTPOS,
)
from tests.test_t0194_migration_contract import (  # noqa: E402
    state_id,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    _identity,
    _node,
)
from tests.test_t0221_backup_contract import (  # noqa: E402
    BackupEngine,
    BackupError,
    serialize_bundle,
)
from tests.test_t0230_restore_contract import (  # noqa: E402
    RestoreEngine,
    RestoreError,
    _fail,
    parse_bundle,
)
from tools.backup_contract_lint import (  # noqa: E402
    CONTRACT as BACKUP_CONTRACT,
)
from tools.restore_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    RECORD,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "restore"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_BC = yaml.safe_load(BACKUP_CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
_RESTORE_RE = re.compile(
    _CC["identifiers"]["restore_id"]["grammar"])
_BACKUP_RE = re.compile(
    _BC["identifiers"]["backup_id"]["grammar"])
_HEAD_RE = re.compile(_BC["identifiers"]["head"]["grammar"])
_STATE_RE = re.compile(_BC["identifiers"]["state_id"]["grammar"])
RESTORE_FIELDS = set(RECORD["fields"])
RECEIPT_FIELDS = {"backup_id", "head", "state_id", "entry_count",
                  "bundle"}
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}


def _raising_parser(bundle):
    raise ValueError("untrusted parser failure")


def _non_dict_parser(bundle):
    return []


def _wrong_content_parser(bundle):
    """A valid record filed under a DIFFERENT record's
    identity."""
    return {_identity(_node(STARTPOS)): _node(KINGS)}


def _key_mismatch_parser(bundle):
    """A valid record filed under another valid record's
    identity."""
    return {_identity(_node(KINGS)): _node(STARTPOS)}


def _divergent_state_parser(bundle):
    """A fully valid state whose id diverges from the
    receipt's."""
    rec = _node(KINGS)
    return {_identity(rec): rec}


ORACLES = {"honest": parse_bundle,
           "raising": _raising_parser,
           "non-dict": _non_dict_parser,
           "wrong-content": _wrong_content_parser,
           "key-mismatch": _key_mismatch_parser,
           "divergent-state": _divergent_state_parser}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
HAPPY_KEYS = {"name", "kind", "oracle", "receipt", "expect"}
MALFORMED_KEYS = {"name", "kind", "oracle", "receipt",
                  "expect_failure", "defect", "scenario",
                  "minimal_repair"}
ROLLBACK_KEYS = {"name", "kind", "oracle", "receipt",
                 "expect_failure", "then_oracle", "then_receipt",
                 "expect"}
REPAIR_FORMS = {"set_receipt_field", "replace_receipt",
                "set_oracle"}

# -- the closed scenario manifests -------------------------------------------
# Every fixture section's actual {name: metadata} map must equal
# its manifest EXACTLY - no missing, substituted, duplicated,
# renamed or extra rows, and every advertised semantic shape is
# asserted before execution.
MRR = "malformed_restore_record"
UB = "unverified_backup"
DP = "divergent_parse"
DS = "divergent_state"

# happy/boundary: name -> (oracle, receipt entry_count)
HAPPY_MANIFEST = {
    "restore-put-put-delete-chain": ("honest", 3),
    "restore-single-put-after-e4": ("honest", 1),
    "restore-put-delete-put": ("honest", 3),
}
BOUNDARY_MANIFEST = {
    "restore-empty-log-backup": ("honest", 0),
    "restore-delete-missing-identity-noop": ("honest", 1),
    "restore-delete-last-record-empty-state": ("honest", 2),
}
# malformed: name -> (failure class, oracle, repair form,
# pinned defect description, CLOSED SCENARIO TAG)
MALFORMED_MANIFEST = {
    "receipt-missing-field": (MRR, "honest", "replace_receipt",
        "the receipt is missing the bundle field",
        "missing-receipt-field"),
    "receipt-extra-field": (MRR, "honest", "replace_receipt",
        "the receipt carries an undeclared extra field",
        "extra-receipt-field"),
    "receipt-bad-backup-id-grammar": (MRR, "honest",
        "set_receipt_field",
        "the backup id fails the backup-id grammar",
        "bad-backup-id-grammar"),
    "receipt-non-int-entry-count": (MRR, "honest",
        "set_receipt_field",
        "the entry count is not an integer",
        "non-int-entry-count"),
    "receipt-non-str-bundle": (MRR, "honest",
        "set_receipt_field",
        "the bundle is not a string",
        "non-str-bundle"),
    "receipt-unencodable-bundle": (MRR, "honest",
        "set_receipt_field",
        "the bundle is not UTF-8 encodable",
        "unencodable-receipt-field"),
    "receipt-wrong-backup-id": (UB, "honest",
        "set_receipt_field",
        "the backup id is well-formed but diverges from the "
        "recomputed one", "wrong-backup-id"),
    "receipt-head-count-inconsistent": (UB, "honest",
        "set_receipt_field",
        "a non-empty backup pins the genesis head",
        "genesis-head-nonzero-count"),
    "oracle-raises": (DP, "raising", "set_oracle",
        "the bundle parser raises during restore",
        "oracle-raises"),
    "oracle-non-dict-output": (DP, "non-dict", "set_oracle",
        "the bundle parser returns a non-mapping",
        "oracle-non-dict-output"),
    "bundle-reordered-records": (DP, "honest",
        "replace_receipt",
        "the bundle decodes to the right state only through "
        "record reordering", "reordered-records"),
    "bundle-duplicate-identical-records": (DP, "honest",
        "replace_receipt",
        "the bundle decodes to the right state only through a "
        "duplicated record block",
        "duplicate-identical-records"),
    "bundle-trailing-blank-line": (DP, "honest",
        "replace_receipt",
        "the bundle carries non-canonical framing",
        "trailing-blank-line"),
    "bundle-duplicate-field-conflicting": (DS, "honest",
        "replace_receipt",
        "the bundle launders an invalid record through a "
        "conflicting duplicate field",
        "duplicate-field-conflicting"),
    "oracle-wrong-content": (DS, "wrong-content", "set_oracle",
        "the parser returns a valid record under a key that "
        "is not its identity", "parser-wrong-content"),
    "oracle-key-mismatch": (DS, "key-mismatch", "set_oracle",
        "the parser returns a record under another record's "
        "identity key", "parser-key-mismatch"),
    "oracle-divergent-state": (DS, "divergent-state",
        "set_oracle",
        "the parser returns a fully valid state whose id "
        "diverges from the receipt", "parser-state-divergence"),
}
# rollback: name -> (failure class, initial oracle, follow-up
# oracle)
ROLLBACK_MANIFEST = {
    "rejected-restore-raising-parser-then-valid-restore": (
        DP, "raising", "honest"),
    "rejected-restore-unverified-receipt-then-valid-restore": (
        UB, "honest", "honest"),
    "rejected-restore-divergent-state-then-valid-restore": (
        DS, "divergent-state", "honest"),
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


def _validate_receipt_shape(receipt, label):
    """The exact normative five-field BACKUP receipt (restore's
    input): exact types, pinned grammars, entry_count a
    non-negative exact int (never bool), bundle an exact
    string."""
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


def _validate_restore_shape(result, label):
    """The exact normative four-field restore record."""
    assert isinstance(result, dict), label
    assert set(result) == RESTORE_FIELDS, label
    assert type(result["restore_id"]) is str and \
        _RESTORE_RE.fullmatch(result["restore_id"]), label
    assert type(result["backup_id"]) is str and \
        _BACKUP_RE.fullmatch(result["backup_id"]), label
    assert type(result["state_id"]) is str and \
        _STATE_RE.fullmatch(result["state_id"]), label
    state = result["state"]
    assert isinstance(state, dict), label
    for key, rec in state.items():
        assert isinstance(key, str), label
        _validate_record_shape(rec, label)


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
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of three
    closed forms, never mixed:
    - set_receipt_field: exactly {"field", "value"}; field a
      declared receipt field; value type-correct for it;
    - replace_receipt: exactly {"receipt"}; a shape-valid
      backup receipt;
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
    "oracle-non-dict-output": "non-dict",
    "parser-wrong-content": "wrong-content",
    "parser-key-mismatch": "key-mismatch",
    "parser-state-divergence": "divergent-state"}
# oracle-tag rows are pairwise discriminated by the receipt's
# pinned entry_count
_ORACLE_RECEIPT_CARDINALITY = {
    "oracle-raises": 3,
    "oracle-non-dict-output": 2,
    "parser-wrong-content": 1,
    "parser-key-mismatch": 0,
    "parser-state-divergence": 4}


def _restore_succeeds(oracle, receipt):
    """The reference engine accepts the restore end to end under
    the given parser."""
    try:
        RestoreEngine(ORACLES[oracle]).restore(
            copy.deepcopy(receipt))
    except RestoreError:
        return False
    return True


def _backup_verifies(receipt):
    """The LINKED backup machinery accepts the receipt."""
    try:
        BackupEngine(serialize_bundle).verify(
            copy.deepcopy(receipt))
    except BackupError:
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
        receipt = case["receipt"]
        assert case["oracle"] == _ORACLE_TAG_SELECTOR[tag], name
        assert receipt["entry_count"] == \
            _ORACLE_RECEIPT_CARDINALITY[tag], name
        _validate_receipt_shape(receipt, name)
        # the receipt is fully well-formed and verifies: the
        # ONLY defect is the parser itself - the honest parser
        # succeeds
        assert _backup_verifies(receipt), name
        assert _restore_succeeds("honest", receipt), name
        form = case["minimal_repair"]["set_oracle"]
        assert form["oracle"] == "honest", name
        assert rep["receipt"] == receipt, name
        # the semantic locus of each parser defect is realized
        if tag == "oracle-raises":
            try:
                ORACLES[case["oracle"]](receipt["bundle"])
                raise AssertionError(name)
            except ValueError:
                return
        if tag == "oracle-non-dict-output":
            out = ORACLES[case["oracle"]](receipt["bundle"])
            assert type(out) is not dict, name
            return
        out = ORACLES[case["oracle"]](receipt["bundle"])
        if tag == "parser-wrong-content":
            assert type(out) is dict and len(out) == 1, name
            key, value = next(iter(out.items()))
            assert _identity(value) is not None, name
            assert key != _identity(value), name
        elif tag == "parser-key-mismatch":
            assert type(out) is dict and len(out) == 1, name
            key, value = next(iter(out.items()))
            assert key != _identity(value), name
        elif tag == "parser-state-divergence":
            assert type(out) is dict and out, name
            for key, value in out.items():
                assert key == _identity(value), name
            assert state_id(out) != receipt["state_id"], name
        return
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
        assert _restore_succeeds("honest", rep_rec), name
    elif tag == "extra-receipt-field":
        extra = set(rec) - RECEIPT_FIELDS
        assert len(extra) == 1, name
        assert set(rec) >= RECEIPT_FIELDS, name
        projected = {k: rec[k] for k in RECEIPT_FIELDS}
        # the repair removes EXACTLY the extra key, nothing else
        assert rep_rec == projected, name
        assert _restore_succeeds("honest", rep_rec), name
    elif tag == "bad-backup-id-grammar":
        assert set(rec) == RECEIPT_FIELDS, name
        bad = rec["backup_id"]
        assert type(bad) is str, name
        assert _BACKUP_RE.fullmatch(bad) is None, name
        # the repair corrects EXACTLY the backup id
        assert _diff_fields(rec, rep_rec, name) == \
            {"backup_id"}, name
        assert _restore_succeeds("honest", rep_rec), name
    elif tag == "non-int-entry-count":
        assert set(rec) == RECEIPT_FIELDS, name
        assert type(rec["entry_count"]) is not int, name
        assert _diff_fields(rec, rep_rec, name) == \
            {"entry_count"}, name
        assert type(rep_rec["entry_count"]) is int, name
        assert _restore_succeeds("honest", rep_rec), name
    elif tag == "non-str-bundle":
        assert set(rec) == RECEIPT_FIELDS, name
        assert type(rec["bundle"]) is not str, name
        assert _diff_fields(rec, rep_rec, name) == \
            {"bundle"}, name
        assert _restore_succeeds("honest", rep_rec), name
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
        assert _restore_succeeds("honest", rep_rec), name
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
        assert _restore_succeeds("honest", rep_rec), name
    elif tag == "genesis-head-nonzero-count":
        assert set(rec) == RECEIPT_FIELDS, name
        assert type(rec["entry_count"]) is int and \
            rec["entry_count"] > 0, name
        assert rec["head"] == GENESIS, name
        # the repair sets EXACTLY the real head
        assert _diff_fields(rec, rep_rec, name) == {"head"}, \
            name
        assert rep_rec["head"] != GENESIS, name
        assert _restore_succeeds("honest", rep_rec), name
    elif tag in ("reordered-records",
                 "duplicate-identical-records",
                 "trailing-blank-line",
                 "duplicate-field-conflicting"):
        # NON-CANONICAL BYTES: the original receipt is
        # grammatical AND passes LINKED backup verification
        # (self-consistent re-forge) - only restore's own guards
        # can reject it.
        _validate_receipt_shape(rec, name)
        assert _backup_verifies(rec), name
        assert rec["bundle"] != rep_rec["bundle"], name
        # the repair substitutes EXACTLY the canonical receipt
        assert _restore_succeeds("honest", rep_rec), name
        if tag in ("reordered-records",
                   "duplicate-identical-records"):
            # decodes to the SAME state as the canonical bundle
            parsed = parse_bundle(rec["bundle"])
            assert parsed == parse_bundle(rep_rec["bundle"]), \
                name
            # but is NOT the canonical serialization of it
            assert serialize_bundle(parsed) != rec["bundle"], \
                name
        elif tag == "trailing-blank-line":
            # non-canonical framing: the canonical bytes plus
            # one blank line; the honest parser cannot even
            # frame it (the failure surfaces through the
            # parser boundary)
            assert rec["bundle"] == \
                rep_rec["bundle"] + "\n", name
            try:
                parse_bundle(rec["bundle"])
                raise AssertionError(name)
            except (IndexError, ValueError):
                pass
        else:
            # a conflicting duplicate field launders an INVALID
            # record past the honest parser
            parsed = parse_bundle(rec["bundle"])
            key = next(iter(parsed))
            assert parsed[key]["digest"] == "0" * 64, name
            canonical = parse_bundle(rep_rec["bundle"])
            assert parsed[key]["digest"] != \
                canonical[key]["digest"], name
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
    """The advertised receipt cardinality is REALLY present, and
    the pinned restore record realizes exactly that cardinality
    - a substituted row can never satisfy the wrong count."""
    name = case["name"]
    assert case["receipt"]["entry_count"] == n_entries, name
    assert case["expect"]["backup_id"] == \
        case["receipt"]["backup_id"], name


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
                assert case["kind"] == "restore", case["name"]
                assert case["oracle"] in ORACLES and \
                    case["then_oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert set(case) == ROLLBACK_KEYS, case["name"]
                _validate_receipt_shape(case["then_receipt"],
                                        case["name"])
                _validate_restore_shape(case["expect"],
                                        case["name"])
                continue
            if section == "malformed":
                assert case["kind"] == "restore", case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert case["expect_failure"] in \
                    FAILURE_CLASSES, case["name"]
                assert isinstance(case["defect"], str) and \
                    case["defect"], case["name"]
                _validate_repair(case)
                # the case-owned receipt payload is
                # intentionally shape-defective on some rows
                # (that is the defect); validate key-set only,
                # never shape.
                assert set(case) == MALFORMED_KEYS, case["name"]
            else:
                assert case["kind"] == "restore", case["name"]
                assert case["oracle"] in ORACLES, case["name"]
                assert set(case) == HAPPY_KEYS, case["name"]
                _validate_receipt_shape(case["receipt"],
                                        case["name"])
                _validate_restore_shape(case["expect"],
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
    replace_receipt / set_oracle change exactly that one
    component with every other component byte-identical."""
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
        elif form_name == "replace_receipt":
            assert rep["oracle"] == case["oracle"], case["name"]
        else:
            assert rep["receipt"] == case["receipt"], \
                case["name"]


def _run_restore(oracle_name, receipt, expect):
    """Restore the pinned receipt; the restore record equals the
    pinned value, its state reserializes BYTE-FOR-BYTE to the
    receipt bundle, a second run is byte-identical and the
    receipt is never mutated."""
    engine = RestoreEngine(ORACLES[oracle_name])
    receipt_p = copy.deepcopy(receipt)
    result = engine.restore(receipt)
    assert result == expect
    # DECISIVE INVARIANT: only byte-canonical bundles restore
    assert serialize_bundle(result["state"]) == \
        receipt["bundle"]
    again = RestoreEngine(ORACLES[oracle_name]).restore(
        copy.deepcopy(receipt_p))
    assert again == result
    assert receipt == receipt_p


def test_happy():
    for name in _names("happy"):
        case = _case("happy", name)
        _run_restore(case["oracle"], case["receipt"],
                     case["expect"])


def test_boundary():
    for name in _names("boundary"):
        case = _case("boundary", name)
        _run_restore(case["oracle"], case["receipt"],
                     case["expect"])


def _exec_malformed(case):
    """The ORIGINAL input rejects with the pinned failure class
    and leaves the receipt byte-identical."""
    receipt_p = copy.deepcopy(case["receipt"])
    engine = RestoreEngine(ORACLES[case["oracle"]])
    try:
        engine.restore(case["receipt"])
    except RestoreError as exc:
        assert exc.failure_class == case["expect_failure"]
        assert exc.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.code in ERROR_ENUM
        assert case["receipt"] == receipt_p
        return
    raise AssertionError("input unexpectedly accepted")


def _exec_repaired(case):
    """The declaratively repaired input succeeds end to end."""
    out = _repaired(case)
    engine = RestoreEngine(ORACLES[out["oracle"]])
    result = engine.restore(out["receipt"])
    _validate_restore_shape(result, case["name"])


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
    engine = RestoreEngine(ORACLES[valid["oracle"]])
    engine.restore(copy.deepcopy(valid["receipt"]))
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
                                "value": "3"}}),
        ("replace_receipt-extra-key",
         {"replace_receipt": {"receipt": copy.deepcopy(
             row["minimal_repair"]["replace_receipt"]
             ["receipt"]), "wat": 1}}),
        ("replace_receipt-bad-shape",
         {"replace_receipt": {"receipt": {"wat": 1}}}),
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
    """A rejected restore leaves the EXACT receipt handed to it
    byte-identical (named working input, snapshotted, passed by
    identity and compared after the call - never a throwaway
    copy), and the subsequent valid follow-up returns the
    pinned restore record."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        engine = RestoreEngine(ORACLES[case["oracle"]])
        work_receipt = copy.deepcopy(case["receipt"])
        snap = _snapshot_work(work_receipt)
        with pytest.raises(RestoreError) as exc:
            engine.restore(work_receipt)
        _assert_work_atomic(snap, work_receipt)
        assert exc.value.failure_class == \
            case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        follow = RestoreEngine(ORACLES[case["then_oracle"]])
        result = follow.restore(
            copy.deepcopy(case["then_receipt"]))
        assert result == case["expect"]


class _ReceiptMutatingRestoreEngine(RestoreEngine):
    """Mutant: corrupts the SUPPLIED receipt before raising the
    pinned rejection."""

    def restore(self, receipt):
        receipt.clear()
        _fail("unverified_backup")


class _OrderMutatingRestoreEngine(RestoreEngine):
    """Mutant: pops and reinserts a receipt field - values
    unchanged, insertion order diverges."""

    def restore(self, receipt):
        receipt["head"] = receipt.pop("head")
        _fail("unverified_backup")


def test_rollback_atomicity_kills_mutants():
    """The atomicity assertions are NOT vacuous: mutant engines
    that mutate the supplied receipt before raising the pinned
    failure class are each KILLED by the exact assertion
    test_rollback runs against the working object - the clear
    mutant diverges in the encoded bytes, the reorder mutant
    keeps == true and diverges ONLY in the bytes."""
    case = _case(
        "rollback",
        "rejected-restore-unverified-receipt-then-valid-"
        "restore")
    work_receipt = copy.deepcopy(case["receipt"])
    snap = _snapshot_work(work_receipt)
    with pytest.raises(RestoreError) as exc:
        _ReceiptMutatingRestoreEngine(
            parse_bundle).restore(work_receipt)
    assert exc.value.failure_class == "unverified_backup"
    assert _encoded_bytes(work_receipt) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_receipt)
    work_receipt = copy.deepcopy(case["receipt"])
    snap = _snapshot_work(work_receipt)
    with pytest.raises(RestoreError) as exc:
        _OrderMutatingRestoreEngine(
            parse_bundle).restore(work_receipt)
    assert exc.value.failure_class == "unverified_backup"
    assert work_receipt == case["receipt"]  # == is blind
    assert _encoded_bytes(work_receipt) != snap[0][0]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_receipt)


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
    - equal unaliased deepcopy replacement of the receipt
      (original identity replaced, every byte equal);
    - exact dict replaced by an equal dict SUBCLASS (runtime
      type diverges, bytes equal) - the fail-closed walk
      rejects it by exact type before any hook can run;
    - hostile subclasses whose hooks would raise
      KeyboardInterrupt / SystemExit / GeneratorExit - the
      rejection surfaces as AssertionError with a cause,
      never as the raw BaseException."""
    case = _case(
        "rollback",
        "rejected-restore-unverified-receipt-then-valid-"
        "restore")

    # -- equal unaliased deepcopy replacement
    work_receipt = copy.deepcopy(case["receipt"])
    snap = _snapshot_work(work_receipt)
    original = work_receipt
    replacement = copy.deepcopy(work_receipt)
    work_receipt = replacement
    # witness: bytes AND topology unchanged, identity replaced
    assert _encoded_bytes(work_receipt) == snap[0][0]
    assert _graph_signature((work_receipt,)) == snap[1]
    assert work_receipt is not original
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_receipt)

    # -- exact dict replaced by an equal subclass
    work_receipt = copy.deepcopy(case["receipt"])
    snap = _snapshot_work(work_receipt)
    work_receipt = _PayloadDict(work_receipt)
    # witness: bytes unchanged, the exact builtin TYPE diverged
    assert _encoded_bytes(work_receipt) == snap[0][0]
    assert type(work_receipt) is not dict
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, work_receipt)

    # -- hostile traversal/encoding hooks surface as
    # AssertionError with the original as cause, never raw
    for hooked in (_HookedDict(case["receipt"]),
                   _HookedList([1])):
        work_receipt = copy.deepcopy(case["receipt"])
        snap = _snapshot_work(work_receipt)
        try:
            _assert_work_atomic(snap, hooked)
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

    work_receipt = copy.deepcopy(case["receipt"])
    snap = _snapshot_work(work_receipt)
    try:
        _assert_work_atomic(snap, _SystemExitDict(work_receipt))
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
    a, b = m["malformed"][0], m["malformed"][6]
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _swap_oracle(m, section):
    a, b = m["malformed"][8], m["malformed"][9]
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
        "malformed_restore_record", "unverified_backup",
        "divergent_parse", "divergent_state"}
