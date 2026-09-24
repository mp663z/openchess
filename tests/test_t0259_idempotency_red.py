"""T0259 permanent red battery for store-idempotency engines.

The battery drives EVERY row of the T0258 conformance fixture
(tests/fixtures/idempotency/cases.json), a closed set of totality
probes and pinned apply/replay probes through an engine class
constructed with a request fingerprinter:

- happy/boundary: the pinned result exactly (exact field types);
  applied: the SAME log and ledger lists extended by exactly one
  entry and one receipt (prior entries/receipts _snap-identical,
  the new entry equal to the request's op/payload and aliasing
  nothing of the request, the stored receipt equal to but never
  the same object as the returned one); replayed: log and ledger
  _snap-identical; the request untouched by value AND identity;
  exactly ONE fingerprinter call with a DETACHED by-value copy of
  (op, payload); determinism by value over fresh copies on the
  SAME instance (never the same object); and idempotency (the
  identical keyed request repeated on the committed state replays
  the identical receipt and appends nothing);
- malformed: the original input rejects with the pinned failure
  class and its mapped code, log, ledger and request untouched,
  the fingerprinter never called unless the class is key_conflict
  or divergent_fingerprint, and the declared minimal repair is
  accepted with the result the reference derives for it;
- rollback: a rejection leaves log, ledger and request untouched,
  then the valid follow-up (on the SAME engine instance when the
  fingerprinter is unchanged) returns the pinned result;
- totality: hostile requests, keys, ops, payloads and records,
  hostile logs and entries (first, middle AND last), hostile
  ledgers and receipts (first, middle AND last, including a
  re-signed receipt that repeats a sequence), dict/list
  subclasses, cyclic/deep/huge values, and hostile fingerprinter
  behavior (raising any BaseException, non-str/str-subclass/
  bad-grammar/unbound/unencodable output, fingerprinters that
  mutate the caller's log, ledger or request or their own
  argument at every depth mid-call - overwriting, clearing,
  popping, appending AND adding a foreign key to every entry,
  receipt and request container) each reject with the pinned
  failure class or, for accept-probes, return the pinned result
  with exactly the pinned commit and, on the SAME instance, still
  reject a bad request typed and still accept a clean apply - any
  other BaseException escaping is a failure.

Standalone-red convention (T0151, T0178, T0187, T0196, T0232,
T0241, T0250): the battery is permanently GREEN against the
contract-derived reference engine from
tests.test_t0257_idempotency_contract and every mutant below is
RED. The production task switches the binding by replacing ONLY
the two binding lines below with the production
IdempotencyEngine / IdempotencyError names; no assertion changes.

Fixture closure: ordered per-section manifests, per-row semantic
pins and closed per-tag checks over the ORIGINAL row data (an
unknown tag raises), and a ROW_DIGESTS whole-row sha256 table whose
key set equals the manifests. test_closure_kills_substitution_mutants
proves substitution mutants are killed with the digest table live
AND with digests neutralized."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0212_wal_contract as _wal_module  # noqa: E402
from tests import test_t0257_idempotency_contract as _reference  # noqa: E402
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    WalEngine,
    WalError,
    _log_of,
    _payload,
    canonical_payload,
)
from tests.test_t0257_idempotency_contract import (  # noqa: E402
    derive_receipt_id,
    request_fingerprint,
)
from tests.test_t0258_idempotency_fixture import (  # noqa: E402
    MALFORMED_MANIFEST as _T0258_MALFORMED,
)
from tests.test_t0258_idempotency_fixture import (  # noqa: E402
    ORACLES,
    _assert_cardinalities,
    _assert_edge,
    _assert_malformed_scenario,
    _repaired,
    _validate_ledger_shape,
    _validate_log_shape,
    _validate_repair,
    _validate_request_shape,
    _validate_result_shape,
)
from tools.idempotency_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
)

# -- binding switch: the production task replaces ONLY these two
IdempotencyEngine = __import__("store.idempotency").idempotency.IdempotencyEngine
IdempotencyError = __import__("store.idempotency").idempotency.IdempotencyError

FIXTURE = (Path(__file__).parent / "fixtures" / "idempotency"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECEIPT_FIELDS = ("receipt_id", "idempotency_key",
                  "request_fingerprint", "entry_id", "sequence")
RESULT_FIELDS = ("outcome", "receipt")
REQUEST_FIELDS = ("idempotency_key", "op", "payload")
ENTRY_FIELDS = ("sequence", "op", "entry_id", "prior_entry_id",
                "payload")
RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
MIR = "malformed_idempotency_request"
CS = "corrupt_source"
CL = "corrupt_ledger"
KC = "key_conflict"
DF = "divergent_fingerprint"
# failure classes raised BEFORE the fingerprinter may be called
PRE_FINGERPRINT = frozenset({MIR, CS, CL})

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, t0258 cardinalities (oracle, outcome,
# receipt sequence, log length, ledger length), pins) where pins =
# (ops of the log, per-entry position S=STARTPOS K=KINGS
# E=AFTER_E4, request op, request position, key length)
HAPPY_MANIFEST = (
    ("apply-first-key-empty-store", ("honest", "applied", 1, 0, 0),
     ((), (), "put", "S", 7)),
    ("apply-new-key-extends-store", ("honest", "applied", 3, 2, 2),
     (("put", "put"), ("S", "K"), "put", "E", 1)),
    ("replay-seen-key-identical-request",
     ("honest", "replayed", 1, 2, 2),
     (("put", "put"), ("S", "K"), "put", "S", 1)),
)
BOUNDARY_MANIFEST = (
    ("key-single-char", ("honest", "applied", 1, 0, 0),
     ((), (), "put", "S", 1)),
    ("key-max-length-128", ("honest", "applied", 2, 1, 1),
     (("put",), ("S",), "put", "K", 128)),
    ("same-payload-distinct-key-applies-again",
     ("honest", "applied", 2, 1, 1),
     (("put",), ("S",), "put", "S", 1)),
    ("apply-over-ledger-covering-log-subset",
     ("honest", "applied", 3, 2, 1),
     (("put", "put"), ("S", "K"), "put", "E", 1)),
    ("replay-delete-receipt-at-tip", ("honest", "replayed", 3, 3, 3),
     (("put", "put", "delete"), ("S", "K", "S"), "delete", "S", 1)),
)
# malformed: (name, failure class, oracle, repair form, closed
# scenario tag)
MALFORMED_MANIFEST = (
    ("request-not-a-dict", MIR, "honest", "replace_request",
     "non-dict-request"),
    ("request-missing-field", MIR, "honest", "replace_request",
     "missing-request-field"),
    ("request-extra-field", MIR, "honest", "replace_request",
     "extra-request-field"),
    ("key-empty", MIR, "honest", "set_request_field", "empty-key"),
    ("key-too-long", MIR, "honest", "set_request_field",
     "key-too-long"),
    ("key-bad-leading-char", MIR, "honest", "set_request_field",
     "key-bad-leading-char"),
    ("key-non-str", MIR, "honest", "set_request_field", "non-str-key"),
    ("op-unregistered", MIR, "honest", "set_request_field",
     "unregistered-request-op"),
    ("payload-identity-mismatch", MIR, "honest", "set_request_field",
     "payload-identity-mismatch"),
    ("log-sequence-gap", CS, "honest", "replace_log", "sequence-gap"),
    ("log-tampered-entry-id", CS, "honest", "replace_log",
     "tampered-entry-id"),
    ("ledger-not-a-list", CL, "honest", "replace_ledger",
     "non-list-ledger"),
    ("ledger-tampered-receipt-id", CL, "honest", "replace_ledger",
     "tampered-receipt-id"),
    ("ledger-receipt-beyond-rolled-back-log", CL, "honest",
     "replace_ledger", "stale-receipt-beyond-log"),
    ("ledger-fingerprint-not-of-entry", CL, "honest",
     "replace_ledger", "receipt-fingerprint-mismatch"),
    ("ledger-duplicate-key", CL, "honest", "replace_ledger",
     "duplicate-ledger-key"),
    ("key-conflict-different-payload", KC, "honest",
     "set_request_field", "conflict-different-payload"),
    ("key-conflict-different-op", KC, "honest", "set_request_field",
     "conflict-different-op"),
    ("oracle-raises", DF, "raising", "set_oracle", "oracle-raises"),
    ("oracle-non-str-output", DF, "non_str_output", "set_oracle",
     "oracle-non-str-output"),
    ("oracle-bad-grammar", DF, "bad_grammar", "set_oracle",
     "oracle-bad-grammar"),
    ("oracle-unbound-token", DF, "unbound_token", "set_oracle",
     "oracle-unbound-token"),
    ("oracle-lone-surrogate", DF, "lone_surrogate", "set_oracle",
     "oracle-lone-surrogate"),
)
# rollback: (name, failure class, oracle, follow-up oracle,
# follow-up outcome, closed tag)
_RB = "rejected-apply-"
ROLLBACK_MANIFEST = (
    (_RB + "raising-fingerprinter-then-valid-apply", DF, "raising",
     "honest", "applied", "raising-then-honest"),
    (_RB + "key-conflict-then-replay", KC, "honest", "honest",
     "replayed", "conflict-then-replay"),
    (_RB + "corrupt-source-then-valid-apply", CS, "honest", "honest",
     "applied", "corrupt-log-then-repaired-log"),
    (_RB + "stale-ledger-after-log-rollback-then-trimmed-reapply", CL,
     "honest", "honest", "applied", "stale-ledger-then-trimmed"),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:apply-first-key-empty-store":
        "ac2315e63298f8db67137c459ef82cdfae3fb45b309afac6f8e12b19bebfcfa7",
    "happy:apply-new-key-extends-store":
        "36b28b01902a178056b166610b4c2cff84beb26b27208137113ce95fde7ce15d",
    "happy:replay-seen-key-identical-request":
        "282674a4ad110af56b766db8cb7412c76ca3e940f19b8e4e8f37f04c37905dc9",
    "boundary:key-single-char": "397f9c7b5fa48c21163d4668b443ceaac8283346f0ab06cfbb14efb47704e54c",
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
    "malformed:key-empty": "091ce1887945205ca05fb64452814f5401409d8ce2a7f47fb7ff0e759bb73a07",
    "malformed:key-too-long": "a61e89b653f47ae707888ad127e8e7302daecb1aeebc9d822aa9acd8d8d485e0",
    "malformed:key-bad-leading-char":
        "aefc1ec20d55c26e0a8835deaaf72ef43b295d9f555b32775e5686e1fd8e71ba",
    "malformed:key-non-str": "d82f7058f7a2a39a02018ea7352c10abc6a40ebe394a9cff4ee458f49233249d",
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
    "malformed:oracle-raises": "6765a7d760faf0ef4d6ad1253c0192cacdf6529c644c6a7d6a15e2c5de7ff6c0",
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
}  # GENERATED


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _section_of(row, cases=None):
    for section, rows in (cases or CASES).items():
        if isinstance(rows, list) and any(r is row for r in rows):
            return section
    for section, manifest in MANIFESTS.items():
        if row["name"] in {m[0] for m in manifest}:
            return section
    raise AssertionError(f"row {row.get('name')!r} has no section")


# -- per-tag semantic checks over ORIGINAL row data ---------------------------
def _wal_verifies(log):
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return False
    return True


def _reference_apply(oracle, log, ledger, request):
    return _reference.IdempotencyEngine(ORACLES[oracle]).apply(
        copy.deepcopy(log), copy.deepcopy(ledger),
        copy.deepcopy(request))


def _reference_failure(oracle, log, ledger, request):
    try:
        _reference_apply(oracle, log, ledger, request)
    except _reference.IdempotencyError as error:
        return error.failure_class
    return None


def _check_result(name, log, ledger, request, expect):
    """The pinned result realizes the contract over the ORIGINAL
    inputs: applied - the receipt names the entry the linked WAL
    appends for the request at len(log)+1; replayed - the receipt
    is the ledger's stored receipt for the key; the fingerprint is
    the local canonical derivation; the id derived."""
    assert _wal_verifies(log), name
    assert list(request) == list(REQUEST_FIELDS), name
    assert list(expect) == list(RESULT_FIELDS), name
    receipt = expect["receipt"]
    assert set(receipt) == set(RECEIPT_FIELDS), name
    key = request["idempotency_key"]
    assert receipt["idempotency_key"] == key, name
    assert receipt["request_fingerprint"] == request_fingerprint(
        request["op"], request["payload"]), name
    assert receipt["receipt_id"] == derive_receipt_id(
        key, receipt["request_fingerprint"], receipt["entry_id"],
        receipt["sequence"]), name
    stored = [r for r in ledger if r["idempotency_key"] == key]
    if expect["outcome"] == "applied":
        assert stored == [], name
        work = copy.deepcopy(log)
        entry = WalEngine(canonical_payload).append(
            work, {"op": request["op"],
                   "payload": copy.deepcopy(request["payload"])})
        assert receipt["sequence"] == len(log) + 1 == \
            entry["sequence"], name
        assert receipt["entry_id"] == entry["entry_id"], name
    else:
        assert expect["outcome"] == "replayed", name
        assert stored == [receipt], name


def _ops(log):
    return tuple(entry["op"] for entry in log)


_POSITIONS = {
    _log_of(("put", pos))[0]["payload"]["record"]["snapshot_fen"]: label
    for label, pos in (("S", STARTPOS), ("K", KINGS), ("E", AFTER_E4))}


def _positions(log):
    """Per-entry snapshot position label; an unknown position
    raises KeyError."""
    return tuple(_POSITIONS[entry["payload"]["record"]["snapshot_fen"]]
                 for entry in log)


def _check_ok(section, case, cards, pins):
    name = case["name"]
    log, ledger, request = case["log"], case["ledger"], case["request"]
    _validate_log_shape(log, name)
    _validate_ledger_shape(ledger, name)
    _validate_request_shape(request, name)
    _validate_result_shape(case["expect"], name)
    _assert_cardinalities(case, cards)
    _assert_edge(section, case)
    _check_result(name, log, ledger, request, case["expect"])
    ops, positions, op, position, key_len = pins
    assert (_ops(log), _positions(log), request["op"],
            _POSITIONS[request["payload"]["record"]["snapshot_fen"]],
            len(request["idempotency_key"])) == \
        (ops, positions, op, position, key_len), name


def _check_malformed(case, meta):
    name, failure, oracle, form, tag = meta
    assert case["expect_failure"] == failure, name
    assert case["oracle"] == oracle, name
    assert list(case["minimal_repair"]) == [form], name
    assert case["scenario"] == tag, name
    assert _T0258_MALFORMED[name] == (failure, oracle, form,
                                      case["defect"], tag), name
    _validate_repair(case)
    _assert_malformed_scenario(case, tag)
    assert _reference_failure(oracle, case["log"], case["ledger"],
                              case["request"]) == failure, name
    fixed = _repaired(case)
    _check_result(name, fixed["log"], fixed["ledger"],
                  fixed["request"],
                  _reference_apply(fixed["oracle"], fixed["log"],
                                   fixed["ledger"], fixed["request"]))


def _check_rollback(case, meta):
    """One closed branch per rollback tag; an unknown tag
    raises."""
    name, failure, oracle, then_oracle, outcome, tag = meta
    log, ledger, request = case["log"], case["ledger"], case["request"]
    then = (case["then_log"], case["then_ledger"], case["then_request"])
    assert case["expect_failure"] == failure, name
    assert case["oracle"] == oracle, name
    assert case["then_oracle"] == then_oracle, name
    for part in (log, then[0]):
        _validate_log_shape(part, name)
    for part in (ledger, then[1]):
        _validate_ledger_shape(part, name)
    for part in (request, then[2]):
        _validate_request_shape(part, name)
    _validate_result_shape(case["expect"], name)
    assert case["expect"]["outcome"] == outcome, name
    assert _reference_failure(oracle, log, ledger, request) == \
        failure, name
    assert _reference_apply(then_oracle, *then) == case["expect"], name
    _check_result(name, *then, case["expect"])
    key = request["idempotency_key"]
    if tag == "raising-then-honest":
        assert then == (log, ledger, request), name
    elif tag == "conflict-then-replay":
        assert then[:2] == (log, ledger), name
        (stored,) = [r for r in ledger if r["idempotency_key"] == key]
        entry = log[stored["sequence"] - 1]
        assert then[2] == {"idempotency_key": key, "op": entry["op"],
                           "payload": entry["payload"]}, name
        assert (request["op"], request["payload"]) != \
            (entry["op"], entry["payload"]), name
    elif tag == "corrupt-log-then-repaired-log":
        assert then[1:] == (ledger, request), name
        assert not _wal_verifies(log) and _wal_verifies(then[0]), name
        assert len(then[0]) == len(log), name
    elif tag == "stale-ledger-then-trimmed":
        assert then[0] == log and then[2] == request, name
        assert then[1] == ledger[:-1], name
        assert ledger[-1]["sequence"] > len(log), name
        assert case["expect"]["receipt"] == ledger[-1], name
    else:
        raise AssertionError(f"unknown rollback tag {tag!r}")


def _row(section, name, cases=None):
    (row,) = [r for r in (cases or CASES)[section]
              if r["name"] == name]
    return row


def _check_row(section, case, meta):
    if section in ("happy", "boundary"):
        _, cards, pins = meta
        _check_ok(section, case, cards, pins)
    elif section == "malformed":
        _check_malformed(case, meta)
    else:
        _check_rollback(case, meta)


def _validate_closure(cases):
    """Ordered names, whole-row digests and per-tag semantic
    checks for every section; the digest table's key set equals
    the manifests."""
    assert set(ROW_DIGESTS) == {
        f"{s}:{m[0]}" for s, man in MANIFESTS.items() for m in man}
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == \
            [m[0] for m in manifest], section
        for row, meta in zip(rows, manifest, strict=True):
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS[label] == _row_digest(row), label
            key = (section, json.dumps(row, sort_keys=True), repr(meta))
            if key not in _CHECKED:
                try:
                    _check_row(section, row, meta)
                except AssertionError as error:
                    _CHECKED[key] = f"{label}: {error}"
                except Exception as error:  # noqa: BLE001
                    _CHECKED[key] = f"{label}: {type(error).__name__}"
                else:
                    _CHECKED[key] = None
            if _CHECKED[key] is not None:
                raise AssertionError(_CHECKED[key])


# memo of per-row semantic check verdicts, keyed by (section,
# canonical row JSON, manifest meta): the checks are pure functions
# of the row, so the kill-proof sweep re-checks each distinct row once
_CHECKED = {}
# -- exact snapshot / aliasing -------------------------------------------------


class _KeyMark:
    __slots__ = ("key",)

    def __init__(self, key):
        self.key = key


def _snap(obj):
    """Exact flat snapshot: type, id, length and key order of every
    container, key identity (text for exact str keys, id otherwise),
    scalar type+value (huge ints by bit length and low bits).
    Iterative, cycle-safe, never hashes, compares or reprs a
    caller-owned key or subclass instance."""
    out, seen, stack = [], set(), [obj]
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is dict or kind is list or kind is tuple:
            if id(node) in seen:
                out.append(("seen", id(node)))
                continue
            seen.add(id(node))
            if kind is dict:
                items = list(dict.items(node))
                out.append(("dict", id(node), len(items)))
                for key, value in reversed(items):
                    stack.append(value)
                    stack.append(_KeyMark(key))
            else:
                values = list(kind.__iter__(node))
                out.append((kind.__name__, id(node), len(values)))
                stack.extend(reversed(values))
        elif kind is _KeyMark:
            key = node.key
            out.append(("key", key if type(key) is str
                        else (type(key).__name__, id(key))))
        elif kind is int:
            out.append(("int", id(node), node.bit_length(),
                        node & 0xFFFF))
        elif kind in (str, bool, float, type(None)):
            out.append((kind.__name__, node))
        else:
            out.append(("object", kind.__name__, id(node)))
    return out


def _not_aliased(result, inputs):
    if not isinstance(result, dict):
        return True
    inner = {e[1] for e in _snap(inputs)
             if e[0] in ("dict", "list", "tuple")}
    return not any(e[0] in ("dict", "list", "tuple") and e[1] in inner
                   for e in _snap(result))



class SK(str):
    """str-subclass key/value whose comparisons raise."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        raise RuntimeError("hostile str __eq__")

    def __ne__(self, other):
        raise RuntimeError("hostile str __ne__")


class HK:
    """Non-str key whose hash collides with a real key and whose
    __eq__ raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


class _DictSub(dict):
    """dict subclass whose accessors raise."""

    def items(self):
        raise RuntimeError("hostile items")

    def keys(self):
        raise RuntimeError("hostile keys")

    def __getitem__(self, key):
        raise RuntimeError("hostile __getitem__")


def _self_ref():
    loop = []
    loop.append(loop)
    return loop


def _deep():
    node = []
    for _ in range(100_000):
        node = [node]
    return node
def _containers(obj):
    return {e[1] for e in _snap(obj) if e[0] in ("dict", "list",
                                                  "tuple")}


class _Counting:
    """Wraps a fingerprinter; records every call's (op, payload)
    BY VALUE and the container ids it was handed."""

    def __init__(self, fingerprinter):
        self.fingerprinter, self.calls = fingerprinter, []

    def __call__(self, op, payload):
        try:
            value = (copy.deepcopy(op), copy.deepcopy(payload))
        except BaseException:  # noqa: BLE001
            value = None
        self.calls.append((value, _containers(payload)))
        return self.fingerprinter(op, payload)


def _engine(cls, oracle):
    counter = _Counting(ORACLES[oracle] if isinstance(oracle, str)
                        else oracle)
    return cls(counter), counter


def _receipt_ok(receipt, expect):
    return (type(receipt) is dict and receipt == expect
            and set(receipt) == set(RECEIPT_FIELDS)
            and type(receipt["sequence"]) is int
            and all(type(receipt[f]) is str for f in RECEIPT_FIELDS
                    if f != "sequence"))


def _result_ok(result, expect):
    return (type(result) is dict and set(result) == set(RESULT_FIELDS)
            and result == expect and type(result["outcome"]) is str
            and _receipt_ok(result["receipt"], expect["receipt"]))


def _checked_apply(engine, counter, log, ledger, request, expect):
    """One successful apply: pinned result; applied - the SAME log
    and ledger lists extended by exactly one entry and one receipt
    (prior items _snap-identical, the new entry realizing the
    request, the stored receipt equal to but not the returned
    object); replayed - log and ledger _snap-identical; request
    untouched; nothing of the result aliased with any input; one
    fingerprinter call with a detached by-value (op, payload)."""
    log_id, ledger_id = id(log), id(ledger)
    prior_log = [_snap(entry) for entry in log]
    prior_ledger = [_snap(receipt) for receipt in ledger]
    whole = (_snap(log), _snap(ledger))
    req_snap = _snap(request)
    want_call = (request["op"], copy.deepcopy(request["payload"]))
    live = _containers((log, ledger, request))
    n_before = len(counter.calls)
    result = engine.apply(log, ledger, request)
    calls = counter.calls[n_before:]
    ok = (_result_ok(result, expect)
          and id(log) == log_id and id(ledger) == ledger_id
          and type(log) is list and type(ledger) is list
          and _snap(request) == req_snap
          and _not_aliased(result, (log, ledger, request))
          and len(calls) == 1 and calls[0][0] == want_call
          and type(calls[0][0][0]) is str
          and not calls[0][1] & live)
    if not ok:
        return result, False
    if expect["outcome"] == "replayed":
        return result, (_snap(log), _snap(ledger)) == whole
    n_log, n_ledger = len(prior_log), len(prior_ledger)
    entry = log[-1] if log else None
    ok = (len(log) == n_log + 1 and len(ledger) == n_ledger + 1
          and [_snap(e) for e in log[:n_log]] == prior_log
          and [_snap(r) for r in ledger[:n_ledger]] == prior_ledger
          and type(entry) is dict
          and entry["entry_id"] == expect["receipt"]["entry_id"]
          and entry["sequence"] == expect["receipt"]["sequence"]
          and entry["op"] == request["op"]
          and entry["payload"] == request["payload"]
          and not _containers(entry) & _containers(request)
          and _receipt_ok(ledger[-1], expect["receipt"])
          and ledger[-1] is not result["receipt"]
          and _wal_verifies(log))
    return result, ok


def _apply_ok(cls, case, prefix="", engine=None):
    """Pinned result and commit, determinism BY VALUE on the SAME
    instance (a repeat over fresh copies never returns the same
    object), and idempotency: the identical request repeated on the
    committed state replays the identical receipt, appends
    nothing."""
    oracle = case[prefix + "oracle"]
    eng, counter = engine or _engine(cls, oracle)
    results, states = [], []
    for _ in range(2):
        log = copy.deepcopy(case[prefix + "log"])
        ledger = copy.deepcopy(case[prefix + "ledger"])
        result, ok = _checked_apply(
            eng, counter, log, ledger,
            copy.deepcopy(case[prefix + "request"]), case["expect"])
        if not ok:
            return False
        results.append(result)
        states.append((log, ledger))
    if results[1] is results[0]:
        return False
    log, ledger = states[1]
    replay = {"outcome": "replayed", "receipt": case["expect"]["receipt"]}
    _, ok = _checked_apply(eng, counter, log, ledger,
                           copy.deepcopy(case[prefix + "request"]), replay)
    return ok


def _rejects(engine, counter, log, ledger, request, failure):
    inputs = (log, ledger, request)
    snap = _snap(inputs)
    n_before = len(counter.calls)
    try:
        engine.apply(log, ledger, request)
    except IdempotencyError as error:
        calls = len(counter.calls) - n_before
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and error.code in ERROR_ENUM
                and _snap(inputs) == snap
                and calls == (0 if failure in PRE_FINGERPRINT else 1))
    return False


def _malformed_ok(cls, case):
    eng, counter = _engine(cls, case["oracle"])
    if not _rejects(eng, counter, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["ledger"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    fixed = _repaired(case)
    expect = _reference_apply(fixed["oracle"], fixed["log"],
                              fixed["ledger"], fixed["request"])
    if fixed["oracle"] != case["oracle"]:
        eng, counter = _engine(cls, fixed["oracle"])
    _, ok = _checked_apply(eng, counter, copy.deepcopy(fixed["log"]),
                           copy.deepcopy(fixed["ledger"]),
                           copy.deepcopy(fixed["request"]), expect)
    return ok


def _rollback_row_ok(cls, case):
    engine = _engine(cls, case["oracle"])
    if not _rejects(*engine, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["ledger"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    same = case["then_oracle"] == case["oracle"]
    return _apply_ok(cls, case, "then_", engine if same else None)


_RUNNERS = {"happy": _apply_ok, "boundary": _apply_ok,
            "malformed": _malformed_ok, "rollback": _rollback_row_ok}
# -- closed totality probes ----------------------------------------------------
def _base_store():
    """A valid three-entry log with a three-receipt ledger (keys a,
    b, c over STARTPOS, KINGS, AFTER_E4), built by the reference."""
    log, ledger = [], []
    engine = _reference.IdempotencyEngine(request_fingerprint)
    for key, pos in (("a", STARTPOS), ("b", KINGS), ("c", AFTER_E4)):
        engine.apply(log, ledger, {"idempotency_key": key, "op": "put",
                                   "payload": _payload(pos)})
    return log, ledger


def _probe_inputs():
    """Applied path: an unseen key d re-puts STARTPOS; every probe
    corrupts one component (or attacks through the
    fingerprinter)."""
    log, ledger = _base_store()
    return log, ledger, {"idempotency_key": "d", "op": "put",
                         "payload": _payload(STARTPOS)}


def _replay_inputs():
    """Replayed path: the seen key b with its identical request."""
    log, ledger = _base_store()
    return log, ledger, {"idempotency_key": "b", "op": "put",
                         "payload": _payload(KINGS)}


def _rekey(mapping, key, new_key):
    return {new_key if k == key else k: v for k, v in mapping.items()}


_HOSTILE_CONTAINERS = (("none", None), ("true", True), ("zero", 0),
                       ("float", 1.5), ("text", "text"))
_FORGED = "wal1:" + "f" * 64


class _IntSub(int):
    pass


class _SKV(str):
    """str subclass VALUE: equal and hashing like its text, but not
    an exact built-in str."""


def _req(fn):
    def build():
        log, ledger, request = _probe_inputs()
        return log, ledger, fn(request), "honest"
    return build


def _payload_req(fn):
    return _req(lambda r: {**r, "payload": fn(r["payload"])})


def _record_req(fn):
    return _payload_req(lambda p: {**p, "record": fn(p["record"])})


def _log(fn):
    def build():
        log, ledger, request = _probe_inputs()
        return fn(log), ledger, request, "honest"
    return build


def _ledger(fn):
    def build():
        log, ledger, request = _probe_inputs()
        return log, fn(ledger), request, "honest"
    return build


def _entry(fn, index):
    """Replace the log entry at INDEX (0: first, 1: middle, -1:
    last) with FN(copy)."""
    def edit(log):
        out = list(log)
        out[index] = fn(copy.deepcopy(log[index]))
        return out
    return _log(edit)


def _payload_edit(fn, index):
    return _entry(lambda e: {**e, "payload": fn(e["payload"])}, index)


def _record_edit(fn, index):
    return _payload_edit(lambda p: {**p, "record": fn(p["record"])},
                         index)


def _receipt(fn, index):
    """Replace the ledger receipt at INDEX (0: first, -1: last)
    with FN(copy)."""
    def edit(ledger):
        out = list(ledger)
        out[index] = fn(copy.deepcopy(ledger[index]))
        return out
    return _ledger(edit)


def _resigned(fn, index):
    """Edit one receipt field, then re-derive its receipt id so
    only the deeper binding can reject it."""
    def edit(receipt):
        out = fn(receipt)
        out["receipt_id"] = derive_receipt_id(
            out["idempotency_key"], out["request_fingerprint"],
            out["entry_id"], out["sequence"])
        return out
    return _receipt(edit, index)


def _fingerprinter(fp):
    def build():
        log, ledger, request = _probe_inputs()
        return log, ledger, request, fp
    return build


def _raises(exc):
    def fp(op, payload):
        raise exc
    return fp


def _honest_then(fn):
    return lambda op, payload: fn(request_fingerprint(op, payload))


_HOSTILE_FINGERPRINTERS = {
    "raises-value-error": _raises(ValueError("x")),
    "raises-runtime-error": _raises(RuntimeError("x")),
    "raises-keyboard-interrupt": _raises(KeyboardInterrupt()),
    "raises-system-exit": _raises(SystemExit(1)),
    "raises-generator-exit": _raises(GeneratorExit()),
    "returns-none": lambda o, p: None,
    "returns-int": lambda o, p: 7,
    "returns-list": _honest_then(lambda t: [t]),
    "returns-bytes": _honest_then(str.encode),
    "returns-str-subclass": _honest_then(lambda t: _SKV(t)),
    "returns-bad-grammar": lambda o, p: "idf1:zz",
    "returns-uppercase-hex": _honest_then(
        lambda t: t[:5] + t[5:].upper()),
    "returns-trailing-newline": _honest_then(lambda t: t + "\n"),
    "returns-lone-surrogate": _honest_then(lambda t: t[:-1] + "\ud800"),
    "returns-unbound-token": lambda o, p: "idf1:" + "f" * 64,
    "returns-other-op-token": lambda o, p: request_fingerprint(
        "delete", p),
    "returns-other-payload-token": lambda o, p: request_fingerprint(
        o, _payload(KINGS)),
}

# Accept-probes: the fingerprinter attacks the caller's OWN log,
# ledger and request (built-in methods only) or its own argument at
# every depth, AFTER deriving the honest token for what it was
# handed. The apply must still return exactly the pinned result
# with exactly the pinned commit.
ACCEPT = "accept"
REPLAY = "replay"
_LIVE_ATTACKS = ("mutates-first-entry", "mutates-last-entry",
                 "mutates-first-record", "appends-entry",
                 "pops-entry", "clears-log", "reorders-entry-keys",
                 "mutates-first-receipt", "mutates-last-receipt-key",
                 "pops-receipt", "appends-receipt", "clears-ledger",
                 "mutates-request-key", "mutates-request-op",
                 "mutates-request-record", "clears-request",
                 "mutates-own-argument-payload",
                 "mutates-own-argument-record",
                 "adds-entry-key", "adds-receipt-key",
                 "adds-request-key", "adds-payload-key",
                 "adds-record-key")
_REPLAY_ATTACKS = ("mutates-first-receipt", "mutates-last-receipt-key",
                   "clears-ledger", "mutates-request-key",
                   "mutates-own-argument-record",
                   "adds-receipt-key", "adds-request-key",
                   "adds-payload-key", "adds-record-key")


def _attack(kind, log, ledger, request, payload):
    if kind in ("mutates-first-entry", "mutates-then-raises"):
        dict.__setitem__(log[0], "entry_id", _FORGED)
    if kind == "mutates-last-entry":
        dict.__setitem__(log[-1], "op", "delete")
    if kind == "mutates-first-record":
        dict.__setitem__(log[0]["payload"]["record"], "digest", "x")
    if kind in ("appends-entry", "mutates-then-raises"):
        list.append(log, copy.deepcopy(log[-1]))
    if kind == "pops-entry":
        list.pop(log)
    if kind == "clears-log":
        list.clear(log)
    if kind == "reorders-entry-keys":
        entry = log[0]
        items = sorted(dict.items(entry), reverse=True)
        dict.clear(entry)
        dict.update(entry, items)
    if kind == "mutates-first-receipt":
        dict.__setitem__(ledger[0], "sequence", 99)
    if kind == "mutates-last-receipt-key":
        dict.__setitem__(ledger[-1], "idempotency_key", "d")
    if kind == "pops-receipt":
        list.pop(ledger)
    if kind == "appends-receipt":
        list.append(ledger, copy.deepcopy(ledger[0]))
    if kind == "clears-ledger":
        list.clear(ledger)
    if kind in ("mutates-request-key", "mutates-then-raises"):
        dict.__setitem__(request, "idempotency_key", "a")
    if kind == "mutates-request-op":
        dict.__setitem__(request, "op", "delete")
    if kind == "mutates-request-record":
        dict.__setitem__(request["payload"]["record"], "digest", "x")
    if kind == "clears-request":
        dict.clear(request)
    # key-ADDING attacks: a restore that update()s without clear()
    # leaves the foreign key behind - caught at every container
    if kind == "adds-entry-key":
        dict.__setitem__(log[0], "x", 1)
    if kind == "adds-receipt-key":
        dict.__setitem__(ledger[0], "x", 1)
    if kind == "adds-request-key":
        dict.__setitem__(request, "x", 1)
    if kind == "adds-payload-key":
        dict.__setitem__(request["payload"], "x", 1)
    if kind == "adds-record-key":
        dict.__setitem__(request["payload"]["record"], "x", 1)
    # own-argument attacks, one per depth: an engine sharing ANY
    # level of the frozen request with the fingerprinter is caught
    if kind == "mutates-own-argument-payload":
        dict.__setitem__(payload, "identity", "x")
    if kind == "mutates-own-argument-record":
        dict.__setitem__(payload["record"], "digest", "x")


def _live(kind, inputs=_probe_inputs):
    def build():
        log, ledger, request = inputs()

        def fp(op, payload):
            token = request_fingerprint(op, payload)
            _attack(kind, log, ledger, request, payload)
            if kind == "mutates-then-raises":
                raise ValueError("mutate then explode")
            return token
        return log, ledger, request, fp
    return build


def _entry_family(out, index, suffix):
    """Entry/payload/record probes against the entry at INDEX:
    0 is the FIRST entry, 1 the MIDDLE, -1 the LAST - the contract
    is fail-closed-typed over the FULL log."""
    def entry(fn):
        return _entry(fn, index)

    def payload(fn):
        return _payload_edit(fn, index)

    def record(fn):
        return _record_edit(fn, index)

    for label, value in (("none", None), ("zero", 0), ("list", []),
                         ("text", "text")):
        out[f"entry-{label}-{suffix}"] = (entry(lambda e, v=value: v), CS)
    out[f"entry-dict-subclass-{suffix}"] = (entry(_DictSub), CS)
    out[f"entry-extra-field-{suffix}"] = (entry(lambda e: {**e, "note": "x"}), CS)
    for field in ENTRY_FIELDS:
        out[f"entry-missing-{field}-{suffix}"] = (
            entry(lambda e, f=field: {k: v for k, v in e.items()
                                       if k != f}), CS)
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"entry-key-{field}-{label}-{suffix}"] = (
                entry(lambda e, f=field, c=cls: _rekey(e, f, c(f))), CS)
    for field in ("op", "entry_id", "prior_entry_id"):
        out[f"entry-value-{field}-str-subclass-{suffix}"] = (
            entry(lambda e, f=field: {**e, f: SK(e[f])}), CS)
    for label, value in (("int-subclass", _IntSub),
                         ("bool", lambda n: True), ("text", str),
                         ("huge-int", lambda n: 10 ** 5000)):
        out[f"entry-value-sequence-{label}-{suffix}"] = (
            entry(lambda e, v=value: {**e, "sequence": v(e["sequence"])}),
            CS)
    out[f"payload-dict-subclass-{suffix}"] = (payload(_DictSub), CS)
    for key in ("identity", "record"):
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"payload-key-{key}-{label}-{suffix}"] = (
                payload(lambda p, k=key, c=cls: _rekey(p, k, c(k))),
                CS)
    out[f"payload-value-identity-str-subclass-{suffix}"] = (
        payload(lambda p: {**p, "identity": SK(p["identity"])}), CS)
    out[f"record-dict-subclass-{suffix}"] = (record(_DictSub), CS)
    for field in RECORD_FIELDS:
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"record-key-{field}-{label}-{suffix}"] = (
                record(lambda r, f=field, c=cls: _rekey(r, f, c(f))),
                CS)
        out[f"record-value-{field}-str-subclass-{suffix}"] = (
            record(lambda r, f=field: {**r, f: SK(r[f])}), CS)
    out[f"record-value-self-referential-{suffix}"] = (
        record(lambda r: {**r, "digest": _self_ref()}), CS)
    out[f"record-value-deep-nested-{suffix}"] = (
        record(lambda r: {**r, "snapshot_fen": _deep()}), CS)
    out[f"record-value-huge-int-{suffix}"] = (
        record(lambda r: {**r, "digest": 10 ** 5000}), CS)


def _receipt_family(out, index, suffix):
    """Receipt probes against the ledger receipt at INDEX: 0 is
    the FIRST, 1 the MIDDLE, -1 the LAST - the ledger is validated
    in full."""
    def receipt(fn):
        return _receipt(fn, index)

    def resigned(fn):
        return _resigned(fn, index)

    for label, value in (("none", None), ("zero", 0), ("list", []),
                         ("text", "text")):
        out[f"receipt-{label}-{suffix}"] = (receipt(lambda r, v=value: v),
                                            CL)
    out[f"receipt-dict-subclass-{suffix}"] = (receipt(_DictSub), CL)
    out[f"receipt-extra-field-{suffix}"] = (
        receipt(lambda r: {**r, "note": "x"}), CL)
    for field in RECEIPT_FIELDS:
        out[f"receipt-missing-{field}-{suffix}"] = (
            receipt(lambda r, f=field: {k: v for k, v in r.items()
                                        if k != f}), CL)
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"receipt-key-{field}-{label}-{suffix}"] = (
                receipt(lambda r, f=field, c=cls: _rekey(r, f, c(f))), CL)
    for field in ("receipt_id", "idempotency_key", "request_fingerprint",
                  "entry_id"):
        out[f"receipt-value-{field}-str-subclass-{suffix}"] = (
            receipt(lambda r, f=field: {**r, f: SK(r[f])}), CL)
        out[f"receipt-value-{field}-int-{suffix}"] = (
            receipt(lambda r, f=field: {**r, f: 7}), CL)
    for label, value in (("int-subclass", _IntSub), ("bool", bool),
                         ("text", str), ("float", float),
                         ("huge-int", lambda n: 10 ** 5000),
                         ("zero", lambda n: 0),
                         ("negative", lambda n: -n),
                         ("beyond-log", lambda n: 99)):
        out[f"receipt-value-sequence-{label}-{suffix}"] = (
            receipt(lambda r, v=value: {**r, "sequence": v(r["sequence"])}),
            CL)
    out[f"receipt-value-self-referential-{suffix}"] = (
        receipt(lambda r: {**r, "entry_id": _self_ref()}), CL)
    out[f"receipt-tampered-id-{suffix}"] = (
        receipt(lambda r: {**r, "receipt_id": "idr1:" + "0" * 64}), CL)
    out[f"receipt-resigned-bad-key-{suffix}"] = (
        resigned(lambda r: {**r, "idempotency_key": ".bad"}), CL)
    out[f"receipt-resigned-foreign-entry-{suffix}"] = (
        resigned(lambda r: {**r, "entry_id": _FORGED}), CL)
    out[f"receipt-resigned-foreign-fingerprint-{suffix}"] = (
        resigned(lambda r: {**r, "request_fingerprint":
                            request_fingerprint("delete",
                                                _payload(KINGS))}), CL)


def _probe_builders():
    """name -> (builder returning (log, ledger, request,
    fingerprinter), pinned failure class, ACCEPT or REPLAY)."""
    out = {}
    for label, value in _HOSTILE_CONTAINERS + (("list", []),
                                               ("tuple", ())):
        out[f"request-{label}"] = (
            _req(lambda r, v=value: copy.deepcopy(v)), MIR)
    out["request-empty"] = (_req(lambda r: {}), MIR)
    out["request-dict-subclass"] = (_req(_DictSub), MIR)
    for field in REQUEST_FIELDS:
        out[f"request-missing-{field}"] = (
            _req(lambda r, f=field: {k: v for k, v in r.items()
                                     if k != f}), MIR)
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"request-key-{field}-{label}"] = (
                _req(lambda r, f=field, c=cls: _rekey(r, f, c(f))), MIR)
    out["request-key-none"] = (_req(lambda r: {**r, None: 1}), MIR)
    out["request-extra-key"] = (_req(lambda r: {**r, "force": True}),
                                MIR)
    for label, value in (("none", None), ("int", 7), ("bytes", b"d"),
                         ("str-subclass", _SKV("d")),
                         ("hostile-str-subclass", SK("d")),
                         ("empty", ""), ("leading-dot", ".d"),
                         ("trailing-newline", "d\n"),
                         ("space", "d d"), ("non-ascii", "d\u00e9"),
                         ("too-long", "d" * 129),
                         ("huge", "d" * 100_000)):
        out[f"request-key-value-{label}"] = (
            _req(lambda r, v=value: {**r, "idempotency_key": v}), MIR)
    for label, value in (("none", None), ("int", 1), ("upper", "PUT"),
                         ("unregistered", "upsert"),
                         ("str-subclass", _SKV("put")),
                         ("hostile-str-subclass", SK("put"))):
        out[f"request-op-{label}"] = (
            _req(lambda r, v=value: {**r, "op": v}), MIR)
    for label, value in (("none", None), ("list", []), ("text", "x")):
        out[f"request-payload-{label}"] = (
            _payload_req(lambda p, v=value: v), MIR)
    out["request-payload-dict-subclass"] = (_payload_req(_DictSub), MIR)
    out["request-payload-extra-key"] = (
        _payload_req(lambda p: {**p, "note": "x"}), MIR)
    for key in ("identity", "record"):
        out[f"request-payload-missing-{key}"] = (
            _payload_req(lambda p, k=key: {x: v for x, v in p.items()
                                           if x != k}), MIR)
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"request-payload-key-{key}-{label}"] = (
                _payload_req(lambda p, k=key, c=cls: _rekey(p, k, c(k))),
                MIR)
    out["request-payload-identity-str-subclass"] = (
        _payload_req(lambda p: {**p, "identity": SK(p["identity"])}), MIR)
    out["request-payload-identity-mismatch"] = (
        _payload_req(lambda p: {**p, "identity":
                                _payload(KINGS)["identity"]}), MIR)
    out["request-record-dict-subclass"] = (_record_req(_DictSub), MIR)
    for field in RECORD_FIELDS:
        out[f"request-record-missing-{field}"] = (
            _record_req(lambda r, f=field: {k: v for k, v in r.items()
                                            if k != f}), MIR)
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"request-record-key-{field}-{label}"] = (
                _record_req(lambda r, f=field, c=cls: _rekey(r, f, c(f))),
                MIR)
        out[f"request-record-value-{field}-str-subclass"] = (
            _record_req(lambda r, f=field: {**r, f: SK(r[f])}), MIR)
    out["request-record-value-self-referential"] = (
        _record_req(lambda r: {**r, "digest": _self_ref()}), MIR)
    out["request-record-value-deep-nested"] = (
        _record_req(lambda r: {**r, "snapshot_fen": _deep()}), MIR)
    out["request-record-value-huge-int"] = (
        _record_req(lambda r: {**r, "digest": 10 ** 5000}), MIR)
    for label, value in _HOSTILE_CONTAINERS + (("dict", {}),
                                               ("tuple", ())):
        out[f"log-{label}"] = (_log(lambda g, v=value: copy.deepcopy(v)),
                               CS)
    out["log-list-subclass"] = (_log(_ListSub), CS)
    out["log-duplicate-entry"] = (_log(lambda g: g + [g[-1]]), CS)
    out["log-reversed"] = (_log(lambda g: g[::-1]), CS)
    out["log-self-referential"] = (_log(_cyclic), CS)
    for index, suffix in ((0, "first"), (1, "middle"), (-1, "last")):
        _entry_family(out, index, suffix)
    for label, value in _HOSTILE_CONTAINERS + (("dict", {}),
                                               ("tuple", ())):
        out[f"ledger-{label}"] = (
            _ledger(lambda g, v=value: copy.deepcopy(v)), CL)
    out["ledger-list-subclass"] = (_ledger(_ListSub), CL)
    out["ledger-duplicate-receipt"] = (_ledger(lambda g: g + [g[-1]]),
                                       CL)
    out["ledger-reversed"] = (_ledger(lambda g: g[::-1]), CL)
    for index, suffix in ((0, "first"), (1, "middle"), (2, "last")):
        out[f"ledger-duplicate-sequence-{suffix}"] = (
            _ledger(lambda g, i=index: _duplicate_sequence(g, i)), CL)
    out["ledger-self-referential"] = (_ledger(_cyclic), CL)
    for index, suffix in ((0, "first"), (1, "middle"), (-1, "last")):
        _receipt_family(out, index, suffix)
    out["request-seen-key-other-payload"] = (
        _req(lambda r: {**r, "idempotency_key": "a",
                        "payload": _payload(KINGS)}), KC)
    out["request-seen-key-other-op"] = (
        _req(lambda r: {**r, "idempotency_key": "a", "op": "delete"}), KC)
    for label, fp in _HOSTILE_FINGERPRINTERS.items():
        out[f"fingerprinter-{label}"] = (_fingerprinter(fp), DF)
    for kind in _LIVE_ATTACKS:
        out[f"fingerprinter-{kind}"] = (_live(kind), ACCEPT)
    for kind in _REPLAY_ATTACKS:
        out[f"fingerprinter-replay-{kind}"] = (
            _live(kind, _replay_inputs), REPLAY)
    out["fingerprinter-mutates-then-raises"] = (
        _live("mutates-then-raises"), DF)
    return out


def _duplicate_sequence(ledger, index):
    """LEDGER with a copy of receipt INDEX inserted right after it
    under a new valid key, receipt_id re-derived: the same
    sequence, fingerprint and entry id twice - a non-strict
    sequence order."""
    twin = dict(ledger[index], idempotency_key=f"k{index}")
    twin["receipt_id"] = derive_receipt_id(
        twin["idempotency_key"], twin["request_fingerprint"],
        twin["entry_id"], twin["sequence"])
    return ledger[:index + 1] + [twin] + ledger[index + 1:]


def _cyclic(log):
    out = list(log)
    out.append(out)
    return out


class _ListSub(list):
    """list subclass whose accessors raise."""

    def __iter__(self):
        raise RuntimeError("hostile __iter__")

    def __len__(self):
        raise RuntimeError("hostile __len__")

    def __getitem__(self, index):
        raise RuntimeError("hostile __getitem__")


PROBE_EXPECT = {
    "outcome": "applied",
    "receipt": {
        "receipt_id": "idr1:9e9849d8f30faf0ceeadfaef5583a28805e88229cfcc28db8a3606f8cb5ba3f3",
        "idempotency_key": "d",
        "request_fingerprint":
            "idf1:731d607fcee3e593daa961c6c3d4855cfdb321c87b14b686a37bb006dee7c8e2",
        "entry_id": "wal1:c524d2ea0de8b9b0bf13a5c3886e2014d689da85c3d5aba25151704069712c49",
        "sequence": 4,
    },
}  # GENERATED
REPLAY_EXPECT = {
    "outcome": "replayed",
    "receipt": {
        "receipt_id": "idr1:9ee8ac4d6e083b9df028446b9de39159a93aa758722fa147ac308b431d397ad6",
        "idempotency_key": "b",
        "request_fingerprint":
            "idf1:71ff589fc1cff6413beaa1a589254fdf4dc0b52e26ff98cd799a79923e5ac942",
        "entry_id": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "sequence": 2,
    },
}  # GENERATED
PROBES = _probe_builders()
PROBE_MANIFEST = (
    "request-none",
    "request-true",
    "request-zero",
    "request-float",
    "request-text",
    "request-list",
    "request-tuple",
    "request-empty",
    "request-dict-subclass",
    "request-missing-idempotency_key",
    "request-key-idempotency_key-str-subclass",
    "request-key-idempotency_key-colliding-hash",
    "request-missing-op",
    "request-key-op-str-subclass",
    "request-key-op-colliding-hash",
    "request-missing-payload",
    "request-key-payload-str-subclass",
    "request-key-payload-colliding-hash",
    "request-key-none",
    "request-extra-key",
    "request-key-value-none",
    "request-key-value-int",
    "request-key-value-bytes",
    "request-key-value-str-subclass",
    "request-key-value-hostile-str-subclass",
    "request-key-value-empty",
    "request-key-value-leading-dot",
    "request-key-value-trailing-newline",
    "request-key-value-space",
    "request-key-value-non-ascii",
    "request-key-value-too-long",
    "request-key-value-huge",
    "request-op-none",
    "request-op-int",
    "request-op-upper",
    "request-op-unregistered",
    "request-op-str-subclass",
    "request-op-hostile-str-subclass",
    "request-payload-none",
    "request-payload-list",
    "request-payload-text",
    "request-payload-dict-subclass",
    "request-payload-extra-key",
    "request-payload-missing-identity",
    "request-payload-key-identity-str-subclass",
    "request-payload-key-identity-colliding-hash",
    "request-payload-missing-record",
    "request-payload-key-record-str-subclass",
    "request-payload-key-record-colliding-hash",
    "request-payload-identity-str-subclass",
    "request-payload-identity-mismatch",
    "request-record-dict-subclass",
    "request-record-missing-variant",
    "request-record-key-variant-str-subclass",
    "request-record-key-variant-colliding-hash",
    "request-record-value-variant-str-subclass",
    "request-record-missing-digest",
    "request-record-key-digest-str-subclass",
    "request-record-key-digest-colliding-hash",
    "request-record-value-digest-str-subclass",
    "request-record-missing-snapshot_fen",
    "request-record-key-snapshot_fen-str-subclass",
    "request-record-key-snapshot_fen-colliding-hash",
    "request-record-value-snapshot_fen-str-subclass",
    "request-record-value-self-referential",
    "request-record-value-deep-nested",
    "request-record-value-huge-int",
    "log-none",
    "log-true",
    "log-zero",
    "log-float",
    "log-text",
    "log-dict",
    "log-tuple",
    "log-list-subclass",
    "log-duplicate-entry",
    "log-reversed",
    "log-self-referential",
    "entry-none-first",
    "entry-zero-first",
    "entry-list-first",
    "entry-text-first",
    "entry-dict-subclass-first",
    "entry-extra-field-first",
    "entry-missing-sequence-first",
    "entry-key-sequence-str-subclass-first",
    "entry-key-sequence-colliding-hash-first",
    "entry-missing-op-first",
    "entry-key-op-str-subclass-first",
    "entry-key-op-colliding-hash-first",
    "entry-missing-entry_id-first",
    "entry-key-entry_id-str-subclass-first",
    "entry-key-entry_id-colliding-hash-first",
    "entry-missing-prior_entry_id-first",
    "entry-key-prior_entry_id-str-subclass-first",
    "entry-key-prior_entry_id-colliding-hash-first",
    "entry-missing-payload-first",
    "entry-key-payload-str-subclass-first",
    "entry-key-payload-colliding-hash-first",
    "entry-value-op-str-subclass-first",
    "entry-value-entry_id-str-subclass-first",
    "entry-value-prior_entry_id-str-subclass-first",
    "entry-value-sequence-int-subclass-first",
    "entry-value-sequence-bool-first",
    "entry-value-sequence-text-first",
    "entry-value-sequence-huge-int-first",
    "payload-dict-subclass-first",
    "payload-key-identity-str-subclass-first",
    "payload-key-identity-colliding-hash-first",
    "payload-key-record-str-subclass-first",
    "payload-key-record-colliding-hash-first",
    "payload-value-identity-str-subclass-first",
    "record-dict-subclass-first",
    "record-key-variant-str-subclass-first",
    "record-key-variant-colliding-hash-first",
    "record-value-variant-str-subclass-first",
    "record-key-digest-str-subclass-first",
    "record-key-digest-colliding-hash-first",
    "record-value-digest-str-subclass-first",
    "record-key-snapshot_fen-str-subclass-first",
    "record-key-snapshot_fen-colliding-hash-first",
    "record-value-snapshot_fen-str-subclass-first",
    "record-value-self-referential-first",
    "record-value-deep-nested-first",
    "record-value-huge-int-first",
    "entry-none-middle",
    "entry-zero-middle",
    "entry-list-middle",
    "entry-text-middle",
    "entry-dict-subclass-middle",
    "entry-extra-field-middle",
    "entry-missing-sequence-middle",
    "entry-key-sequence-str-subclass-middle",
    "entry-key-sequence-colliding-hash-middle",
    "entry-missing-op-middle",
    "entry-key-op-str-subclass-middle",
    "entry-key-op-colliding-hash-middle",
    "entry-missing-entry_id-middle",
    "entry-key-entry_id-str-subclass-middle",
    "entry-key-entry_id-colliding-hash-middle",
    "entry-missing-prior_entry_id-middle",
    "entry-key-prior_entry_id-str-subclass-middle",
    "entry-key-prior_entry_id-colliding-hash-middle",
    "entry-missing-payload-middle",
    "entry-key-payload-str-subclass-middle",
    "entry-key-payload-colliding-hash-middle",
    "entry-value-op-str-subclass-middle",
    "entry-value-entry_id-str-subclass-middle",
    "entry-value-prior_entry_id-str-subclass-middle",
    "entry-value-sequence-int-subclass-middle",
    "entry-value-sequence-bool-middle",
    "entry-value-sequence-text-middle",
    "entry-value-sequence-huge-int-middle",
    "payload-dict-subclass-middle",
    "payload-key-identity-str-subclass-middle",
    "payload-key-identity-colliding-hash-middle",
    "payload-key-record-str-subclass-middle",
    "payload-key-record-colliding-hash-middle",
    "payload-value-identity-str-subclass-middle",
    "record-dict-subclass-middle",
    "record-key-variant-str-subclass-middle",
    "record-key-variant-colliding-hash-middle",
    "record-value-variant-str-subclass-middle",
    "record-key-digest-str-subclass-middle",
    "record-key-digest-colliding-hash-middle",
    "record-value-digest-str-subclass-middle",
    "record-key-snapshot_fen-str-subclass-middle",
    "record-key-snapshot_fen-colliding-hash-middle",
    "record-value-snapshot_fen-str-subclass-middle",
    "record-value-self-referential-middle",
    "record-value-deep-nested-middle",
    "record-value-huge-int-middle",
    "entry-none-last",
    "entry-zero-last",
    "entry-list-last",
    "entry-text-last",
    "entry-dict-subclass-last",
    "entry-extra-field-last",
    "entry-missing-sequence-last",
    "entry-key-sequence-str-subclass-last",
    "entry-key-sequence-colliding-hash-last",
    "entry-missing-op-last",
    "entry-key-op-str-subclass-last",
    "entry-key-op-colliding-hash-last",
    "entry-missing-entry_id-last",
    "entry-key-entry_id-str-subclass-last",
    "entry-key-entry_id-colliding-hash-last",
    "entry-missing-prior_entry_id-last",
    "entry-key-prior_entry_id-str-subclass-last",
    "entry-key-prior_entry_id-colliding-hash-last",
    "entry-missing-payload-last",
    "entry-key-payload-str-subclass-last",
    "entry-key-payload-colliding-hash-last",
    "entry-value-op-str-subclass-last",
    "entry-value-entry_id-str-subclass-last",
    "entry-value-prior_entry_id-str-subclass-last",
    "entry-value-sequence-int-subclass-last",
    "entry-value-sequence-bool-last",
    "entry-value-sequence-text-last",
    "entry-value-sequence-huge-int-last",
    "payload-dict-subclass-last",
    "payload-key-identity-str-subclass-last",
    "payload-key-identity-colliding-hash-last",
    "payload-key-record-str-subclass-last",
    "payload-key-record-colliding-hash-last",
    "payload-value-identity-str-subclass-last",
    "record-dict-subclass-last",
    "record-key-variant-str-subclass-last",
    "record-key-variant-colliding-hash-last",
    "record-value-variant-str-subclass-last",
    "record-key-digest-str-subclass-last",
    "record-key-digest-colliding-hash-last",
    "record-value-digest-str-subclass-last",
    "record-key-snapshot_fen-str-subclass-last",
    "record-key-snapshot_fen-colliding-hash-last",
    "record-value-snapshot_fen-str-subclass-last",
    "record-value-self-referential-last",
    "record-value-deep-nested-last",
    "record-value-huge-int-last",
    "ledger-none",
    "ledger-true",
    "ledger-zero",
    "ledger-float",
    "ledger-text",
    "ledger-dict",
    "ledger-tuple",
    "ledger-list-subclass",
    "ledger-duplicate-receipt",
    "ledger-reversed",
    "ledger-duplicate-sequence-first",
    "ledger-duplicate-sequence-middle",
    "ledger-duplicate-sequence-last",
    "ledger-self-referential",
    "receipt-none-first",
    "receipt-zero-first",
    "receipt-list-first",
    "receipt-text-first",
    "receipt-dict-subclass-first",
    "receipt-extra-field-first",
    "receipt-missing-receipt_id-first",
    "receipt-key-receipt_id-str-subclass-first",
    "receipt-key-receipt_id-colliding-hash-first",
    "receipt-missing-idempotency_key-first",
    "receipt-key-idempotency_key-str-subclass-first",
    "receipt-key-idempotency_key-colliding-hash-first",
    "receipt-missing-request_fingerprint-first",
    "receipt-key-request_fingerprint-str-subclass-first",
    "receipt-key-request_fingerprint-colliding-hash-first",
    "receipt-missing-entry_id-first",
    "receipt-key-entry_id-str-subclass-first",
    "receipt-key-entry_id-colliding-hash-first",
    "receipt-missing-sequence-first",
    "receipt-key-sequence-str-subclass-first",
    "receipt-key-sequence-colliding-hash-first",
    "receipt-value-receipt_id-str-subclass-first",
    "receipt-value-receipt_id-int-first",
    "receipt-value-idempotency_key-str-subclass-first",
    "receipt-value-idempotency_key-int-first",
    "receipt-value-request_fingerprint-str-subclass-first",
    "receipt-value-request_fingerprint-int-first",
    "receipt-value-entry_id-str-subclass-first",
    "receipt-value-entry_id-int-first",
    "receipt-value-sequence-int-subclass-first",
    "receipt-value-sequence-bool-first",
    "receipt-value-sequence-text-first",
    "receipt-value-sequence-float-first",
    "receipt-value-sequence-huge-int-first",
    "receipt-value-sequence-zero-first",
    "receipt-value-sequence-negative-first",
    "receipt-value-sequence-beyond-log-first",
    "receipt-value-self-referential-first",
    "receipt-tampered-id-first",
    "receipt-resigned-bad-key-first",
    "receipt-resigned-foreign-entry-first",
    "receipt-resigned-foreign-fingerprint-first",
    "receipt-none-middle",
    "receipt-zero-middle",
    "receipt-list-middle",
    "receipt-text-middle",
    "receipt-dict-subclass-middle",
    "receipt-extra-field-middle",
    "receipt-missing-receipt_id-middle",
    "receipt-key-receipt_id-str-subclass-middle",
    "receipt-key-receipt_id-colliding-hash-middle",
    "receipt-missing-idempotency_key-middle",
    "receipt-key-idempotency_key-str-subclass-middle",
    "receipt-key-idempotency_key-colliding-hash-middle",
    "receipt-missing-request_fingerprint-middle",
    "receipt-key-request_fingerprint-str-subclass-middle",
    "receipt-key-request_fingerprint-colliding-hash-middle",
    "receipt-missing-entry_id-middle",
    "receipt-key-entry_id-str-subclass-middle",
    "receipt-key-entry_id-colliding-hash-middle",
    "receipt-missing-sequence-middle",
    "receipt-key-sequence-str-subclass-middle",
    "receipt-key-sequence-colliding-hash-middle",
    "receipt-value-receipt_id-str-subclass-middle",
    "receipt-value-receipt_id-int-middle",
    "receipt-value-idempotency_key-str-subclass-middle",
    "receipt-value-idempotency_key-int-middle",
    "receipt-value-request_fingerprint-str-subclass-middle",
    "receipt-value-request_fingerprint-int-middle",
    "receipt-value-entry_id-str-subclass-middle",
    "receipt-value-entry_id-int-middle",
    "receipt-value-sequence-int-subclass-middle",
    "receipt-value-sequence-bool-middle",
    "receipt-value-sequence-text-middle",
    "receipt-value-sequence-float-middle",
    "receipt-value-sequence-huge-int-middle",
    "receipt-value-sequence-zero-middle",
    "receipt-value-sequence-negative-middle",
    "receipt-value-sequence-beyond-log-middle",
    "receipt-value-self-referential-middle",
    "receipt-tampered-id-middle",
    "receipt-resigned-bad-key-middle",
    "receipt-resigned-foreign-entry-middle",
    "receipt-resigned-foreign-fingerprint-middle",
    "receipt-none-last",
    "receipt-zero-last",
    "receipt-list-last",
    "receipt-text-last",
    "receipt-dict-subclass-last",
    "receipt-extra-field-last",
    "receipt-missing-receipt_id-last",
    "receipt-key-receipt_id-str-subclass-last",
    "receipt-key-receipt_id-colliding-hash-last",
    "receipt-missing-idempotency_key-last",
    "receipt-key-idempotency_key-str-subclass-last",
    "receipt-key-idempotency_key-colliding-hash-last",
    "receipt-missing-request_fingerprint-last",
    "receipt-key-request_fingerprint-str-subclass-last",
    "receipt-key-request_fingerprint-colliding-hash-last",
    "receipt-missing-entry_id-last",
    "receipt-key-entry_id-str-subclass-last",
    "receipt-key-entry_id-colliding-hash-last",
    "receipt-missing-sequence-last",
    "receipt-key-sequence-str-subclass-last",
    "receipt-key-sequence-colliding-hash-last",
    "receipt-value-receipt_id-str-subclass-last",
    "receipt-value-receipt_id-int-last",
    "receipt-value-idempotency_key-str-subclass-last",
    "receipt-value-idempotency_key-int-last",
    "receipt-value-request_fingerprint-str-subclass-last",
    "receipt-value-request_fingerprint-int-last",
    "receipt-value-entry_id-str-subclass-last",
    "receipt-value-entry_id-int-last",
    "receipt-value-sequence-int-subclass-last",
    "receipt-value-sequence-bool-last",
    "receipt-value-sequence-text-last",
    "receipt-value-sequence-float-last",
    "receipt-value-sequence-huge-int-last",
    "receipt-value-sequence-zero-last",
    "receipt-value-sequence-negative-last",
    "receipt-value-sequence-beyond-log-last",
    "receipt-value-self-referential-last",
    "receipt-tampered-id-last",
    "receipt-resigned-bad-key-last",
    "receipt-resigned-foreign-entry-last",
    "receipt-resigned-foreign-fingerprint-last",
    "request-seen-key-other-payload",
    "request-seen-key-other-op",
    "fingerprinter-raises-value-error",
    "fingerprinter-raises-runtime-error",
    "fingerprinter-raises-keyboard-interrupt",
    "fingerprinter-raises-system-exit",
    "fingerprinter-raises-generator-exit",
    "fingerprinter-returns-none",
    "fingerprinter-returns-int",
    "fingerprinter-returns-list",
    "fingerprinter-returns-bytes",
    "fingerprinter-returns-str-subclass",
    "fingerprinter-returns-bad-grammar",
    "fingerprinter-returns-uppercase-hex",
    "fingerprinter-returns-trailing-newline",
    "fingerprinter-returns-lone-surrogate",
    "fingerprinter-returns-unbound-token",
    "fingerprinter-returns-other-op-token",
    "fingerprinter-returns-other-payload-token",
    "fingerprinter-mutates-first-entry",
    "fingerprinter-mutates-last-entry",
    "fingerprinter-mutates-first-record",
    "fingerprinter-appends-entry",
    "fingerprinter-pops-entry",
    "fingerprinter-clears-log",
    "fingerprinter-reorders-entry-keys",
    "fingerprinter-mutates-first-receipt",
    "fingerprinter-mutates-last-receipt-key",
    "fingerprinter-pops-receipt",
    "fingerprinter-appends-receipt",
    "fingerprinter-clears-ledger",
    "fingerprinter-mutates-request-key",
    "fingerprinter-mutates-request-op",
    "fingerprinter-mutates-request-record",
    "fingerprinter-clears-request",
    "fingerprinter-mutates-own-argument-payload",
    "fingerprinter-mutates-own-argument-record",
    "fingerprinter-adds-entry-key",
    "fingerprinter-adds-receipt-key",
    "fingerprinter-adds-request-key",
    "fingerprinter-adds-payload-key",
    "fingerprinter-adds-record-key",
    "fingerprinter-replay-mutates-first-receipt",
    "fingerprinter-replay-mutates-last-receipt-key",
    "fingerprinter-replay-clears-ledger",
    "fingerprinter-replay-mutates-request-key",
    "fingerprinter-replay-mutates-own-argument-record",
    "fingerprinter-replay-adds-receipt-key",
    "fingerprinter-replay-adds-request-key",
    "fingerprinter-replay-adds-payload-key",
    "fingerprinter-replay-adds-record-key",
    "fingerprinter-mutates-then-raises",
)  # GENERATED
PROBE_COUNT = 411  # GENERATED


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    log, ledger, request, fp = build()
    engine, counter = _engine(cls, fp)
    if failure in (ACCEPT, REPLAY):
        # SAME INSTANCE: accepts under attack, then still rejects a
        # bad request typed and still accepts a clean apply
        expect, clean = (PROBE_EXPECT, _probe_inputs) \
            if failure == ACCEPT else (REPLAY_EXPECT, _replay_inputs)
        try:
            _, ok = _checked_apply(engine, counter, log, ledger,
                                   request, expect)
            bad_log, bad_ledger, bad = _probe_inputs()
            ok = ok and _rejects(engine, counter, bad_log, bad_ledger,
                                 {**bad, "idempotency_key": ""}, MIR)
            _, again = _checked_apply(engine, counter, *clean(), expect)
        except BaseException:  # noqa: BLE001 - any rejection fails
            return False
        return ok and again
    try:
        return _rejects(engine, counter, log, ledger, request, failure)
    except BaseException:  # noqa: BLE001 - a raw escape is the defect
        return False


def _probe(cls, executed=None, first_only=False, only=None):
    """Every manifest row and every totality probe through CLS;
    returns failing labels (only the first when FIRST_ONLY).
    Untrusted-component boundary: BaseException is caught."""
    failures = []
    jobs = []
    for section, manifest in MANIFESTS.items():
        rows = {r["name"]: r for r in CASES[section]}
        for meta in manifest:
            jobs.append((f"{section}:{meta[0]}",
                         lambda s=section, r=rows[meta[0]]:
                         _RUNNERS[s](cls, r)))
    for name in PROBE_MANIFEST:
        jobs.append((f"totality:{name}",
                     lambda n=name: _totality_ok(cls, n)))
    for label, job in jobs:
        if only is not None and label not in only:
            continue
        try:
            ok = job()
        except BaseException as error:  # noqa: BLE001
            failures.append(f"{label}:{type(error).__name__}")
        else:
            if executed is not None:
                executed.append(label)
            if not ok:
                failures.append(label)
        if first_only and failures:
            return failures
    return failures
# -- mutants -------------------------------------------------------------------
def _fake_result():
    return {"outcome": "replayed", "receipt": {
        "receipt_id": "idr1:" + "0" * 64, "idempotency_key": "x",
        "request_fingerprint": "idf1:" + "0" * 64,
        "entry_id": _FORGED, "sequence": 1}}


class AcceptsAll(IdempotencyEngine):
    def apply(self, log, ledger, request):
        try:
            return super().apply(log, ledger, request)
        except IdempotencyError:
            return _fake_result()


class WrongCode(IdempotencyEngine):
    def apply(self, log, ledger, request):
        try:
            return super().apply(log, ledger, request)
        except IdempotencyError as error:
            raise IdempotencyError(error.failure_class,
                                   "internal") from None


def _remap(frm, to):
    class Remap(IdempotencyEngine):
        def apply(self, log, ledger, request):
            try:
                return super().apply(log, ledger, request)
            except IdempotencyError as error:
                if error.failure_class == frm:
                    raise IdempotencyError(
                        to, FAILURE_MAPPING[to]) from None
                raise
    Remap.__name__ = f"Remap_{frm}_to_{to}"
    return Remap


class DoubleFingerprinterCall(IdempotencyEngine):
    def __init__(self, fingerprinter):
        def twice(op, payload):
            fingerprinter(op, copy.deepcopy(payload))
            return fingerprinter(op, payload)
        super().__init__(twice)


def _peek_request(request):
    if type(request) is dict and type(request.get("op")) is str and \
            type(request.get("payload")) is dict:
        try:
            return request["op"], copy.deepcopy(request["payload"])
        except BaseException:  # noqa: BLE001
            return None
    return None


class UnguardedFingerprinter(IdempotencyEngine):
    """Calls the fingerprinter once OUTSIDE the boundary, before
    validation."""

    def apply(self, log, ledger, request):
        peek = _peek_request(request)
        if peek is not None:
            self.fingerprinter(*peek)
        return super().apply(log, ledger, request)


class NoCommit(IdempotencyEngine):
    def apply(self, log, ledger, request):
        saved = (list(log), list(ledger)) if type(log) is list and \
            type(ledger) is list else None
        out = super().apply(log, ledger, request)
        log[:], ledger[:] = saved
        return out


class EntryWithoutReceipt(IdempotencyEngine):
    def apply(self, log, ledger, request):
        n = len(ledger) if type(ledger) is list else 0
        out = super().apply(log, ledger, request)
        del ledger[n:]
        return out


class DoubleReceipt(IdempotencyEngine):
    def apply(self, log, ledger, request):
        n = len(ledger) if type(ledger) is list else 0
        out = super().apply(log, ledger, request)
        if len(ledger) > n:
            ledger.append(dict(ledger[-1]))
        return out


class ReplayAppends(IdempotencyEngine):
    """A replay still re-appends the entry (not exactly-once)."""

    def apply(self, log, ledger, request):
        out = super().apply(log, ledger, request)
        if out["outcome"] == "replayed":
            log.append(copy.deepcopy(log[out["receipt"]["sequence"] - 1]))
        return out


class SharesStoredReceipt(IdempotencyEngine):
    """Stores the SAME receipt object it returns."""

    def apply(self, log, ledger, request):
        n = len(ledger) if type(ledger) is list else 0
        out = super().apply(log, ledger, request)
        if len(ledger) > n:
            ledger[-1] = out["receipt"]
        return out


class SharesRequestPayload(IdempotencyEngine):
    """Commits the caller's own payload object into the log."""

    def apply(self, log, ledger, request):
        n = len(log) if type(log) is list else 0
        out = super().apply(log, ledger, request)
        if len(log) > n:
            log[-1]["payload"] = request["payload"]
        return out


class MutatesRequestOnSuccess(IdempotencyEngine):
    def apply(self, log, ledger, request):
        out = super().apply(log, ledger, request)
        request["idempotency_key"] = "zz"
        return out


class ReordersEntryKeys(IdempotencyEngine):
    def apply(self, log, ledger, request):
        out = super().apply(log, ledger, request)
        for entry in log:
            items = list(entry.items())
            entry.clear()
            entry.update(reversed(items))
        return out


class CopiesPriorReceipts(IdempotencyEngine):
    def apply(self, log, ledger, request):
        out = super().apply(log, ledger, request)
        ledger[:] = copy.deepcopy(ledger)
        return out


class StaleReceiptId(IdempotencyEngine):
    def apply(self, log, ledger, request):
        out = super().apply(log, ledger, request)
        out["receipt"]["receipt_id"] = "idr1:" + hashlib.sha256(
            out["receipt"]["idempotency_key"].encode()).hexdigest()
        return out


class CachedResult(IdempotencyEngine):
    """Class-level cache: a repeated apply returns the SAME result
    object."""
    _cache = {}

    def apply(self, log, ledger, request):
        out = super().apply(log, ledger, request)
        return CachedResult._cache.setdefault(
            (out["outcome"], out["receipt"]["receipt_id"]), out)


class RawLedgerPeek(IdempotencyEngine):
    """Reads the live last receipt's key before ledger
    validation."""

    def apply(self, log, ledger, request):
        if type(ledger) is list and ledger:
            ledger[-1]["idempotency_key"]  # noqa: B018
        return super().apply(log, ledger, request)


class RawReceiptKeySet(IdempotencyEngine):
    def apply(self, log, ledger, request):
        if type(ledger) is list:
            for receipt in ledger:
                if isinstance(receipt, dict):
                    set(receipt.keys()) == set(RECEIPT_FIELDS)  # noqa: B015
        return super().apply(log, ledger, request)


class RawRecordPeek(IdempotencyEngine):
    """Reads the live request record before request validation."""

    def apply(self, log, ledger, request):
        if type(request) is dict and type(request.get("payload")) is dict:
            request["payload"].get("record", {})["digest"]  # noqa: B018
        return super().apply(log, ledger, request)


class RawInteriorKeySet(IdempotencyEngine):
    def apply(self, log, ledger, request):
        if type(log) is list:
            for entry in log[1:-1]:
                if isinstance(entry, dict):
                    set(entry.keys()) == set(ENTRY_FIELDS)  # noqa: B015
        return super().apply(log, ledger, request)


def _source_mutant(name, edits, extra=None):
    """A one-guard edit of the reference engine: each OLD must
    occur in the reference source (first occurrence replaced);
    EXTRA names are added to the exec namespace."""
    src = inspect.getsource(_reference.IdempotencyEngine)
    for old, new in edits:
        if old not in src:
            raise AssertionError(f"{name}: edit site missing: {old!r}")
        src = src.replace(old, new, 1)
    namespace = dict(vars(_reference), **(extra or {}))
    # the mutant raises the BOUND error class, so a correct rejection
    # counts as one under any binding (reference or production)
    namespace["IdempotencyError"] = IdempotencyError

    def _bound_fail(cls):
        raise IdempotencyError(cls, FAILURE_MAPPING[cls])

    namespace["_fail"] = _bound_fail
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return namespace["IdempotencyEngine"]


def _unguarded_wal():
    """The linked WAL engine with its own exact-str-key guard
    disabled, so a mutant that drops this layer's key guard is
    not masked by the lower layer."""
    namespace = dict(vars(_wal_module),
                     _exact_str_keys=lambda mapping: True)
    exec(compile(inspect.getsource(_wal_module.WalEngine),  # noqa: S102
                 "<unguarded wal>", "exec"), namespace)
    return namespace["WalEngine"](canonical_payload)


_I12 = " " * 12
KeyOnlyDeduplication = _source_mutant("key-only-deduplication", [(
    'if stored["request_fingerprint"] != token:', "if False:")])
NoDeduplication = _source_mutant("no-deduplication", [(
    "if stored is not None:", "if False:")])
LiveRequestReread = _source_mutant("live-request-reread", [(
    f"{_I12}token = self._fingerprint(frozen_req)\n",
    f"{_I12}token = self._fingerprint(frozen_req)\n"
    f"{_I12}frozen_req[\"idempotency_key\"] = "
    "request.get(\"idempotency_key\")\n")])
SkipsFingerprintBinding = _source_mutant("skips-fingerprint-binding", [(
    'if out != request_fingerprint(frozen_req["op"], payload):',
    "if False:")])
IsinstanceFingerprinterOutput = _source_mutant(
    "isinstance-fingerprinter-output", [(
        "if type(out) is not str or _FP_RE.fullmatch(out) is None:",
        "if not isinstance(out, str) or _FP_RE.fullmatch(out) is None:")])
NarrowFingerprinterBoundary = _source_mutant(
    "narrow-fingerprinter-boundary", [(
        "except BaseException:", "except Exception:")])
RecordSharedFingerprinterArgument = _source_mutant(
    "record-shared-fingerprinter-argument", [(
        '                 "record": dict(payload["record"])})',
        '                 "record": payload["record"]})')])
SharedFingerprinterArgument = _source_mutant(
    "shared-fingerprinter-argument", [(
        '                {"identity": payload["identity"],\n'
        '                 "record": dict(payload["record"])})',
        "                payload)")])
NoRequestKeyGuard = _source_mutant("no-request-key-guard", [(
    "if type(request) is not dict or not _str_keyed(request) or",
    "if type(request) is not dict or")])
NoPayloadKeyGuard = _source_mutant("no-payload-key-guard", [(
    'if not _payload_str_keyed(request["payload"]):', "if False:")],
    {"_WAL": _unguarded_wal()})
NoReceiptKeyGuard = _source_mutant("no-receipt-key-guard", [(
    "                    not _str_keyed(receipt) or \\\n", "")])
NoLogKeyGuard = _source_mutant("no-log-key-guard", [(
    "if not _log_str_keyed(log):", "if False:")],
    {"_WAL": _unguarded_wal()})
NoSequenceOrder = _source_mutant("no-sequence-order", [(
    "if type(seq) is not int or seq <= last_seq or",
    "if type(seq) is not int or")])
NonStrictSequenceOrder = _source_mutant("non-strict-sequence-order", [(
    "seq <= last_seq", "seq < last_seq")])
NoLedgerUniqueness = _source_mutant("no-ledger-uniqueness", [(
    "if key in seen_keys:", "if False:")])
LedgerUnboundToLog = _source_mutant("ledger-unbound-to-log", [(
    'if entry["entry_id"] != entry_id or', "if False and")])
NoLogRestore = _source_mutant("no-log-restore", [(
    f"{_I12}WalEngine._restore_log(log, *saved_log)\n", "")])
NoLedgerRestore = _source_mutant("no-ledger-restore", [(
    f"{_I12}ledger[:] = container\n", "")])
NoReceiptRestore = _source_mutant("no-receipt-restore", [(
    "                obj.update(snap)\n", "                pass\n")])
NoRequestRestore = _source_mutant("no-request-restore", [(
    f"{_I12}req.update(req_copy)\n", "")])
NoRecordRestore = _source_mutant("no-record-restore", [(
    f"{_I12}rec.update(rec_copy)\n", "")])

class _NoClearRestoreWal(WalEngine):
    """The linked WAL engine whose log restore update()s every
    entry, payload and record WITHOUT clear()."""

    @staticmethod
    def _restore_log(log, saved_container, saved_entries):
        log[:] = saved_container
        for entry, e_copy, payload, p_copy, record, r_copy in \
                saved_entries:
            record.update(r_copy)
            payload.update(p_copy)
            entry.update(e_copy)


NoLogClear = _source_mutant("no-log-clear", [(
    f"{_I12}WalEngine._restore_log(log, *saved_log)\n",
    f"{_I12}WalEngine._restore_log(log, *saved_log)\n")],
    {"WalEngine": _NoClearRestoreWal})
NoReceiptClear = _source_mutant("no-receipt-clear", [(
    "                obj.clear()\n", "")])
NoRecordClear = _source_mutant("no-record-clear", [(
    f"{_I12}rec.clear()\n", "")])
NoPayloadClear = _source_mutant("no-payload-clear", [(
    f"{_I12}pay.clear()\n", "")])
NoRequestClear = _source_mutant("no-request-clear", [(
    f"{_I12}req.clear()\n", "")])

MUTANTS = {
    "accepts-all": AcceptsAll,
    "wrong-code": WrongCode,
    "malformed-as-corrupt-source": _remap(MIR, CS),
    "corrupt-source-as-ledger": _remap(CS, CL),
    "corrupt-ledger-as-source": _remap(CL, CS),
    "conflict-as-divergent": _remap(KC, DF),
    "divergent-as-conflict": _remap(DF, KC),
    "double-fingerprinter-call": DoubleFingerprinterCall,
    "unguarded-fingerprinter": UnguardedFingerprinter,
    "no-commit": NoCommit,
    "entry-without-receipt": EntryWithoutReceipt,
    "double-receipt": DoubleReceipt,
    "replay-appends": ReplayAppends,
    "shares-stored-receipt": SharesStoredReceipt,
    "shares-request-payload": SharesRequestPayload,
    "mutates-request-on-success": MutatesRequestOnSuccess,
    "reorders-entry-keys": ReordersEntryKeys,
    "copies-prior-receipts": CopiesPriorReceipts,
    "stale-receipt-id": StaleReceiptId,
    "cached-result": CachedResult,
    "raw-ledger-peek": RawLedgerPeek,
    "raw-receipt-key-set": RawReceiptKeySet,
    "raw-record-peek": RawRecordPeek,
    "raw-interior-key-set": RawInteriorKeySet,
    "key-only-deduplication": KeyOnlyDeduplication,
    "no-deduplication": NoDeduplication,
    "live-request-reread": LiveRequestReread,
    "skips-fingerprint-binding": SkipsFingerprintBinding,
    "isinstance-fingerprinter-output": IsinstanceFingerprinterOutput,
    "narrow-fingerprinter-boundary": NarrowFingerprinterBoundary,
    "record-shared-fingerprinter-argument":
        RecordSharedFingerprinterArgument,
    "shared-fingerprinter-argument": SharedFingerprinterArgument,
    "no-request-key-guard": NoRequestKeyGuard,
    "no-payload-key-guard": NoPayloadKeyGuard,
    "no-receipt-key-guard": NoReceiptKeyGuard,
    "no-log-key-guard": NoLogKeyGuard,
    "no-sequence-order": NoSequenceOrder,
    "non-strict-sequence-order": NonStrictSequenceOrder,
    "no-ledger-uniqueness": NoLedgerUniqueness,
    "ledger-unbound-to-log": LedgerUnboundToLog,
    "no-log-restore": NoLogRestore,
    "no-ledger-restore": NoLedgerRestore,
    "no-receipt-restore": NoReceiptRestore,
    "no-request-restore": NoRequestRestore,
    "no-record-restore": NoRecordRestore,
    "no-log-clear": NoLogClear,
    "no-receipt-clear": NoReceiptClear,
    "no-record-clear": NoRecordClear,
    "no-payload-clear": NoPayloadClear,
    "no-request-clear": NoRequestClear,
}

MUTANT_TARGETS = {
    "accepts-all": "malformed:request-not-a-dict",
    "wrong-code": "malformed:request-not-a-dict",
    "malformed-as-corrupt-source": "malformed:request-not-a-dict",
    "corrupt-source-as-ledger": "malformed:log-sequence-gap",
    "corrupt-ledger-as-source": "malformed:ledger-not-a-list",
    "conflict-as-divergent": "malformed:key-conflict-different-payload",
    "divergent-as-conflict": "malformed:oracle-raises",
    "double-fingerprinter-call": "happy:apply-first-key-empty-store",
    "unguarded-fingerprinter": "happy:apply-first-key-empty-store",
    "no-commit": "happy:apply-first-key-empty-store",
    "entry-without-receipt": "happy:apply-first-key-empty-store",
    "double-receipt": "happy:apply-first-key-empty-store",
    "replay-appends": "happy:apply-first-key-empty-store",
    "shares-stored-receipt": "happy:apply-first-key-empty-store",
    "shares-request-payload": "happy:apply-first-key-empty-store",
    "mutates-request-on-success": "happy:apply-first-key-empty-store",
    "reorders-entry-keys": "happy:apply-first-key-empty-store",
    "copies-prior-receipts": "happy:apply-first-key-empty-store",
    "stale-receipt-id": "happy:apply-first-key-empty-store",
    "cached-result": "happy:apply-first-key-empty-store",
    "raw-ledger-peek": "totality:ledger-self-referential",
    "raw-receipt-key-set": "totality:receipt-dict-subclass-first",
    "raw-record-peek": "totality:request-key-payload-str-subclass",
    "raw-interior-key-set": "totality:entry-dict-subclass-middle",
    "key-only-deduplication": "malformed:key-conflict-different-payload",
    "no-deduplication": "happy:apply-first-key-empty-store",
    "live-request-reread": "totality:fingerprinter-mutates-request-key",
    "skips-fingerprint-binding": "malformed:oracle-unbound-token",
    "isinstance-fingerprinter-output": "totality:fingerprinter-returns-str-subclass",
    "narrow-fingerprinter-boundary": "totality:fingerprinter-raises-keyboard-interrupt",
    "record-shared-fingerprinter-argument": "totality:fingerprinter-mutates-own-argument-record",
    "shared-fingerprinter-argument": "totality:fingerprinter-mutates-own-argument-payload",
    "no-request-key-guard": "totality:request-key-idempotency_key-str-subclass",
    "no-payload-key-guard": "totality:request-payload-key-identity-colliding-hash",
    "no-receipt-key-guard": "totality:receipt-key-receipt_id-str-subclass-first",
    "no-log-key-guard": "totality:entry-key-sequence-colliding-hash-first",
    "no-sequence-order": "totality:ledger-reversed",
    "no-ledger-uniqueness": "malformed:ledger-duplicate-key",
    "ledger-unbound-to-log": "malformed:ledger-fingerprint-not-of-entry",
    "no-log-restore": "totality:fingerprinter-mutates-first-entry",
    "no-ledger-restore": "totality:fingerprinter-pops-receipt",
    "no-receipt-restore": "happy:apply-first-key-empty-store",
    "no-request-restore": "happy:apply-first-key-empty-store",
    "no-record-restore": "happy:apply-first-key-empty-store",
    "no-log-clear": "totality:fingerprinter-adds-entry-key",
    "no-receipt-clear": "totality:fingerprinter-adds-receipt-key",
    "no-record-clear": "totality:fingerprinter-adds-record-key",
    "no-payload-clear": "totality:fingerprinter-adds-payload-key",
    "no-request-clear": "totality:fingerprinter-adds-request-key",
    "non-strict-sequence-order": "totality:ledger-duplicate-sequence-first",
}  # GENERATED-CHECKED


# -- kill-proof: substitution mutants vs the closure ---------------------------
def _payload_of(row):
    return {k: v for k, v in row.items() if k != "name"}


def _regenerated(row):
    """ROW with its pinned result regenerated from the reference
    over the row's own inputs (kept as-is when the reference
    rejects), so only the semantic pins can kill the edit."""
    with contextlib.suppress(_reference.IdempotencyError):
        row["expect"] = _reference_apply(row["oracle"], row["log"],
                                         row["ledger"], row["request"])
    return row


def _erasures(section, row):
    """Single-edge erasures of ROW. An erasure equal to its row is
    an equivalent mutant and is dropped."""
    out = []
    if section in ("happy", "boundary"):
        rekeyed = copy.deepcopy(row)
        rekeyed["request"]["idempotency_key"] = "zz-erased"
        out.append(("key-changed", _regenerated(rekeyed)))
        if row["log"]:
            dropped = copy.deepcopy(row)
            dropped["log"] = dropped["log"][:-1]
            out.append(("last-entry-dropped", _regenerated(dropped)))
        grown = copy.deepcopy(row)
        WalEngine(canonical_payload).append(
            grown["log"], {"op": "put", "payload": _payload(KINGS)})
        out.append(("log-grown", _regenerated(grown)))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        parts = _repaired(row)
        fixed.update(log=parts["log"], ledger=parts["ledger"],
                     request=parts["request"], oracle=parts["oracle"])
        out.append(("defect-repaired", fixed))
    else:
        tame = copy.deepcopy(row)
        tame["oracle"] = "honest"
        out.append(("oracle-honest", tame))
        swapped = copy.deepcopy(row)
        swapped["then_oracle"] = row["oracle"]
        out.append(("then-oracle-swapped", swapped))
        rewound = copy.deepcopy(row)
        rewound["then_log"], rewound["then_ledger"] = \
            copy.deepcopy(row["log"]), copy.deepcopy(row["ledger"])
        out.append(("follow-up-state-rewound", rewound))
        repeated = copy.deepcopy(row)
        repeated["then_request"] = copy.deepcopy(row["request"])
        out.append(("follow-up-repeats-rejected-request", repeated))
    return [(label, m) for label, m in out if m != row]


def _substitution_mutants():
    out = []
    labels = [(s, r["name"]) for s in MANIFESTS for r in CASES[s]]
    for sa, na in labels:
        for sb, nb in labels:
            if (sa, na) == (sb, nb):
                continue
            m = copy.deepcopy(CASES)
            i = [r["name"] for r in m[sa]].index(na)
            src = next(r for r in CASES[sb] if r["name"] == nb)
            m[sa][i] = dict(copy.deepcopy(src), name=na)
            out.append((f"payload:{sa}:{na}<-{sb}:{nb}", m))
    for section in MANIFESTS:
        rows = CASES[section]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                m = copy.deepcopy(CASES)
                m[section][i]["name"], m[section][j]["name"] = \
                    rows[j]["name"], rows[i]["name"]
                out.append((f"name-swap:{section}:{i}:{j}", m))
        for label, fn in (("reversed", lambda r: r.reverse()),
                          ("dropped", lambda r: r.pop()),
                          ("duplicated",
                           lambda r: r.append(copy.deepcopy(r[0])))):
            m = copy.deepcopy(CASES)
            fn(m[section])
            out.append((f"{label}:{section}", m))
        for i, row in enumerate(CASES[section]):
            for label, mutant in _erasures(section, row):
                m = copy.deepcopy(CASES)
                m[section][i] = mutant
                out.append((f"erased:{section}:{row['name']}:{label}",
                            m))
    return out


# -- tests -----------------------------------------------------------------------
def test_closure():
    _validate_closure(CASES)


def test_row_digest_table_is_closed():
    assert set(ROW_DIGESTS) == {
        f"{s}:{r['name']}" for s in MANIFESTS for r in CASES[s]}
    for s in MANIFESTS:
        for r in CASES[s]:
            assert ROW_DIGESTS[f"{s}:{r['name']}"] == _row_digest(r)


def test_no_equivalent_payload_pairs():
    """No two rows share a payload, so every substitution is a
    real mutant."""
    rows = [_payload_of(r) for s in MANIFESTS for r in CASES[s]]
    assert all(a != b for i, a in enumerate(rows)
               for b in rows[i + 1:])


def test_probe_manifest_closed_and_ordered():
    assert list(PROBES) == list(PROBE_MANIFEST)
    assert len(PROBE_MANIFEST) == PROBE_COUNT


def test_probe_expect_is_the_reference_result():
    log, ledger, request = _probe_inputs()
    assert _reference_apply("honest", log, ledger, request) == \
        PROBE_EXPECT
    assert PROBE_EXPECT["outcome"] == "applied"
    assert PROBE_EXPECT["receipt"]["sequence"] == len(log) + 1


def test_replay_expect_is_the_stored_receipt():
    log, ledger, request = _replay_inputs()
    assert _reference_apply("honest", log, ledger, request) == \
        REPLAY_EXPECT
    assert REPLAY_EXPECT["outcome"] == "replayed"
    assert [r for r in ledger if r["idempotency_key"] ==
            request["idempotency_key"]] == [REPLAY_EXPECT["receipt"]]


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(IdempotencyEngine, executed=executed) == []
    assert executed == [f"{s}:{m[0]}" for s, man in MANIFESTS.items()
                        for m in man] + [
        f"totality:{n}" for n in PROBE_MANIFEST]


def test_identity_source_mutant_is_green_under_current_binding():
    """Structural guard: an unedited reference-source mutant passes
    the whole battery under the CURRENT binding, so a source mutant
    dies only for its edit - never because it raises a different
    error class than the one the probes catch."""
    assert _probe(_source_mutant("identity", [])) == []


def test_mutant_targets_closed():
    assert set(MUTANT_TARGETS) == set(MUTANTS)


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_red_on_its_exact_target(name):
    """Every mutant fails the battery ON its pinned target label
    (a battery label, so the mutant is red); the reference is green
    on every target (below) and on the whole battery."""
    label = MUTANT_TARGETS[name]
    failures = _probe(MUTANTS[name], only={label})
    assert [":".join(f.split(":")[:2]) for f in failures] == \
        [label], (name, failures)


def test_reference_green_on_every_mutant_target():
    assert _probe(IdempotencyEngine,
                  only=set(MUTANT_TARGETS.values())) == []


def test_every_row_has_a_real_erasure():
    for section in MANIFESTS:
        for row in CASES[section]:
            erasures = _erasures(section, row)
            assert erasures, (section, row["name"])
            assert all(m != row for _, m in erasures)


@pytest.mark.parametrize("with_digests", [True, False],
                         ids=["with-digests", "digests-neutralized"])
def test_closure_kills_substitution_mutants(with_digests,
                                            monkeypatch):
    """Every cross-section payload substitution, rename, reorder,
    drop, duplicate and single-edge erasure is killed by
    _validate_closure with the digest table live AND with
    _row_digest monkeypatched to return the table value (digests
    neutralized)."""
    if not with_digests:
        monkeypatch.setattr(
            sys.modules[__name__], "_row_digest",
            lambda row: ROW_DIGESTS.get(
                f"{_section_of(row)}:{row['name']}", ""))
    mutants = _substitution_mutants()
    assert len(mutants) > 1000
    survivors = []
    for label, m in mutants:
        try:
            _validate_closure(m)
        except (AssertionError, ValueError, KeyError, TypeError):
            continue
        survivors.append(label)
    assert survivors == [], survivors
