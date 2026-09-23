"""T0258: idempotency conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0257 idempotency contract. The cases execute against the
contract-derived reference in
tests.test_t0257_idempotency_contract (itself fully derived from
data/contracts/idempotency.yaml plus the linked WAL and rollback
contracts) - nothing is re-implemented here. Pinned receipts,
logs and ledgers in the fixture were computed from that reference
at authoring time, so any contract or derivation drift breaks
this battery. Every malformed case is discriminating (repairing
ONLY its declared defect locus makes the case valid) and rollback
cases prove a rejected apply leaves the exact supplied log,
ledger and request bit-identical before the valid follow-up
commits.

The input-atomicity machinery (fail-closed exact-type identity
walk, joint alias topology, order-sensitive encoded bytes) is
IMPORTED from the T0240 rollback fixture, never restated; its
hook-safety proofs live there. This battery re-proves it is
non-vacuous against idempotency-engine mutants.

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

from tests.test_t0212_wal_contract import (  # noqa: E402
    WalEngine,
    WalError,
    _identity,
    canonical_payload,
)
from tests.test_t0240_rollback_fixture import (  # noqa: E402
    _assert_work_atomic,
    _encoded_bytes,
    _graph_signature,
    _snapshot_work,
)
from tests.test_t0257_idempotency_contract import (  # noqa: E402
    IdempotencyEngine,
    IdempotencyError,
    _fail,
    derive_receipt_id,
    request_fingerprint,
)
from tools.idempotency_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    RECORD,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "idempotency"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
OUTCOMES = set(_CC["outcomes"]["values"])
_IDS = _CC["identifiers"]
_RECEIPT_RE = re.compile(_IDS["receipt_id"]["grammar"])
_KEY_RE = re.compile(_IDS["idempotency_key"]["grammar"])
_FP_RE = re.compile(_IDS["request_fingerprint"]["grammar"])
_ENTRY_RE = re.compile(_IDS["entry_id"]["grammar"])
RECEIPT_FIELDS = set(RECORD["fields"])
REQUEST_KEYS = {"idempotency_key", "op", "payload"}
WAL_OPS = {"put", "delete"}


def _raising_fp(op, payload):
    raise ValueError("untrusted fingerprinter failure")


def _non_str_fp(op, payload):
    return []


def _bad_grammar_fp(op, payload):
    return "idf1:zz"


def _unbound_token_fp(op, payload):
    """Grammar-valid but NOT bound to the local request
    derivation."""
    return "idf1:" + "f" * 64


def _surrogate_fp(op, payload):
    return "\ud800"


ORACLES = {"honest": request_fingerprint,
           "raising": _raising_fp,
           "non_str_output": _non_str_fp,
           "bad_grammar": _bad_grammar_fp,
           "unbound_token": _unbound_token_fp,
           "lone_surrogate": _surrogate_fp}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "rollback"}
HAPPY_KEYS = {"name", "kind", "oracle", "log", "ledger",
              "request", "expect"}
MALFORMED_KEYS = {"name", "kind", "oracle", "log", "ledger",
                  "request", "expect_failure", "defect",
                  "scenario", "minimal_repair"}
ROLLBACK_KEYS = {"name", "kind", "oracle", "log", "ledger",
                 "request", "expect_failure", "then_oracle",
                 "then_log", "then_ledger", "then_request",
                 "expect"}
REPAIR_FORMS = {"set_request_field", "replace_request",
                "replace_log", "replace_ledger", "set_oracle"}
KIND = "idempotent_apply"

# -- the closed scenario manifests -------------------------------------------
MIR = "malformed_idempotency_request"
CS = "corrupt_source"
CL = "corrupt_ledger"
KC = "key_conflict"
DF = "divergent_fingerprint"

# happy/boundary: name -> (oracle, outcome, receipt sequence,
# pre-call log length, pre-call ledger length)
HAPPY_MANIFEST = {
    "apply-first-key-empty-store": ("honest", "applied", 1, 0, 0),
    "apply-new-key-extends-store": ("honest", "applied", 3, 2, 2),
    "replay-seen-key-identical-request": (
        "honest", "replayed", 1, 2, 2),
}
BOUNDARY_MANIFEST = {
    "key-single-char": ("honest", "applied", 1, 0, 0),
    "key-max-length-128": ("honest", "applied", 2, 1, 1),
    "same-payload-distinct-key-applies-again": (
        "honest", "applied", 2, 1, 1),
    "apply-over-ledger-covering-log-subset": (
        "honest", "applied", 3, 2, 1),
    "replay-delete-receipt-at-tip": (
        "honest", "replayed", 3, 3, 3),
}
# malformed: name -> (failure class, oracle, repair form,
# pinned defect description, CLOSED SCENARIO TAG)
MALFORMED_MANIFEST = {
    "request-not-a-dict": (MIR, "honest", "replace_request",
        "the request is a list, not a dict", "non-dict-request"),
    "request-missing-field": (MIR, "honest", "replace_request",
        "the request is missing the op field",
        "missing-request-field"),
    "request-extra-field": (MIR, "honest", "replace_request",
        "the request carries a stray field",
        "extra-request-field"),
    "key-empty": (MIR, "honest", "set_request_field",
        "the idempotency key is the empty string", "empty-key"),
    "key-too-long": (MIR, "honest", "set_request_field",
        "the idempotency key is 129 characters, one past the "
        "grammar bound", "key-too-long"),
    "key-bad-leading-char": (MIR, "honest", "set_request_field",
        "the idempotency key starts with a non-alphanumeric "
        "character", "key-bad-leading-char"),
    "key-non-str": (MIR, "honest", "set_request_field",
        "the idempotency key is an int, not a string",
        "non-str-key"),
    "op-unregistered": (MIR, "honest", "set_request_field",
        "the request op is outside the wal registry",
        "unregistered-request-op"),
    "payload-identity-mismatch": (MIR, "honest",
        "set_request_field",
        "the payload identity does not derive from its record",
        "payload-identity-mismatch"),
    "log-sequence-gap": (CS, "honest", "replace_log",
        "a log entry sequence skips a position", "sequence-gap"),
    "log-tampered-entry-id": (CS, "honest", "replace_log",
        "a log entry id does not re-derive from its content",
        "tampered-entry-id"),
    "ledger-not-a-list": (CL, "honest", "replace_ledger",
        "the ledger is a dict, not a list", "non-list-ledger"),
    "ledger-tampered-receipt-id": (CL, "honest", "replace_ledger",
        "a receipt id does not re-derive from its fields",
        "tampered-receipt-id"),
    "ledger-receipt-beyond-rolled-back-log": (CL, "honest",
        "replace_ledger",
        "the log was rolled back past a receipt that the ledger "
        "still holds", "stale-receipt-beyond-log"),
    "ledger-fingerprint-not-of-entry": (CL, "honest",
        "replace_ledger",
        "a re-signed receipt names a fingerprint that is not its "
        "entry's", "receipt-fingerprint-mismatch"),
    "ledger-duplicate-key": (CL, "honest", "replace_ledger",
        "two re-signed receipts share one idempotency key",
        "duplicate-ledger-key"),
    "key-conflict-different-payload": (KC, "honest",
        "set_request_field",
        "a seen key arrives with a different payload",
        "conflict-different-payload"),
    "key-conflict-different-op": (KC, "honest",
        "set_request_field",
        "a seen key arrives with a different op",
        "conflict-different-op"),
    "oracle-raises": (DF, "raising", "set_oracle",
        "the fingerprinter raises instead of returning a token",
        "oracle-raises"),
    "oracle-non-str-output": (DF, "non_str_output", "set_oracle",
        "the fingerprinter returns a list, not a string",
        "oracle-non-str-output"),
    "oracle-bad-grammar": (DF, "bad_grammar", "set_oracle",
        "the fingerprinter token violates the pinned grammar",
        "oracle-bad-grammar"),
    "oracle-unbound-token": (DF, "unbound_token", "set_oracle",
        "the fingerprinter token is grammar-valid but not bound "
        "to the local request derivation",
        "oracle-unbound-token"),
    "oracle-lone-surrogate": (DF, "lone_surrogate", "set_oracle",
        "the fingerprinter token is not UTF-8 encodable",
        "oracle-lone-surrogate"),
}
# rollback: name -> (failure class, initial oracle, follow-up
# oracle, follow-up outcome)
ROLLBACK_MANIFEST = {
    "rejected-apply-raising-fingerprinter-then-valid-apply": (
        DF, "raising", "honest", "applied"),
    "rejected-apply-key-conflict-then-replay": (
        KC, "honest", "honest", "replayed"),
    "rejected-apply-corrupt-source-then-valid-apply": (
        CS, "honest", "honest", "applied"),
    "rejected-apply-stale-ledger-after-log-rollback-then-"
    "trimmed-reapply": (CL, "honest", "honest", "applied"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}
_SET_FIELDS = {"idempotency_key", "op", "payload"}

# content binding: canonical sha256 of every WHOLE row (oracle,
# log, ledger, request, pinned result, repair, defect, scenario,
# follow-up). Any edit to a row must update this table in the
# same change; its key set equals the manifests exactly.
ROW_DIGESTS = {
    "happy:apply-first-key-empty-store":
        "ac2315e63298f8db67137c459ef82cdfae3fb45b309afac6f8e12b19bebfcfa7",
    "happy:apply-new-key-extends-store":
        "36b28b01902a178056b166610b4c2cff84beb26b27208137113ce95fde7ce15d",
    "happy:replay-seen-key-identical-request":
        "282674a4ad110af56b766db8cb7412c76ca3e940f19b8e4e8f37f04c37905dc9",
    "boundary:key-single-char":
        "397f9c7b5fa48c21163d4668b443ceaac8283346f0ab06cfbb14efb47704e54c",
    "boundary:key-max-length-128":
        "9be73f4ec0d47d7cd382a470f2b18642c92ca5fb3c4d88b60f86c00eb473fb37",
    "boundary:same-payload-distinct-key-applies-again":
        "1975fb615e3aaf53123eba1ecc374f38952289d9caaa2df49b5ec4afbaa9abea",
    "boundary:apply-over-ledger-covering-log-subset":
        "9fdc891133f60ca5f040caf4df9bd7b21474323679ec0825ff40155c593aca81",
    "boundary:replay-delete-receipt-at-tip":
        "1972dc2bfdd83b25bbfe1943294e8e6c749d8369670bfde708651f42dfbb525c",
    "malformed:request-not-a-dict":
        "bc63a455eb0796f633e04275f466ee87d73703b2dfb990ad96d036784d402295",
    "malformed:request-missing-field":
        "9625eed992df7935a9be1652f3ca079c31d80ee2dc2fd7965dd8facb4d9b2a20",
    "malformed:request-extra-field":
        "b3bc31be9f50b055e9b3775e13551c02bb38f5d681c071a0734038bb055c09a4",
    "malformed:key-empty":
        "091ce1887945205ca05fb64452814f5401409d8ce2a7f47fb7ff0e759bb73a07",
    "malformed:key-too-long":
        "a61e89b653f47ae707888ad127e8e7302daecb1aeebc9d822aa9acd8d8d485e0",
    "malformed:key-bad-leading-char":
        "aefc1ec20d55c26e0a8835deaaf72ef43b295d9f555b32775e5686e1fd8e71ba",
    "malformed:key-non-str":
        "d82f7058f7a2a39a02018ea7352c10abc6a40ebe394a9cff4ee458f49233249d",
    "malformed:op-unregistered":
        "28bf8393b4ba7933670792900fa06aac78eeb16e08d5ae07eb8e607d877acf9e",
    "malformed:payload-identity-mismatch":
        "f88f3e9cdedbac37a0cb094280dd6ab84809e3e38906d1e16a2cdd05dd2a568c",
    "malformed:log-sequence-gap":
        "4d43aba108dc8741d89c93a02376a7404e1915e67dde45a568fada62a8412525",
    "malformed:log-tampered-entry-id":
        "11f9ba3d7e5da553cab968afac8278c7bb7af97de1dcd3e249fb2241cdd78ec6",
    "malformed:ledger-not-a-list":
        "5b997380dbfb633d46ec681a19eb5a459ab11aa632d198b3116b09a02b985e32",
    "malformed:ledger-tampered-receipt-id":
        "07329166550da8da06cd620b9b2418deef63b8c4c03a8ba624878416f1cd31e7",
    "malformed:ledger-receipt-beyond-rolled-back-log":
        "a61029fdc5c55d9e33b0020cdb86b7f356a75b03250c67a75796e79604472a07",
    "malformed:ledger-fingerprint-not-of-entry":
        "f9f0a791bf028175f3b73f9b350e2783b544501176c095cc5887d0fc63863df6",
    "malformed:ledger-duplicate-key":
        "15625eb6e3e51d8e8e9f52ebd14ede5eba43c7b3ae6d5f22ed2559011deb17b7",
    "malformed:key-conflict-different-payload":
        "d1c67669e49cf8fde24a930b6cc38854f063608583aabe44d0b6d6222bd37a08",
    "malformed:key-conflict-different-op":
        "bcaf14c1e19b3c590e69dfbb04b872fc8c58062f6ca63c60e4a047a70b7eff20",
    "malformed:oracle-raises":
        "6765a7d760faf0ef4d6ad1253c0192cacdf6529c644c6a7d6a15e2c5de7ff6c0",
    "malformed:oracle-non-str-output":
        "aaeb83cc8f00942338de3eac4c43bfc3c2391c6c1e7da9e8b819f4fbead3b0eb",
    "malformed:oracle-bad-grammar":
        "c93b96577149d94a22084bad3699f88c93884ababdf79a9b0425a036853a804c",
    "malformed:oracle-unbound-token":
        "6bbac13bb208837a2dec16199e89b20702f337f4c452f5022abb5f19c0e389f6",
    "malformed:oracle-lone-surrogate":
        "b112cec4ddbd26701d73ff583939db6861a3a099b7b5764965fd2f2861f0e879",
    "rollback:rejected-apply-raising-fingerprinter-then-valid-apply":
        "406e729b9593637c49c7aee7ed41faecb1831d66ccc279a9a0745b5c3e9d795e",
    "rollback:rejected-apply-key-conflict-then-replay":
        "4b785f56de9dce8ebd723ee5c8bcd754d44ceb7ccb25786604ab5a9d420b7859",
    "rollback:rejected-apply-corrupt-source-then-valid-apply":
        "5dff04091150febc0252c6e3d38c072f9affb9e06f9b917c15396e002a9ae24c",
    "rollback:rejected-apply-stale-ledger-after-log-rollback-then-trimmed-reapply":
        "3a433b24ea81db86058d589f5b3eb557d17664723a18de69ff3b6e6a2a1e305f",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _names(section):
    return [case["name"] for case in CASES[section]]


def _case(section, name):
    return next(case for case in CASES[section]
                if case["name"] == name)


def _validate_log_shape(log, label):
    assert type(log) is list, label
    for entry in log:
        assert type(entry) is dict, label


def _validate_receipt_shape(receipt, label):
    assert type(receipt) is dict, label
    assert set(receipt) == RECEIPT_FIELDS, label
    assert _RECEIPT_RE.fullmatch(receipt["receipt_id"]), label
    assert type(receipt["idempotency_key"]) is str, label
    assert _KEY_RE.fullmatch(receipt["idempotency_key"]), label
    assert _FP_RE.fullmatch(receipt["request_fingerprint"]), label
    assert _ENTRY_RE.fullmatch(receipt["entry_id"]), label
    assert type(receipt["sequence"]) is int, label
    assert receipt["sequence"] >= 1, label


def _validate_ledger_shape(ledger, label):
    assert type(ledger) is list, label
    for receipt in ledger:
        _validate_receipt_shape(receipt, label)


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert set(request) == REQUEST_KEYS, label
    assert type(request["idempotency_key"]) is str, label
    assert type(request["op"]) is str, label
    assert type(request["payload"]) is dict, label


def _validate_result_shape(result, label):
    assert type(result) is dict, label
    assert set(result) == {"outcome", "receipt"}, label
    assert result["outcome"] in OUTCOMES, label
    _validate_receipt_shape(result["receipt"], label)


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
        out["request"][form["field"]] = copy.deepcopy(
            form["value"])
    elif "replace_request" in rep:
        out["request"] = copy.deepcopy(
            rep["replace_request"]["request"])
    elif "replace_log" in rep:
        out["log"] = copy.deepcopy(rep["replace_log"]["log"])
    elif "replace_ledger" in rep:
        out["ledger"] = copy.deepcopy(
            rep["replace_ledger"]["ledger"])
    else:
        out["oracle"] = rep["set_oracle"]["oracle"]
    return out


def _validate_repair(case):
    """The exact minimal_repair tagged union - one of five
    closed forms, never mixed:
    - set_request_field: exactly {"field", "value"}; field one
      of the request fields; the request must be a dict;
    - replace_request: exactly {"request"}; a shape-valid
      request;
    - replace_log: exactly {"log"}; a shape-valid wal log;
    - replace_ledger: exactly {"ledger"}; a shape-valid ledger;
    - set_oracle: exactly {"oracle"}; a declared selector."""
    rep = case["minimal_repair"]
    assert type(rep) is dict, case["name"]
    assert len(rep) == 1 and set(rep) <= REPAIR_FORMS, \
        case["name"]
    form = next(iter(rep.values()))
    assert type(form) is dict, case["name"]
    if "set_request_field" in rep:
        assert set(form) == {"field", "value"}, case["name"]
        assert form["field"] in _SET_FIELDS, case["name"]
        assert type(case["request"]) is dict, case["name"]
        assert form["field"] in case["request"], case["name"]
    elif "replace_request" in rep:
        assert set(form) == {"request"}, case["name"]
        _validate_request_shape(form["request"], case["name"])
    elif "replace_log" in rep:
        assert set(form) == {"log"}, case["name"]
        _validate_log_shape(form["log"], case["name"])
    elif "replace_ledger" in rep:
        assert set(form) == {"ledger"}, case["name"]
        _validate_ledger_shape(form["ledger"], case["name"])
    else:
        assert set(form) == {"oracle"}, case["name"]
        assert form["oracle"] in ORACLES, case["name"]


def _apply(oracle, log, ledger, request):
    return IdempotencyEngine(ORACLES[oracle]).apply(
        copy.deepcopy(log), copy.deepcopy(ledger),
        copy.deepcopy(request))


def _derive(oracle, log, ledger, request, label):
    """Structure-time re-derivation: a pinned success row whose
    inputs the reference REJECTS is a fixture defect, reported as
    AssertionError, never a raw engine error."""
    try:
        return _apply(oracle, log, ledger, request)
    except IdempotencyError as exc:
        raise AssertionError(
            f"{label}: pinned success rejected as "
            f"{exc.failure_class}") from None


def _failure_of(oracle, log, ledger, request):
    """The reference engine's failure class, or None when the
    apply succeeds end to end."""
    try:
        _apply(oracle, log, ledger, request)
    except IdempotencyError as exc:
        return exc.failure_class
    return None


def _wal_verifies(log):
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return False
    return True


def _diff_fields(original, repaired, label):
    assert type(original) is dict and type(repaired) is dict, \
        label
    assert set(original) == set(repaired), label
    return {key for key in original
            if original[key] != repaired[key]}


def _diff_receipts(ledger, repaired, label):
    """Same-length ledgers: {index: differing field set} for
    every receipt that differs."""
    assert type(ledger) is list and type(repaired) is list, label
    assert len(ledger) == len(repaired), label
    out = {}
    for index, (a, b) in enumerate(zip(ledger, repaired,
                                       strict=True)):
        fields = _diff_fields(a, b, label)
        if fields:
            out[index] = fields
    return out


# -- closed scenario tags with semantic defect-locus assertions --------------
# Each malformed row carries a closed machine scenario tag;
# _assert_malformed_scenario verifies over the ORIGINAL data that
# (a) the data realizes exactly the defect the tag names, and (b)
# the minimal repair changes exactly that locus, after which the
# reference engine accepts the apply end to end. Every check fails
# CLOSED as AssertionError.

_ORACLE_TAG_SELECTOR = {
    "oracle-raises": "raising",
    "oracle-non-str-output": "non_str_output",
    "oracle-bad-grammar": "bad_grammar",
    "oracle-unbound-token": "unbound_token",
    "oracle-lone-surrogate": "lone_surrogate"}
# oracle-tag rows are pairwise discriminated by (log length,
# ledger length, key already seen)
_ORACLE_CARDINALITY = {
    "oracle-raises": (0, 0, False),
    "oracle-non-str-output": (1, 1, False),
    "oracle-bad-grammar": (2, 2, False),
    "oracle-unbound-token": (3, 3, True),
    "oracle-lone-surrogate": (2, 1, False)}


def _seen(ledger, key):
    return any(r["idempotency_key"] == key for r in ledger)


def _stored(ledger, key):
    return next(r for r in ledger if r["idempotency_key"] == key)


def _only_request_repaired(case, rep, name):
    assert rep["log"] == case["log"], name
    assert rep["ledger"] == case["ledger"], name
    assert rep["oracle"] == case["oracle"], name


def _only_request_field(case, rep, field, name):
    _only_request_repaired(case, rep, name)
    assert case["minimal_repair"]["set_request_field"][
        "field"] == field, name
    assert _diff_fields(case["request"], rep["request"],
                        name) == {field}, name


def _check_oracle_scenario(case, tag, rep):
    name = case["name"]
    log, ledger, request = case["log"], case["ledger"], \
        case["request"]
    assert case["oracle"] == _ORACLE_TAG_SELECTOR[tag], name
    _validate_log_shape(log, name)
    _validate_ledger_shape(ledger, name)
    _validate_request_shape(request, name)
    assert (len(log), len(ledger),
            _seen(ledger, request["idempotency_key"])) == \
        _ORACLE_CARDINALITY[tag], name
    # the ONLY defect is the fingerprinter itself
    assert _wal_verifies(log), name
    assert _failure_of("honest", log, ledger, request) is None, \
        name
    assert case["minimal_repair"]["set_oracle"]["oracle"] == \
        "honest", name
    assert rep["log"] == log and rep["ledger"] == ledger, name
    assert rep["request"] == request, name
    payload = copy.deepcopy(request["payload"])
    if tag == "oracle-raises":
        try:
            ORACLES[case["oracle"]](request["op"], payload)
            raise AssertionError(name)
        except ValueError:
            return
    out = ORACLES[case["oracle"]](request["op"], payload)
    if tag == "oracle-non-str-output":
        assert type(out) is not str, name
    elif tag == "oracle-bad-grammar":
        assert type(out) is str, name
        assert _FP_RE.fullmatch(out) is None, name
    elif tag == "oracle-unbound-token":
        assert type(out) is str and _FP_RE.fullmatch(out), name
        assert out != request_fingerprint(request["op"],
                                          request["payload"]), name
    else:
        assert type(out) is str, name
        try:
            out.encode("utf-8")
            raise AssertionError(name)
        except UnicodeEncodeError:
            pass


def _check_request_scenario(case, tag, rep):
    name = case["name"]
    request = case["request"]
    rreq = rep["request"]
    if tag == "non-dict-request":
        assert type(request) is not dict, name
        _only_request_repaired(case, rep, name)
    elif tag == "missing-request-field":
        assert type(request) is dict, name
        assert set(request) < REQUEST_KEYS, name
        assert len(REQUEST_KEYS - set(request)) == 1, name
        _only_request_repaired(case, rep, name)
        assert {k: rreq[k] for k in request} == request, name
    elif tag == "extra-request-field":
        assert type(request) is dict, name
        assert set(request) > REQUEST_KEYS, name
        _only_request_repaired(case, rep, name)
        assert rreq == {k: request[k] for k in REQUEST_KEYS}, name
    elif tag in ("empty-key", "key-too-long",
                 "key-bad-leading-char", "non-str-key"):
        _only_request_field(case, rep, "idempotency_key", name)
        key = request["idempotency_key"]
        if tag == "empty-key":
            assert key == "", name
        elif tag == "key-too-long":
            assert type(key) is str and len(key) == 129, name
            assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*",
                                key), name
        elif tag == "key-bad-leading-char":
            assert type(key) is str and key, name
            assert not key[0].isalnum(), name
            assert _KEY_RE.fullmatch("a" + key[1:]), name
        else:
            assert type(key) is not str, name
        assert not _seen(case["ledger"],
                         rreq["idempotency_key"]), name
    elif tag == "unregistered-request-op":
        _only_request_field(case, rep, "op", name)
        assert type(request["op"]) is str, name
        assert request["op"] not in WAL_OPS, name
    elif tag == "payload-identity-mismatch":
        _only_request_field(case, rep, "payload", name)
        payload, rpayload = request["payload"], rreq["payload"]
        assert payload["record"] == rpayload["record"], name
        assert payload["identity"] != rpayload["identity"], name
        assert rpayload["identity"] == _identity(
            rpayload["record"]), name
    else:
        raise AssertionError(f"{name}: unknown request tag {tag!r}")
    assert _failure_of(rep["oracle"], rep["log"], rep["ledger"],
                       rreq) is None, name


def _check_log_scenario(case, tag, rep):
    name = case["name"]
    log = case["log"]
    _validate_log_shape(log, name)
    assert "replace_log" in case["minimal_repair"], name
    assert rep["request"] == case["request"], name
    assert rep["ledger"] == case["ledger"], name
    assert not _wal_verifies(log), name
    assert _wal_verifies(rep["log"]), name
    assert len(log) == len(rep["log"]), name
    diffs = [i for i, (a, b) in enumerate(
        zip(log, rep["log"], strict=True)) if a != b]
    assert len(diffs) == 1, name
    field_diff = _diff_fields(log[diffs[0]], rep["log"][diffs[0]],
                              name)
    if tag == "sequence-gap":
        assert field_diff == {"sequence"}, name
        assert [e["sequence"] for e in log] != \
            list(range(1, len(log) + 1)), name
    elif tag == "tampered-entry-id":
        assert field_diff == {"entry_id"}, name
        assert [e["sequence"] for e in log] == \
            list(range(1, len(log) + 1)), name
        assert all(e["op"] in WAL_OPS for e in log), name
    else:
        raise AssertionError(f"{name}: unknown log tag {tag!r}")
    assert _failure_of(rep["oracle"], rep["log"], rep["ledger"],
                       rep["request"]) is None, name


def _check_ledger_scenario(case, tag, rep):
    name = case["name"]
    ledger, rledger = case["ledger"], rep["ledger"]
    log = case["log"]
    assert "replace_ledger" in case["minimal_repair"], name
    assert rep["request"] == case["request"], name
    assert rep["log"] == log, name
    assert _wal_verifies(log), name
    _validate_ledger_shape(rledger, name)
    if tag == "non-list-ledger":
        assert type(ledger) is not list, name
    elif tag == "tampered-receipt-id":
        diff = _diff_receipts(ledger, rledger, name)
        assert list(diff.values()) == [{"receipt_id"}], name
        (index,) = diff
        bad = ledger[index]
        assert _RECEIPT_RE.fullmatch(bad["receipt_id"]), name
        assert bad["receipt_id"] != derive_receipt_id(
            bad["idempotency_key"], bad["request_fingerprint"],
            bad["entry_id"], bad["sequence"]), name
    elif tag == "stale-receipt-beyond-log":
        assert any(r["sequence"] > len(log) for r in ledger), name
        assert rledger == [r for r in ledger
                           if r["sequence"] <= len(log)], name
        assert len(rledger) < len(ledger), name
    elif tag == "receipt-fingerprint-mismatch":
        diff = _diff_receipts(ledger, rledger, name)
        assert list(diff.values()) == [
            {"request_fingerprint", "receipt_id"}], name
        (index,) = diff
        bad = ledger[index]
        entry = log[bad["sequence"] - 1]
        assert bad["entry_id"] == entry["entry_id"], name
        assert bad["receipt_id"] == derive_receipt_id(
            bad["idempotency_key"], bad["request_fingerprint"],
            bad["entry_id"], bad["sequence"]), name  # re-signed
        assert bad["request_fingerprint"] != request_fingerprint(
            entry["op"], entry["payload"]), name
    elif tag == "duplicate-ledger-key":
        diff = _diff_receipts(ledger, rledger, name)
        assert list(diff.values()) == [
            {"idempotency_key", "receipt_id"}], name
        keys = [r["idempotency_key"] for r in ledger]
        assert len(keys) != len(set(keys)), name
        (index,) = diff
        bad = ledger[index]
        assert bad["receipt_id"] == derive_receipt_id(
            bad["idempotency_key"], bad["request_fingerprint"],
            bad["entry_id"], bad["sequence"]), name  # re-signed
    else:
        raise AssertionError(f"{name}: unknown ledger tag {tag!r}")
    assert _failure_of(rep["oracle"], log, rledger,
                       rep["request"]) is None, name


def _check_conflict_scenario(case, tag, rep):
    name = case["name"]
    request, ledger, log = case["request"], case["ledger"], \
        case["log"]
    _validate_request_shape(request, name)
    _validate_ledger_shape(ledger, name)
    key = request["idempotency_key"]
    assert _seen(ledger, key), name
    stored = _stored(ledger, key)
    entry = log[stored["sequence"] - 1]
    assert stored["request_fingerprint"] != request_fingerprint(
        request["op"], request["payload"]), name
    if tag == "conflict-different-payload":
        assert request["op"] == entry["op"], name
        assert request["payload"] != entry["payload"], name
    elif tag == "conflict-different-op":
        assert request["op"] != entry["op"], name
        assert request["payload"] == entry["payload"], name
    else:
        raise AssertionError(f"{name}: unknown conflict tag {tag!r}")
    # the locus IS the key: a fresh key makes it a valid apply
    _only_request_field(case, rep, "idempotency_key", name)
    assert not _seen(ledger, rep["request"]["idempotency_key"]), \
        name
    result = _apply(rep["oracle"], log, ledger, rep["request"])
    assert result["outcome"] == "applied", name


_REQUEST_TAGS = {"non-dict-request", "missing-request-field",
                 "extra-request-field", "empty-key",
                 "key-too-long", "key-bad-leading-char",
                 "non-str-key", "unregistered-request-op",
                 "payload-identity-mismatch"}
_LOG_TAGS = {"sequence-gap", "tampered-entry-id"}
_LEDGER_TAGS = {"non-list-ledger", "tampered-receipt-id",
                "stale-receipt-beyond-log",
                "receipt-fingerprint-mismatch",
                "duplicate-ledger-key"}
_CONFLICT_TAGS = {"conflict-different-payload",
                  "conflict-different-op"}


def _check_malformed_scenario(case, tag):
    rep = _repaired(case)
    if tag in _ORACLE_TAG_SELECTOR:
        _check_oracle_scenario(case, tag, rep)
    elif tag in _REQUEST_TAGS:
        _check_request_scenario(case, tag, rep)
    elif tag in _LOG_TAGS:
        _check_log_scenario(case, tag, rep)
    elif tag in _LEDGER_TAGS:
        _check_ledger_scenario(case, tag, rep)
    elif tag in _CONFLICT_TAGS:
        _check_conflict_scenario(case, tag, rep)
    else:
        raise AssertionError(
            f"{case['name']}: unknown scenario tag {tag!r}")


def _assert_malformed_scenario(case, tag):
    """Fail-closed wrapper: every mismatch or unexpected shape is
    an AssertionError, never a raw escape."""
    try:
        _check_malformed_scenario(case, tag)
    except AssertionError:
        raise
    except BaseException as exc:
        raise AssertionError(
            f"{case.get('name', '?')}: scenario {tag!r} check "
            f"raised unexpected {type(exc).__name__}") from None


def _key_rejected(case, key):
    """The same row with ONLY the key replaced fails as
    malformed_idempotency_request."""
    request = dict(case["request"], idempotency_key=key)
    return _failure_of(case["oracle"], case["log"], case["ledger"],
                       request) == MIR


def _check_edge(section, case):
    """Semantic edge realization over the ORIGINAL row data: one
    closed branch per happy/boundary name (an unknown name
    raises), so a renamed, swapped or substituted row can never
    carry another row's edge."""
    name = case["name"]
    log, ledger, request = case["log"], case["ledger"], \
        case["request"]
    key = request["idempotency_key"]
    receipt = case["expect"]["receipt"]
    seen = _seen(ledger, key)
    if name == "apply-first-key-empty-store":
        assert log == [] and ledger == [], name
        assert not seen and receipt["sequence"] == 1, name
        assert len(key) > 1, name
    elif name == "apply-new-key-extends-store":
        assert log and ledger and not seen, name
        assert all(request["payload"] != e["payload"]
                   for e in log), name
        assert receipt["sequence"] == len(log) + 1, name
    elif name == "replay-seen-key-identical-request":
        assert seen, name
        stored = _stored(ledger, key)
        assert stored == receipt, name
        assert stored["sequence"] < len(log), name  # not the tip
        entry = log[stored["sequence"] - 1]
        assert (request["op"], request["payload"]) == \
            (entry["op"], entry["payload"]), name
    elif name == "key-single-char":
        assert type(key) is str and len(key) == 1, name
        assert _key_rejected(case, ""), name
        assert log == [] and ledger == [], name
    elif name == "key-max-length-128":
        assert type(key) is str and len(key) == 128, name
        assert _KEY_RE.fullmatch(key), name
        assert not _KEY_RE.fullmatch(key + "a"), name
        assert _key_rejected(case, key + "x"), name
        assert not seen, name
    elif name == "same-payload-distinct-key-applies-again":
        assert not seen, name
        assert len(key) < 128, name
        assert log, name
        assert (request["op"], request["payload"]) == \
            (log[-1]["op"], log[-1]["payload"]), name
        assert any(r["request_fingerprint"] ==
                   receipt["request_fingerprint"]
                   for r in ledger), name
    elif name == "apply-over-ledger-covering-log-subset":
        assert len(ledger) < len(log), name
        assert not seen, name
        assert [r["entry_id"] for r in ledger] == \
            [e["entry_id"] for e in log[:len(ledger)]], name
        covered = {r["sequence"] for r in ledger}
        assert set(range(1, len(log) + 1)) - covered, name
    elif name == "replay-delete-receipt-at-tip":
        assert request["op"] == "delete", name
        assert seen and ledger[-1]["idempotency_key"] == key, name
        assert ledger[-1]["sequence"] == len(log), name
        assert receipt == ledger[-1], name
    else:
        raise AssertionError(f"{section}: unknown edge row {name!r}")


def _assert_edge(section, case):
    try:
        _check_edge(section, case)
    except AssertionError:
        raise
    except BaseException as exc:
        raise AssertionError(
            f"{case.get('name', '?')}: edge check raised "
            f"{type(exc).__name__}") from None


def _assert_cardinalities(case, want):
    """The advertised outcome, sequence and pre-call sizes are
    REALLY present, and the pinned result re-derives from the
    reference engine."""
    name = case["name"]
    oracle, outcome, sequence, log_len, ledger_len = want
    assert case["oracle"] == oracle, name
    assert len(case["log"]) == log_len, name
    assert len(case["ledger"]) == ledger_len, name
    assert case["expect"]["outcome"] == outcome, name
    assert case["expect"]["receipt"]["sequence"] == sequence, name
    derived = _derive(oracle, case["log"], case["ledger"],
                      case["request"], name)
    assert derived == case["expect"], name


def _validate_structure(cases):
    assert type(cases) is dict
    assert set(cases) == TOP_KEYS
    assert type(cases["schema"]) is int, "schema must be an int"
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == \
        _CC["versioning"]["base_path"]
    assert isinstance(cases["notes"], str) and cases["notes"]
    for section in MANIFESTS:
        assert type(cases[section]) is list and cases[section], \
            f"{section} must be a non-empty list"
    for section, manifest in MANIFESTS.items():
        names = [case["name"] for case in cases[section]]
        assert len(names) == len(set(names)), (
            f"{section} has duplicate names")
        # ORDERED closure: the exact manifest sequence
        assert names == list(manifest), f"{section} order drifted"
        for case in cases[section]:
            label = f"{section}:{case['name']}"
            assert ROW_DIGESTS.get(label) == _row_digest(case), \
                label
        assert set(names) == set(manifest), (
            f"{section} scenario set drifted: "
            f"missing={set(manifest) - set(names)} "
            f"extra={set(names) - set(manifest)}")
        for case in cases[section]:
            name = case["name"]
            want = manifest[name]
            assert case["kind"] == KIND, name
            assert case["oracle"] in ORACLES, name
            if section in ("happy", "boundary"):
                assert set(case) == HAPPY_KEYS, name
                _validate_log_shape(case["log"], name)
                _validate_ledger_shape(case["ledger"], name)
                _validate_request_shape(case["request"], name)
                _validate_result_shape(case["expect"], name)
                _assert_cardinalities(case, want)
                _assert_edge(section, case)
            elif section == "malformed":
                failure, oracle, repair, defect, tag = want
                assert set(case) == MALFORMED_KEYS, name
                assert case["expect_failure"] == failure, name
                assert case["expect_failure"] in FAILURE_CLASSES, \
                    name
                assert case["oracle"] == oracle, name
                _validate_repair(case)
                assert set(case["minimal_repair"]) == {repair}, name
                assert case["defect"] == defect, name
                assert case["scenario"] == tag, name
                assert _failure_of(case["oracle"], case["log"],
                                   case["ledger"],
                                   case["request"]) == failure, name
                _assert_malformed_scenario(case, tag)
            else:
                failure, oracle, then_oracle, outcome = want
                assert set(case) == ROLLBACK_KEYS, name
                assert case["expect_failure"] == failure, name
                assert case["oracle"] == oracle, name
                assert case["then_oracle"] == then_oracle, name
                _validate_log_shape(case["then_log"], name)
                _validate_ledger_shape(case["then_ledger"], name)
                _validate_request_shape(case["then_request"], name)
                _validate_result_shape(case["expect"], name)
                assert case["expect"]["outcome"] == outcome, name
                assert _failure_of(case["oracle"], case["log"],
                                   case["ledger"],
                                   case["request"]) == failure, name
                derived = _derive(then_oracle, case["then_log"],
                                  case["then_ledger"],
                                  case["then_request"], name)
                assert derived == case["expect"], name
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared == FAILURE_CLASSES
    outcomes = {c["expect"]["outcome"]
                for s in ("happy", "boundary") for c in cases[s]}
    assert outcomes == OUTCOMES


def test_fixture_structure():
    _validate_structure(CASES)


def test_fixture_schema_version_mutations_fail():
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
        form = set(case["minimal_repair"])
        if form != {"set_oracle"}:
            assert rep["oracle"] == case["oracle"], name
        if form != {"replace_log"}:
            assert rep["log"] == case["log"], name
        if form != {"replace_ledger"}:
            assert rep["ledger"] == case["ledger"], name
        if not form & {"replace_request", "set_request_field"}:
            assert rep["request"] == case["request"], name
        result = _apply(rep["oracle"], rep["log"], rep["ledger"],
                        rep["request"])
        _validate_result_shape(result, name)


def _run_apply(case):
    """Execute on working copies: pinned result, exact commit
    effect (applied: exactly one entry and one receipt appended;
    replayed: nothing changes), untouched request, WAL still
    verifies, deterministic re-run, and idempotent repeat."""
    oracle = ORACLES[case["oracle"]]
    log, ledger, request = case["log"], case["ledger"], \
        case["request"]
    work_log = copy.deepcopy(log)
    work_ledger = copy.deepcopy(ledger)
    work_req = copy.deepcopy(request)
    result = IdempotencyEngine(oracle).apply(
        work_log, work_ledger, work_req)
    assert result == case["expect"], case["name"]
    receipt = result["receipt"]
    if result["outcome"] == "applied":
        assert work_log[:-1] == log, case["name"]
        assert len(work_log) == len(log) + 1, case["name"]
        entry = work_log[-1]
        assert entry["entry_id"] == receipt["entry_id"]
        assert entry["sequence"] == receipt["sequence"] == \
            len(work_log)
        assert entry["op"] == request["op"]
        assert entry["payload"] == request["payload"]
        assert work_ledger == ledger + [receipt], case["name"]
    else:
        assert work_log == log and work_ledger == ledger, \
            case["name"]
        assert receipt in ledger, case["name"]
    assert work_req == request, case["name"]
    assert receipt["request_fingerprint"] == request_fingerprint(
        request["op"], request["payload"])
    assert WalEngine(canonical_payload).replay(work_log)[
        "applied"] == len(work_log)
    # deterministic over fresh copies
    assert _apply(case["oracle"], log, ledger, request) == \
        case["expect"]
    # IDEMPOTENT: repeating the same keyed request replays the
    # identical receipt and appends nothing
    before = copy.deepcopy((work_log, work_ledger))
    again = IdempotencyEngine(oracle).apply(
        work_log, work_ledger, copy.deepcopy(request))
    assert again == {"outcome": "replayed", "receipt": receipt}
    assert (work_log, work_ledger) == before


def test_happy():
    for name in _names("happy"):
        _run_apply(_case("happy", name))


def test_boundary():
    for name in _names("boundary"):
        _run_apply(_case("boundary", name))


def _exec_malformed(case):
    engine = IdempotencyEngine(ORACLES[case["oracle"]])
    work = (copy.deepcopy(case["log"]),
            copy.deepcopy(case["ledger"]),
            copy.deepcopy(case["request"]))
    snap = _snapshot_work(*work)
    with pytest.raises(IdempotencyError) as exc:
        engine.apply(*work)
    _assert_work_atomic(snap, *work)
    assert exc.value.failure_class == case["expect_failure"], \
        case["name"]
    assert exc.value.code == FAILURE_MAPPING[
        case["expect_failure"]], case["name"]
    assert exc.value.code in ERROR_ENUM, case["name"]


def _exec_repaired(case):
    rep = _repaired(case)
    result = _apply(rep["oracle"], rep["log"], rep["ledger"],
                    rep["request"])
    _validate_result_shape(result, case["name"])


@pytest.mark.parametrize("name", list(MALFORMED_MANIFEST))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    assert list(MALFORMED_MANIFEST) == _names("malformed")


def test_collection_guard_detects_late_fixture_row():
    injected = copy.deepcopy(CASES)
    row = copy.deepcopy(injected["happy"][0])
    row["name"] = "late-injected-row"
    injected["happy"].append(row)
    with pytest.raises(AssertionError):
        _validate_structure(injected)
    assert set(_names("happy")) == set(HAPPY_MANIFEST)


def test_injected_valid_malformed_row_fails_rejection():
    """A malformed row silently swapped for its repaired (valid)
    form is caught: the pinned-failure check in structure
    validation rejects it, and the rejection executor fails."""
    for name in ("key-conflict-different-payload",
                 "ledger-receipt-beyond-rolled-back-log",
                 "oracle-unbound-token"):
        m = copy.deepcopy(CASES)
        index = _names("malformed").index(name)
        rep = _repaired(m["malformed"][index])
        for key in ("log", "ledger", "request", "oracle"):
            m["malformed"][index][key] = rep[key]
        with pytest.raises(AssertionError):
            _validate_structure(m)
        with pytest.raises(BaseException) as exc:
            _exec_malformed(m["malformed"][index])
        assert not isinstance(exc.value, IdempotencyError)


def _repair_mutations():
    def bad_form_name(c):
        c["minimal_repair"] = {"set_field": {
            "field": "idempotency_key", "value": "b"}}

    def two_forms(c):
        c["minimal_repair"] = {
            "set_request_field": {"field": "idempotency_key",
                                  "value": "b"},
            "set_oracle": {"oracle": "honest"}}

    def missing_value(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "idempotency_key"}}

    def bad_field(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "bogus", "value": "b"}}

    def wrong_locus_value(c):
        c["minimal_repair"] = {"set_request_field": {
            "field": "idempotency_key", "value": "a"}}  # seen key

    def bad_oracle(c):
        c["minimal_repair"] = {"set_oracle": {"oracle": "bogus"}}

    def replace_log_extra_key(c):
        c["minimal_repair"] = {"replace_log": {
            "log": [], "extra": True}}

    def replace_ledger_bad_shape(c):
        c["minimal_repair"] = {"replace_ledger": {
            "ledger": [{"receipt_id": "x"}]}}

    def replace_request_bad_shape(c):
        c["minimal_repair"] = {"replace_request": {
            "request": {"idempotency_key": "b"}}}

    def wrong_form_for_tag(c):
        c["minimal_repair"] = {"replace_request": {
            "request": {"idempotency_key": "b", "op": "put",
                        "payload": copy.deepcopy(
                            c["request"]["payload"])}}}

    return [bad_form_name, two_forms, missing_value, bad_field,
            wrong_locus_value, bad_oracle, replace_log_extra_key,
            replace_ledger_bad_shape, replace_request_bad_shape,
            wrong_form_for_tag]


def test_repair_form_mutations_fail_structure():
    index = _names("malformed").index("key-empty")
    for mutate in _repair_mutations():
        m = copy.deepcopy(CASES)
        mutate(m["malformed"][index])
        with pytest.raises(AssertionError):
            _validate_structure(m)


def test_rollback():
    """A rejected apply leaves the EXACT objects handed to it
    bit-identical (identity walk, alias topology, encoded bytes),
    and the subsequent valid follow-up returns the pinned
    result."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        engine = IdempotencyEngine(ORACLES[case["oracle"]])
        work = (copy.deepcopy(case["log"]),
                copy.deepcopy(case["ledger"]),
                copy.deepcopy(case["request"]))
        snap = _snapshot_work(*work)
        with pytest.raises(IdempotencyError) as exc:
            engine.apply(*work)
        _assert_work_atomic(snap, *work)
        assert exc.value.failure_class == case["expect_failure"]
        assert exc.value.code == FAILURE_MAPPING[
            case["expect_failure"]]
        assert exc.value.code in ERROR_ENUM
        follow = IdempotencyEngine(ORACLES[case["then_oracle"]])
        result = follow.apply(copy.deepcopy(case["then_log"]),
                              copy.deepcopy(case["then_ledger"]),
                              copy.deepcopy(case["then_request"]))
        assert result == case["expect"]


def test_stale_ledger_reapply_rederives_the_original_receipt():
    """After the log rollback and ledger trim, re-applying the
    rolled-back key re-derives the exact receipt the stale ledger
    held - deterministic re-apply, never a replay of the stale
    receipt."""
    case = _case("rollback", "rejected-apply-stale-ledger-after-"
                             "log-rollback-then-trimmed-reapply")
    stale = case["ledger"]
    assert len(stale) == len(case["then_ledger"]) + 1
    assert case["expect"]["outcome"] == "applied"
    assert case["expect"]["receipt"] == stale[-1]


# -- atomicity mutants ------------------------------------------------------------


class _LogMutatingEngine(IdempotencyEngine):
    def apply(self, log, ledger, request):
        log.append({"junk": True})
        _fail("divergent_fingerprint")


class _LedgerMutatingEngine(IdempotencyEngine):
    def apply(self, log, ledger, request):
        ledger.clear()
        _fail("divergent_fingerprint")


class _RequestMutatingEngine(IdempotencyEngine):
    def apply(self, log, ledger, request):
        request["idempotency_key"] = "rewritten"
        _fail("divergent_fingerprint")


class _DeepMutatingEngine(IdempotencyEngine):
    def apply(self, log, ledger, request):
        request["payload"]["record"]["digest"] = "pdv1:" + "0" * 64
        _fail("divergent_fingerprint")


class _OrderMutatingEngine(IdempotencyEngine):
    def apply(self, log, ledger, request):
        receipt = ledger[0]
        receipt["receipt_id"] = receipt.pop("receipt_id")
        _fail("divergent_fingerprint")


class _AliasBreakingEngine(IdempotencyEngine):
    def apply(self, log, ledger, request):
        request["payload"] = copy.deepcopy(request["payload"])
        _fail("divergent_fingerprint")


class _CommitThenFailEngine(IdempotencyEngine):
    """Behavioral mutant: commits the entry and receipt, THEN
    consults the fingerprinter - an oracle failure leaves an
    orphan commit."""

    def apply(self, log, ledger, request):
        result = IdempotencyEngine(request_fingerprint).apply(
            log, ledger, request)
        IdempotencyEngine._fingerprint(self, {
            "idempotency_key": request["idempotency_key"],
            "op": request["op"],
            "payload": copy.deepcopy(request["payload"])})
        return result


def test_rollback_atomicity_kills_mutants():
    """The atomicity assertions are NOT vacuous against
    idempotency-engine mutants: each raises the pinned failure
    after corrupting the SUPPLIED log, ledger or request; the
    corruption is observable at the claimed layer and
    _assert_work_atomic KILLS it."""
    case = _case("rollback", "rejected-apply-raising-"
                             "fingerprinter-then-valid-apply")
    for mutant in (_LogMutatingEngine, _LedgerMutatingEngine,
                   _RequestMutatingEngine, _DeepMutatingEngine,
                   _CommitThenFailEngine):
        work = (copy.deepcopy(case["log"]),
                copy.deepcopy(case["ledger"]),
                copy.deepcopy(case["request"]))
        snap = _snapshot_work(*work)
        with pytest.raises(IdempotencyError) as exc:
            mutant(ORACLES[case["oracle"]]).apply(*work)
        assert exc.value.failure_class == case["expect_failure"]
        assert any(_encoded_bytes(root) != want
                   for root, want in zip(work, snap[0],
                                         strict=True))
        with pytest.raises(AssertionError):
            _assert_work_atomic(snap, *work)

    # order mutant: == survives, only the bytes diverge
    work = (copy.deepcopy(case["log"]),
            copy.deepcopy(case["ledger"]),
            copy.deepcopy(case["request"]))
    snap = _snapshot_work(*work)
    with pytest.raises(IdempotencyError):
        _OrderMutatingEngine(request_fingerprint).apply(*work)
    assert work[1] == case["ledger"]  # == is blind
    assert _encoded_bytes(work[1]) != snap[0][1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, *work)

    # alias mutant: the request payload deliberately aliases the
    # log entry's equal payload; the mutant swaps in an equal
    # deepcopy - per-root bytes survive, only topology diverges
    same = _case("happy", "replay-seen-key-identical-request")
    work = (copy.deepcopy(same["log"]),
            copy.deepcopy(same["ledger"]),
            copy.deepcopy(same["request"]))
    assert work[2]["payload"] == work[0][0]["payload"]
    work[2]["payload"] = work[0][0]["payload"]
    snap = _snapshot_work(*work)
    with pytest.raises(IdempotencyError):
        _AliasBreakingEngine(request_fingerprint).apply(*work)
    for root, want in zip(work, snap[0], strict=True):
        assert _encoded_bytes(root) == want
    assert _graph_signature(work) != snap[1]
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, *work)


def test_reference_engine_preserves_aliases_on_rejection():
    """Counter-test to the alias mutant: the REAL engine,
    rejecting with the same aliased inputs, preserves identity,
    topology and bytes."""
    same = _case("happy", "replay-seen-key-identical-request")
    work = (copy.deepcopy(same["log"]),
            copy.deepcopy(same["ledger"]),
            copy.deepcopy(same["request"]))
    work[2]["payload"] = work[0][0]["payload"]
    snap = _snapshot_work(*work)
    with pytest.raises(IdempotencyError) as exc:
        IdempotencyEngine(_raising_fp).apply(*work)
    assert exc.value.failure_class == DF
    _assert_work_atomic(snap, *work)


# -- closed scenario coverage ---------------------------------------------------


def test_dispatch_sections_match_manifest():
    for section, manifest in MANIFESTS.items():
        assert _names(section) == list(manifest), section


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
    other = sorted(set(MANIFESTS) - {section})
    m[section][0] = copy.deepcopy(m[other[0]][0])


def _swap_failure(m, section):
    a = m["malformed"][0]
    b = next(c for c in m["malformed"]
             if c["expect_failure"] == CL)
    a["expect_failure"], b["expect_failure"] = (
        b["expect_failure"], a["expect_failure"])


def _swap_oracle(m, section):
    a = next(c for c in m["malformed"]
             if c["name"] == "oracle-bad-grammar")
    b = next(c for c in m["malformed"]
             if c["name"] == "oracle-unbound-token")
    a["oracle"], b["oracle"] = b["oracle"], a["oracle"]


def _swap_repair(m, section):
    a = next(c for c in m["malformed"]
             if "replace_ledger" in c["minimal_repair"])
    b = next(c for c in m["malformed"]
             if "set_request_field" in c["minimal_repair"])
    a["minimal_repair"], b["minimal_repair"] = (
        b["minimal_repair"], a["minimal_repair"])


def _swap_scenario(m, section):
    a, b = m["malformed"][1], m["malformed"][2]
    a["scenario"], b["scenario"] = b["scenario"], a["scenario"]


def _swap_outcome_rows(m, section):
    """Swap the request of a replay row with an apply row: names
    stay, the advertised outcome no longer re-derives."""
    a = m["happy"][0]
    b = next(c for c in m["happy"]
             if c["expect"]["outcome"] == "replayed")
    a["expect"], b["expect"] = b["expect"], a["expect"]


def _stale_then_ledger(m, section):
    row = m["rollback"][-1]
    row["then_ledger"] = copy.deepcopy(row["ledger"])


def _section_mutation_cases():
    cases = []
    for section in MANIFESTS:
        cases.append((f"{section}-pop", section, _pop_first))
        cases.append((f"{section}-rename", section,
                      _rename_first))
        cases.append((f"{section}-add-extra", section,
                      _add_extra))
        cases.append((f"{section}-substitute", section,
                      _substitute_first))
        cases.append((f"{section}-move", section, _move_row))
    cases += [("malformed-swap-failure", "malformed",
               _swap_failure),
              ("malformed-swap-oracle", "malformed", _swap_oracle),
              ("malformed-swap-repair", "malformed", _swap_repair),
              ("malformed-swap-scenario", "malformed",
               _swap_scenario),
              ("happy-swap-outcome", "happy", _swap_outcome_rows),
              ("rollback-stale-then-ledger", "rollback",
               _stale_then_ledger)]
    return cases


@pytest.mark.parametrize(
    "label,section,mutate", _section_mutation_cases(),
    ids=[c[0] for c in _section_mutation_cases()])
def test_section_mutations_fail_structure(label, section, mutate):
    m = copy.deepcopy(CASES)
    mutate(m, section)
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_failure_class_and_outcome_coverage():
    declared = {c["expect_failure"] for c in CASES["malformed"]}
    assert declared == FAILURE_CLASSES
    assert declared == {MIR, CS, CL, KC, DF}
    outcomes = {c["expect"]["outcome"]
                for s in ("happy", "boundary") for c in CASES[s]}
    assert outcomes == OUTCOMES == {"applied", "replayed"}


# -- fixture-only substitution kill-proof ---------------------------------------


def _swap_names(m, section, a, b):
    rows = {r["name"]: r for r in m[section]}
    rows[a]["name"], rows[b]["name"] = b, a
    # keep the manifest ORDER so only content moved
    m[section].sort(key=lambda r: list(MANIFESTS[section]).index(
        r["name"]))


def _replace_payload(m, section, target, source):
    rows = {r["name"]: r for r in m[section]}
    for key in rows[target]:
        if key != "name":
            rows[target][key] = copy.deepcopy(rows[source][key])


def _same_pin_pairs():
    """Every pair of happy/boundary rows sharing one manifest
    pin tuple - exactly the rows cardinality checks alone cannot
    tell apart."""
    rows = [(s, n, MANIFESTS[s][n]) for s in ("happy", "boundary")
            for n in MANIFESTS[s]]
    return [(a[0], a[1], b[0], b[1])
            for i, a in enumerate(rows) for b in rows[i + 1:]
            if a[2] == b[2]]


def test_same_pin_pairs_are_the_known_ones():
    assert {(a, b) for _s, a, _t, b in _same_pin_pairs()} == {
        ("apply-first-key-empty-store", "key-single-char"),
        ("key-max-length-128",
         "same-payload-distinct-key-applies-again")}


@pytest.mark.parametrize("with_digests", [True, False],
                         ids=["with-digests", "edge-only"])
def test_fixture_only_substitutions_are_killed(with_digests,
                                               monkeypatch):
    """Verifier mutants A (name swap between same-pin rows) and B
    (whole-payload substitution between same-pin rows), plus the
    same across every same-pin pair and a malformed/rollback
    payload substitution, all mutating ONLY cases.json content in
    memory. Each is killed by structure validation both WITH the
    digest table and with it DISABLED - the semantic edge checks
    alone are sufficient for happy/boundary."""
    if not with_digests:
        monkeypatch.setattr(sys.modules[__name__], "_row_digest",
                            lambda row: ROW_DIGESTS[
                                f"{_section_of(row)}:{row['name']}"])
    mutants = []
    for s1, a, s2, b in _same_pin_pairs():
        m = copy.deepcopy(CASES)
        if s1 == s2:
            _swap_names(m, s1, a, b)
            mutants.append((f"swap:{a}<->{b}", m))
        for target, source in ((a, b), (b, a)):
            m = copy.deepcopy(CASES)
            rows_t = {r["name"]: r for r in m[s1] + m[s2]}
            for key in rows_t[target]:
                if key != "name":
                    rows_t[target][key] = copy.deepcopy(
                        rows_t[source][key])
            mutants.append((f"replace:{target}<-{source}", m))
    for label, m in mutants:
        with pytest.raises(AssertionError):
            _validate_structure(m)
        assert label


def _section_of(row):
    for section in MANIFESTS:
        if row["name"] in MANIFESTS[section]:
            return section
    return "?"


def test_row_digest_detects_malformed_and_rollback_payload_swap():
    """A payload swap between two malformed rows of the SAME
    failure class and repair form, and a follow-up swap between
    rollback rows, are caught by the whole-row digest."""
    m = copy.deepcopy(CASES)
    _replace_payload(m, "malformed", "key-empty",
                     "key-bad-leading-char")
    with pytest.raises(AssertionError):
        _validate_structure(m)
    m = copy.deepcopy(CASES)
    rows = {r["name"]: r for r in m["rollback"]}
    a = rows["rejected-apply-raising-fingerprinter-then-valid-apply"]
    b = rows["rejected-apply-corrupt-source-then-valid-apply"]
    a["then_request"], b["then_request"] = \
        b["then_request"], a["then_request"]
    with pytest.raises(AssertionError):
        _validate_structure(m)


def test_row_digest_table_is_closed():
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    for section in MANIFESTS:
        for row in CASES[section]:
            assert ROW_DIGESTS[f"{section}:{row['name']}"] == \
                _row_digest(row)


def _named(m, section, name):
    return next(r for r in m[section] if r["name"] == name)


def _verifier_mutants():
    """The verifiers' confirmed fixture-only mutants A-E plus the
    single-field edge erasures, each applied to an in-memory copy
    of cases.json with its pinned result REGENERATED from the
    reference where the row stays executable - so only the
    closure (order, digests, edge realization) can kill it."""
    out = []

    def regen(row):
        row["expect"] = _apply(row["oracle"], row["log"],
                               row["ledger"], row["request"])

    # A: swap names of two same-pin boundary rows
    m = copy.deepcopy(CASES)
    _swap_names(m, "boundary", "key-max-length-128",
                "same-payload-distinct-key-applies-again")
    out.append(("A-name-swap", m))
    # B: key-single-char carries apply-first-key-empty-store data
    m = copy.deepcopy(CASES)
    tgt = _named(m, "boundary", "key-single-char")
    src = _named(m, "happy", "apply-first-key-empty-store")
    for key in tgt:
        if key != "name":
            tgt[key] = copy.deepcopy(src[key])
    out.append(("B-payload-substitution", m))
    # D: new-key row given the starting-position payload
    m = copy.deepcopy(CASES)
    row = _named(m, "happy", "apply-new-key-extends-store")
    row["request"]["payload"] = copy.deepcopy(
        _named(m, "happy", "apply-first-key-empty-store")[
            "request"]["payload"])
    regen(row)
    out.append(("D-new-key-startpos-payload", m))
    # E: reversed happy and boundary rows
    m = copy.deepcopy(CASES)
    m["happy"].reverse()
    m["boundary"].reverse()
    out.append(("E-reversed-rows", m))
    # edge erasures
    for name, mutate in (
            ("key-max-length-128",
             lambda r: r["request"].__setitem__(
                 "idempotency_key", "Kxxxx")),
            ("key-single-char",
             lambda r: r["request"].__setitem__(
                 "idempotency_key", "cc")),
            ("same-payload-distinct-key-applies-again",
             lambda r: r["request"].__setitem__(
                 "payload", copy.deepcopy(
                     _named(CASES, "happy",
                            "apply-new-key-extends-store")[
                         "request"]["payload"])))):
        m = copy.deepcopy(CASES)
        row = _named(m, "boundary", name)
        mutate(row)
        regen(row)
        out.append((f"edge-erased:{name}", m))
    # replay-delete-receipt-at-tip replaced by a mid-log put replay
    m = copy.deepcopy(CASES)
    row = _named(m, "boundary", "replay-delete-receipt-at-tip")
    first = row["ledger"][0]
    entry = row["log"][first["sequence"] - 1]
    row["request"] = {"idempotency_key": first["idempotency_key"],
                      "op": entry["op"],
                      "payload": copy.deepcopy(entry["payload"])}
    regen(row)
    out.append(("edge-erased:replay-delete-receipt-at-tip", m))
    return out


@pytest.mark.parametrize("with_digests", [True, False],
                         ids=["with-digests", "digests-neutralized"])
def test_closure_kills_verifier_mutants(with_digests, monkeypatch):
    """Every verifier mutant A-E and each single-field edge
    erasure is killed by _validate_structure both with the digest
    table live AND with _row_digest monkeypatched to return the
    table value (digests neutralized): ordered names and the
    semantic edge checks close happy/boundary on their own."""
    if not with_digests:
        monkeypatch.setattr(sys.modules[__name__], "_row_digest",
                            lambda row: ROW_DIGESTS[
                                f"{_section_of(row)}:{row['name']}"])
    for label, m in _verifier_mutants():
        with pytest.raises(AssertionError):
            _validate_structure(m)
        assert label
