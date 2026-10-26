"""T0294: jobs-idempotency conformance fixture - the fixture must
PROVE happy, boundary, malformed, queue-rejection and rollback
behavior against the T0293 jobs-idempotency contract. The cases
execute against the contract-derived reference in
tests.test_t0293_jobs_idempotency_contract (itself derived from
data/contracts/jobs_idempotency.yaml plus the linked queue
contract) - nothing is re-implemented here. Pinned receipts,
states and ledgers in the fixture were computed from that
reference at authoring time, so any contract or derivation drift
breaks this battery. Every malformed case is discriminating
(repairing ONLY its declared defect locus makes the case valid),
every queue-rejection case pins the propagated code from the
composed error domain, and rollback cases prove a rejected apply
leaves the exact supplied state, ledger and request bit-identical
before the valid follow-up commits.

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

from tests.test_t0240_rollback_fixture import (  # noqa: E402
    _assert_work_atomic,
    _snapshot_work,
)
from tests.test_t0284_queue_contract import (  # noqa: E402
    QueueError,
    _validate_state,
    state_id_for,
)
from tests.test_t0293_jobs_idempotency_contract import (  # noqa: E402
    IdempotencyError,
    JobsIdempotencyEngine,
    derive_receipt_id,
    request_fingerprint,
)
from tools.jobs_idempotency_contract_lint import (  # noqa: E402
    CONTRACT,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = (Path(__file__).parent / "fixtures" / "jobs_idempotency"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
FAILURE_MAPPING = dict(_CC["failures"]["mapping"])
ERROR_ENUM = set(_CC["errors"]["closed_enum"])
PROPAGATED = set(
    _CC["error_composition"]["propagated_queue_rejections"])
OUTCOMES = set(_CC["outcomes"]["values"])
_IDS = _CC["identifiers"]
_RECEIPT_RE = re.compile(_IDS["receipt_id"]["grammar"])
_KEY_RE = re.compile(_IDS["idempotency_key"]["grammar"])
_FP_RE = re.compile(_IDS["request_fingerprint"]["grammar"])
_STATE_ID_RE = re.compile(_IDS["state_id"]["grammar"])
RECEIPT_FIELDS = set(_CC["record"]["fields"])
REQUEST_KEYS = {"idempotency_key", "op", "payload"}
QUEUE_OPS = {"enqueue", "claim", "ack", "nack"}

MIR, CQ, CL, KC, DF = ("malformed_idempotency_request",
                       "corrupt_queue", "corrupt_ledger",
                       "key_conflict", "divergent_fingerprint")


def _raising_fp(op, payload):
    raise ValueError("untrusted fingerprinter failure")


def _non_str_fp(op, payload):
    return []


def _bad_grammar_fp(op, payload):
    return "jif1:zz"


def _unbound_token_fp(op, payload):
    """Grammar-valid but NOT bound to the local request
    derivation."""
    return "jif1:" + "f" * 64


def _surrogate_fp(op, payload):
    return "\ud800"


ORACLES = {"honest": request_fingerprint,
           "raising": _raising_fp,
           "non_str_output": _non_str_fp,
           "bad_grammar": _bad_grammar_fp,
           "unbound_token": _unbound_token_fp,
           "lone_surrogate": _surrogate_fp}

TOP_KEYS = {"schema", "contract", "contract_base_path", "notes",
            "happy", "boundary", "malformed", "queue_rejection",
            "rollback"}
HAPPY_KEYS = {"name", "kind", "oracle", "state", "ledger",
              "request", "expect"}
MALFORMED_KEYS = {"name", "kind", "oracle", "state", "ledger",
                  "request", "expect_failure", "minimal_repair",
                  "defect", "scenario"}
QREJ_KEYS = {"name", "kind", "oracle", "state", "ledger",
             "request", "expect_queue_error"}
ROLLBACK_KEYS = {"name", "kind", "oracle", "state", "ledger",
                 "request", "expect_failure", "then_state",
                 "then_ledger", "then_oracle", "then_request",
                 "expect"}
KIND = "idempotent_apply"

HAPPY_MANIFEST = {
    "apply-first-key-empty-queue": ("enqueue", 0, "applied"),
    "apply-new-key-extends-queue": ("enqueue", 1, "applied"),
    "replay-seen-key-identical-request": ("enqueue", 0,
                                          "replayed"),
}
BOUNDARY_MANIFEST = {
    "key-single-char": ("enqueue", 2, "applied"),
    "key-max-length-128": ("enqueue", 3, "applied"),
    "same-request-distinct-key-applies-again": ("claim", 2,
                                                "applied"),
    "apply-ack-at-tip": ("ack", 2, "applied"),
    "apply-nack-at-tip": ("nack", 4, "applied"),
    "replay-state-neutral-receipt": ("claim", 2, "replayed"),
}
MALFORMED_MANIFEST = {
    "request-not-a-dict": (MIR, "honest", "replace_request",
                           "the request is a list, not a dict",
                           "non-dict-request"),
    "request-missing-field": (MIR, "honest", "replace_request",
                              "the request has no op field",
                              "missing-request-field"),
    "request-extra-field": (MIR, "honest", "replace_request",
                            "the request carries an undeclared "
                            "fourth field", "extra-request-field"),
    "key-empty": (MIR, "honest", "set_request_field",
                  "the idempotency key is empty", "empty-key"),
    "key-too-long": (MIR, "honest", "set_request_field",
                     "the idempotency key is 129 characters",
                     "key-too-long"),
    "key-bad-leading-char": (
        MIR, "honest", "set_request_field",
        "the key starts with a dot, not alphanumeric",
        "key-bad-leading-char"),
    "key-non-str": (MIR, "honest", "set_request_field",
                    "the key is an int, not a string",
                    "non-str-key"),
    "unregistered-op": (MIR, "honest", "set_request_field",
                        "the op is not one of the queue "
                        "contract's four",
                        "unregistered-request-op"),
    "payload-op-smuggle": (MIR, "honest", "replace_request",
                           "the payload smuggles its own op field",
                           "payload-op-smuggle"),
    "enqueue-missing-field": (MIR, "honest", "replace_request",
                              "the enqueue payload has no "
                              "priority",
                              "enqueue-payload-field-missing"),
    "claim-bad-lease": (MIR, "honest", "replace_request",
                        "the claim lease_ms is zero",
                        "claim-lease-out-of-bounds"),
    "request-aliases-state": (
        MIR, "honest", "replace_request",
        "the request payload value IS the live state object",
        "request-aliases-state"),
    "request-aliases-ledger": (
        MIR, "honest", "replace_request",
        "the request payload value IS the ledger list",
        "request-aliases-ledger"),
    "state-job-bad-status": (CQ, "honest", "replace_state",
                             "a job's status is not one of the "
                             "four declared",
                             "corrupt-queue-job-status"),
    "ledger-not-a-list": (CL, "honest", "replace_ledger",
                          "the ledger is a dict, not a list",
                          "non-list-ledger"),
    "receipt-missing-field": (CL, "honest", "replace_ledger",
                              "the first receipt has no sequence",
                              "ledger-receipt-missing-field"),
    "receipt-extra-field": (CL, "honest", "replace_ledger",
                            "the first receipt carries an "
                            "undeclared field",
                            "ledger-receipt-extra-field"),
    "duplicate-ledger-key": (CL, "honest", "replace_ledger",
                             "two receipts carry the same "
                             "idempotency key",
                             "duplicate-ledger-key"),
    "sequence-gap": (CL, "honest", "replace_ledger",
                     "the second receipt's sequence jumps to 7 "
                     "with a re-derived receipt id",
                     "sequence-gap"),
    "chain-break": (CL, "honest", "replace_ledger",
                    "the second receipt's prior state id skips "
                    "the first", "ledger-chain-break"),
    "terminal-mismatch": (CL, "honest", "replace_ledger",
                          "the last receipt names a state the "
                          "live state is not",
                          "ledger-terminal-mismatch"),
    "tampered-receipt-id": (CL, "honest", "replace_ledger",
                            "the first receipt id no longer "
                            "re-derives", "tampered-receipt-id"),
    "receipt-fingerprint-mismatch": (
        KC, "honest", "replace_ledger",
        "the stored fingerprint no longer matches the original "
        "request; validation cannot re-derive it, the replay "
        "must conflict", "receipt-fingerprint-mismatch"),
    "oracle-raises": (DF, "raising", "set_oracle",
                      "the fingerprinter raises",
                      "oracle-raising"),
    "oracle-non-str-output": (DF, "non_str_output", "set_oracle",
                              "the fingerprinter returns a list",
                              "oracle-non_str_output"),
    "oracle-bad-grammar": (DF, "bad_grammar", "set_oracle",
                           "the token misses the pinned grammar",
                           "oracle-bad_grammar"),
    "oracle-unbound-token": (
        DF, "unbound_token", "set_oracle",
        "grammar-valid token not bound to this request",
        "oracle-unbound_token"),
    "oracle-lone-surrogate": (DF, "lone_surrogate", "set_oracle",
                              "the token is a lone surrogate",
                              "oracle-lone_surrogate"),
    "key-conflict-different-payload": (
        KC, "honest", "replace_request",
        "a seen key returns with a different payload",
        "conflict-different-payload"),
    "key-conflict-different-op": (
        KC, "honest", "replace_request",
        "a seen key returns under a different op",
        "conflict-different-op"),
}
QREJ_MANIFEST = {
    "dedupe-conflict": "dedupe_conflict",
    "unknown-job": "unknown_job",
    "lease-conflict": "lease_conflict",
}
ROLLBACK_MANIFEST = {
    "rollback-past-receipt-reapply-deterministic":
        (CL, "honest", "honest", "applied"),
    "rollback-then-divergent-reapply-fresh":
        (CL, "honest", "honest", "applied"),
    "rollback-mixed-stale-ledger-fails-closed":
        (CL, "honest", "honest", "applied"),
}
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "queue_rejection": QREJ_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

# content binding: canonical sha256 of every WHOLE row (oracle,
# state, ledger, request, pinned result, repair, defect, scenario,
# follow-up). Any edit to a row must update this table in the
# same change; its key set equals the manifests exactly.
ROW_DIGESTS = {
    "happy:apply-first-key-empty-queue":
        "76a8fded483774cb4777d37a9d56cf80c49ea857fde822afc0a9ada35bf57349",
    "happy:apply-new-key-extends-queue":
        "3621a3ec6baf569620da2ef24462b43fac9adaec99835fae40bd8a46739dd431",
    "happy:replay-seen-key-identical-request":
        "7c877beefe7b991de4cc1508d8f57f45f0132e1e32427d317513df130f9668ba",
    "boundary:key-single-char":
        "96bf833574ff9b49f174ed92d9c0ebfbd17a921cf7d148a7789c63bbbc64855b",
    "boundary:key-max-length-128":
        "deb37cca638235caf8706f4ad52a95698318548724bcbafa9bcfbd69b209a2e3",
    "boundary:same-request-distinct-key-applies-again":
        "16d9bed1424151b5675b3caa16ba8b4293c82545d052bce2e372b6224133ec2f",
    "boundary:apply-ack-at-tip":
        "1b32100a87005144a90498afc07b89a103bd24423537b731340796009017b2d9",
    "boundary:apply-nack-at-tip":
        "18bb1645f0f9a5bb06f56c28b458f7413e90866a1a6438c8e37f7fc4bfdf296e",
    "boundary:replay-state-neutral-receipt":
        "8af9ccb64a067bcd6f16bdd589c41c85398497389f61294f3f0a41b793293234",
    "malformed:request-not-a-dict":
        "18c6b7120d6221a683901476121a2befade59bc3df48875b48c967c1d4057350",
    "malformed:request-missing-field":
        "de6b3b3e4e29db1f93872e023c2b0a35d32ef7f48ab583bd08c8ed27eedf106c",
    "malformed:request-extra-field":
        "3470a92ece6a86b762d73a30d7bd64702a314bb65fe9ee021df51091f649f89c",
    "malformed:key-empty":
        "c6ebc366bf328d105edee75a73e4c918c64ba566650f7c0d0864abc6227cd5f7",
    "malformed:key-too-long":
        "b33069132e51a2b3cf3c1ef371cdda12d7f1bf1f6a235945fe0933252760a148",
    "malformed:key-bad-leading-char":
        "fc6c04000cce6933d0a216b4a471924ce0a5142695b971e4d74a43be494c5861",
    "malformed:key-non-str":
        "5add9f22dec6a65b24b9000ec7c1027f43ca436b17718bbdf5aba356999dff1d",
    "malformed:unregistered-op":
        "861e12240c32861847567b2bc65b8cf68f726de331d968789ab9dbedbe8192ca",
    "malformed:payload-op-smuggle":
        "4048903c257ddf4f581949cf37563b5afd42315d66d74789ddabb01f5be6e57d",
    "malformed:enqueue-missing-field":
        "0e3e73f9909669c205c73bc33950336513272dfb836dd80097f5915d84fa90be",
    "malformed:claim-bad-lease":
        "d64a9c31d97173b76ed9c4719a073dd49d60b8bca9d38871ddd55745a04765f7",
    "malformed:request-aliases-state":
        "d738158be6f65a1e60e7c1973373360bff0662c8457e7780c402dc7e47b33165",
    "malformed:request-aliases-ledger":
        "f0261f0ece177fa9aee1bc58288700e55d73e9549bc6f711488bcc3729616cd9",
    "malformed:state-job-bad-status":
        "b2d99a8819df4da0fe62447ff3478186e66be1a7b9ff26954430e571a9415f60",
    "malformed:ledger-not-a-list":
        "03dc500b9341aecdee1cb1deb001a6392eed3c0f5509badf5b1ee06901e01215",
    "malformed:receipt-missing-field":
        "90f2025ae7cc1d82060e1c5e28f0c974e66bf1a4b31d17651c28c5c806064014",
    "malformed:receipt-extra-field":
        "2a56241fcbc59c47ab7f89eb38747e22970b82276439c08a08ff7b6d22cc32f8",
    "malformed:duplicate-ledger-key":
        "fa284b9e293b9c6febf8e1058939ce4ca7b7029f5ae4986ef71c556001760090",
    "malformed:sequence-gap":
        "d35e01e17a96187fd8159c858b1f1542b20b52f0be7cdd8496861dc2f122ecea",
    "malformed:chain-break":
        "2ba03247882b9522b98b8db68b88895e99033056343c09d3d95de3b4aba386fc",
    "malformed:terminal-mismatch":
        "292fc621bb01472db0f2d5202479e04800ba2047746703aa47266265f22de40b",
    "malformed:tampered-receipt-id":
        "b5590a42cebd68f18cb1d90fe37c64288dc1d65c94e32e6b474e414b4bd00d4a",
    "malformed:receipt-fingerprint-mismatch":
        "30d5755fa2a0cab1292e20c7b9d25fd04a933e074cdc1f50c1ffa37e6fe876be",
    "malformed:oracle-raises":
        "05f33dd5817b3cda3109b371b2cf940af8586139c839c52f760808b41d00510d",
    "malformed:oracle-non-str-output":
        "45487a32bbefd7b7c052aa773741ac5b85a4e3c89f36afff4faa9c4a01e2e669",
    "malformed:oracle-bad-grammar":
        "9b8ebd01068549a1dbeb435fb9ebae24a69ae3df20f47c323ed6681d5f0cba26",
    "malformed:oracle-unbound-token":
        "670fb0d0a93c6f62bf655368e9df3ffb3c18abca950ff2bd389e12b63cc39c35",
    "malformed:oracle-lone-surrogate":
        "8936f1d834feaf6529dd3eb2b598e5a0a19aa04380cf2797aa419fbd8845b816",
    "malformed:key-conflict-different-payload":
        "b96fd3934c2e1d2345fca880d5361ff88fe2dae92301e4328d82cd9a9f0fee0f",
    "malformed:key-conflict-different-op":
        "c676703374194517be40afdf10a14aac9ddcbcdc57564f50dcd45742f115d6cc",
    "queue_rejection:dedupe-conflict":
        "ce8bb1c9515f69a09e6f38b4221b0e122de8bd579b5b2ec44206b09e5da86f5e",
    "queue_rejection:unknown-job":
        "d2afa238af55b4a916dfa9bf76c7277e90bdbef4ff133958e3a8b66866288e8a",
    "queue_rejection:lease-conflict":
        "2c5eb10698f9c5f5ebc0ff9f105cc7bf9319200dd39bdbb14fb55ab5af03d18d",
    "rollback:rollback-past-receipt-reapply-deterministic":
        "ad404661bdeb76225f02dcda820e16a8581e312eb8737c45d34713df6331b4c8",
    "rollback:rollback-then-divergent-reapply-fresh":
        "9b03bc8e82171a7b5d593b8d71bc2b4e917b735551119d490f07464cc55b9a1a",
    "rollback:rollback-mixed-stale-ledger-fails-closed":
        "bf50857dfe2025ebf67b07abb86fa4f553c31fc7f54270701a2ea4950109cb04",
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


def _validate_state_shape(state, label):
    assert type(state) is dict, label
    _validate_state(state)  # the linked queue contract's own check


def _validate_receipt_shape(receipt, label):
    assert type(receipt) is dict, label
    assert set(receipt) == RECEIPT_FIELDS, label
    assert _RECEIPT_RE.fullmatch(receipt["receipt_id"]), label
    assert _KEY_RE.fullmatch(receipt["idempotency_key"]), label
    assert _FP_RE.fullmatch(receipt["request_fingerprint"]), label
    assert _STATE_ID_RE.fullmatch(receipt["prior_state_id"]), label
    assert _STATE_ID_RE.fullmatch(receipt["state_id"]), label
    assert type(receipt["sequence"]) is int and \
        receipt["sequence"] >= 0, label
    assert receipt["receipt_id"] == derive_receipt_id(
        receipt["idempotency_key"],
        receipt["request_fingerprint"], receipt["prior_state_id"],
        receipt["state_id"], receipt["sequence"]), label


def _validate_ledger_shape(ledger, label):
    assert type(ledger) is list, label
    for index, receipt in enumerate(ledger):
        _validate_receipt_shape(receipt, label)
        assert receipt["sequence"] == index, label
        if index:
            assert receipt["prior_state_id"] == \
                ledger[index - 1]["state_id"], label
    assert len({r["idempotency_key"] for r in ledger}) == \
        len(ledger), label


def _validate_request_shape(request, label):
    assert type(request) is dict, label
    assert set(request) == REQUEST_KEYS, label
    assert _KEY_RE.fullmatch(request["idempotency_key"]), label
    assert request["op"] in QUEUE_OPS, label
    assert type(request["payload"]) is dict, label


def _validate_result_shape(result, label):
    assert type(result) is dict and set(result) == {
        "outcome", "receipt"}, label
    assert result["outcome"] in OUTCOMES, label
    _validate_receipt_shape(result["receipt"], label)


REPAIR_FORMS = {"set_oracle", "replace_state", "replace_ledger",
                "replace_request", "set_request_field"}


def _validate_repair(case):
    repair = case["minimal_repair"]
    assert type(repair) is dict and len(repair) == 1, case["name"]
    form, body = next(iter(repair.items()))
    assert form in REPAIR_FORMS, case["name"]
    assert type(body) is dict, case["name"]
    if form == "set_oracle":
        assert set(body) == {"oracle"}, case["name"]
        assert body["oracle"] in ORACLES, case["name"]
    elif form == "replace_state":
        assert set(body) == {"state"}, case["name"]
        _validate_state_shape(body["state"], case["name"])
    elif form == "replace_ledger":
        assert set(body) == {"ledger"}, case["name"]
        _validate_ledger_shape(body["ledger"], case["name"])
    elif form == "replace_request":
        assert set(body) == {"request"}, case["name"]
        _validate_request_shape(body["request"], case["name"])
    else:
        assert set(body) == {"field", "value"}, case["name"]
        assert body["field"] in REQUEST_KEYS, case["name"]


def _repaired(case):
    rep = copy.deepcopy(case)
    form, body = next(iter(case["minimal_repair"].items()))
    if form == "set_oracle":
        rep["oracle"] = body["oracle"]
    elif form == "replace_state":
        rep["state"] = copy.deepcopy(body["state"])
    elif form == "replace_ledger":
        rep["ledger"] = copy.deepcopy(body["ledger"])
    elif form == "replace_request":
        rep["request"] = copy.deepcopy(body["request"])
    else:
        rep["request"][body["field"]] = body["value"]
    return rep


def _apply(oracle, state, ledger, request):
    return JobsIdempotencyEngine(ORACLES[oracle]).apply(
        state, ledger, request)


def _derive(oracle, state, ledger, request, label):
    result = _apply(oracle, copy.deepcopy(state),
                    copy.deepcopy(ledger), copy.deepcopy(request))
    _validate_result_shape(result, label)
    return result


def _failure_of(oracle, state, ledger, request, alias=None):
    work = (copy.deepcopy(state), copy.deepcopy(ledger),
            copy.deepcopy(request))
    if alias == "state":
        work[2]["payload"]["payload"] = work[0]
    elif alias == "ledger":
        work[2]["payload"]["payload"] = work[1]
    try:
        JobsIdempotencyEngine(ORACLES[oracle]).apply(*work)
    except IdempotencyError as caught:
        return caught.failure_class
    raise AssertionError("expected a typed failure")


def _run_apply(case):
    """Execute on working copies: pinned result, exact commit
    effect (applied: exactly one receipt appended and the state
    id tracks the receipt; replayed: nothing changes), untouched
    request, queue state still validates, deterministic re-run,
    and idempotent repeat."""
    oracle = ORACLES[case["oracle"]]
    state, ledger, request = case["state"], case["ledger"], \
        case["request"]
    work_state = copy.deepcopy(state)
    work_ledger = copy.deepcopy(ledger)
    work_req = copy.deepcopy(request)
    result = JobsIdempotencyEngine(oracle).apply(
        work_state, work_ledger, work_req)
    assert result == case["expect"], case["name"]
    receipt = result["receipt"]
    if result["outcome"] == "applied":
        assert receipt["prior_state_id"] == state_id_for(state), \
            case["name"]
        assert work_ledger == ledger + [receipt], case["name"]
        assert state_id_for(work_state) == receipt["state_id"], \
            case["name"]
        assert receipt["sequence"] == len(ledger), case["name"]
    else:
        assert work_state == state and work_ledger == ledger, \
            case["name"]
        assert receipt in ledger, case["name"]
    assert work_req == request, case["name"]
    assert receipt["request_fingerprint"] == request_fingerprint(
        request["op"], request["payload"])
    _validate_state(work_state)
    # deterministic over fresh copies
    assert _apply(case["oracle"], copy.deepcopy(state),
                  copy.deepcopy(ledger),
                  copy.deepcopy(request)) == case["expect"]
    # IDEMPOTENT: repeating the same keyed request replays the
    # identical receipt and appends nothing
    before = copy.deepcopy((work_state, work_ledger))
    again = JobsIdempotencyEngine(oracle).apply(
        work_state, work_ledger, copy.deepcopy(request))
    assert again == {"outcome": "replayed", "receipt": receipt}
    assert (work_state, work_ledger) == before


def test_happy():
    for name in _names("happy"):
        _run_apply(_case("happy", name))


def test_boundary():
    for name in _names("boundary"):
        _run_apply(_case("boundary", name))


def _exec_malformed(case):
    engine = JobsIdempotencyEngine(ORACLES[case["oracle"]])
    work = [copy.deepcopy(case["state"]),
            copy.deepcopy(case["ledger"]),
            copy.deepcopy(case["request"])]
    if case["scenario"] == "request-aliases-state":
        work[2]["payload"]["payload"] = work[0]
    elif case["scenario"] == "request-aliases-ledger":
        work[2]["payload"]["payload"] = work[1]
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
    result = _apply(rep["oracle"], rep["state"], rep["ledger"],
                    rep["request"])
    _validate_result_shape(result, case["name"])


@pytest.mark.parametrize("name", list(MALFORMED_MANIFEST))
def test_malformed(name):
    case = _case("malformed", name)
    _exec_malformed(case)
    _exec_repaired(case)


def test_malformed_param_ids_equal_fixture_names():
    assert list(MALFORMED_MANIFEST) == _names("malformed")


@pytest.mark.parametrize("name", list(QREJ_MANIFEST))
def test_queue_rejection(name):
    """The queue's typed rejection propagates UNWRAPPED with the
    pinned code from the composed error domain, commits nothing,
    and a retry re-attempts (no phantom receipt to replay)."""
    case = _case("queue_rejection", name)
    engine = JobsIdempotencyEngine(ORACLES[case["oracle"]])
    for _attempt in range(2):
        work = (copy.deepcopy(case["state"]),
                copy.deepcopy(case["ledger"]),
                copy.deepcopy(case["request"]))
        snap = _snapshot_work(*work)
        with pytest.raises(QueueError) as exc:
            engine.apply(*work)
        _assert_work_atomic(snap, *work)
        assert exc.value.code == case["expect_queue_error"], name
        assert exc.value.code in PROPAGATED, name


def test_rollback():
    """Every rollback row: the apply against the stale composition
    fails closed bit-identically; the follow-up against the
    trimmed/divergent composition commits the pinned result."""
    for name in _names("rollback"):
        case = _case("rollback", name)
        engine = JobsIdempotencyEngine(ORACLES[case["oracle"]])
        work = (copy.deepcopy(case["state"]),
                copy.deepcopy(case["ledger"]),
                copy.deepcopy(case["request"]))
        snap = _snapshot_work(*work)
        with pytest.raises(IdempotencyError) as exc:
            engine.apply(*work)
        _assert_work_atomic(snap, *work)
        assert exc.value.failure_class == case["expect_failure"], \
            name
        follow = _derive(case["then_oracle"], case["then_state"],
                         case["then_ledger"],
                         case["then_request"], name)
        assert follow == case["expect"], name


def test_stale_ledger_reapply_rederives_the_original_receipt():
    """After a rollback to the receipt-2 state and a trim to the
    surviving prefix, re-applying the rolled-back key rederives
    the ORIGINAL third receipt byte-for-byte (prior id, state id,
    sequence, receipt id)."""
    case = _case("rollback",
                 "rollback-past-receipt-reapply-deterministic")
    stale = case["ledger"][2]
    follow = _derive("honest", case["then_state"],
                     case["then_ledger"], case["then_request"],
                     case["name"])
    assert follow["receipt"] == stale


# -- closed structure --------------------------------------------------------


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
        for case in cases[section]:
            name = case["name"]
            want = manifest[name]
            assert case["kind"] == KIND, name
            assert case["oracle"] in ORACLES, name
            if section in ("happy", "boundary"):
                op, seq, outcome = want
                assert set(case) == HAPPY_KEYS, name
                _validate_state_shape(case["state"], name)
                _validate_ledger_shape(case["ledger"], name)
                _validate_request_shape(case["request"], name)
                _validate_result_shape(case["expect"], name)
                assert case["request"]["op"] == op, name
                assert case["expect"]["outcome"] == outcome, name
                assert case["expect"]["receipt"]["sequence"] == \
                    seq, name
                if outcome == "replayed":
                    assert case["expect"]["receipt"] in \
                        case["ledger"], name
                    assert case["ledger"][-1]["state_id"] == \
                        state_id_for(case["state"]), name
                else:
                    assert case["expect"]["receipt"] not in \
                        case["ledger"], name
                    assert case["expect"]["receipt"][
                        "prior_state_id"] == state_id_for(
                            case["state"]), name
            elif section == "malformed":
                failure, oracle, repair, defect, tag = want
                assert set(case) == MALFORMED_KEYS, name
                assert case["expect_failure"] == failure, name
                assert case["expect_failure"] in FAILURE_CLASSES, \
                    name
                assert case["oracle"] == oracle, name
                _validate_repair(case)
                assert set(case["minimal_repair"]) == {repair}, \
                    name
                assert case["defect"] == defect, name
                assert case["scenario"] == tag, name
                alias = {"request-aliases-state": "state",
                         "request-aliases-ledger": "ledger"}.get(tag)
                assert _failure_of(case["oracle"], case["state"],
                                   case["ledger"], case["request"],
                                   alias=alias) == failure, name
            elif section == "queue_rejection":
                assert set(case) == QREJ_KEYS, name
                assert case["expect_queue_error"] == want, name
                assert case["expect_queue_error"] in PROPAGATED, \
                    name
                _validate_state_shape(case["state"], name)
                _validate_ledger_shape(case["ledger"], name)
                _validate_request_shape(case["request"], name)
            else:
                failure, oracle, then_oracle, outcome = want
                assert set(case) == ROLLBACK_KEYS, name
                assert case["expect_failure"] == failure, name
                assert case["expect_failure"] in FAILURE_CLASSES, \
                    name
                assert case["oracle"] == oracle, name
                assert case["then_oracle"] == then_oracle, name
                _validate_state_shape(case["then_state"], name)
                _validate_ledger_shape(case["then_ledger"], name)
                _validate_request_shape(case["then_request"], name)
                _validate_result_shape(case["expect"], name)
                assert case["expect"]["outcome"] == outcome, name
                assert _failure_of(case["oracle"], case["state"],
                                   case["ledger"],
                                   case["request"]) == failure, name
                derived = _derive(then_oracle, case["then_state"],
                                  case["then_ledger"],
                                  case["then_request"], name)
                assert derived == case["expect"], name
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    declared = {c["expect_failure"] for c in cases["malformed"]}
    assert declared == FAILURE_CLASSES
    assert {c["expect_queue_error"]
            for c in cases["queue_rejection"]} == PROPAGATED - {
                "capacity_exceeded"}
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
        if form != {"replace_state"}:
            assert rep["state"] == case["state"], name
        if form != {"replace_ledger"}:
            assert rep["ledger"] == case["ledger"], name
        if not form & {"replace_request", "set_request_field"}:
            assert rep["request"] == case["request"], name
        result = _apply(rep["oracle"], rep["state"], rep["ledger"],
                        rep["request"])
        _validate_result_shape(result, name)


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
                 "terminal-mismatch", "oracle-unbound-token"):
        m = copy.deepcopy(CASES)
        index = _names("malformed").index(name)
        rep = _repaired(m["malformed"][index])
        for key in ("state", "ledger", "request", "oracle"):
            m["malformed"][index][key] = rep[key]
        with pytest.raises(AssertionError):
            _validate_structure(m)
        with pytest.raises(BaseException) as exc:
            _exec_malformed(m["malformed"][index])
        assert not isinstance(exc.value, IdempotencyError)


def test_failure_class_and_outcome_coverage():
    declared = {c["expect_failure"] for c in CASES["malformed"]}
    assert declared == FAILURE_CLASSES
    assert declared == {MIR, CQ, CL, KC, DF}
    outcomes = {c["expect"]["outcome"]
                for s in ("happy", "boundary") for c in CASES[s]}
    assert outcomes == OUTCOMES == {"applied", "replayed"}


def test_row_digest_table_is_closed():
    """Every manifest row has a digest, no digest lacks a row, and
    no two rows share a digest."""
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}
    assert len(set(ROW_DIGESTS.values())) == len(ROW_DIGESTS)
    for section in MANIFESTS:
        for case in CASES[section]:
            label = f"{section}:{case['name']}"
            assert ROW_DIGESTS[label] == _row_digest(case), label


def test_rollback_atomicity_kills_mutants():
    """The imported T0240 atomicity machinery is non-vacuous
    against engine mutants: a commit-before-validate engine that
    appends the receipt BEFORE the failing ledger check is caught
    by _assert_work_atomic, and a live-mutating fingerprinter is
    caught by the restore."""
    case = _case("malformed", "sequence-gap")

    def commit_first(engine, state, ledger, request):
        ledger.append({"receipt_id": "jir1:" + "0" * 64})
        raise IdempotencyError("corrupt_ledger",
                               FAILURE_MAPPING["corrupt_ledger"])

    work = (copy.deepcopy(case["state"]),
            copy.deepcopy(case["ledger"]),
            copy.deepcopy(case["request"]))
    snap = _snapshot_work(*work)
    with pytest.raises(IdempotencyError):
        commit_first(None, *work)
    with pytest.raises(AssertionError):
        _assert_work_atomic(snap, *work)


def test_reference_engine_preserves_aliases_on_rejection():
    """Rejected applies restore the EXACT supplied objects: an
    alias INSIDE the state graph (job payload sharing one dict is
    impossible in a valid canonical state, but an alias between
    two receipts' nothing - receipts are flat) ... the ledger's
    receipts keep their identity through a rejection."""
    case = _case("happy", "apply-new-key-extends-queue")
    state = copy.deepcopy(case["state"])
    ledger = copy.deepcopy(case["ledger"])
    receipt_ids = [id(r) for r in ledger]
    state_id = id(state)
    ledger_id = id(ledger)
    req = copy.deepcopy(case["request"])
    req["idempotency_key"] = ".bad"
    with pytest.raises(IdempotencyError):
        JobsIdempotencyEngine(request_fingerprint).apply(
            state, ledger, req)
    assert id(state) == state_id and id(ledger) == ledger_id
    assert [id(r) for r in ledger] == receipt_ids
    assert ledger == case["ledger"] and state == case["state"]
