"""T0223: standalone backup red battery.

The battery stays green on its own head by executing the T0221 reference
backup engine (tests.test_t0221_backup_contract) over T0222's pinned
vectors (tests/fixtures/backup/cases.json), following the standalone-red
convention. The later implementation task must switch ONLY the bindings in
the "production bindings" block to the production backup engine; every
section below - closure, battery, totality probes and mutant kills - stays
byte-for-byte unchanged when proving the production implementation green.

What is proven:
- happy / boundary: backup returns the pinned receipt with exactly one
  serializer call, the log is left identical by value and object id, the
  receipt verifies locally with zero serializer calls, verify returns a
  fresh object and leaves its input identical, a second run is
  byte-identical;
- malformed: the ORIGINAL input rejects with the pinned failure class and
  code, inputs identical by value and object id, and the declared minimal
  neighbour (repair touching only the defect locus) is accepted;
- rollback: the rejected call leaves the very objects handed to it
  identical by value and object id, then the pinned follow-up commits;
- same-instance recovery: after every rejection (malformed, rollback,
  forgery) the SAME engine instance still verifies a known-good receipt
  into a fresh equal object, still rejects EVERY FORGED_MANIFEST receipt
  plus one cross-receipt splice with their pinned classes and codes, and,
  with an honest serializer, backs up every happy and boundary row log,
  the rollback two-put log and a pinned six-entry log (longer than any
  fixture log) in sequence, bracketed by the empty log, each to its
  pinned receipt - closed over the fixture, never a hand-picked sample;
  follow-ups run on the rejecting instance first and then on a fresh one;
- self-consistent forgeries (FORGED_MANIFEST): receipts whose backup id is
  recomputed over forged fields (count 0 on a non-genesis head, genesis
  head with a positive count, empty-log receipt with a non-empty state id,
  head / state-id / backup-id grammar violations) reject with pinned
  classes;
- integrity splice: every field of every valid receipt swapped in from a
  different valid receipt fails verify (never silently accepted);
- detachment: the serializer's state argument is a fresh copy (mutating it
  cannot change the receipt) and the receipt shares no container with the
  log;
- totality (PROBE_MANIFEST, in-file builders, production bindings only):
  backup over logs carrying HK (hash collides with a field name, raising
  __eq__) and SK (str subclass, raising __eq__) keys in entry / entry
  payload / entry record, SK and int-subclass values for op, sequence,
  entry_id and prior_entry_id, dict and list subclasses for log and entry,
  a self-referencing log, 10**5-level nesting and a 5000-digit sequence;
  hostile serializers (ValueError, KeyboardInterrupt, SystemExit,
  GeneratorExit, hash-raising str, non-str, lone low surrogate) and
  serializers mutating their argument or the live log mid-call; verify
  over receipts carrying HK / SK keys in every field, SK / int-subclass /
  bool / negative field values, entry counts past the int64 domain
  ceiling (2**63, 10**5000 - 1), dict and list subclasses, a
  self-referencing receipt and a 10**5-deep bundle. Each rejects with a
  pinned typed BackupError class and code (never a raw escape) with inputs
  identical by value and object id, or - for the mid-call mutators -
  returns the honest receipt with the inputs restored. The ceiling is
  also pinned from below: a self-consistent receipt at entry_count
  2**63 - 1 (backup id recomputed) must verify to a fresh equal object,
  so an over-tight bound is caught too.

Closure standard: closed ordered manifests, per-row semantic pins over the
original data, closed per-tag defect-locus / edge checks (an unknown tag
raises), a whole-row sha256 ROW_DIGESTS table whose key set equals the
manifests, a substitution-mutant kill test run with and without digests,
and black-box engine mutants each with a non-vacuous witness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# -- production bindings (the implementation task swaps ONLY these) --------
from tests import test_t0221_backup_contract as _ref  # noqa: E402

BackupEngine = _ref.BackupEngine
BackupError = _ref.BackupError
serialize_bundle = _ref.serialize_bundle
FAILURE_MAPPING = _ref.FAILURE_MAPPING
GENESIS = _ref.GENESIS
EMPTY_STATE_ID = _ref.state_id({})


def derive_backup_id(head, state_id, entry_count, bundle):
    """The bound canonical backup-id derivation, used only to build
    self-consistent forgeries. The implementation task must bind an
    equivalent that REPRODUCES THE PINNED RECEIPTS' backup ids (enforced
    by test_forgery_derivation_is_faithful) - not merely whatever
    derivation production happens to expose."""
    return BackupEngine._derive_backup_id(
        head, state_id, entry_count, bundle, "malformed_backup_record")
# ---------------------------------------------------------------------------

FIXTURE = ROOT / "tests" / "fixtures" / "backup" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
SECTIONS = ("happy", "boundary", "malformed", "rollback")

BACKUP_RE = re.compile(r"bck1:[0-9a-f]{64}")
HEAD_RE = re.compile(r"wal0:0{64}|wal1:[0-9a-f]{64}")
STATE_RE = re.compile(r"gs1:[0-9a-f]{64}")
ENTRY_RE = re.compile(r"wal1:[0-9a-f]{64}")
REGISTERED_OPS = ("put", "delete")
RECEIPT_FIELDS = ("backup_id", "head", "state_id", "entry_count", "bundle")
ENTRY_FIELDS = {"entry_id", "sequence", "op", "payload", "prior_entry_id"}
RECORD_FIELDS = {"variant", "digest", "snapshot_fen"}

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


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

MBR = "malformed_backup_record"
CS = "corrupt_source"
DS = "divergent_snapshot"
DB = "divergent_backup"

# -- closed ordered manifests with semantic pins over the ORIGINAL data -----
# happy/boundary: name -> (oracle, ((op, snapshot_fen), ...) source log,
#                          entry_count, live identity count)
HAPPY_MANIFEST = {
    "backup-put-put-delete-chain": (
        "honest", (("put", START), ("put", KINGS), ("delete", START)), 3, 1),
    "backup-single-put-after-e4": ("honest", (("put", E4),), 1, 1),
    "backup-put-delete-put": (
        "honest", (("put", KINGS), ("delete", KINGS), ("put", START)), 3, 1),
}
BOUNDARY_MANIFEST = {
    "backup-empty-log": ("honest", (), 0, 0),
    "backup-delete-missing-identity-noop": ("honest", (("delete", E4),), 1, 0),
    "backup-delete-last-record-empty-state": (
        "honest", (("put", KINGS), ("delete", KINGS)), 2, 0),
}
# malformed: name -> (kind, failure class, oracle, repair form, CLOSED TAG,
#   backup rows: original source-log signature (None = not a list),
#                repaired source-log signature (None = log unchanged);
#   verify rows: anchor log whose reference backup the repaired receipt
#                must equal, and that anchor's source-log signature)
MALFORMED_MANIFEST = {
    "source-log-not-a-list": (
        "backup", MBR, "honest", "replace_log", "non-list-source-log",
        None,
        (("put", START), ("put", KINGS), ("delete", START))),
    "source-sequence-gap": (
        "backup", CS, "honest", "replace_log", "sequence-gap",
        (("put", START), ("put", KINGS)),
        (("put", START), ("put", KINGS))),
    "source-broken-prior-link": (
        "backup", CS, "honest", "replace_log", "broken-prior-link",
        (("put", START), ("put", KINGS)),
        (("put", START), ("put", KINGS))),
    "source-tampered-entry-id": (
        "backup", CS, "honest", "replace_log", "tampered-entry-id",
        (("put", START), ("put", KINGS)),
        (("put", START), ("put", KINGS))),
    "source-unknown-op": (
        "backup", CS, "honest", "replace_log", "unregistered-op",
        (("squash", START), ("put", KINGS)),
        (("put", START), ("put", KINGS))),
    "oracle-raises": (
        "backup", DS, "raising", "set_oracle", "oracle-raises",
        (("put", START),),
        None),
    "oracle-non-str-output": (
        "backup", DS, "non-str", "set_oracle", "oracle-non-str-output",
        (("put", START), ("put", KINGS)),
        None),
    "oracle-lone-surrogate": (
        "backup", DS, "lone-surrogate", "set_oracle",
        "oracle-lone-surrogate",
        (),
        None),
    "receipt-missing-field": (
        "verify", MBR, "honest", "replace_receipt", "missing-receipt-field",
        "two-put-log",
        (("put", START), ("put", KINGS))),
    "receipt-extra-field": (
        "verify", MBR, "honest", "replace_receipt", "extra-receipt-field",
        "two-put-log",
        (("put", START), ("put", KINGS))),
    "receipt-bad-backup-id-grammar": (
        "verify", MBR, "honest", "set_receipt_field",
        "bad-backup-id-grammar",
        "two-put-log",
        (("put", START), ("put", KINGS))),
    "receipt-non-int-entry-count": (
        "verify", MBR, "honest", "set_receipt_field", "non-int-entry-count",
        "two-put-log",
        (("put", START), ("put", KINGS))),
    "receipt-unencodable-bundle": (
        "verify", MBR, "honest", "set_receipt_field",
        "unencodable-receipt-field",
        "two-put-log",
        (("put", START), ("put", KINGS))),
    "receipt-wrong-backup-id": (
        "verify", DB, "honest", "set_receipt_field", "wrong-backup-id",
        "two-put-log",
        (("put", START), ("put", KINGS))),
    "receipt-head-count-inconsistent": (
        "verify", DB, "honest", "set_receipt_field",
        "genesis-head-nonzero-count",
        "two-put-log",
        (("put", START), ("put", KINGS))),
    "receipt-forged-empty-state": (
        "verify", DB, "honest", "set_receipt_field",
        "forged-empty-state-id",
        "empty-log",
        ()),
}
# rollback: name -> (kind, failure class, oracle, then_oracle,
#                    rejected source length or None, follow-up signature,
#                    relation tag)
ROLLBACK_MANIFEST = {
    "rejected-backup-raising-serializer-then-valid-backup": (
        "backup", DS, "raising", "honest", 3,
        (("put", START), ("put", KINGS), ("delete", START)),
        "same-log-honest-serializer"),
    "rejected-backup-corrupt-source-then-valid-backup": (
        "backup", CS, "honest", "honest", 2,
        (("put", START), ("put", KINGS)), "single-entry-id-repair"),
    "rejected-verify-forged-receipt-then-valid-verify": (
        "verify", DB, "honest", "honest", None, 2,
        "single-backup-id-repair"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST, "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST, "rollback": ROLLBACK_MANIFEST}

# whole-row sha256 over canonical JSON; key set == manifests
ROW_DIGESTS = {
    "happy:backup-put-put-delete-chain":
        "4b5293286bc5abbb01b7c0e1824509180161de9ca493b0871c8dc757344cae79",
    "happy:backup-single-put-after-e4":
        "e9b0f61ec26f02da8aa5f98af1cd75c60968b428f223307cb87de803348d3a19",
    "happy:backup-put-delete-put":
        "926e4da44213a2a2ad1c03234d6792604ce7fb5131c7d815194dcd274b953ab6",
    "boundary:backup-empty-log":
        "acf151657cd4df4aea5a782b4294a786ecb0ae360fc025e5c5d3efa1c8f90e9a",
    "boundary:backup-delete-missing-identity-noop":
        "b4a44a329fd2e138b3801743d806ad9674c772347807268c658cfff0b64e861d",
    "boundary:backup-delete-last-record-empty-state":
        "4fd1e37d506c1a95a6722d691df465e2e38a7f388254755289db9759f6f70247",
    "malformed:source-log-not-a-list":
        "c0b215fb6da64f1b1f75c74724989b3eefb46c9499d18b09312c4acac18f7c0b",
    "malformed:source-sequence-gap":
        "b5493c665f21e73d4414c2abbfef22c55d4113a5cd4591e9e706d4685407e7b0",
    "malformed:source-broken-prior-link":
        "56b2218005f8a3eb91ecb1e425a0be5d7a2f84989e19fb9346454013030f9515",
    "malformed:source-tampered-entry-id":
        "b03d14d8ecd164d49afcd1002679d25ac126923d28e0b03c56d84f1794d15787",
    "malformed:source-unknown-op":
        "1a2b8442c4422331bad6264ba7e0713ee94cf8ab42f41ccfe039c07a3b8ec050",
    "malformed:oracle-raises":
        "8bd60afd9904695e90c319fb9290a2c41ef6152ce42868740192d70ebf9acfce",
    "malformed:oracle-non-str-output":
        "a3d43f945fa551cdcca8d701fc579db765cecedbfdca1aa4ddd6847caf7c73b4",
    "malformed:oracle-lone-surrogate":
        "78ad2e18d2f45e651da5e31dcda3ed8136c09c7236145c1ccf2318edd522e777",
    "malformed:receipt-missing-field":
        "b203154b3292d2b2cb35674a9c624bb2636fe5110ce56bb6d092155347d6ba6d",
    "malformed:receipt-extra-field":
        "14a4a3f3df114d5d52117ce1a9d0a8bd69bb47a9bd53e5f6ceb10197bf075a89",
    "malformed:receipt-bad-backup-id-grammar":
        "d3a56e5aa6f4193d8fda780a7d6db37fc02e1c56cfd7d7642522604ebc51bab2",
    "malformed:receipt-non-int-entry-count":
        "f6ec8e036d76627ad8d52c0352e3f10e68588e7ec6fe2a899e584359717ced6e",
    "malformed:receipt-unencodable-bundle":
        "6acf4f8044e71c73145963503cb2d131776ac5ded935d98bb2df06df310bdfca",
    "malformed:receipt-wrong-backup-id":
        "3544722b9245f70cd5e0c807693633b3b9856d316c07f29fec41e3df7da93572",
    "malformed:receipt-head-count-inconsistent":
        "92cdee8407f36c724c1979a94d2b43982ca5807da4115719d3a9cdac328dfb0b",
    "malformed:receipt-forged-empty-state":
        "04900fe59c7a5941d46c58403c8e69969e44377c79a05af87d8539c482991b7d",
    "rollback:rejected-backup-raising-serializer-then-valid-backup":
        "176bd0e6e4108256600f4688013385477b814d9e8f588dcd07c5418b9a4cf660",
    "rollback:rejected-backup-corrupt-source-then-valid-backup":
        "3a43ded89c166b5ce4f4f8bafaff1ad41f2d0bc51513cc6bdc7e66aa95b2f320",
    "rollback:rejected-verify-forged-receipt-then-valid-verify":
        "7aad47fc3597bcbea46ddc4a5b129be58ab015cfa338c7ecd595edaa85c857b3",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _diff_paths(a, b, path=()):
    """Every leaf path where two JSON values differ (type-exact)."""
    if type(a) is dict and type(b) is dict:
        out = set()
        for key in set(a) | set(b):
            if key not in a or key not in b:
                out.add((*path, key))
            else:
                out |= _diff_paths(a[key], b[key], (*path, key))
        return out
    if type(a) is list and type(b) is list:
        if len(a) != len(b):
            return {(*path, "#len")}
        out = set()
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out |= _diff_paths(x, y, (*path, i))
        return out
    return set() if (type(a), a) == (type(b), b) else {path}


def _repaired(case):
    """Apply the declarative minimal repair (one closed form)."""
    rep = case["minimal_repair"]
    assert len(rep) == 1, case["name"]
    out = {k: copy.deepcopy(v) for k, v in case.items()
           if k not in ("defect", "expect_failure", "scenario",
                        "minimal_repair")}
    (form, body), = rep.items()
    if form == "set_receipt_field":
        assert set(body) == {"field", "value"}, case["name"]
        assert body["field"] in RECEIPT_FIELDS, case["name"]
        out["receipt"][body["field"]] = copy.deepcopy(body["value"])
    elif form == "replace_receipt":
        assert set(body) == {"receipt"}, case["name"]
        out["receipt"] = copy.deepcopy(body["receipt"])
    elif form == "replace_log":
        assert set(body) == {"log"}, case["name"]
        out["log"] = copy.deepcopy(body["log"])
    elif form == "set_oracle":
        assert set(body) == {"oracle"}, case["name"]
        out["oracle"] = body["oracle"]
    else:
        raise AssertionError(f"{case['name']}: unknown repair form {form}")
    return out


def _valid_log(log, name):
    assert type(log) is list, name
    prior = GENESIS
    for i, entry in enumerate(log, start=1):
        assert type(entry) is dict and set(entry) == ENTRY_FIELDS, name
        assert ENTRY_RE.fullmatch(entry["entry_id"]), name
        assert type(entry["sequence"]) is int and entry["sequence"] == i
        assert entry["prior_entry_id"] == prior, name
        assert entry["op"] in REGISTERED_OPS, name
        assert set(entry["payload"]) == {"identity", "record"}, name
        assert set(entry["payload"]["record"]) == RECORD_FIELDS, name
        prior = entry["entry_id"]


def _valid_receipt(receipt, name):
    assert type(receipt) is dict, name
    assert set(receipt) == set(RECEIPT_FIELDS), name
    assert BACKUP_RE.fullmatch(receipt["backup_id"]), name
    assert HEAD_RE.fullmatch(receipt["head"]), name
    assert STATE_RE.fullmatch(receipt["state_id"]), name
    assert type(receipt["entry_count"]) is int, name
    assert receipt["entry_count"] >= 0, name
    assert type(receipt["bundle"]) is str, name
    receipt["bundle"].encode("utf-8")


def _live_state(log):
    live = {}
    for entry in log:
        ident = entry["payload"]["identity"]
        if entry["op"] == "put":
            live[ident] = entry["payload"]["record"]
        else:
            live.pop(ident, None)
    return live


def _log_sig(log):
    return tuple((e["op"], e["payload"]["record"]["snapshot_fen"])
                 for e in log)


# -- closed per-tag defect-locus checks (unknown tag raises) ---------------
# closed tag -> call kind (literal, independent of the manifests)
_TAG_KINDS = {
    "non-list-source-log": "backup", "sequence-gap": "backup",
    "broken-prior-link": "backup", "tampered-entry-id": "backup",
    "unregistered-op": "backup", "oracle-raises": "backup",
    "oracle-non-str-output": "backup", "oracle-lone-surrogate": "backup",
    "missing-receipt-field": "verify", "extra-receipt-field": "verify",
    "bad-backup-id-grammar": "verify", "non-int-entry-count": "verify",
    "unencodable-receipt-field": "verify", "wrong-backup-id": "verify",
    "genesis-head-nonzero-count": "verify",
    "forged-empty-state-id": "verify",
}


def _check_tag(case, tag):
    name = case["name"]
    if tag not in _TAG_KINDS:
        raise AssertionError(f"{name}: unknown scenario tag {tag!r}")
    assert case["kind"] == _TAG_KINDS[tag], name
    rep = _repaired(case)
    if tag in ("oracle-raises", "oracle-non-str-output",
               "oracle-lone-surrogate"):
        selector = {"oracle-raises": "raising",
                    "oracle-non-str-output": "non-str",
                    "oracle-lone-surrogate": "lone-surrogate"}[tag]
        assert case["kind"] == "backup", name
        assert case["oracle"] == selector, name
        assert rep["oracle"] == "honest", name
        assert rep["log"] == case["log"], name
        _valid_log(case["log"], name)
        return
    if case["kind"] == "backup":
        log, fixed = case["log"], rep["log"]
        _valid_log(fixed, name)
        assert rep["oracle"] == case["oracle"] == "honest", name
        if tag == "non-list-source-log":
            assert type(log) is not list, name
            assert _diff_paths(log, fixed) == {()}, name
            return
        locus = _diff_paths(log, fixed)
        assert len(locus) == 1, name
        (idx, field), = locus
        bad, good = log[idx][field], fixed[idx][field]
        if tag == "sequence-gap":
            assert field == "sequence" and idx > 0, name
            assert type(bad) is int and bad > idx + 1, name
        elif tag == "broken-prior-link":
            assert field == "prior_entry_id" and idx > 0, name
            assert bad != log[idx - 1]["entry_id"], name
            assert good == log[idx - 1]["entry_id"], name
        elif tag == "tampered-entry-id":
            assert field == "entry_id", name
            assert type(bad) is str and ENTRY_RE.fullmatch(bad), name
            assert bad != good, name
        elif tag == "unregistered-op":
            assert field == "op", name
            assert type(bad) is str and bad not in REGISTERED_OPS, name
        else:
            raise AssertionError(f"{name}: unknown scenario tag {tag!r}")
        return
    receipt, fixed = case["receipt"], rep["receipt"]
    _valid_receipt(fixed, name)
    assert rep["oracle"] == case["oracle"] == "honest", name
    locus = _diff_paths(receipt, fixed)
    assert len(locus) == 1, name
    (field,), = locus
    if tag == "missing-receipt-field":
        assert field in RECEIPT_FIELDS and field not in receipt, name
    elif tag == "extra-receipt-field":
        assert field not in RECEIPT_FIELDS and field in receipt, name
        assert field not in fixed, name
    else:
        assert field in RECEIPT_FIELDS and set(receipt) == set(fixed)
        bad, good = receipt[field], fixed[field]
        if tag == "bad-backup-id-grammar":
            assert field == "backup_id", name
            assert not (type(bad) is str and BACKUP_RE.fullmatch(bad))
        elif tag == "non-int-entry-count":
            assert field == "entry_count", name
            assert type(bad) is not int and type(good) is int, name
        elif tag == "unencodable-receipt-field":
            assert type(bad) is str, name
            with pytest.raises(UnicodeEncodeError):
                bad.encode("utf-8")
        elif tag == "wrong-backup-id":
            assert field == "backup_id", name
            assert type(bad) is str and BACKUP_RE.fullmatch(bad), name
            assert bad != good, name
        elif tag == "genesis-head-nonzero-count":
            assert field == "head" and bad == GENESIS != good, name
            assert receipt["entry_count"] > 0, name
        elif tag == "forged-empty-state-id":
            assert field == "state_id" and receipt["entry_count"] == 0
            assert STATE_RE.fullmatch(bad) and bad != EMPTY_STATE_ID
            assert good == EMPTY_STATE_ID, name
        else:
            raise AssertionError(f"{name}: unknown scenario tag {tag!r}")


_ANCHORS = {
    "two-put-log": lambda cases: _bn(
        cases, "rollback",
        "rejected-backup-corrupt-source-then-valid-backup")["then_log"],
    "empty-log": lambda cases: [],
}


def _check_malformed_pins(cases, row, pin_a, pin_b):
    """Pin every backup row's original AND repaired source log, and bind
    every verify row's repaired receipt to the reference backup of a
    pinned anchor log - so no executable payload can be copied between
    rows without the closure noticing."""
    name = row["name"]
    rep = _repaired(row)
    if row["kind"] == "backup":
        log = row["log"]
        assert (None if type(log) is not list else _log_sig(log)) == \
            pin_a, name
        repaired = rep["log"]
        if pin_b is None:
            assert repaired == log, name
        else:
            assert _log_sig(repaired) == pin_b, name
            assert len(repaired) == len(pin_b), name
        return
    anchor = copy.deepcopy(_ANCHORS[pin_a](cases))
    assert _log_sig(anchor) == pin_b, name
    fixed = rep["receipt"]
    assert BackupEngine(serialize_bundle).backup(anchor) == fixed, name
    assert BackupEngine(serialize_bundle).verify(
        copy.deepcopy(fixed)) == fixed, name


def _check_rollback_relation(case, tag):
    name = case["name"]
    exp = case["expect"]
    _valid_receipt(exp, name)
    if tag == "same-log-honest-serializer":
        assert case["kind"] == "backup", name
        assert case["then_log"] == case["log"], name
        _valid_log(case["then_log"], name)
        assert exp["entry_count"] == len(case["then_log"]), name
        assert exp["head"] == case["then_log"][-1]["entry_id"], name
    elif tag == "single-entry-id-repair":
        assert case["kind"] == "backup", name
        _valid_log(case["then_log"], name)
        locus = _diff_paths(case["log"], case["then_log"])
        assert len(locus) == 1, name
        (idx, field), = locus
        assert field == "entry_id", name
        assert ENTRY_RE.fullmatch(case["log"][idx]["entry_id"]), name
        assert exp["entry_count"] == len(case["then_log"]), name
        assert exp["head"] == case["then_log"][-1]["entry_id"], name
    elif tag == "single-backup-id-repair":
        assert case["kind"] == "verify", name
        _valid_receipt(case["then_receipt"], name)
        assert _diff_paths(case["receipt"], case["then_receipt"]) == {
            ("backup_id",)}, name
        assert BACKUP_RE.fullmatch(case["receipt"]["backup_id"]), name
        assert exp == case["then_receipt"], name
    else:
        raise AssertionError(f"{name}: unknown rollback relation {tag!r}")


def _check_edges(section, case):
    name = case["name"]
    log, exp = case["log"], case["expect"]
    _valid_log(log, name)
    _valid_receipt(exp, name)
    live = _live_state(log)
    assert exp["entry_count"] == len(log), name
    assert exp["head"] == (log[-1]["entry_id"] if log else GENESIS), name
    assert (exp["state_id"] == EMPTY_STATE_ID) == (live == {}), name
    assert (exp["bundle"] == "") == (live == {}), name
    for entry in log:
        ident = entry["payload"]["identity"]
        assert (ident in exp["bundle"]) == (ident in live), name
    if section == "boundary":
        if name == "backup-empty-log":
            assert log == [] and exp["head"] == GENESIS, name
        elif name == "backup-delete-missing-identity-noop":
            (entry,) = log
            assert entry["op"] == "delete" and live == {}, name
        elif name == "backup-delete-last-record-empty-state":
            (put, delete) = log
            assert put["payload"] == delete["payload"], name
            assert (put["op"], delete["op"]) == ("put", "delete"), name
        else:
            raise AssertionError(f"{name}: unknown boundary edge")
    else:
        assert live != {}, name


def _validate_closure(cases):
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == list(manifest), section
        for row in rows:
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS.get(label) == _row_digest(row), label
            meta = manifest[row["name"]]
            if section in ("happy", "boundary"):
                assert (row["oracle"], _log_sig(row["log"]),
                        row["expect"]["entry_count"],
                        len(_live_state(row["log"]))) == meta, label
                _check_edges(section, row)
            elif section == "malformed":
                kind, failure, oracle, form, tag, pin_a, pin_b = meta
                assert (row["kind"], row["expect_failure"], row["oracle"],
                        next(iter(row["minimal_repair"])),
                        row["scenario"]) == (kind, failure, oracle, form,
                                             tag), label
                _check_tag(row, tag)
                _check_malformed_pins(cases, row, pin_a, pin_b)
            else:
                kind, failure, oracle, then, nlog, sig, tag = meta
                if kind == "backup":
                    got = (len(row["log"]), _log_sig(row["then_log"]))
                else:
                    got = (None, row["then_receipt"]["entry_count"])
                assert (row["kind"], row["expect_failure"], row["oracle"],
                        row["then_oracle"], *got) == (
                    kind, failure, oracle, then, nlog, sig), label
                _check_rollback_relation(row, tag)
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}


def test_fixture_closure():
    assert set(CASES) >= set(SECTIONS)
    _validate_closure(CASES)


def test_unknown_tags_raise():
    for row in CASES["malformed"]:
        with pytest.raises(AssertionError, match="unknown scenario tag"):
            _check_tag(row, "not-a-tag")
    assert set(_TAG_KINDS) == {m[4] for m in MALFORMED_MANIFEST.values()}
    with pytest.raises(AssertionError, match="unknown rollback relation"):
        _check_rollback_relation(CASES["rollback"][0], "not-a-tag")


# -- identity-and-value snapshots (hostile-safe) ----------------------------
def _snap(obj):
    """Flat identity-and-value snapshot. Iterative (survives 10**5
    nesting), cycle-safe, never hashes/compares/reprs a caller-owned key
    or subclass instance, never converts a huge int to text."""
    out, seen, stack = [], set(), [obj]
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is dict or kind is list:
            if id(node) in seen:
                out.append(("seen", id(node)))
                continue
            seen.add(id(node))
            if kind is dict:
                items = list(dict.items(node))
                out.append(("dict", id(node), len(items)))
                for key, value in reversed(items):
                    stack.append(value)
                    stack.append(("key", key if type(key) is str
                                  else id(key)))
            else:
                values = list(list.__iter__(node))
                out.append(("list", id(node), len(values)))
                stack.extend(reversed(values))
        elif kind is tuple and len(node) == 2 and node[0] == "key":
            out.append(node)
        elif kind is int:
            out.append(("int", id(node), node.bit_length(), node & 0xFFFF))
        elif kind in (str, bool, float, type(None)):
            out.append((kind.__name__, node))
        else:
            out.append(("object", kind.__name__, id(node)))
    return out


def _containers(obj):
    """ids of every dict/list reachable from obj (iterative)."""
    out, stack = set(), [obj]
    while stack:
        node = stack.pop()
        if type(node) in (dict, list) and id(node) not in out:
            out.add(id(node))
            stack.extend(dict.values(node) if type(node) is dict
                         else list.__iter__(node))
    return out


class _Counting:
    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def __call__(self, state):
        self.calls += 1
        return self.fn(state)


# -- the red battery (engine factory parameter lets mutants reuse it) ------
def _run_backup(make, case):
    runs = []
    for _ in range(2):
        log = copy.deepcopy(case["log"])
        before = _snap(log)
        counting = _Counting(ORACLES[case["oracle"]])
        receipt = make(counting).backup(log)
        assert counting.calls == 1, case["name"]
        assert receipt == case["expect"], case["name"]
        assert _snap(log) == before, case["name"]
        assert not (_containers(receipt) & _containers(log)), case["name"]
        verifier_calls = _Counting(ORACLES[case["oracle"]])
        submitted = copy.deepcopy(receipt)
        sub_before = _snap(submitted)
        verified = make(verifier_calls).verify(submitted)
        assert verifier_calls.calls == 0, case["name"]
        assert verified == receipt, case["name"]
        assert verified is not submitted, case["name"]
        assert _snap(submitted) == sub_before, case["name"]
        runs.append(receipt)
    assert runs[0] == runs[1], case["name"]


def _call(make, case, oracle, log=None, receipt=None):
    engine = make(ORACLES[oracle])
    if case["kind"] == "backup":
        return engine.backup(log)
    return engine.verify(receipt)


def _same_instance_followups(engine, name, honest):
    """An engine that just rejected must still behave, both ways, closed
    over the full fixture rather than a hand-picked sample: the SAME
    instance (i) verifies a known-good receipt into a fresh equal object,
    (ii) still REJECTS every FORGED_MANIFEST receipt plus one cross-receipt
    splice, each with its pinned class and code, inputs unchanged, and
    (iii) - when its serializer is honest - backs up every happy and
    boundary row log in sequence (bracketed by the empty log, then the
    two-put rollback log), each to its own pinned receipt, so no log
    length or shape can be singled out and a replayed earlier result
    cannot pass."""
    good = _base_receipt()
    out = engine.verify(good)
    assert out == _base_receipt() and out is not good, name
    bad_rows = [(forged, build, expected)
                for forged, (build, expected) in FORGED_MANIFEST.items()]
    bad_rows.append(("followup-splice", _followup_splice,
                     FOLLOWUP_SPLICE_CLASS))
    for forged, build, expected in bad_rows:
        bad = build()
        before = _snap(bad)
        try:
            engine.verify(bad)
        except BackupError as exc:
            assert exc.failure_class == expected, (name, forged)
            assert exc.code == FAILURE_MAPPING[expected], (name, forged)
            assert _snap(bad) == before, (name, forged)
        else:
            raise AssertionError(f"{name}: follow-up accepted {forged}")
    if honest:
        assert engine.backup([]) == _empty_receipt(), name
        for row in _followup_backup_rows():
            log = copy.deepcopy(row["log"])
            assert engine.backup(log) == row["expect"], (name, row["name"])
        assert engine.backup([]) == _empty_receipt(), name


def _followup_backup_rows():
    """Every happy and boundary row, the rollback two-put log, then a
    pinned log LONGER than every fixture log (so a length cut-off above
    the fixture maximum cannot hide)."""
    rows = [r for s in ("happy", "boundary") for r in CASES[s]]
    two = _bn(CASES, "rollback",
              "rejected-backup-corrupt-source-then-valid-backup")
    return rows + [{"name": two["name"], "log": two["then_log"],
                    "expect": two["expect"]},
                   {"name": "followup-long", "log": _followup_long_log(),
                    "expect": _followup_long_receipt()}]


# a six-entry log built by the T0212 contract WAL; log and receipt are
# pinned by whole-object sha256 plus semantic pins
FOLLOWUP_LONG_OPS = (("put", START), ("put", KINGS), ("put", E4),
                     ("delete", START), ("delete", E4), ("put", START))
FOLLOWUP_LONG_LOG_SHA256 = (
    "086be7fba9e0986b414d010bb793a854a0d45ea12ffcb5b606e1323bc679cf82")
FOLLOWUP_LONG_RECEIPT = {
    "backup_id": "bck1:ad7522b5e33482dc22b51fd052404f2aba9d3e08c83279c4"
                 "d5165b397c84a65e",
    "head": "wal1:9d7db3bea850790cb33b01ae4116d78beab8f026afd42268372c0e"
            "15b86b1d21",
    "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341"
                "683233e65a33",
    "entry_count": 6,
}
# live after the six ops: KINGS and START (E4 put then deleted, START
# deleted then re-put), in identity order
FOLLOWUP_LONG_BUNDLE = (
    "('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')\n"
    "digest=pdv1:5a52f52530c2a06135595264d05d0e93dc422ee8fb28818eef7d85a70"
    "6bb949b|snapshot_fen=4k3/8/8/8/8/8/8/4K3 w - - 0 1|variant=standard\n"
    "('standard', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', "
    "'KQkq', '-')\n"
    "digest=pdv1:66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2cae32a8"
    "b88ffe|snapshot_fen=rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq"
    " - 0 1|variant=standard\n")
FOLLOWUP_LONG_RECEIPT_SHA256 = (
    "6681ad0a4cf7ef40aece5f8e912600f6536ba0ada78ca3de68eb41c372624b52")


def _sha(obj):
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True).encode()).hexdigest()


def _followup_long_log():
    from tests import test_t0212_wal_contract as _wal
    log = _wal._log_of(*FOLLOWUP_LONG_OPS)
    assert _sha(log) == FOLLOWUP_LONG_LOG_SHA256
    return log


def _followup_long_receipt():
    receipt = dict(FOLLOWUP_LONG_RECEIPT, bundle=FOLLOWUP_LONG_BUNDLE)
    assert _sha(receipt) == FOLLOWUP_LONG_RECEIPT_SHA256
    return copy.deepcopy(receipt)


# (target row, donor row, field) spliced; pinned reference failure class
FOLLOWUP_SPLICE_SOURCE = ("backup-put-put-delete-chain",
                          "backup-single-put-after-e4", "bundle")
FOLLOWUP_SPLICE_CLASS = DB


def _followup_splice():
    target, donor, field = FOLLOWUP_SPLICE_SOURCE
    receipts = {n: r for _, n, r in _valid_receipts()}
    return dict(copy.deepcopy(receipts[target]),
                **{field: copy.deepcopy(receipts[donor][field])})


def test_followup_closure_is_non_vacuous():
    """The follow-up covers every happy/boundary row, a log longer than
    every fixture log, all seven forgeries and a splice the reference
    rejects with its pinned class."""
    rows = _followup_backup_rows()
    names = [r["name"] for r in rows]
    fixture = [r["name"] for s in ("happy", "boundary") for r in CASES[s]]
    assert names[:len(fixture)] == fixture
    longest = max(len(r.get(k) or []) for s in SECTIONS for r in CASES[s]
                  for k in ("log", "then_log")
                  if isinstance(r.get(k), list))
    long_row = rows[-1]
    assert len(long_row["log"]) == 6 > longest
    assert long_row["expect"]["entry_count"] == 6
    assert long_row["expect"]["head"] == long_row["log"][-1]["entry_id"]
    assert BackupEngine(serialize_bundle).backup(long_row["log"]) == \
        long_row["expect"]
    assert len({r["expect"]["backup_id"] for r in rows}) == len(rows)
    spliced = _followup_splice()
    with pytest.raises(BackupError) as exc:
        BackupEngine(serialize_bundle).verify(spliced)
    assert exc.value.failure_class == FOLLOWUP_SPLICE_CLASS


def _run_malformed(make, case):
    log = copy.deepcopy(case.get("log"))
    receipt = copy.deepcopy(case.get("receipt"))
    before = (_snap(log), _snap(receipt))
    engine = make(ORACLES[case["oracle"]])
    try:
        (engine.backup(log) if case["kind"] == "backup"
         else engine.verify(receipt))
    except BackupError as exc:
        assert exc.failure_class == case["expect_failure"], case["name"]
        assert exc.code == FAILURE_MAPPING[case["expect_failure"]]
        assert (_snap(log), _snap(receipt)) == before, case["name"]
    else:
        raise AssertionError(f"{case['name']}: input unexpectedly accepted")
    rep = _repaired(case)
    engines = [make(ORACLES[rep["oracle"]])]
    if rep["oracle"] == case["oracle"]:
        engines.insert(0, engine)  # same rejecting instance first
    for eng in engines:
        if case["kind"] == "backup":
            out = eng.backup(copy.deepcopy(rep["log"]))
            _valid_receipt(out, case["name"])
            assert out["entry_count"] == len(rep["log"]), case["name"]
            assert make(serialize_bundle).verify(copy.deepcopy(out)) == out
        else:
            submitted = copy.deepcopy(rep["receipt"])
            out = eng.verify(submitted)
            assert out == rep["receipt"] and out is not submitted
    _same_instance_followups(engine, case["name"],
                             case["oracle"] == "honest")


def _run_rollback(make, case):
    log = copy.deepcopy(case.get("log"))
    receipt = copy.deepcopy(case.get("receipt"))
    before = (_snap(log), _snap(receipt))
    engine = make(ORACLES[case["oracle"]])
    with pytest.raises(BackupError) as exc:
        (engine.backup(log) if case["kind"] == "backup"
         else engine.verify(receipt))
    assert exc.value.failure_class == case["expect_failure"], case["name"]
    assert exc.value.code == FAILURE_MAPPING[case["expect_failure"]]
    assert (_snap(log), _snap(receipt)) == before, case["name"]
    engines = [make(ORACLES[case["then_oracle"]])]
    if case["then_oracle"] == case["oracle"]:
        engines.insert(0, engine)  # the SAME rejecting instance first
    for eng in engines:
        if case["kind"] == "backup":
            then_log = copy.deepcopy(case["then_log"])
            then_before = _snap(then_log)
            out = eng.backup(then_log)
            assert _snap(then_log) == then_before, case["name"]
        else:
            then_receipt = copy.deepcopy(case["then_receipt"])
            out = eng.verify(then_receipt)
            assert out is not then_receipt, case["name"]
        assert out == case["expect"], case["name"]
    _same_instance_followups(engine, case["name"],
                             case["oracle"] == "honest")


# -- self-consistent forgeries (backup id recomputed) ------------------------
def _forge(base, **fields):
    receipt = dict(base, **fields)
    receipt["backup_id"] = derive_backup_id(
        receipt["head"], receipt["state_id"], receipt["entry_count"],
        receipt["bundle"])
    return receipt


def _empty_receipt():
    return copy.deepcopy(_bn(CASES, "boundary", "backup-empty-log")["expect"])


# closed ordered: name -> (builder, pinned class). Every forgery carries a
# backup id recomputed over its own fields, so ONLY the head/count
# consistency and grammar checks can reject it.
FORGED_MANIFEST = {
    "count0-nongenesis-head": (
        lambda: _forge(_base_receipt(), entry_count=0), DB),
    "genesis-head-positive-count": (
        lambda: _forge(_base_receipt(), head=GENESIS), DB),
    "count0-genesis-head-nonempty-state-id": (
        lambda: _forge(_empty_receipt(),
                       state_id=_base_receipt()["state_id"]), DB),
    "head-grammar-non-hex": (
        lambda: _forge(_base_receipt(), head="wal1:" + "g" * 64), MBR),
    "head-grammar-wrong-version": (
        lambda: _forge(_base_receipt(), head="wal2:" + "0" * 64), MBR),
    "state-id-grammar-uppercase": (
        lambda: _forge(_base_receipt(), state_id="gs1:" + "G" * 64), MBR),
    "backup-id-grammar-uppercase": (
        lambda: dict(_base_receipt(),
                     backup_id=_base_receipt()["backup_id"].upper()), MBR),
}


def _run_forged(make, name):
    build, expected = FORGED_MANIFEST[name]
    receipt = build()
    before = _snap(receipt)
    engine = make(serialize_bundle)
    try:
        engine.verify(receipt)
    except BackupError as exc:
        assert exc.failure_class == expected, name
        assert exc.code == FAILURE_MAPPING[expected], name
        assert _snap(receipt) == before, name
    except BaseException as exc:
        raise AssertionError(
            f"{name}: raw {type(exc).__name__} escaped") from None
    else:
        raise AssertionError(f"{name}: self-consistent forgery accepted")
    _same_instance_followups(engine, name, True)


def test_forgery_derivation_is_faithful():
    """Non-vacuity: the bound derivation reproduces every pinned receipt's
    backup id, so each forgery is genuinely self-consistent."""
    receipts = [r["expect"] for s in ("happy", "boundary") for r in CASES[s]]
    for receipt in receipts:
        assert derive_backup_id(receipt["head"], receipt["state_id"],
                                receipt["entry_count"],
                                receipt["bundle"]) == receipt["backup_id"]
    assert list(FORGED_MANIFEST) == [
        "count0-nongenesis-head", "genesis-head-positive-count",
        "count0-genesis-head-nonempty-state-id", "head-grammar-non-hex",
        "head-grammar-wrong-version", "state-id-grammar-uppercase",
        "backup-id-grammar-uppercase"]


@pytest.mark.parametrize("name", list(FORGED_MANIFEST))
def test_verify_rejects_self_consistent_forgeries(name):
    _run_forged(BackupEngine, name)


def _valid_receipts():
    return [(section, row["name"], row["expect"])
            for section in ("happy", "boundary") for row in CASES[section]]


def _run_splice(make, target):
    """Every field of one valid receipt replaced by the differing value
    from another valid receipt must fail verify - typed."""
    section, name, receipt = target
    caught = 0
    for _, other_name, other in _valid_receipts():
        if other_name == name:
            continue
        for field in RECEIPT_FIELDS:
            if other[field] == receipt[field]:
                continue
            spliced = dict(receipt, **{field: other[field]})
            before = _snap(spliced)
            try:
                make(serialize_bundle).verify(spliced)
            except BackupError as exc:
                assert exc.failure_class in (DB, MBR), (name, field)
                assert _snap(spliced) == before, (name, field)
                caught += 1
            else:
                raise AssertionError(f"{name}: spliced {field} accepted")
    assert caught > 0, name


def _ids(section):
    return [r["name"] for r in CASES[section]]


@pytest.mark.parametrize("case", CASES["happy"], ids=_ids("happy"))
def test_backup_happy(case):
    _run_backup(BackupEngine, case)


@pytest.mark.parametrize("case", CASES["boundary"], ids=_ids("boundary"))
def test_backup_boundary(case):
    _run_backup(BackupEngine, case)


@pytest.mark.parametrize("case", CASES["malformed"], ids=_ids("malformed"))
def test_backup_malformed_and_minimal_neighbour(case):
    _run_malformed(BackupEngine, case)


@pytest.mark.parametrize("case", CASES["rollback"], ids=_ids("rollback"))
def test_backup_rollback_and_recovery(case):
    _run_rollback(BackupEngine, case)


@pytest.mark.parametrize("target", _valid_receipts(),
                         ids=[n for _, n, _ in _valid_receipts()])
def test_verify_rejects_cross_receipt_splices(target):
    _run_splice(BackupEngine, target)


def test_param_ids_equal_manifest_order():
    for section, manifest in MANIFESTS.items():
        assert _ids(section) == list(manifest), section


# -- totality: hostile inputs through the production bindings ONLY ---------
# The fixture rows are all well-formed JSON, so they cannot prove the
# engine is total. These probes are in-file builders (hostile keys and
# subclasses do not serialize) and run exclusively through the production
# bindings.


class HK:
    """Hostile key: hash collides with a field name, __eq__ raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


class SK(str):
    """str subclass key/value whose comparisons raise."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile str __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile str __ne__")


class IK(int):
    """int subclass value whose comparisons raise."""

    __hash__ = int.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile int __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile int __ne__")


class _EvilDict(dict):
    def keys(self):
        raise RuntimeError("evil keys")

    def items(self):
        raise RuntimeError("evil items")

    def __iter__(self):
        raise RuntimeError("evil iter")

    def __getitem__(self, key):
        raise RuntimeError("evil getitem")


class _EvilList(list):
    def __iter__(self):
        raise RuntimeError("evil iter")

    def __len__(self):
        raise RuntimeError("evil len")

    def __getitem__(self, index):
        raise RuntimeError("evil getitem")


class _EvilStr(str):
    def __hash__(self):
        raise RuntimeError("evil hash")


def _bn(c, section, name):
    (row,) = [r for r in c[section] if r["name"] == name]
    return row


def _base_log():
    return copy.deepcopy(
        _bn(CASES, "happy", "backup-put-put-delete-chain")["log"])


def _base_receipt():
    return copy.deepcopy(
        _bn(CASES, "happy", "backup-put-put-delete-chain")["expect"])


def _rekey(mapping, field, key_cls):
    out = {k: v for k, v in mapping.items() if k != field}
    out[key_cls(field)] = mapping[field]
    return out


def _deep(levels):
    node = []
    for _ in range(levels):
        node = [node]
    return node


def _probe(kind, log=None, receipt=None, oracle="honest"):
    return kind, log, receipt, oracle


def _log_key(where, key_cls):
    def build():
        log = _base_log()
        if where == "entry":
            log[1] = _rekey(log[1], "entry_id", key_cls)
        elif where == "payload":
            log[1]["payload"] = _rekey(log[1]["payload"], "identity",
                                       key_cls)
        else:
            log[1]["payload"]["record"] = _rekey(
                log[1]["payload"]["record"], "digest", key_cls)
        return _probe("backup", log=log)
    return build


def _entry_value(field, value):
    def build():
        log = _base_log()
        log[1][field] = value()
        return _probe("backup", log=log)
    return build


def _receipt_key(field, key_cls):
    def build():
        return _probe("verify",
                      receipt=_rekey(_base_receipt(), field, key_cls))
    return build


def _receipt_value(field, value):
    def build():
        receipt = _base_receipt()
        receipt[field] = value()
        return _probe("verify", receipt=receipt)
    return build


def _shape(target):
    def build():
        log, receipt = _base_log(), _base_receipt()
        if target == "log-list-sub":
            return _probe("backup", log=_EvilList(log))
        if target == "log-dict":
            return _probe("backup", log=dict(enumerate(log)))
        if target == "log-dict-sub":
            return _probe("backup", log=_EvilDict(enumerate(log)))
        if target == "entry-dict-sub":
            log[1] = _EvilDict(log[1])
            return _probe("backup", log=log)
        if target == "entry-list-sub":
            log[1] = _EvilList(list(log[1].values()))
            return _probe("backup", log=log)
        if target == "entry-payload-dict-sub":
            log[1]["payload"] = _EvilDict(log[1]["payload"])
            return _probe("backup", log=log)
        if target == "self-referencing-log":
            log.append(log)
            return _probe("backup", log=log)
        if target == "deep-nesting-log":
            log[1]["payload"] = _deep(10**5)
            return _probe("backup", log=log)
        if target == "huge-int-sequence":
            log[1]["sequence"] = 10**5000 - 1  # 5000 digits
            return _probe("backup", log=log)
        if target == "receipt-dict-sub":
            return _probe("verify", receipt=_EvilDict(receipt))
        if target == "receipt-list":
            return _probe("verify", receipt=list(receipt.values()))
        if target == "receipt-list-sub":
            return _probe("verify",
                          receipt=_EvilList(list(receipt.values())))
        if target == "self-referencing-receipt":
            receipt["bundle"] = receipt
            return _probe("verify", receipt=receipt)
        if target == "deep-nesting-bundle":
            receipt["bundle"] = _deep(10**5)
            return _probe("verify", receipt=receipt)
        raise AssertionError(f"unknown shape probe {target!r}")
    return build


def _raise(exc):
    def serializer(state):
        raise exc("hostile serializer")
    return serializer


_PROBE_SERIALIZERS = {
    "raises-value-error": _raise(ValueError),
    "raises-keyboard-interrupt": _raise(KeyboardInterrupt),
    "raises-system-exit": _raise(SystemExit),
    "raises-generator-exit": _raise(GeneratorExit),
    "returns-evil-str": lambda state: _EvilStr("bundle"),
    "returns-list": lambda state: [],
    "returns-lone-low-surrogate": lambda state: "\udfff",
}


def _hostile_serializer(which):
    def build():
        return _probe("backup", log=_base_log(), oracle=which)
    return build


def _live_mutating_serializer(which):
    """The serializer mutates its argument or the caller's live log."""
    def build():
        log = _base_log()

        def serializer(state):
            out = serialize_bundle(state)
            if which == "mutates-argument":
                for rec in state.values():
                    rec["digest"] = "forged"
                state["injected"] = {"variant": "x"}
            elif which == "clears-argument":
                state.clear()
            elif which == "mutates-log":
                log[0]["injected"] = 1
                del log[1]
                log.append({"junk": True})
            else:
                raise AssertionError(f"unknown serializer probe {which!r}")
            return out
        return _probe("backup", log=log, oracle=serializer)
    return build


ACCEPT = "accept-honest-receipt"
ACCEPT_UNCHANGED = "accept-receipt-unchanged"
# closed ordered probe manifest: name -> pinned outcome (failure class,
# ACCEPT = the honest receipt with inputs restored, or ACCEPT_UNCHANGED =
# verify returns a fresh object equal to the submitted in-domain receipt)
PROBE_MANIFEST = {
    "hk-key-entry": CS,
    "hk-key-entry-payload": CS,
    "hk-key-entry-record": CS,
    "sk-key-entry": CS,
    "sk-key-entry-payload": CS,
    "sk-key-entry-record": CS,
    "sk-value-op": CS,
    "ik-value-op": CS,
    "sk-value-sequence": CS,
    "ik-value-sequence": CS,
    "sk-value-entry-id": CS,
    "ik-value-entry-id": CS,
    "sk-value-prior-entry-id": CS,
    "ik-value-prior-entry-id": CS,
    "log-list-sub": MBR,
    "log-dict": MBR,
    "log-dict-sub": MBR,
    "entry-dict-sub": CS,
    "entry-list-sub": CS,
    "entry-payload-dict-sub": CS,
    "self-referencing-log": CS,
    "deep-nesting-log": CS,
    "huge-int-sequence": CS,
    "serializer-raises-value-error": DS,
    "serializer-raises-keyboard-interrupt": DS,
    "serializer-raises-system-exit": DS,
    "serializer-raises-generator-exit": DS,
    "serializer-returns-evil-str": DS,
    "serializer-returns-list": DS,
    "serializer-returns-lone-low-surrogate": DS,
    "serializer-mutates-argument": ACCEPT,
    "serializer-clears-argument": ACCEPT,
    "serializer-mutates-log": ACCEPT,
    "sk-value-backup-id": MBR,
    "sk-value-head": MBR,
    "sk-value-state-id": MBR,
    "sk-value-bundle": MBR,
    "ik-value-entry-count": MBR,
    "bool-entry-count": MBR,
    "negative-entry-count": MBR,
    "receipt-dict-sub": MBR,
    "receipt-list": MBR,
    "receipt-list-sub": MBR,
    "self-referencing-receipt": MBR,
    "deep-nesting-bundle": MBR,
    "hk-key-receipt-backup-id": MBR,
    "hk-key-receipt-head": MBR,
    "hk-key-receipt-state-id": MBR,
    "hk-key-receipt-entry-count": MBR,
    "hk-key-receipt-bundle": MBR,
    "sk-key-receipt-backup-id": MBR,
    "sk-key-receipt-head": MBR,
    "sk-key-receipt-state-id": MBR,
    "sk-key-receipt-entry-count": MBR,
    "sk-key-receipt-bundle": MBR,
    "entry-count-one-past-int64": MBR,
    "entry-count-5000-digits": MBR,
    "entry-count-at-int64-ceiling": ACCEPT_UNCHANGED,
}


def _probe_builders():
    out = {}
    for prefix, cls in (("hk", HK), ("sk", SK)):
        for where in ("entry", "payload", "record"):
            label = "entry" if where == "entry" else f"entry-{where}"
            out[f"{prefix}-key-{label}"] = _log_key(where, cls)
    for field, label, sval, ival in (
            ("op", "op", "put", 1),
            ("sequence", "sequence", "2", 2),
            ("entry_id", "entry-id", "wal1:" + "0" * 64, 7),
            ("prior_entry_id", "prior-entry-id", "wal1:" + "0" * 64, 7)):
        out[f"sk-value-{label}"] = _entry_value(field, lambda s=sval: SK(s))
        out[f"ik-value-{label}"] = _entry_value(field, lambda i=ival: IK(i))
    for target in ("log-list-sub", "log-dict", "log-dict-sub",
                   "entry-dict-sub", "entry-list-sub",
                   "entry-payload-dict-sub", "self-referencing-log",
                   "deep-nesting-log", "huge-int-sequence"):
        out[target] = _shape(target)
    for which in _PROBE_SERIALIZERS:
        out[f"serializer-{which}"] = _hostile_serializer(which)
    for which in ("mutates-argument", "clears-argument", "mutates-log"):
        out[f"serializer-{which}"] = _live_mutating_serializer(which)
    base = _base_receipt()
    for field, label in (("backup_id", "backup-id"), ("head", "head"),
                         ("state_id", "state-id"), ("bundle", "bundle")):
        out[f"sk-value-{label}"] = _receipt_value(
            field, lambda v=base[field]: SK(v))
    out["ik-value-entry-count"] = _receipt_value(
        "entry_count", lambda: IK(base["entry_count"]))
    out["bool-entry-count"] = _receipt_value("entry_count", lambda: True)
    out["negative-entry-count"] = _receipt_value("entry_count", lambda: -1)
    for target in ("receipt-dict-sub", "receipt-list", "receipt-list-sub",
                   "self-referencing-receipt", "deep-nesting-bundle"):
        out[target] = _shape(target)
    for prefix, cls in (("hk", HK), ("sk", SK)):
        for field in RECEIPT_FIELDS:
            out[f"{prefix}-key-receipt-{field.replace('_', '-')}"] = \
                _receipt_key(field, cls)
    out["entry-count-one-past-int64"] = _receipt_value(
        "entry_count", lambda: 2 ** 63)
    out["entry-count-5000-digits"] = _receipt_value(
        "entry_count", lambda: 10 ** 5000 - 1)
    out["entry-count-at-int64-ceiling"] = _ceiling_receipt_probe
    return out


INT64_MAX = 2 ** 63 - 1


def _ceiling_receipt():
    """Self-consistent in-domain receipt at the entry_count ceiling: the
    base receipt with entry_count = 2**63 - 1 and its backup id
    recomputed, so only an over-tight bound can reject it."""
    receipt = _base_receipt()
    receipt["entry_count"] = INT64_MAX
    receipt["backup_id"] = derive_backup_id(
        receipt["head"], receipt["state_id"], INT64_MAX, receipt["bundle"])
    return receipt


def _ceiling_receipt_probe():
    return _probe("verify", receipt=_ceiling_receipt())


PROBES = _probe_builders()
HOSTILE_KEY_PROBES = [n for n in PROBE_MANIFEST
                      if n.startswith(("hk-key-", "sk-key-"))]


def _run_probe(make, name):
    kind, log, receipt, oracle = PROBES[name]()
    expected = PROBE_MANIFEST[name]
    if isinstance(oracle, str):
        oracle = _PROBE_SERIALIZERS.get(oracle, ORACLES.get(oracle))
    before = (_snap(log), _snap(receipt))
    engine = make(oracle)
    try:
        result = (engine.backup(log) if kind == "backup"
                  else engine.verify(receipt))
    except BackupError as exc:
        assert expected != ACCEPT, f"{name}: rejected {exc.failure_class}"
        assert exc.failure_class == expected, name
        assert exc.code == FAILURE_MAPPING[expected], name
        assert (_snap(log), _snap(receipt)) == before, name
        return
    except BaseException as exc:  # a raw escape is the defect
        raise AssertionError(
            f"{name}: raw {type(exc).__name__} escaped") from None
    assert expected in (ACCEPT, ACCEPT_UNCHANGED), \
        f"{name}: hostile input accepted"
    if expected == ACCEPT_UNCHANGED:
        assert result == receipt and result is not receipt, name
        assert (_snap(log), _snap(receipt)) == before, name
        return
    assert result == make(serialize_bundle).backup(_base_log()), name
    assert (_snap(log), _snap(receipt)) == before, name


def test_probe_manifest_closed_and_ordered():
    assert list(PROBES) == list(PROBE_MANIFEST)
    assert len(HOSTILE_KEY_PROBES) == 16
    assert set(PROBE_MANIFEST.values()) == {MBR, CS, DS, ACCEPT,
                                            ACCEPT_UNCHANGED}
    assert [n for n, v in PROBE_MANIFEST.items()
            if v == ACCEPT_UNCHANGED] == ["entry-count-at-int64-ceiling"]


def test_ceiling_receipt_is_in_domain_and_self_consistent():
    """Non-vacuity: the ceiling probe differs from the base receipt only
    in entry_count (= int64 max) and its recomputed backup id, and sits
    exactly one below the rejected one-past probe."""
    base, ceiling = _base_receipt(), _ceiling_receipt()
    assert ceiling["entry_count"] == 2 ** 63 - 1 == _ref._COUNT_MAX
    assert {k for k in RECEIPT_FIELDS if ceiling[k] != base[k]} == \
        {"entry_count", "backup_id"}
    over = PROBES["entry-count-one-past-int64"]()[2]
    assert over["entry_count"] == ceiling["entry_count"] + 1


@pytest.mark.parametrize("name", list(PROBE_MANIFEST))
def test_backup_total_over_hostile_inputs(name):
    _run_probe(BackupEngine, name)


# -- black-box engine mutants, each with a non-vacuous witness -------------
class _AcceptAll:
    """Never rejects: echoes a fabricated receipt."""

    def __init__(self, serializer):
        self._real = BackupEngine(serialize_bundle)

    def backup(self, log):
        try:
            return self._real.backup(log)
        except BackupError:
            return {"backup_id": "bck1:" + "0" * 64}

    def verify(self, receipt):
        try:
            return self._real.verify(receipt)
        except BackupError:
            return dict(receipt) if type(receipt) is dict else {}


class _PartialCommit(BackupEngine):
    """On rejection, swaps an equal-but-foreign entry object into the
    supplied log / reorders the supplied receipt."""

    def backup(self, log):
        try:
            return super().backup(log)
        except BackupError:
            if type(log) is list and log:
                log[0] = copy.deepcopy(log[0])
            raise

    def verify(self, receipt):
        try:
            return super().verify(receipt)
        except BackupError:
            if type(receipt) is dict and receipt:
                key = next(iter(receipt))
                receipt[key] = receipt.pop(key)
            raise


class _CollapsedClass(BackupEngine):
    """Maps every rejection to malformed_backup_record."""

    def backup(self, log):
        try:
            return super().backup(log)
        except BackupError:
            _ref._fail(MBR)

    def verify(self, receipt):
        try:
            return super().verify(receipt)
        except BackupError:
            _ref._fail(MBR)


class _StaleHead(BackupEngine):
    """Backs up the right bundle but reports the genesis head."""

    def backup(self, log):
        return dict(super().backup(log), head=GENESIS)


class _OverStrict(BackupEngine):
    """Rejects every source log containing a delete."""

    def backup(self, log):
        if type(log) is list and any(
                type(e) is dict and e.get("op") == "delete" for e in log):
            _ref._fail(CS)
        return super().backup(log)


class _DoubleSerialize(BackupEngine):
    """Calls the untrusted serializer twice per backup."""

    def backup(self, log):
        receipt = super().backup(log)
        self.serializer({})
        return receipt


class _VerifyCallsSerializer(BackupEngine):
    """verify consults the untrusted serializer."""

    def verify(self, receipt):
        self.serializer({})
        return super().verify(receipt)


class _VerifyReturnsInput(BackupEngine):
    """verify hands back the caller's own receipt object."""

    def verify(self, receipt):
        super().verify(receipt)
        return receipt


class _MutLogOnSuccess(BackupEngine):
    """Swaps an equal-but-foreign entry into the log after success."""

    def backup(self, log):
        receipt = super().backup(log)
        if log:
            log[0] = copy.deepcopy(log[0])
        return receipt


class _TrustStoredId(BackupEngine):
    """verify checks shape but never recomputes the backup id."""

    def verify(self, receipt):
        try:
            return super().verify(receipt)
        except BackupError as exc:
            if exc.failure_class == DB and receipt["entry_count"] > 0 \
                    and receipt["head"] != GENESIS:
                return dict(receipt)
            raise


class _GuardlessLog(BackupEngine):
    """Compares entry key sets before validating key types."""

    def backup(self, log):
        if type(log) is list:
            for entry in list.__iter__(log):
                if type(entry) is dict:
                    set(entry.keys()) != ENTRY_FIELDS  # noqa: B015
                    payload = dict.get(entry, "payload")
                    if type(payload) is dict:
                        set(payload.keys()) != {"identity", "record"}  # noqa: B015
                        record = dict.get(payload, "record")
                        if type(record) is dict:
                            set(record.keys()) != RECORD_FIELDS  # noqa: B015
        return super().backup(log)


class _NoHeadCountConsistency(BackupEngine):
    """verify without the head/count consistency checks."""

    def verify(self, receipt):
        try:
            return super().verify(receipt)
        except BackupError as exc:
            if exc.failure_class == DB and derive_backup_id(
                    receipt["head"], receipt["state_id"],
                    receipt["entry_count"],
                    receipt["bundle"]) == receipt["backup_id"]:
                return dict(receipt)
            raise


class _PermissiveGrammar(BackupEngine):
    """verify with one identifier grammar check dropped."""

    PATTERN = None

    def verify(self, receipt):
        saved = getattr(_ref, self.PATTERN)
        setattr(_ref, self.PATTERN, re.compile(r".*", re.S))
        try:
            return super().verify(receipt)
        finally:
            setattr(_ref, self.PATTERN, saved)


class _NoHeadGrammar(_PermissiveGrammar):
    PATTERN = "_HEAD_RE"


class _NoStateGrammar(_PermissiveGrammar):
    PATTERN = "_STATE_RE"


class _StatefulPoison(BackupEngine):
    """Goes bad after its first rejection: later backups return a wrong
    bundle, later verifies hand back the caller's own object."""

    def __init__(self, serializer):
        super().__init__(serializer)
        self._poisoned = False

    def backup(self, log):
        if self._poisoned:
            return dict(super().backup(log), bundle="poisoned")
        try:
            return super().backup(log)
        except BackupError:
            self._poisoned = True
            raise

    def verify(self, receipt):
        if self._poisoned:
            return receipt
        try:
            return super().verify(receipt)
        except BackupError:
            self._poisoned = True
            raise


class _PoisonVerifyOnly(BackupEngine):
    """After its first verify rejection, verify accepts anything."""

    def __init__(self, serializer):
        super().__init__(serializer)
        self._poisoned = False

    def verify(self, receipt):
        if self._poisoned:
            return dict(receipt)
        try:
            return super().verify(receipt)
        except BackupError:
            self._poisoned = True
            raise


class _PoisonStale(BackupEngine):
    """After any rejection, backup replays the previous receipt."""

    def __init__(self, serializer):
        super().__init__(serializer)
        self._poisoned, self._last = False, None

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except BackupError:
            self._poisoned = True
            raise

    def backup(self, log):
        if self._poisoned and self._last is not None:
            return dict(self._last)
        receipt = self._guard(super().backup, log)
        self._last = receipt
        return receipt

    def verify(self, receipt):
        return self._guard(super().verify, receipt)


class _PoisonNarrow(BackupEngine):
    """After its first rejection, verify still rejects uppercase backup
    ids and entry_count == 0 but accepts everything else."""

    def __init__(self, serializer):
        super().__init__(serializer)
        self._poisoned = False

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except BackupError:
            self._poisoned = True
            raise

    def backup(self, log):
        return self._guard(super().backup, log)

    def verify(self, receipt):
        if self._poisoned and type(receipt) is dict and not (
                receipt.get("entry_count") == 0 or
                re.search(r"[A-F]", str(receipt.get("backup_id", "")))):
            return copy.deepcopy(receipt)
        return self._guard(super().verify, receipt)


class _PoisonBigLog(BackupEngine):
    """After any rejection, backup of a log longer than 3 entries returns
    the base receipt."""

    def __init__(self, serializer):
        super().__init__(serializer)
        self._poisoned = False

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except BackupError:
            self._poisoned = True
            raise

    def backup(self, log):
        if self._poisoned and type(log) is list and len(log) > 3:
            return _base_receipt()
        return self._guard(super().backup, log)

    def verify(self, receipt):
        return self._guard(super().verify, receipt)
class _GuardlessVerify(BackupEngine):
    """Compares the receipt key set before validating key types."""

    def verify(self, receipt):
        if type(receipt) is dict:
            set(receipt.keys()) != set(RECEIPT_FIELDS)  # noqa: B015
        return super().verify(receipt)


class _NoCountBound(BackupEngine):
    """verify without the entry_count domain ceiling."""

    def verify(self, receipt):
        saved = _ref._COUNT_MAX
        _ref._COUNT_MAX = float("inf")
        try:
            return super().verify(receipt)
        finally:
            _ref._COUNT_MAX = saved


class _TightCountBound(BackupEngine):
    """verify with an entry_count ceiling one below the int64 domain."""

    def verify(self, receipt):
        saved = _ref._COUNT_MAX
        _ref._COUNT_MAX = 2 ** 63 - 2
        try:
            return super().verify(receipt)
        finally:
            _ref._COUNT_MAX = saved


_BATTERY = {"happy": _run_backup, "boundary": _run_backup,
            "malformed": _run_malformed, "rollback": _run_rollback,
            "splice": _run_splice, "totality": _run_probe,
            "forged": _run_forged}
# mutant -> {section: minimum caught rows}
_ENGINE_MUTANTS = {
    "accept-all": (_AcceptAll, {"malformed": 16, "rollback": 3}),
    "partial-commit": (_PartialCommit, {"malformed": 14, "rollback": 3}),
    "collapsed-class": (_CollapsedClass, {"malformed": 10, "rollback": 3}),
    "stale-head": (_StaleHead, {"happy": 3, "boundary": 2}),
    "over-strict": (_OverStrict, {"happy": 2, "boundary": 2}),
    "double-serialize": (_DoubleSerialize, {"happy": 3, "boundary": 3}),
    "verify-calls-serializer": (_VerifyCallsSerializer,
                                {"happy": 3, "boundary": 3}),
    "verify-returns-input": (_VerifyReturnsInput,
                             {"happy": 3, "boundary": 3, "rollback": 1}),
    "mut-log-on-success": (_MutLogOnSuccess, {"happy": 3, "boundary": 2}),
    "trust-stored-id": (_TrustStoredId, {"malformed": 1, "rollback": 1,
                                         "splice": 5}),
    "guardless-log": (_GuardlessLog, {"totality": 6}),
    "no-head-count-consistency": (_NoHeadCountConsistency, {"forged": 3}),
    "no-head-grammar": (_NoHeadGrammar, {"forged": 2}),
    "no-state-grammar": (_NoStateGrammar, {"forged": 1}),
    "stateful-poison": (_StatefulPoison, {"malformed": 16, "rollback": 3,
                                          "forged": 7}),
    "poison-verify-only": (_PoisonVerifyOnly, {"malformed": 16,
                                               "rollback": 3,
                                               "forged": 7}),
    "poison-stale": (_PoisonStale, {"malformed": 13, "rollback": 2,
                                    "forged": 7}),
    "poison-narrow": (_PoisonNarrow, {"malformed": 16, "rollback": 3,
                                      "forged": 7}),
    "poison-big-log": (_PoisonBigLog, {"malformed": 13, "rollback": 2,
                                       "forged": 7}),
    "guardless-verify": (_GuardlessVerify, {"totality": 10}),
    "no-count-bound": (_NoCountBound, {"totality": 2}),
    "tight-count-bound": (_TightCountBound, {"totality": 1}),
}


def _section_rows(section):
    if section == "splice":
        return _valid_receipts()
    if section == "totality":
        return list(PROBE_MANIFEST)
    if section == "forged":
        return list(FORGED_MANIFEST)
    return CASES[section]


def _caught(make, section):
    n = 0
    for case in _section_rows(section):
        try:
            _BATTERY[section](make, copy.deepcopy(case))
        except (AssertionError, BackupError, pytest.fail.Exception):
            n += 1
    return n


@pytest.mark.parametrize("mutant", list(_ENGINE_MUTANTS))
def test_battery_kills_engine_mutants(mutant):
    make, expected = _ENGINE_MUTANTS[mutant]
    for section, minimum in expected.items():
        assert _caught(make, section) >= minimum, (mutant, section)
    # witness: the reference engine passes the very same rows
    for section in expected:
        assert _caught(BackupEngine, section) == 0, section


# -- substitution mutants: caught with AND without the digest table --------
def _swap(c, section, a, b, *keys):
    x, y = _bn(c, section, a), _bn(c, section, b)
    for key in keys:
        x[key], y[key] = y[key], x[key]


def _regen(row):
    row["expect"] = BackupEngine(ORACLES[row["oracle"]]).backup(
        copy.deepcopy(row["log"]))


def _happy_logs_swapped_regenerated(c):
    _swap(c, "happy", "backup-put-put-delete-chain",
          "backup-put-delete-put", "log")
    for name in ("backup-put-put-delete-chain", "backup-put-delete-put"):
        _regen(_bn(c, "happy", name))


def _boundary_one_way_copy_regenerated(c):
    row = _bn(c, "boundary", "backup-delete-missing-identity-noop")
    row["log"] = copy.deepcopy(
        _bn(c, "happy", "backup-single-put-after-e4")["log"])
    _regen(row)


def _rollback_follow_up_is_rejected_input(c):
    row = _bn(c, "rollback",
              "rejected-backup-corrupt-source-then-valid-backup")
    row["then_log"] = copy.deepcopy(row["log"])


def _rollback_then_log_swapped_regenerated(c):
    row = _bn(c, "rollback",
              "rejected-backup-raising-serializer-then-valid-backup")
    row["then_log"] = copy.deepcopy(
        _bn(c, "happy", "backup-put-delete-put")["log"])
    row["expect"] = BackupEngine(serialize_bundle).backup(
        copy.deepcopy(row["then_log"]))


def _rollback_verify_follow_up_is_forged(c):
    row = _bn(c, "rollback",
              "rejected-verify-forged-receipt-then-valid-verify")
    row["then_receipt"] = copy.deepcopy(row["receipt"])


def _copy_fields(c, target, donor, *keys):
    src = _bn(c, "malformed", donor)
    for key in keys:
        _bn(c, "malformed", target)[key] = copy.deepcopy(src[key])


_CLOSURE_MUTANTS = {
    "swap-happy-names": lambda c: _swap(
        c, "happy", "backup-put-put-delete-chain",
        "backup-put-delete-put", "name"),
    "happy-logs-swapped-regenerated": _happy_logs_swapped_regenerated,
    "swap-boundary-names": lambda c: _swap(
        c, "boundary", "backup-delete-missing-identity-noop",
        "backup-delete-last-record-empty-state", "name"),
    "boundary-one-way-copy-regenerated": _boundary_one_way_copy_regenerated,
    "swap-tampered-unknown-op-logs": lambda c: _swap(
        c, "malformed", "source-tampered-entry-id", "source-unknown-op",
        "log", "minimal_repair"),
    "swap-gap-broken-link-logs": lambda c: _swap(
        c, "malformed", "source-sequence-gap", "source-broken-prior-link",
        "log", "minimal_repair"),
    "swap-wrong-id-bad-grammar-receipts": lambda c: _swap(
        c, "malformed", "receipt-wrong-backup-id",
        "receipt-bad-backup-id-grammar", "receipt", "minimal_repair"),
    "swap-head-count-forged-state-receipts": lambda c: _swap(
        c, "malformed", "receipt-head-count-inconsistent",
        "receipt-forged-empty-state", "receipt", "minimal_repair"),
    "non-int-count-copies-unencodable-receipt": lambda c: _copy_fields(
        c, "receipt-non-int-entry-count", "receipt-unencodable-bundle",
        "receipt"),
    "missing-field-copies-extra-field": lambda c: _copy_fields(
        c, "receipt-missing-field", "receipt-extra-field", "receipt",
        "minimal_repair"),
    "oracle-rows-log-copy-raises-from-non-str": lambda c: _copy_fields(
        c, "oracle-raises", "oracle-non-str-output", "log"),
    "oracle-rows-log-copy-lone-surrogate-from-raises": lambda c:
        _copy_fields(c, "oracle-lone-surrogate", "oracle-raises", "log"),
    "oracle-rows-log-copy-non-str-from-lone-surrogate": lambda c:
        _copy_fields(c, "oracle-non-str-output", "oracle-lone-surrogate",
                     "log"),
    "non-list-copies-gap-repair": lambda c: _copy_fields(
        c, "source-log-not-a-list", "source-sequence-gap",
        "minimal_repair"),
    "non-list-copies-unknown-op-repair": lambda c: _copy_fields(
        c, "source-log-not-a-list", "source-unknown-op", "minimal_repair"),
    "wrong-id-copies-forged-state-receipt": lambda c: _copy_fields(
        c, "receipt-wrong-backup-id", "receipt-forged-empty-state",
        "receipt"),
    "raising-serializer-becomes-non-str": lambda c: _bn(
        c, "malformed", "oracle-raises").update(oracle="non-str"),
    "drop-sequence-gap": lambda c: c["malformed"].remove(
        _bn(c, "malformed", "source-sequence-gap")),
    "duplicate-oracle-raises": lambda c: c["malformed"].append(
        copy.deepcopy(_bn(c, "malformed", "oracle-raises"))),
    "rollback-follow-up-is-rejected-input":
        _rollback_follow_up_is_rejected_input,
    "rollback-then-log-swapped-regenerated":
        _rollback_then_log_swapped_regenerated,
    "rollback-verify-follow-up-is-forged":
        _rollback_verify_follow_up_is_forged,
}


def _section_of(cases, row):
    for section in SECTIONS:
        if any(r is row for r in cases[section]):
            return section
    raise AssertionError("row not in fixture")


@pytest.mark.parametrize("mutant", list(_CLOSURE_MUTANTS))
@pytest.mark.parametrize("digests", [True, False],
                         ids=["with-digests", "closure-only"])
def test_closure_kills_substitution_mutants(mutant, digests, monkeypatch):
    """Every substitution is caught - and caught by the semantic closure
    ALONE, not only by the row digest table."""
    cases = copy.deepcopy(CASES)
    _CLOSURE_MUTANTS[mutant](cases)
    assert cases != CASES, mutant  # non-vacuous: the mutant changed data
    if not digests:
        monkeypatch.setattr(sys.modules[__name__], "_row_digest",
                            lambda row: ROW_DIGESTS.get(
                                f"{_section_of(cases, row)}:{row['name']}"))
    with pytest.raises((AssertionError, KeyError, ValueError, TypeError,
                        BackupError)):
        _validate_closure(cases)


def test_closure_only_mode_still_accepts_clean_fixture(monkeypatch):
    """Witness: the digest bypass alone does not make closure fail."""
    monkeypatch.setattr(sys.modules[__name__], "_row_digest",
                        lambda row: ROW_DIGESTS.get(
                            f"{_section_of(CASES, row)}:{row['name']}"))
    _validate_closure(CASES)
