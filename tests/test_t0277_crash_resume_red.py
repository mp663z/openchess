"""T0277 permanent red battery for store crash-resume engines.

The battery drives EVERY row of the T0276 conformance fixture
(tests/fixtures/crash_resume/cases.json), a closed set of totality
probes and pinned accept probes through an engine class
constructed with a quarantine sink:

- happy/boundary: the pinned receipt exactly (exact field types);
  the SAME log list truncated to exactly the resumed prefix, every
  kept entry the SAME object and _snap-identical, exactly ONE sink
  call (an empty tail included) with a DETACHED by-value copy of
  the torn tail; the request untouched by value AND identity;
  nothing of the receipt aliased with any input; determinism by
  value over fresh copies on the SAME instance (never the same
  object); and idempotency (resuming the resumed log at its own
  length discards nothing at the pinned head and state);
- malformed: the original input rejects with the pinned failure
  class and its mapped code, log and request untouched, the sink
  called only for divergent_quarantine (exactly once), and the
  declared minimal repair is accepted with the receipt the
  reference derives for it;
- rollback: a hostile sink bound to the caller's live log and
  request is rejected with every input restored by value AND
  identity, then the valid follow-up over the very same objects
  returns the pinned receipt and commits the pinned surviving log;
- totality: hostile requests and checkpoints, hostile logs,
  out-of-domain values and in-domain WAL damage in the FIRST,
  MIDDLE AND LAST entry at every nesting depth - both in the torn
  tail and in the acknowledged (durable) prefix - str-subclass,
  colliding-hash, non-str and lone-surrogate keys, dict/list
  subclasses, cycles, intra-entry aliases, depth and int-digit
  edges, bool / None / empty-str / multi-byte / finite-float leaf
  edges, and hostile sink behavior (raising any BaseException,
  non-str/str-subclass/bad-grammar/unbound/unframed output, sinks
  that mutate the caller's log or request or their own argument at
  every depth mid-call - overwriting, clearing, popping, appending
  AND adding a foreign key - or mutate then raise) each reject with
  the pinned failure class or, for accept probes, return the pinned
  receipt with exactly the pinned commit and, on the SAME instance,
  still reject a bad request typed and still accept a clean
  salvage - any other BaseException escaping is a failure.

Standalone-red convention (T0151, T0178, T0187, T0196, T0232,
T0241, T0250, T0259, T0268): the battery is permanently GREEN
against the contract-derived reference engine from
tests.test_t0275_crash_resume_contract and every mutant below is
RED. The production task switches the binding by replacing ONLY
the two binding lines below with the production ResumeEngine /
ResumeError names; no assertion changes. Reference-source mutants
raise the BOUND error class (see _source_mutant), guarded by an
identity mutant that must pass.

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
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0275_crash_resume_contract as _reference  # noqa: E402
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    WalEngine,
    WalError,
    _log_of,
    canonical_payload,
)
from tests.test_t0275_crash_resume_contract import (  # noqa: E402
    quarantine_tail,
)
from tests.test_t0276_crash_resume_fixture import (  # noqa: E402
    BOUNDARY_MANIFEST as _T0276_BOUNDARY,
)
from tests.test_t0276_crash_resume_fixture import (  # noqa: E402
    HAPPY_MANIFEST as _T0276_HAPPY,
)
from tests.test_t0276_crash_resume_fixture import (  # noqa: E402
    MALFORMED_MANIFEST as _T0276_MALFORMED,
)
from tests.test_t0276_crash_resume_fixture import (  # noqa: E402
    ROLLBACK_MANIFEST as _T0276_ROLLBACK,
)
from tests.test_t0276_crash_resume_fixture import (  # noqa: E402
    SINKS,
    TORN_FRAGMENT,
    _check_malformed_scenario,
    _max_depth,
    _max_int_digits,
    _rollback_sink,
    _validate_receipt,
    _validate_success_row,
)
from tools.crash_resume_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_DIGITS,
)

# -- binding (the production task replaces ONLY these two lines) ---------------
ResumeEngine = __import__("store.crash_resume").crash_resume.ResumeEngine
ResumeError = __import__("store.crash_resume").crash_resume.ResumeError

FIXTURE = (Path(__file__).parent / "fixtures" / "crash_resume"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECEIPT_FIELDS = ("resume_id", "head", "state_id", "resumed_count",
                  "discarded_count", "quarantine_token")
MRR = "malformed_resume_record"
UC = "unknown_checkpoint"
CS = "corrupt_source"
DQ = "divergent_quarantine"
# failure classes raised BEFORE the sink may be called
PRE_SINK = frozenset({MRR, UC, CS})
G0 = GENESIS

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, t0276 cardinalities (log length,
# checkpoint, resumed_count, discarded_count, head kind), pins)
# where pins = (per-entry position S=STARTPOS K=KINGS E=AFTER_E4
# ('-' for a record-less entry), per-entry op or entry type name)
HAPPY_MANIFEST = (
    ("clean_log_resumes_unchanged", (3, 3, 3, 0, "wal1"),
     (("S", "K", "E"), ("put", "put", "put"))),
    ("torn_fragment_discarded", (4, 3, 3, 1, "wal1"),
     (("S", "K", "E", "-"), ("put", "put", "put", "pu"))),
    ("torn_broken_chain_discarded", (4, 3, 3, 1, "wal1"),
     (("S", "K", "E", "K"), ("put", "put", "put", "delete"))),
)
BOUNDARY_MANIFEST = (
    ("empty_log_genesis", (0, 0, 0, 0, "wal0"), ((), ())),
    ("checkpoint_zero_whole_log_torn", (3, 0, 0, 3, "wal0"),
     (("S", "K", "E"), ("put", "put", "put"))),
    ("valid_looking_entries_after_first_invalid", (4, 2, 2, 2, "wal1"),
     (("S", "K", "-", "E"), ("put", "put", "NoneType", "put"))),
    ("tail_depth_at_limit", (4, 3, 3, 1, "wal1"),
     (("S", "K", "E", "-"), ("put", "put", "put", "list"))),
    ("tail_int_at_digit_limit", (4, 3, 3, 1, "wal1"),
     (("S", "K", "E", "-"), ("put", "put", "put", "-"))),
)
# malformed: (name, failure class, sink, repair locus, pinned defect
# text); the name is the closed scenario tag of the t0276 checker
MALFORMED_MANIFEST = tuple(
    (name, *meta) for name, meta in (
        ("log_not_a_list", (MRR, "canonical", "log",
                            "the source log is a dict, not a list")),
        ("request_missing_checkpoint", (
            MRR, "canonical", "request",
            "request has no checkpoint_sequence")),
        ("request_extra_field", (
            MRR, "canonical", "request",
            "request carries an unknown extra field")),
        ("checkpoint_bool", (
            MRR, "canonical", "request",
            "checkpoint_sequence is a bool, not an int")),
        ("checkpoint_string", (MRR, "canonical", "request",
                               "checkpoint_sequence is a string")),
        ("checkpoint_negative", (UC, "canonical", "request",
                                 "checkpoint_sequence is -1")),
        ("checkpoint_past_log_end", (
            CS, "canonical", "request",
            "checkpoint 5 acknowledges entries the log does not have")),
        ("durable_entry_damaged", (
            CS, "canonical", "log",
            "acknowledged entry 2 has a wrong digest")),
        ("tail_too_deep", (MRR, "canonical", "log",
                           "the torn entry nests 65 deep")),
        ("tail_int_past_digit_bound", (
            MRR, "canonical", "log",
            "the torn entry holds a 4001-digit int")),
        ("tail_lone_surrogate", (
            MRR, "canonical", "log",
            "the torn entry holds a lone surrogate, not UTF-8 "
            "encodable")),
        ("sink_raises", (DQ, "raising", "sink",
                         "the quarantine sink raises")),
        ("sink_non_string", (DQ, "bytes", "sink",
                             "the sink returns bytes")),
        ("sink_bad_grammar", (
            DQ, "bad_grammar", "sink",
            "the sink token breaks the pinned grammar")),
        ("sink_empty_tail_token", (
            DQ, "empty_tail_token", "sink",
            "the sink returns the token of an empty tail")),
        ("sink_unframed_token", (
            DQ, "unframed", "sink",
            "the sink hashes the tail without length framing")),
    ))
# rollback: (name, rejected sink, failure class, log length,
# checkpoint, follow-up (resumed_count, discarded_count), closed
# attack kind)
ROLLBACK_MANIFEST = (
    ("mutate_inputs_then_raise", "mutate_inputs_then_raise", DQ, 4, 3,
     (3, 1), "scribble-then-raise"),
    ("mutate_inputs_then_wrong_token", "mutate_inputs_then_wrong_token",
     DQ, 4, 3, (3, 1), "scribble-then-empty-tail-token"),
    ("mutate_tail_arg_then_matching_token",
     "mutate_tail_arg_then_matching_token", DQ, 4, 3, (3, 1),
     "own-argument-smuggle"),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:clean_log_resumes_unchanged":
        "82006fd90a3991dedc223b4faf9188194c90363ce005edc926414ec600600602",
    "happy:torn_fragment_discarded":
        "74c04ee56d4676bee9880a8ae9973662d3e411d0594f850f1a28552952b15183",
    "happy:torn_broken_chain_discarded":
        "46db686dbababa3fa80643a2c9250e1ffd67ccdd3fa103c5c3eb1aa420be3501",
    "boundary:empty_log_genesis":
        "6063c70b67086a13f7b1a8c415a38fb2d0ca6d083235c4c128233c2ec00bf9c9",
    "boundary:checkpoint_zero_whole_log_torn":
        "c9b2dbdf83221ab35a73372b96124b3e2effc364853850041357416be40309ab",
    "boundary:valid_looking_entries_after_first_invalid":
        "28793d5347cac0d7d776749496c84adb0f76b12176f01c0811cdd0dd7fdf2ba2",
    "boundary:tail_depth_at_limit":
        "a655a53aa0e0837f63ee5eda1bcbea344f5a3d41ff90df0b8ed782d7b933590e",
    "boundary:tail_int_at_digit_limit":
        "dae960514cf9675272dab32a4be174bec527a3c63d6970d0964521df26f1a58c",
    "malformed:log_not_a_list": "fbbd15656185b268314b34ac3188d36c5df0f0ddaa84ca3a51e85948b9b98b9f",
    "malformed:request_missing_checkpoint":
        "66733ed8973ee23e6a61b57ccd3c6fed0962249270fdf40a4ce2843dbe4392c7",
    "malformed:request_extra_field":
        "1c66e997e0e6f6cc485e02fd293a0bea226784cf2bfcab784de642df431a68c9",
    "malformed:checkpoint_bool":
        "53d849abc2cdddbfb7af73e7de1990655f96329907742aa8b29e2b407630d5a9",
    "malformed:checkpoint_string":
        "7c8d0300e208e2ab930591853110ce76b16d5f7d0e842d556feae9a8f761d81d",
    "malformed:checkpoint_negative":
        "b29276a60d8f92b31c43fc1c55f335a6314c8f9ecfc59b39ddd71eab5ba53376",
    "malformed:checkpoint_past_log_end":
        "064a8955260d5756714aa5eac061fa02bc7d0212586d17cad8266d966fb1a943",
    "malformed:durable_entry_damaged":
        "f95c56c71cf7600e8d3495b475aca48a3e2eee34e897aeec6e5de38d5fc11486",
    "malformed:tail_too_deep": "9557a38b6392b40225fc845835cfe2c7e3bf08f93ae648e4a836f9d60d2fb606",
    "malformed:tail_int_past_digit_bound":
        "cadc23dfa876e09255ff14c5033084f4f28122cdef42d659b79134be30c87635",
    "malformed:tail_lone_surrogate":
        "f23eef3022b426f897fb970ec32f718708710be04ad42725406df07b79371e46",
    "malformed:sink_raises": "7eec5077c238bdd4de196efe1ad06509e2c7334fb4964114565a7559514ee1dc",
    "malformed:sink_non_string":
        "d7034d56bd271c636e8272b728751c775897afee725d1ac0c410cc29a61abc0c",
    "malformed:sink_bad_grammar":
        "6aee9b7855f2349a5c772f2d7841c5f748d656c274f6508cc55912ad9e6914ca",
    "malformed:sink_empty_tail_token":
        "5911d5fb2f575b877a63ffa0af24a59e9646f00d9866e545af0c94a576de5e49",
    "malformed:sink_unframed_token":
        "f2e6461fa6cd57c1c0ff0595e5d869d6e248372c26e085b2884c8c43e6569c8d",
    "rollback:mutate_inputs_then_raise":
        "facd5ff14cf1154123a55c5689eb2cd0266af2640a4d11cd9a8d2c84303f260d",
    "rollback:mutate_inputs_then_wrong_token":
        "42f5a0e54e80862c02503df919136777d286bbe8659284d59e0e8a722238bebf",
    "rollback:mutate_tail_arg_then_matching_token":
        "c0ee6c12f8965899fbbff972b8b909c705a7dab08f50c3e22269cf8286c48ecf",
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


# -- local, independent derivation -----------------------------------------------
def _typed(value):
    """Type-exact canonical text of an admitted JSON value (True is
    never 1, 1.0 is never 1)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _local_admissible(entry):
    """LOCAL admission (independent of the reference walker):
    exact built-in JSON values only - None, bool, int of at most
    MAX_INT_DIGITS decimal digits, finite float, UTF-8 encodable
    str, list, dict with exact-str UTF-8 keys - nesting at most
    MAX_DEPTH containers, no container met twice."""
    seen, stack = set(), [(entry, 1)]
    while stack:
        node, depth = stack.pop()
        kind = type(node)
        if kind in (list, dict):
            if depth > MAX_DEPTH or id(node) in seen:
                return False
            seen.add(id(node))
            if kind is dict:
                for key, value in dict.items(node):
                    if type(key) is not str or not _local_leaf(key):
                        return False
                    stack.append((value, depth + 1))
            else:
                stack.extend((v, depth + 1) for v in list.__iter__(node))
        elif not _local_leaf(node):
            return False
    return True


_INT_BITS = math.ceil(MAX_INT_DIGITS * math.log2(10)) + 1


def _local_leaf(node):
    kind = type(node)
    if node is None or kind is bool:
        return True
    if kind is int:
        # bit pre-screen keeps str() under the interpreter's limit
        if node.bit_length() > _INT_BITS:
            return False
        return len(str(abs(node))) <= MAX_INT_DIGITS
    if kind is float:
        return math.isfinite(node)
    if kind is str:
        try:
            node.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True
    return False


def _wal_replay(log):
    """Linked WAL replay of LOG (head, state_id), or None."""
    try:
        out = WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return None
    return out["head"], out["state_id"]


def _local_prefix(log):
    """Length of the longest admitted, WAL-replaying prefix by
    LINEAR search - independent of the reference's downward scan."""
    count = 0
    for k in range(1, len(log) + 1):
        if not _local_admissible(log[k - 1]) or \
                _wal_replay(log[:k]) is None:
            break
        count = k
    return count


def _local_receipt(log, request):
    """The receipt the contract pins for (LOG, REQUEST), derived
    locally; asserts the inputs resume."""
    checkpoint = request["checkpoint_sequence"]
    k = _local_prefix(log)
    assert 0 <= checkpoint <= k
    assert all(_local_admissible(e) for e in log[k:])
    head, sid = _wal_replay(log[:k])
    token = quarantine_tail(json.loads(_typed(log[k:])))
    return {"resume_id": ResumeEngine._derive_resume_id(
                head, sid, k, len(log) - k, token),
            "head": head, "state_id": sid, "resumed_count": k,
            "discarded_count": len(log) - k, "quarantine_token": token}


def _reference_resume(sink, log, request):
    return _reference.ResumeEngine(SINKS.get(sink, sink)).resume(
        copy.deepcopy(log), copy.deepcopy(request))


def _reference_failure(sink, log, request):
    try:
        _reference_resume(sink, log, request)
    except _reference.ResumeError as error:
        return error.failure_class
    return None


def _check_receipt(name, log, request, expect):
    """The pinned receipt realizes the contract over the ORIGINAL
    inputs: prefix, head, state, token and resume id by the LOCAL
    derivation."""
    assert set(expect) == set(RECEIPT_FIELDS), name
    _validate_receipt(expect, name)
    assert expect == _local_receipt(log, request), name


# -- per-tag semantic checks over ORIGINAL row data ---------------------------
_POSITIONS = {
    _log_of(("put", pos))[0]["payload"]["record"]["snapshot_fen"]: label
    for label, pos in (("S", STARTPOS), ("K", KINGS), ("E", AFTER_E4))}


def _position(entry):
    """Snapshot position label of one entry; '-' when the entry
    carries no record; an unknown position raises KeyError."""
    try:
        fen = entry["payload"]["record"]["snapshot_fen"]
    except (TypeError, KeyError):
        return "-"
    return _POSITIONS[fen]


def _shape(entry):
    if type(entry) is not dict:
        return type(entry).__name__
    return entry.get("op", "-")


def _pins(log):
    return (tuple(_position(e) for e in log),
            tuple(_shape(e) for e in log))


def _edge_clean(case):
    assert case["log"] == case["expect_log"]
    assert _wal_replay(case["log"]) is not None


def _edge_fragment(case):
    assert case["log"][3:] == [TORN_FRAGMENT]


def _edge_broken_chain(case):
    log = case["log"]
    assert set(log[3]) == set(log[2])
    assert log[3]["prior_entry_id"] != log[2]["entry_id"]
    assert _wal_replay(log[:3]) is not None


def _edge_empty(case):
    assert case["log"] == [] and case["expect"]["head"] == G0


def _edge_checkpoint_zero(case):
    assert _wal_replay(case["log"][:1]) is None


def _edge_valid_after_invalid(case):
    log, k = case["log"], case["expect"]["resumed_count"]
    assert _wal_replay(log[:k + 1]) is None
    assert _wal_replay(log[:k] + log[k + 1:]) is not None


def _edge_depth(case):
    assert _max_depth(case["log"][3]) == MAX_DEPTH


def _edge_digits(case):
    assert _max_int_digits(case["log"][3]) == MAX_INT_DIGITS


_OK_EDGES = {
    "clean_log_resumes_unchanged": _edge_clean,
    "torn_fragment_discarded": _edge_fragment,
    "torn_broken_chain_discarded": _edge_broken_chain,
    "empty_log_genesis": _edge_empty,
    "checkpoint_zero_whole_log_torn": _edge_checkpoint_zero,
    "valid_looking_entries_after_first_invalid":
        _edge_valid_after_invalid,
    "tail_depth_at_limit": _edge_depth,
    "tail_int_at_digit_limit": _edge_digits,
}


def _check_ok(section, case, cards, pins):
    name = case["name"]
    log, request = case["log"], case["request"]
    assert (_T0276_HAPPY if section == "happy"
            else _T0276_BOUNDARY)[name] == cards, name
    assert set(case) == {"name", "why", "log", "request", "sink",
                         "expect", "expect_log"}, name
    assert case["sink"] == "canonical", name
    _validate_success_row(case, name)
    exp = case["expect"]
    assert (len(log), request["checkpoint_sequence"],
            exp["resumed_count"], exp["discarded_count"],
            exp["head"].split(":")[0]) == cards, name
    _check_receipt(name, log, request, exp)
    assert case["expect_log"] == log[:exp["resumed_count"]], name
    assert _pins(log) == pins, name
    _OK_EDGES[name](case)  # an unknown name raises KeyError


def _check_malformed(case, meta):
    name, failure, sink, locus, defect = meta
    assert set(case) == {"name", "defect", "expect_failure", "log",
                         "request", "sink", "minimal_repair"}, name
    assert (case["expect_failure"], case["sink"],
            list(case["minimal_repair"]), case["defect"]) == \
        (failure, sink, [locus], defect), name
    assert _T0276_MALFORMED[name] == (failure, sink, locus, defect), name
    _check_malformed_scenario(case)
    assert _reference_failure(sink, case["log"],
                              case["request"]) == failure, name
    fixed = _repaired(case)
    assert _reference_resume(fixed["sink"], fixed["log"],
                             fixed["request"]) == \
        _local_receipt(fixed["log"], fixed["request"]), name


def _repaired(case):
    parts = {"log": case["log"], "request": case["request"],
             "sink": case["sink"]}
    (locus, value), = case["minimal_repair"].items()
    parts[locus] = value
    return parts


def _scribbled(log, request):
    """Did the hostile sink leave the caller-visible scribble on a
    SCRATCH copy (reference-free check of the attack's reach)."""
    return log == [{"forged": True}] and request == {
        "checkpoint_sequence": 0, "extra": 1}


def _attack_kind(case):
    """Closed attack classification of the rejected sink over a
    scratch copy of the row's own inputs; an unknown sink raises."""
    log, request = copy.deepcopy(case["log"]), copy.deepcopy(
        case["request"])
    sink = _rollback_sink(case["rejected_sink"], log, request)
    tail = copy.deepcopy(case["log"][case["expect"]["resumed_count"]:])
    try:
        out = sink(tail)
    except RuntimeError:
        assert _scribbled(log, request)
        return "scribble-then-raise"
    if _scribbled(log, request):
        assert out == quarantine_tail([])
        return "scribble-then-empty-tail-token"
    assert tail[-1] == {"smuggled": True}
    assert out == quarantine_tail(tail)
    return "own-argument-smuggle"


def _check_rollback(case, meta):
    name, sink, failure, length, checkpoint, counts, kind = meta
    assert set(case) == {"name", "why", "log", "request",
                         "rejected_sink", "expect_failure", "expect",
                         "expect_log"}, name
    assert (case["rejected_sink"], case["expect_failure"],
            len(case["log"]), case["request"]) == \
        (sink, failure, length,
         {"checkpoint_sequence": checkpoint}), name
    assert _T0276_ROLLBACK[name] == (sink, failure, length,
                                     *counts), name
    _validate_success_row(case, name)
    exp = case["expect"]
    assert (exp["resumed_count"], exp["discarded_count"]) == counts, name
    _check_receipt(name, case["log"], case["request"], exp)
    assert case["expect_log"] == case["log"][:counts[0]], name
    assert _attack_kind(case) == kind, name
    log, request = copy.deepcopy(case["log"]), copy.deepcopy(
        case["request"])
    assert _reference_failure(
        _rollback_sink(sink, log, request), log, request) == failure


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
# -- engine runners ---------------------------------------------------------------
def _containers(obj):
    return {e[1] for e in _snap(obj) if e[0] in ("dict", "list",
                                                  "tuple")}


class _Counting:
    """Wraps a sink; records every call's argument BY VALUE and the
    container ids it was handed."""

    def __init__(self, sink):
        self.sink, self.calls = sink, []

    def __call__(self, tail):
        try:
            value = copy.deepcopy(tail)
        except BaseException:  # noqa: BLE001
            value = None
        self.calls.append((value, _containers(tail)))
        return self.sink(tail)


def _engine(cls, sink):
    counter = _Counting(SINKS[sink] if isinstance(sink, str) else sink)
    return cls(counter), counter


def _receipt_ok(receipt, expect):
    return (type(receipt) is dict and receipt == expect
            and list(receipt) == list(RECEIPT_FIELDS)
            and all(type(receipt[f]) is str
                    for f in ("resume_id", "head", "state_id",
                              "quarantine_token"))
            and type(receipt["resumed_count"]) is int
            and type(receipt["discarded_count"]) is int)


def _same_value(a, b):
    """Type-exact by-value equality of two JSON values."""
    try:
        return _typed(a) == _typed(b)
    except (TypeError, ValueError):
        return False


def _checked_resume(engine, counter, log, request, expect):
    """One successful resume: the pinned receipt; the SAME log list
    truncated to the resumed prefix, every kept entry the SAME
    object and _snap-identical; exactly one sink call (an empty
    tail included) with a detached by-value copy of the torn tail;
    request untouched; nothing of the receipt aliased with any
    input."""
    log_id = id(log)
    count = expect["resumed_count"]
    kept_ids = [id(entry) for entry in log[:count]]
    kept = [_snap(entry) for entry in log[:count]]
    req_snap = _snap(request)
    want_tail = copy.deepcopy(log[count:])
    live = _containers(log) | _containers(request)
    n_before = len(counter.calls)
    receipt = engine.resume(log, request)
    calls = counter.calls[n_before:]
    ok = (_receipt_ok(receipt, expect)
          and id(log) == log_id and type(log) is list
          and _snap(request) == req_snap
          and _not_aliased(receipt, (log, request))
          and len(log) == count
          and [id(entry) for entry in log] == kept_ids
          and [_snap(entry) for entry in log] == kept
          and len(calls) == 1 and type(calls[0][0]) is list
          and _same_value(calls[0][0], want_tail)
          and not calls[0][1] & live)
    return receipt, ok


def _clean_after(expect):
    """The receipt a resume of the committed log at its own length
    must return: nothing discarded, same head and state."""
    head, sid = expect["head"], expect["state_id"]
    count, token = expect["resumed_count"], quarantine_tail([])
    return {"resume_id": _reference.ResumeEngine._derive_resume_id(
                head, sid, count, 0, token),
            "head": head, "state_id": sid, "resumed_count": count,
            "discarded_count": 0, "quarantine_token": token}


def _resume_ok(cls, case, engine=None):
    """Pinned receipt and commit, determinism BY VALUE on the SAME
    instance (a repeat over fresh copies never returns the same
    object), and idempotency: resuming the committed log at its own
    length discards nothing at the pinned head and state."""
    eng, counter = engine or _engine(cls, case.get("sink", "canonical"))
    results, logs = [], []
    for _ in range(2):
        log = copy.deepcopy(case["log"])
        receipt, ok = _checked_resume(
            eng, counter, log, copy.deepcopy(case["request"]),
            case["expect"])
        if not ok or log != case["expect_log"]:
            return False
        results.append(receipt)
        logs.append(log)
    if results[1] is results[0]:
        return False
    _, ok = _checked_resume(
        eng, counter, logs[1],
        {"checkpoint_sequence": len(logs[1])},
        _clean_after(case["expect"]))
    return ok


def _rejects(engine, counter, log, request, failure):
    inputs = (log, request)
    snap = _snap(inputs)
    n_before = len(counter.calls)
    try:
        engine.resume(log, request)
    except ResumeError as error:
        calls = len(counter.calls) - n_before
        return (type(error) is ResumeError
                and error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and error.code in ERROR_ENUM
                and _snap(inputs) == snap
                and calls == (0 if failure in PRE_SINK else 1))
    return False


def _malformed_ok(cls, case):
    eng, counter = _engine(cls, case["sink"])
    if not _rejects(eng, counter, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    fixed = _repaired(case)
    expect = _reference_resume(fixed["sink"], fixed["log"],
                               fixed["request"])
    if fixed["sink"] != case["sink"]:
        eng, counter = _engine(cls, fixed["sink"])
    _, ok = _checked_resume(eng, counter, copy.deepcopy(fixed["log"]),
                            copy.deepcopy(fixed["request"]), expect)
    return ok


def _rollback_row_ok(cls, case):
    """Rejection by a sink bound to the caller's LIVE inputs leaves
    them identical by value and identity; the valid follow-up over
    the very same objects commits as pinned."""
    log, request = copy.deepcopy(case["log"]), copy.deepcopy(
        case["request"])
    hostile = _rollback_sink(case["rejected_sink"], log, request)
    if not _rejects(*_engine(cls, hostile), log, request,
                    case["expect_failure"]):
        return False
    receipt_ok = _checked_resume(*_engine(cls, "canonical"), log,
                                 request, case["expect"])[1]
    return receipt_ok and log == case["expect_log"] and _resume_ok(
        cls, {**case, "sink": "canonical"})


_RUNNERS = {"happy": _resume_ok, "boundary": _resume_ok,
            "malformed": _malformed_ok, "rollback": _rollback_row_ok}


# -- closed totality probes ----------------------------------------------------
_FORGED = "wal1:" + "f" * 64
_HUGE = 10 ** MAX_INT_DIGITS          # MAX_INT_DIGITS + 1 digits: out
_EDGE = 10 ** MAX_INT_DIGITS - 1      # exactly MAX_INT_DIGITS digits


def _base_log():
    return _log_of(("put", STARTPOS), ("put", KINGS), ("put", AFTER_E4))


def _salvage_inputs():
    """Salvage path: a three-entry log whose tail entry id is
    forged, checkpoint 2 - the torn tail is one full entry (payload
    and record included)."""
    log = _base_log()
    log[2]["entry_id"] = _FORGED
    return log, {"checkpoint_sequence": 2}


def _clean_inputs():
    return _base_log(), {"checkpoint_sequence": 3}


class _IntSub(int):
    pass


class _ListSub(list):
    """list subclass whose accessors raise."""

    def __iter__(self):
        raise RuntimeError("hostile __iter__")

    def __len__(self):
        raise RuntimeError("hostile __len__")

    def __getitem__(self, index):
        raise RuntimeError("hostile __getitem__")


def _rekey(mapping, key, new_key):
    return {new_key if k == key else k: v for k, v in mapping.items()}


def _nest(depth, leaf="x"):
    """A list chain whose innermost leaf sits at nesting DEPTH
    relative to the chain's own container (1 = [leaf])."""
    node = leaf
    for _ in range(depth):
        node = [node]
    return node


def _request(fn):
    """Salvage log, canonical sink, request replaced by FN(request)."""
    def build():
        log, request = _salvage_inputs()
        return log, fn(request), "canonical"
    return build


def _log(fn, checkpoint=3):
    def build():
        return fn(_base_log()), {"checkpoint_sequence": checkpoint}, \
            "canonical"
    return build


def _entry(fn, index, checkpoint):
    """Base log with entry INDEX replaced by FN(entry, log)."""
    def edit(log):
        log[index] = fn(log[index], log)
        return log
    return _log(edit, checkpoint)


def _sink(sink, inputs=None):
    def build():
        log, request = (inputs or _salvage_inputs)()
        return log, request, sink
    return build


def _set(path, value):
    """fn(entry, log) setting entry[path...] = VALUE(entry, log)
    on the live entry."""
    def fn(entry, log):
        node = entry
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value(entry, log) if callable(value) else value
        return entry
    return fn


# the digest VALUE sits under entry(1) > payload(2) > record(3): a
# list chain placed there has its innermost list at depth 3 + chain
_DEPTH_ABOVE_DIGEST = 3

ACCEPT = "accept"
_LIVE_ATTACKS = (
    "mutates-first-entry", "mutates-last-entry", "mutates-first-record",
    "appends-entry", "pops-entry", "clears-log", "replaces-first-entry",
    "reorders-entry-keys", "adds-entry-key", "adds-payload-key",
    "adds-record-key", "mutates-checkpoint", "clears-request",
    "adds-request-key", "own-argument-appends", "own-argument-clears",
    "own-argument-entry", "own-argument-adds-entry-key",
    "own-argument-payload", "own-argument-record")
# live attacks that also run on a CLEAN resume (the sink is called
# with an empty tail there too)
_CLEAN_LIVE_ATTACKS = ("mutates-last-entry", "clears-log",
                       "adds-entry-key", "adds-record-key",
                       "adds-request-key", "own-argument-appends")


def _attack(kind, log, request, tail):
    if kind in ("mutates-first-entry", "mutates-then-raises"):
        dict.__setitem__(log[0], "entry_id", _FORGED)
    if kind == "mutates-last-entry":
        dict.__setitem__(log[-1], "op", "delete")
    if kind == "mutates-first-record":
        dict.__setitem__(log[0]["payload"]["record"], "digest", "x")
    if kind in ("appends-entry", "mutates-then-raises"):
        list.append(log, copy.deepcopy(log[0]))
    if kind == "pops-entry":
        list.pop(log)
    if kind == "clears-log":
        list.clear(log)
    if kind == "replaces-first-entry":
        list.__setitem__(log, 0, {})
    if kind == "reorders-entry-keys":
        entry = log[0]
        items = sorted(dict.items(entry), reverse=True)
        dict.clear(entry)
        dict.update(entry, items)
    if kind == "adds-entry-key":
        dict.__setitem__(log[0], "x", 1)
    if kind == "adds-payload-key":
        dict.__setitem__(log[0]["payload"], "x", 1)
    if kind == "adds-record-key":
        dict.__setitem__(log[0]["payload"]["record"], "x", 1)
    if kind in ("mutates-checkpoint", "mutates-then-raises"):
        dict.__setitem__(request, "checkpoint_sequence", 0)
    if kind == "clears-request":
        dict.clear(request)
    if kind == "adds-request-key":
        dict.__setitem__(request, "x", 1)
    # own-argument attacks, one per depth: an engine sharing ANY
    # level of the frozen tail with the sink is caught
    if kind == "own-argument-appends":
        list.append(tail, "x")
    if kind == "own-argument-clears":
        list.clear(tail)
    if kind == "own-argument-entry":
        dict.__setitem__(tail[0], "op", "x")
    if kind == "own-argument-adds-entry-key":
        dict.__setitem__(tail[0], "x", 1)
    if kind == "own-argument-payload":
        dict.__setitem__(tail[0]["payload"], "identity", "x")
    if kind == "own-argument-record":
        dict.__setitem__(tail[0]["payload"]["record"], "digest", "x")


def _live(kind, inputs=None):
    def build():
        log, request = (inputs or _salvage_inputs)()

        def sink(tail):
            token = quarantine_tail(tail)
            _attack(kind, log, request, tail)
            if kind == "mutates-then-raises":
                raise ValueError("mutate then explode")
            return token
        return log, request, sink
    return build


def _nested_torn():
    return {"a": {"b": 1}, "c": [1, [2]]}


def _nested_tail_inputs():
    """Three torn entries whose NESTED dict and list containers are
    distinct objects (checkpoint 3): the frozen tail must be a deep,
    detached copy at every depth."""
    return _base_log() + [_nested_torn() for _ in range(3)], \
        {"checkpoint_sequence": 3}


_NESTED_ATTACKS = {
    "sets-nested-dict-value":
        lambda e: dict.__setitem__(e["a"], "b", 2),
    "adds-nested-dict-key":
        lambda e: dict.__setitem__(e["a"], "x", 1),
    "clears-nested-dict": lambda e: dict.clear(e["a"]),
    "appends-nested-list": lambda e: list.append(e["c"], 3),
    "pops-nested-list": lambda e: list.pop(e["c"]),
    "appends-doubly-nested-list": lambda e: list.append(e["c"][1], 3),
    "adds-entry-key": lambda e: dict.__setitem__(e, "x", 1),
}
def _list_tail_inputs():
    """Three LIST-typed torn entries (in domain), nested list and
    dict containers distinct per entry (checkpoint 3)."""
    return _base_log() + [[1, [2, {"k": "v"}]] for _ in range(3)], \
        {"checkpoint_sequence": 3}


_LIST_ATTACKS = {
    "appends-list-entry": lambda e: list.append(e, 9),
    "pops-list-entry": lambda e: list.pop(e),
    "clears-list-entry": lambda e: list.clear(e),
    "appends-nested-list-in-list-entry":
        lambda e: list.append(e[1], 9),
    "sets-dict-in-list-entry":
        lambda e: dict.__setitem__(e[1][1], "k", "w"),
    "adds-key-to-dict-in-list-entry":
        lambda e: dict.__setitem__(e[1][1], "x", 1),
}
# live attacks on the salvage tail entry's payload / record
_TAIL_ATTACKS = {
    "mutates-tail-record-digest": lambda e: dict.__setitem__(
        e["payload"]["record"], "digest", "x"),
    "adds-tail-record-key": lambda e: dict.__setitem__(
        e["payload"]["record"], "x", 1),
    "adds-tail-payload-key": lambda e: dict.__setitem__(
        e["payload"], "x", 1),
    "replaces-tail-payload": lambda e: dict.__setitem__(
        e, "payload", {}),
}


def _tail_live(attack, index, inputs):
    """Sink returning the token of its OWN argument after mutating
    live log[INDEX] (a torn-tail entry) through ATTACK."""
    def build():
        log, request = inputs()
        entry = log[index]  # the LIVE torn entry object

        def sink(tail):
            token = quarantine_tail(tail)
            attack(entry)
            return token
        return log, request, sink
    return build


# cross-entry sharing is admitted: admission is alias-free PER
# discarded entry (the reference reading of the contract's
# admission clause); the whole tail is still discarded and frozen
_TAIL_POS = ((3, "first"), (4, "middle"), (5, "last"))
_OTHER = {3: 4, 4: 3, 5: 3}


def _tail_alias(edit):
    """Nested-tail inputs with EDIT(log, index, other) applied."""
    def family(index):
        def build():
            log, request = _nested_tail_inputs()
            edit(log, index, _OTHER[index])
            return log, request, "canonical"
        return build
    return family


def _same_entry(log, index, other):
    log[index] = log[other]


def _shared_dict(log, index, other):
    log[index]["a"] = log[other]["a"]


def _shared_list(log, index, other):
    log[index]["c"] = log[other]["c"]


def _shares_durable_record(log, index, other):
    log[index]["r"] = log[index - 3]["payload"]["record"]


def _repeats_durable_entry(log, index, other):
    log[index] = log[index - 3]


_CROSS_ALIASES = {
    "tail-same-entry-twice": _same_entry,
    "tail-entries-share-dict": _shared_dict,
    "tail-entries-share-list": _shared_list,
    "tail-shares-durable-record": _shares_durable_record,
    "tail-repeats-durable-entry": _repeats_durable_entry,
}


def _raises(exc):
    def sink(tail):
        raise exc("hostile sink")
    return sink


def _returns(value):
    def sink(tail):
        return value(tail) if callable(value) else value
    return sink


class _TokenSub(str):
    pass


_ENTRY_KEYS = ("sequence", "op", "entry_id", "prior_entry_id",
               "payload")
_RECORD_PATH = ("payload", "record")
_DIGEST = _RECORD_PATH + ("digest",)

# out-of-domain leaves: never admitted at any depth
_BAD_LEAVES = (("float-nan", math.nan), ("float-inf", math.inf),
               ("float-negative-inf", -math.inf), ("bytes", b"x"),
               ("tuple", ()), ("set", frozenset()),
               ("int-subclass", _IntSub(1)), ("str-subclass", SK("x")),
               ("lone-surrogate", "\ud800"),
               ("int-past-digit-bound", _HUGE),
               ("negative-int-past-digit-bound", -_HUGE))
# in-domain leaf edges the canonical tail must carry exactly
_LEAF_EDGES = (("true", True), ("false", False), ("none", None),
               ("empty-str", ""), ("utf8-2-byte", "\u00e9"),
               ("utf8-3-byte", "\u20ac"), ("utf8-4-byte", "\U0001d11e"),
               ("finite-float", 0.5), ("negative-zero-float", -0.0),
               ("max-float", 1.7976931348623157e308),
               ("int-at-digit-limit", _EDGE),
               ("negative-int-at-digit-limit", -_EDGE))


def _shared_pair(entry, log):
    shared = [1]
    return [shared, shared]


def _entry_family(out, index, suffix):
    """Entry probes against log entry INDEX: 0 is the FIRST, 1 the
    MIDDLE, 2 the LAST. With checkpoint INDEX the entry is in the
    torn tail (out of domain: malformed; in domain: salvaged back to
    INDEX); with checkpoint INDEX + 1 it is ACKNOWLEDGED (damage of
    any kind: corrupt_source)."""
    def tail(fn):
        return _entry(fn, index, index)

    def durable(fn):
        return _entry(fn, index, index + 1)

    hostile = [(f"entry-{label}", lambda e, g, v=value: v)
               for label, value in _BAD_LEAVES]
    hostile += [
        ("entry-dict-subclass", lambda e, g: _DictSub(e)),
        ("entry-list-subclass", lambda e, g: _ListSub([1])),
        ("entry-key-int", lambda e, g: {**e, 7: "x"}),
        ("entry-key-lone-surrogate", lambda e, g: {**e, "\ud800": 1}),
        ("entry-payload-dict-subclass",
         _set(("payload",), lambda e, g: _DictSub(e["payload"]))),
        ("entry-self-referential", _set(_DIGEST, lambda e, g: e)),
        ("entry-intra-alias", _set(_DIGEST, _shared_pair)),
        ("entry-over-depth", _set(_DIGEST, _nest(
            MAX_DEPTH - _DEPTH_ABOVE_DIGEST + 1)))]
    for key in _ENTRY_KEYS:
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            hostile.append((f"entry-key-{key}-{label}",
                            lambda e, g, k=key, c=cls: _rekey(e, k, c(k))))
    for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
        hostile.append((f"entry-payload-key-record-{label}", _set(
            ("payload",), lambda e, g, c=cls:
            _rekey(e["payload"], "record", c("record")))))
        hostile.append((f"entry-record-key-digest-{label}", _set(
            _RECORD_PATH, lambda e, g, c=cls:
            _rekey(e["payload"]["record"], "digest", c("digest")))))
    for label, value in _BAD_LEAVES:
        hostile.append((f"entry-sequence-{label}",
                        _set(("sequence",), value)))
        hostile.append((f"entry-record-digest-{label}",
                        _set(_DIGEST, value)))
    for label, fn in hostile:
        out[f"{label}-{suffix}"] = (tail(fn), MRR)
        out[f"durable-{label}-{suffix}"] = (durable(fn), CS)
    # inside the domain, WAL-damaged: salvaged back to INDEX (an
    # accept probe with its own pinned receipt) when torn, and
    # corrupt_source when acknowledged
    corruptions = [
        ("forged-entry-id", _set(("entry_id",), _FORGED)),
        ("sequence-shifted", _set(("sequence",), lambda e, g:
                                  e["sequence"] + 1)),
        ("prior-link-broken", _set(("prior_entry_id",), _FORGED)),
        ("op-changed", _set(("op",), "delete")),
        ("digest-altered", _set(_DIGEST, "pdv1:" + "0" * 64)),
        ("missing-field", lambda e, g: {k: v for k, v in e.items()
                                        if k != "prior_entry_id"}),
        ("extra-field", lambda e, g: {**e, "note": "x"}),
        ("garbage-text", lambda e, g: "garbage"),
        ("garbage-int", lambda e, g: 7),
        ("garbage-list", lambda e, g: []),
        ("garbage-empty-dict", lambda e, g: {}),
        ("equal-distinct-containers", _set(
            _DIGEST, lambda e, g: [[1], [1]])),
        ("depth-at-limit", _set(_DIGEST, _nest(
            MAX_DEPTH - _DEPTH_ABOVE_DIGEST)))]
    for label, value in _LEAF_EDGES:
        corruptions.append((f"entry-{label}",
                            lambda e, g, v=value: v))
        corruptions.append((f"sequence-{label}",
                            _set(("sequence",), value)))
        corruptions.append((f"digest-{label}", _set(_DIGEST, value)))
    for label, fn in corruptions:
        out[f"wal-{label}-{suffix}"] = (tail(fn), ACCEPT)
        out[f"durable-wal-{label}-{suffix}"] = (durable(fn), CS)


def _self_append(log):
    log.append(log)
    return log


def _probe_builders():
    out = {}
    for label, value in (("none", None),
                         ("list", [["checkpoint_sequence", 2]]),
                         ("text", "checkpoint_sequence"), ("zero", 0),
                         ("tuple", (("checkpoint_sequence", 2),))):
        out[f"request-{label}"] = (_request(lambda r, v=value: v), MRR)
    out["request-dict-subclass"] = (_request(_DictSub), MRR)
    out["request-empty"] = (_request(lambda r: {}), MRR)
    out["request-extra-field"] = (_request(
        lambda r: {**r, "force": True}), MRR)
    out["request-other-field"] = (_request(lambda r: {"checkpoint": 2}),
                                  MRR)
    out["request-key-lone-surrogate"] = (_request(
        lambda r: {"\ud800": 2}), MRR)
    for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
        out[f"request-key-checkpoint-{label}"] = (_request(
            lambda r, c=cls: _rekey(r, "checkpoint_sequence",
                                    c("checkpoint_sequence"))), MRR)
        out[f"request-extra-key-{label}"] = (_request(
            lambda r, c=cls: {**r, c("force"): 1}), MRR)
    for label, value in (("none", None), ("true", True),
                         ("false", False), ("text", "2"),
                         ("float", 2.0), ("nan", math.nan),
                         ("int-subclass", _IntSub(2)),
                         ("str-subclass", SK("2")), ("list", [2])):
        out[f"request-checkpoint-{label}"] = (_request(
            lambda r, v=value: {"checkpoint_sequence": v}), MRR)
    for label, value, failure in (
            ("negative", -1, UC), ("huge-negative", -(10 ** 5000), UC),
            ("past-log-end", 4, CS), ("huge", 10 ** 5000, CS),
            ("past-valid-prefix", 3, CS)):
        out[f"request-checkpoint-{label}"] = (_request(
            lambda r, v=value: {"checkpoint_sequence": v}), failure)
    for value in (0, 1):
        out[f"request-checkpoint-{value}"] = (_request(
            lambda r, v=value: {"checkpoint_sequence": v}), ACCEPT)
    for label, value in (("none", None), ("dict", {}), ("text", "x"),
                         ("zero", 0), ("tuple", ()), ("bytes", b"")):
        out[f"log-{label}"] = (_log(lambda g, v=value: v), MRR)
    out["log-list-subclass"] = (_log(lambda g: _ListSub(g)), MRR)
    out["log-self-referential"] = (_log(_self_append), MRR)
    out["log-contains-log-copy"] = (_log(lambda g: g + [g]), ACCEPT)
    out["log-deep"] = (_log(lambda g: g + [_deep()]), MRR)
    out["log-huge-int"] = (_log(lambda g: g + [10 ** 5000]), MRR)
    out["log-clean-at-checkpoint"] = (_log(lambda g: g, 3), ACCEPT)
    out["log-clean-checkpoint-zero"] = (_log(lambda g: g, 0), ACCEPT)
    out["log-empty"] = (_log(lambda g: [], 0), ACCEPT)
    out["log-empty-checkpoint-one"] = (_log(lambda g: [], 1), CS)
    out["log-two-torn-entries"] = (_log(
        lambda g: g + [TORN_FRAGMENT, None], 3), ACCEPT)
    out["log-hostile-after-torn"] = (_log(
        lambda g: g + [TORN_FRAGMENT, SK("x")], 3), MRR)
    for index, suffix in ((0, "first"), (1, "middle"), (2, "last")):
        _entry_family(out, index, suffix)
    for label, edit in _CROSS_ALIASES.items():
        for index, suffix in _TAIL_POS:
            out[f"{label}-{suffix}"] = (_tail_alias(edit)(index), ACCEPT)
    for label, exc in (("exception", ValueError),
                       ("keyboard-interrupt", KeyboardInterrupt),
                       ("system-exit", SystemExit),
                       ("generator-exit", GeneratorExit)):
        out[f"sink-raises-{label}"] = (_sink(_raises(exc)), DQ)
        # the sink is called once even for an EMPTY tail
        out[f"sink-raises-{label}-on-clean-log"] = (
            _sink(_raises(exc), _clean_inputs), DQ)
    # ... and never before a pre-sink rejection
    for label, failure, inputs in (
            ("corrupt-source", CS,
             lambda: (_salvage_inputs()[0], {"checkpoint_sequence": 3})),
            ("malformed-request", MRR,
             lambda: (_salvage_inputs()[0], {"checkpoint_sequence": "2"})),
            ("unknown-checkpoint", UC,
             lambda: (_salvage_inputs()[0], {"checkpoint_sequence": -1})),
            ("malformed-tail", MRR,
             lambda: (_base_log() + ["\ud800"], {"checkpoint_sequence": 3}))):
        out[f"sink-raises-on-{label}"] = (
            _sink(_raises(ValueError), inputs), failure)
    for label, value in (
            ("none", None), ("zero", 0), ("list", []),
            ("bytes", lambda t: quarantine_tail(t).encode()),
            ("str-subclass", lambda t: _TokenSub(quarantine_tail(t))),
            ("uppercase", lambda t: quarantine_tail(t).upper()),
            ("trailing-newline", lambda t: quarantine_tail(t) + "\n"),
            ("wrong-scheme", lambda t: "qtn2:" + quarantine_tail(t)[5:]),
            ("bad-grammar", "qtn1:zz"),
            ("unbound-token", "qtn1:" + "f" * 64),
            ("full-log-token",
             lambda t: quarantine_tail(_salvage_inputs()[0])),
            ("empty-tail-token", lambda t: quarantine_tail([])),
            ("prefix-token",
             lambda t: quarantine_tail(_salvage_inputs()[0][:2])),
            ("unframed-token", SINKS["unframed"]),
            ("lone-surrogate", "\ud800"),
            ("surrogate-in-token", "qtn1:" + "a" * 63 + "\ud800")):
        out[f"sink-returns-{label}"] = (_sink(_returns(value)), DQ)
    out["sink-returns-tail-token-on-clean-log"] = (_sink(
        _returns(lambda t: quarantine_tail([TORN_FRAGMENT])),
        _clean_inputs), DQ)
    out["sink-mutates-then-raises"] = (_live("mutates-then-raises"), DQ)
    for kind in _LIVE_ATTACKS:
        out[f"sink-{kind}"] = (_live(kind), ACCEPT)
    for kind, attack in _NESTED_ATTACKS.items():
        for index, suffix in ((3, "first"), (4, "middle"), (5, "last")):
            out[f"sink-tail-{kind}-{suffix}"] = (
                _tail_live(attack, index, _nested_tail_inputs), ACCEPT)
    for kind, attack in _LIST_ATTACKS.items():
        for index, suffix in ((3, "first"), (4, "middle"), (5, "last")):
            out[f"sink-tail-{kind}-{suffix}"] = (
                _tail_live(attack, index, _list_tail_inputs), ACCEPT)
    for kind, attack in _TAIL_ATTACKS.items():
        out[f"sink-{kind}"] = (
            _tail_live(attack, 2, _salvage_inputs), ACCEPT)
    for kind in _CLEAN_LIVE_ATTACKS:
        out[f"sink-{kind}-on-clean-log"] = (
            _live(kind, _clean_inputs), ACCEPT)
    return out


PROBE_EXPECT = {
    "request-checkpoint-0": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "request-checkpoint-1": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "log-contains-log-copy": {
        "resume_id": "rsm1:dd862d1abc577497aa299c785b9aa208be75981e66df3c45284338201bb02b26",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:ff88ac2b7249143c531a426213f4f9af284c3136a0ce7e049e1a5f1b51c1b2bf",
    },
    "log-clean-at-checkpoint": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "log-clean-checkpoint-zero": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "log-empty": {
        "resume_id": "rsm1:c8eadd8f1edf681ca8677525db167091386045a51843e806807f1861c6f1ea2f",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "log-two-torn-entries": {
        "resume_id": "rsm1:6f680dfff0656ad98937396f4edf5a3f2705aef0b1f24b51e61b6494e99b32b3",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:c9ac58f3146203f27ff70b3dc18c976a2cf1fd19e6749c9601a413394798ed68",
    },
    "wal-forged-entry-id-first": {
        "resume_id": "rsm1:1c7ea5d7f793012f54fc47a0658d704b4c9e76a77b985f4c430d59a38e71bde4",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:cd9d0fae5cfc4e6d5e2d38d43f43989df4fd6d82c310ac34a2626cc13b366d89",
    },
    "wal-sequence-shifted-first": {
        "resume_id": "rsm1:08be5e6df5281a2103eb3699802733b26314da2a229cf23dbc9386d25b8a9e8a",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:2070da4de87f59172de6043be7cfeaf1a7d0df689e32e1f05dd57ca830496dbf",
    },
    "wal-prior-link-broken-first": {
        "resume_id": "rsm1:077480ae41403e891b42cd335c7ddb2909813e472c7073d07592a99f946dc7cf",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:bc08911e370247325a2e5b417ee732dcffe5804c1b9db605f63685de0aabf50e",
    },
    "wal-op-changed-first": {
        "resume_id": "rsm1:d09140347657193a2ee7f0106e4eff9f5ad95ea1f33ed11d80f459925cbfc5c6",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:d6aafd378d08d2ea8af1f389552e9188adba451f0fc14443327b20affc08a595",
    },
    "wal-digest-altered-first": {
        "resume_id": "rsm1:b641b93faff3607fd30f77eb1befb3a9c212bbe0bf2086513a72bbb3d7ecc252",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:982466c75165210899e56915fd94ba6c70529fd0acb584f242969dca44634cb3",
    },
    "wal-missing-field-first": {
        "resume_id": "rsm1:f4e723007113695da14cc32b5e9aadadb15c3e3d4f521f5839ba00287737863a",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:bdc41567adf4209a474a810f0bb5a89a0d37381adcb0ad9e175b63c67a1cf30e",
    },
    "wal-extra-field-first": {
        "resume_id": "rsm1:81fee965124cc6d3212d35d4f0178229cbd60d4b9ef237b95c6872108ae87367",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a22d29ea36d8c501f2598624db01f09219a82c740bad3b75b448f4e63d99f831",
    },
    "wal-garbage-text-first": {
        "resume_id": "rsm1:c7305f41bd56ede82b57859e3ebff6d6bab4f5b2ff71dd6bd7f222acc7c25f58",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:69fd88945a206c56471659c40775f7ac61c3295455ed02d813c315984ce52df9",
    },
    "wal-garbage-int-first": {
        "resume_id": "rsm1:9999c5dd6d832fcae1a8ec56ab933ef2c0c82e8b53ff5f2c758ad325d330e657",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:7fa053448d37f98c55e759653d2cc742f5c51d03538f85cd65909f2ce1b78fc8",
    },
    "wal-garbage-list-first": {
        "resume_id": "rsm1:296b11883ccdeab05e3a67f8ed5d6988afd230c844dad884d9060eedbabcae54",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:218643610d271d734c2da3c3d5513807eda5340878c485916d6e9649a1156859",
    },
    "wal-garbage-empty-dict-first": {
        "resume_id": "rsm1:665d2d636c69fc68b9ade2dcc45c4242f22120fce9c3202530978a1647930a12",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:4fecd76a383849471fd1d9d3c9c354f8aa163256bdfd9cadc15575ff4e8132bd",
    },
    "wal-equal-distinct-containers-first": {
        "resume_id": "rsm1:fc854370470042bb499f127cc0741f18f86bd7310f439e5c7751073fb0b615a2",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:f3b22c42d40938d2231cdebbaf509fd318256be9781147bc21e7df6b43bda8cf",
    },
    "wal-depth-at-limit-first": {
        "resume_id": "rsm1:8c17132b37676325774f95ed8e5da3222611c5b84281bcd049eb2dfe8a8e6ca4",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:37eb3794de5c7eda932f2887091acfdf041f541e3f0bfbc646c0d0ea60c49e77",
    },
    "wal-entry-true-first": {
        "resume_id": "rsm1:bb643457c61a2026a87df58ac53e1cf995b6ffa838482cdca7b98821c2c6e7ce",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:d3697be9f75ecbcc3666c3f51c81bef277903e78b343760cd1d5a8632207bce7",
    },
    "wal-sequence-true-first": {
        "resume_id": "rsm1:7cd2ee9ea217512afcdef0645e4db8e32c0755b106c67a73a4ffaf51acdfff2d",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:70105125b40bcba5f79357ac995e476ae27a11efe5636a3f13c4faeecf6afb1a",
    },
    "wal-digest-true-first": {
        "resume_id": "rsm1:42d3e0303b87e32d2ded04a98f127b0054bae6d60289a7b438d0345a1f6a023e",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:1754ac1dec8855faa5ab3d533fababf250680fd53a0ae0b5f74a2563abd86191",
    },
    "wal-entry-false-first": {
        "resume_id": "rsm1:8051b5f6b22d71afedc73a02f7a48a4606ab9b24aff3deaff6877b688938202c",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a9a950d7b751470d2bf13827bf8c6e6c0df68bafb2761f0567bef329b6a259bb",
    },
    "wal-sequence-false-first": {
        "resume_id": "rsm1:b5b06aaa9ba7e72b59a761c20b78812dd2a7a4b893a1e129c10835037bd46ae9",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:0c05a1ef2602377941d7e864a4265b131ba6c716fa790c2bb14755e6c0290607",
    },
    "wal-digest-false-first": {
        "resume_id": "rsm1:d0753397eef6154096e4cf761fb0dfc80b5005428f9d758ee6ed9318e6803410",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:28e5047880d5ff589bd2e9c29622608a2d12f78ee13765eb7c7276b1be3a42cb",
    },
    "wal-entry-none-first": {
        "resume_id": "rsm1:1d5e0f43ca0689b46e396ebd97143b3c697bdb0d784ade957c29a92f43ae0ebb",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:195eceaf25d6860a562a72db378f6de94f7dc1e6a1b180464561c1a0795fca29",
    },
    "wal-sequence-none-first": {
        "resume_id": "rsm1:5506989bd16b2a6b9adf87a9afc0faf5ced07541364bfeee8413eeeec7e0fdfb",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:14772c71be98a5d337dba3de8eef448b95692fa4b2d590b443b8a14cd4626fff",
    },
    "wal-digest-none-first": {
        "resume_id": "rsm1:4cc41d115bd6376a69af6c53b66d3cc3998cf1091b8896bf7479c566aaf9be27",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:c460c579b3712562de4d3409a7e39b129f7be04f7228da6c2a4aaa161b0f06c0",
    },
    "wal-entry-empty-str-first": {
        "resume_id": "rsm1:40e3ff7580f0dabc45884258235a21411a6eaa3f536e3a150ec53287a3b158f7",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:0441680bd25b367dff2ab3e3f5800bf42e13cca58654e42a014346c21d4cae6d",
    },
    "wal-sequence-empty-str-first": {
        "resume_id": "rsm1:aa16652ccab6e89a435bc34fb42370171662ab4a56ab6be1c91fe6a38f641816",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:79fbb7b5451d7aea9f0b86d5b2ba7171903d29e16b5d0ec3395a7e770620f86b",
    },
    "wal-digest-empty-str-first": {
        "resume_id": "rsm1:46ff01eb71e6b528277c2b3cd27f43b23f8695d4f8c7e959312a8aa5c1d03f01",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:b04d77745b9627ff67d77d3fa4d09d33d096cef2573fe686af5e3a0e7d8f78f6",
    },
    "wal-entry-utf8-2-byte-first": {
        "resume_id": "rsm1:b1d3f5ccb8b3b661f6d169c1ed6acf00f51e25a90c1ec39e7d54ed95be4cf48c",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:afa98f2761bba0fa852313e205a698bc3beae6d665dd960f4810e6be05b48546",
    },
    "wal-sequence-utf8-2-byte-first": {
        "resume_id": "rsm1:ca22cb0d35f9b193b594b7bd72482cff5317d613a4d9a22654c9b6e99d820b07",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:f95c6ba1d35f35afc7a4f6e896a890e983befbde916b927ccac876abb98f0561",
    },
    "wal-digest-utf8-2-byte-first": {
        "resume_id": "rsm1:88648733aefe4d7961f6bf53e6ea1efc7a79b294a44e5a19e897e6bae79fb19e",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:16708e0359c6cd9bd725418a394fc136593c942355221fc6327ae4d46e2f1dc3",
    },
    "wal-entry-utf8-3-byte-first": {
        "resume_id": "rsm1:9dec0e43db38b72a17d0caaecab95004afb93dcff07e061eb1e94bd95f1efb7e",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a47154b8bf62f4719bac922581e12032aa02778da73980ed7d395d1d2d12a63e",
    },
    "wal-sequence-utf8-3-byte-first": {
        "resume_id": "rsm1:16f12f374abdbdbcf1ea5083b6e4aab18d605659d4d79bf1ea45abe5ca159efd",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:82eedafa0b84a853e5cfa471dcb0054184aeae11d5dc347eec24bdd4a7006b71",
    },
    "wal-digest-utf8-3-byte-first": {
        "resume_id": "rsm1:9d1521873f2aad246a114dbc0ca54cc94199d53232d3d39f6a220b0c1fc7b57a",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:20ca06bc835dea14383267884f911ddc4a3cc34a37858d636b614d8be064c375",
    },
    "wal-entry-utf8-4-byte-first": {
        "resume_id": "rsm1:c4155e984ff643a56fdb40fe48ea04c209c43ebdee65ca9dd0879d53f4b25881",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:8533aecd199b668c12d2a396b5a95b9711ce11442909f35cd7d073342cc18363",
    },
    "wal-sequence-utf8-4-byte-first": {
        "resume_id": "rsm1:f35aa8d91565af54a88c8b43f0a5bd4c81a430024e2ef99abc88098b8eda4259",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:9634d80b5a920739b2a228bff91a92323392122afc663425b304a9b7710b51a4",
    },
    "wal-digest-utf8-4-byte-first": {
        "resume_id": "rsm1:1c8abd3a8b227e9ecb42f917567a5efa43789693cfd1f972f799ffa939d01e77",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:c1a253ca098245b2a00e8b9540bf0ba13d2a841ce6e030e2f6ddc03ec5303c1f",
    },
    "wal-entry-finite-float-first": {
        "resume_id": "rsm1:0165cf45b2f3c5c26a52e5d630d998d2657754da30946897706991b8ae971a6f",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:7e80120382d4c578ef7018c21f028e5749b0d88e4a81e3c1f67d7a18f4af6751",
    },
    "wal-sequence-finite-float-first": {
        "resume_id": "rsm1:be6ea6b65888b4abcddb5e0364641dd5e8d911d17ebc2d61cd89a4a63c59af2a",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:bc4f3c77f40c0537c79e5066c28e3b6e80f6fc647f5840520118e7ab5fcf17ad",
    },
    "wal-digest-finite-float-first": {
        "resume_id": "rsm1:2a96d5a798ffd20439bc56d86e7526e2350abbd71bd700d42af1dfcbf9985227",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:2b1a7c84e76d3538cba44d22504b0c71076977b15d27fc34192753d4135001b9",
    },
    "wal-entry-negative-zero-float-first": {
        "resume_id": "rsm1:932be1a5b5099a931b28a1de8f27fa198e70950fb0c530143b3da93dacfa8b9d",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a9809e21550fdbd0b77433d0514b4d150439dd1017349e62d12ffb5a2537f53c",
    },
    "wal-sequence-negative-zero-float-first": {
        "resume_id": "rsm1:c70d947d4657db36545e9fbab710e43f2b3a65ef755fb2002d0d861a59da97b8",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:616bace1397f8373c32365bf22168ff619e7a230fde1a9079be95b41b910ef29",
    },
    "wal-digest-negative-zero-float-first": {
        "resume_id": "rsm1:c2236040f7bd29512476331f7629fac481ba8c89ae45bc2fbbe062ad7d0d5ad4",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:5fd69b55a18afc6a194b760e32b65c8c8f7f132879ba71f9b373291065923288",
    },
    "wal-entry-max-float-first": {
        "resume_id": "rsm1:8c4dbfa4875a4c5f2aca080edb82dc61914950072d063683401537f09bb2f16d",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:e5885e16c4103d38e1de8bf54c810a60a4f6dcd87c6b727767e7fcab1f41edb0",
    },
    "wal-sequence-max-float-first": {
        "resume_id": "rsm1:af225cc79af366889b49befd8c2cd13ee75041b846da9484dcc1c82401f0e352",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:ab3c67f91657a45713e6c1ee353268dcee2da102dc1a1dea07873951ff2c6806",
    },
    "wal-digest-max-float-first": {
        "resume_id": "rsm1:42e56aa429934e52dba6670d5e9be373e4b101909af23f855c3e1b50aebd0847",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:b5f87af29d6bccc0c6c129de66d7209b84897e96c442e8f559b1bcba9dc8d6b0",
    },
    "wal-entry-int-at-digit-limit-first": {
        "resume_id": "rsm1:10385b6e28b786cd6fd9952fcd38877f4f9bb82408fcf9aef5c5b2f69159ac21",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:473253b150747535557f1584a36be8c95fd7541b81afba2747d5cc1681ee447f",
    },
    "wal-sequence-int-at-digit-limit-first": {
        "resume_id": "rsm1:cb7da7a44423e84b1ab7d1f3720fe15e48ca6300ca110d2d203bfec44cd1741e",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:00348628779e57f4a1d561485bda565fca5fc4d222bca447bae830389cfa103a",
    },
    "wal-digest-int-at-digit-limit-first": {
        "resume_id": "rsm1:4507de0d8d583486342e6508ac266619ba4e08016785f763913cb6cc5be5561c",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:eb6b912ba5dbb9297cfe6e1b5dd2f3406edcc0abf7043b04b3915f4d31b131c8",
    },
    "wal-entry-negative-int-at-digit-limit-first": {
        "resume_id": "rsm1:09e57d4d340b135e5ec8ea9818231a952a75396f0d3c4035c80d54bff5b19a77",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:f395ce74a6acd6d1a0aedf40c9c2a4a30a330a214cf73cf3a2376d49aa41d85c",
    },
    "wal-sequence-negative-int-at-digit-limit-first": {
        "resume_id": "rsm1:c3174055d92c1e96e7a2b763cdb5ab6946d54a0f9d62b5661f94bc3dafe7a81d",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:83c2825a1a1562633eac512f5a95ee4ccb366db090c63f67ff501459598a1bc4",
    },
    "wal-digest-negative-int-at-digit-limit-first": {
        "resume_id": "rsm1:e296d51ea636d0ea2f79a1f835234b93d1afbb7c309673c621a36ddc053cc26f",
        "head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "state_id": "gs1:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "resumed_count": 0,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:6cc4b3af733026d45fc47987dc3a9c38f6cb861fae16928785df3ede2fbb175d",
    },
    "wal-forged-entry-id-middle": {
        "resume_id": "rsm1:c8a7f8cb205fbe0e0f04f3ce63c8144652ab75818c233885368662d07576a3e6",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:8478c61a1cde6d39e1d8b3981c72d61b70b07e44297c93785624711d7a40a848",
    },
    "wal-sequence-shifted-middle": {
        "resume_id": "rsm1:46ac58efbb5f66fe32d00da97097ae5c719d23ce7d13dfc9943dc0b3a9006f4a",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:f74e9a00bf6a4f52ececcf51bddf6f1d608b447e02f073dfd444c4acf405d796",
    },
    "wal-prior-link-broken-middle": {
        "resume_id": "rsm1:731defcba69b446f3c428781fc23576c87cb84ed5a95a9afd71e051598be7fb3",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:b61d9d87e424553740d57e4b6ac32dda94e493155b2129ecc8df0c366922d6fd",
    },
    "wal-op-changed-middle": {
        "resume_id": "rsm1:a69781e1e87731471ce0182f0d77badd417d42977c86043d66609be253573900",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:3b603116a1ecae2b09fee8a9808d608cd689ca8306ae808e55bcd8fa09f97a0a",
    },
    "wal-digest-altered-middle": {
        "resume_id": "rsm1:de5160b1011e758f35f04b90d71eb21fdb828b0c598b69c4c413b6dd3eed0810",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:16040dd3fe24b1dfc819122cbc30b26361b1d7525fe851f9428c110d7320dd17",
    },
    "wal-missing-field-middle": {
        "resume_id": "rsm1:ed76fe2b114dc56fcc34765e293a0952113806fc1a21fb922bf2911a60c8ef7b",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:04749eec509e74b38e449e87fe53f47be1bd309e1c5a91714209d7059a331c19",
    },
    "wal-extra-field-middle": {
        "resume_id": "rsm1:759478ea9c76d0d83e13311483cf544ae9d6d1363436c38488c7de2a397f5751",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:cd6c8334dad439983276ec48436673ab2cfcc377702afc09d561b6911a5ac386",
    },
    "wal-garbage-text-middle": {
        "resume_id": "rsm1:e36ff6970ab6860f27317dcd197d3919d2a524a96d55d4392dcab7576eb1cba7",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:d8c44f3521ad9b02c5177d25932240beca7d71513ab41ef2b7bcc68d560eef5a",
    },
    "wal-garbage-int-middle": {
        "resume_id": "rsm1:58b343522f358e71d67622c6ebbfe0e78d67557700a8ee4763ed5bb2ae9b24ab",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:95331047d0bf7773072d4a3d79da5e50b41263ed2f9e23699417adc406f95df0",
    },
    "wal-garbage-list-middle": {
        "resume_id": "rsm1:15064b08252e6ea0fe40ec84863cf4ac3c9d69c19547ecbc2869e908cc95c8d5",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:2d714f10d300f11dcf809ae26457d8aeb7a2e10b70cb94d7f2dc862a0e84064f",
    },
    "wal-garbage-empty-dict-middle": {
        "resume_id": "rsm1:007f5a890efc40943bc282a550778de7e9ed6e90318db6d55074a775c044da4c",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:3b2779c756775ad8cc245b3c8c1542f5d76baad676907977edae76ae284a3c5f",
    },
    "wal-equal-distinct-containers-middle": {
        "resume_id": "rsm1:aa64aa8d9a4841ba2ef895003176e32a97772cea91ab1a6c5600bbec7b4db483",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:7b42f8942d8f21dd41762a2eb68070c0bd4a8e144ab050b9e34e7d931c35d99a",
    },
    "wal-depth-at-limit-middle": {
        "resume_id": "rsm1:f5c055e5a6e6144e2353d3cc3a9faf92c799e755c304f7acfc51b384fc719e3a",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:f699a8751e1843c9177a9f0f13daa9f804713e6ab65cd13cf8ef934aabab8fb5",
    },
    "wal-entry-true-middle": {
        "resume_id": "rsm1:2fad3e3ed6190c23ac5ee2da9004b0768aa67d20b00cd9c0f235becb0139ec38",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:f4877ff2f0638d315d3cb9def5e83b53b775b616d7cd5b53ee7ed3e1f704d9da",
    },
    "wal-sequence-true-middle": {
        "resume_id": "rsm1:a4c94e18dbdb8b6922e1018f2169b599d3fe7e27ebb60d4a71e713913660c9e5",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:aebcf0ff578d2e7963907f5794154754321302f036f82edfea8dc083e098a7b7",
    },
    "wal-digest-true-middle": {
        "resume_id": "rsm1:97ecb4d0d2c9e0ab4f5f9df8ceb370678dbedffa0aa650b79dc497e48007863e",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:4c5dbdcac290540d3efeec18a9812a4a5c6d74a891ab8a120c2ce3c252452974",
    },
    "wal-entry-false-middle": {
        "resume_id": "rsm1:7e663a432938ab6f4b85594a315f5f6d465d040002283abe3fe8874dd3e4db2b",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:d483392747d1ec8484e6bb42775a8d10e65c156bb516965272340788b2ba1197",
    },
    "wal-sequence-false-middle": {
        "resume_id": "rsm1:f0c55816d8637d60d029ebd97979eec8c0390987c3db7c7389984b08d40b0573",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:0afad40ee3567f56f6f83772fdd92ea33bc9447026e8b4c9f9f5d04d49df1352",
    },
    "wal-digest-false-middle": {
        "resume_id": "rsm1:f9edeac3834170258d2bdbc600d27a812f427424c451b085a17faba4eb59435d",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:236509d1f13f746a58af2967c0c07ed3745a3877b1767cc67d89257452584481",
    },
    "wal-entry-none-middle": {
        "resume_id": "rsm1:c6f0a4c96d1c116cf62c1513b03e1f397853ac2947566df145bfab89509559e9",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:6da4cb66b08fa700bed56dfb1cb55bca375e1f08a98eeff6b24ac7202ef8a337",
    },
    "wal-sequence-none-middle": {
        "resume_id": "rsm1:544ab894177ea0a31713e6ec2f6639ddd0e99b200828789b02499b76776a1232",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:6b78f8888e96b0c02148f0ad77be8673b14c39c519609b123f588fa7e21314c7",
    },
    "wal-digest-none-middle": {
        "resume_id": "rsm1:51d92eb9f496a6eaa67daf747f2539bc5ea01f29d4debf3d404dd4b0d728d943",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:999ac2fb7c9128bbf8ddb232d627bbb856ed78fa27a96dc0f3b814a0266098c2",
    },
    "wal-entry-empty-str-middle": {
        "resume_id": "rsm1:257c82aa6b936e53fa4acef83e355fa08f738ed9cd7e61018626fcf75d334c79",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:4cbf2fcc4f3f5479402923ca4087cf95c2c2ed0447b34d7a6849db0fe2dfbb38",
    },
    "wal-sequence-empty-str-middle": {
        "resume_id": "rsm1:4a6ea5f61b296e7e9bc820f877ffd0ed7ef8d11ca61859ad9800d3228a0a68e3",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:dde9581b97107b9d6d396ae2221d1092b407d29a6918629ea7bf719fde429b1c",
    },
    "wal-digest-empty-str-middle": {
        "resume_id": "rsm1:2a5934dbb0d81ba30714e236c5c93f993157fee6c1a526dd8d290cfcf1f6643f",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:aeb6971e917f67538a598f2ea70d9ef877059c0b8488ff2f36b7547037b26993",
    },
    "wal-entry-utf8-2-byte-middle": {
        "resume_id": "rsm1:7ef406715b7432bdc2cc047b1d70c39f5b8bb6621ef1ee866edb1ba417e9f7c4",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:ea2f208315fd0483cccb5c598690bd0dd03fd277f1126edf3b843540390d241f",
    },
    "wal-sequence-utf8-2-byte-middle": {
        "resume_id": "rsm1:c5a1a5419d7289da3685b5f2bf1f87ddca12f53042f82553e966e238a1570186",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:9a21ee0df0ba9296df792589934fe903d6625749ad9909cfc46b0c28a0f866da",
    },
    "wal-digest-utf8-2-byte-middle": {
        "resume_id": "rsm1:304b8bff3e3d2f5f87a13407cc18e147775fedd2b5d12cbbe265e28bbe06a0d7",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:8404607a646a20664206fdefdcd9007831829c4f2d1c8e9fb67f2f3724f53f6a",
    },
    "wal-entry-utf8-3-byte-middle": {
        "resume_id": "rsm1:b8b1c4064f951fa6c00f6d8747bbd03bc8d1f6531e50dc6718701597326b1d5b",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:b071391f80529eac2b4ba7ebdf7df3f8019de10642ad697bc96ad7196ed65b93",
    },
    "wal-sequence-utf8-3-byte-middle": {
        "resume_id": "rsm1:c1c34b6f0bc35f99e80d69d0acf84d08c21618b02a13f6279ad34194dbeda6ae",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:6f6ac8d57235657cd69738a6aa68031b716feb04b613c1045924b090863027d6",
    },
    "wal-digest-utf8-3-byte-middle": {
        "resume_id": "rsm1:3b474f5e8249a3e78f0cf0c4d0ebf7aecc7c968c56e0640bd4534e2d6e54f676",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:a2aa9dbfc1fe706adb2219e654a4399530eaf97275095552bc3c7dc631e5333f",
    },
    "wal-entry-utf8-4-byte-middle": {
        "resume_id": "rsm1:546dacadb3be8b5d0081ec837ed468e945981c00bda1e41e25059c09b33da617",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:49aae7b3d9964402a6c5cc77de659ecd87c4f2025e5ba65b68a50dc1d79e0ae0",
    },
    "wal-sequence-utf8-4-byte-middle": {
        "resume_id": "rsm1:96adb9c4a9429938bc4d86845f4c30ffd58fae6532e5825e8595351e22ee8a29",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:9f5cbadb24778b300a1748bd29c2cdc19610266f0642dbba595520e054317155",
    },
    "wal-digest-utf8-4-byte-middle": {
        "resume_id": "rsm1:d1b57fbf20f06dd4f8e536e63e5c0668aa93291f4f4a8fb7d9c830a75dfe9604",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:a0c3b8c9d3497acf6ea0e42b6fff43363d9415d66e421fc294dcda09f6315a5a",
    },
    "wal-entry-finite-float-middle": {
        "resume_id": "rsm1:ab50186ade6a38fbc27cc8f0268f355e1e741a902b9812f7c8e8aaef81f05c43",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:57826f73aa42957919ffe0a4fd303ce2a5b896e9bb4fcd896a9ecc8884bfecea",
    },
    "wal-sequence-finite-float-middle": {
        "resume_id": "rsm1:8739da9031ff78afa2b4819db79aef2c60115c0fa2f4c0efd5893081445bf7f9",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:c9b18925caff4f8a4378cf9b74faa484fbcbfa428b5720fb98bd4ea5bf359421",
    },
    "wal-digest-finite-float-middle": {
        "resume_id": "rsm1:832ad417e04cd29b1e41f1974b9080028f5b856c11408798c88e96e4a6611684",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:ef91764b7a4cec4b7a948c5a39f5610591574c7e423e484ab285ac2697ac99fd",
    },
    "wal-entry-negative-zero-float-middle": {
        "resume_id": "rsm1:77e2a66db879a34a9bae117f790f7bbee7bb37b2a7f2e647f70c7620e869176d",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:00f54d87ed25c4b1179e2f405eacae6fcae5cd464eb15347405a0a6f94f4dc96",
    },
    "wal-sequence-negative-zero-float-middle": {
        "resume_id": "rsm1:75860001638cc542ab8c32cf5b084c24817d3d0ae7ffe315d6122bb5011d7d1b",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:b1be0363e5d2013badaf76b2ae37bd81db6a2fe8b31243ebe0436e8085997899",
    },
    "wal-digest-negative-zero-float-middle": {
        "resume_id": "rsm1:98059a43f56b283f2011070e3a85014a13c9f6ccb25ec34cd336b608b72685db",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:bf8a76f5190d7c1c01748394638bdd31ea71619404edcf170fe798353a2f8acd",
    },
    "wal-entry-max-float-middle": {
        "resume_id": "rsm1:1d81c391391b1cd33d713634aa4a28c0fb764f70d484f9c68d4f6e86bb13652b",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:a8b089e30fa872149de4af4b46487a4975730f46ce530ffcdb54b21ac7212cf3",
    },
    "wal-sequence-max-float-middle": {
        "resume_id": "rsm1:8b88059c750eccdf18766738c970d8567fcf1447c5edd4a940c85e897df9ef8e",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:29668457bab412543703abc892aa0ec22937d7b3d9e5627dac8b95d05a1e9392",
    },
    "wal-digest-max-float-middle": {
        "resume_id": "rsm1:244a67aba1a97895761073698559dc2eaa3b64a8ff7ef7192e9b46066cbf210e",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:61dc448dfcc41cadefc3d52e0c1c4bb163f53922c08936c28496fe07bb07845e",
    },
    "wal-entry-int-at-digit-limit-middle": {
        "resume_id": "rsm1:77c14c5038020552421d4a21f5fae32e895055fd9fd6f1a61f36e72cb47cf2aa",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:8cd3be6630dd94e8a5c28574d7312d1b32ece34ea39e930360148ddb94d9b282",
    },
    "wal-sequence-int-at-digit-limit-middle": {
        "resume_id": "rsm1:3ce8b8a2ede0f25b081493d7db241c1002ab5c53aacd0bcf1ab56faaf6d552b9",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:c45d709a4619d1c6e19a643824c126940f6cd61728d0303172127f81ca6b17e0",
    },
    "wal-digest-int-at-digit-limit-middle": {
        "resume_id": "rsm1:9eecd1cad3d3bed74f0a23c22d9037e55028e77a74eb076e38dced4e5c0d9cd9",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:248a059a87e3a0f7893c04343873af38ce3ffc06a8225da638e123de4bfab532",
    },
    "wal-entry-negative-int-at-digit-limit-middle": {
        "resume_id": "rsm1:89c536193933cc3f0e6a8805d6134075db0eda1bc11952fcd4a05d7fdb36ad74",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:6723dde1dd6813447503a4b4f42d4d2161d0d4bce5a7b011bb96d36adb46b23b",
    },
    "wal-sequence-negative-int-at-digit-limit-middle": {
        "resume_id": "rsm1:f30262698c106f6f91f59ac9b84191be42625240ff2740635d9f3ee6aebd506e",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:a93e984c3402d81df8b27a76c3697f31d1e7b3b6d433f63119311be681797f38",
    },
    "wal-digest-negative-int-at-digit-limit-middle": {
        "resume_id": "rsm1:823269b434b18f4ae8155b11c77fff4150713d412124e3a8e38d20d631599b02",
        "head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "state_id": "gs1:92b2bc73d0eedf92c99fb0853b3bd31701bef018ca39282cc3c5c5eb5888a60a",
        "resumed_count": 1,
        "discarded_count": 2,
        "quarantine_token":
            "qtn1:63db8d4b7d09836682bedd63f0115dfcfcf1cd4b6cb0d8b2c686a4abeed79c3b",
    },
    "wal-forged-entry-id-last": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "wal-sequence-shifted-last": {
        "resume_id": "rsm1:c0e308ec7e7af252c82832460154eb979b83cc0f9cc060f52a2b541f8b57e7f9",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:b7ca80f76db6cc52d59193f067a7dc4b620ca9dc65cc704551986294c8296294",
    },
    "wal-prior-link-broken-last": {
        "resume_id": "rsm1:3a40c7fa8caaf6a3d5bca0c044e4208a6f68534034bf7ab621c59e8050435e1e",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:87d5a2fab4c706adaba6d1542301a760d5c8850132c0b43caaf066d55256cbc8",
    },
    "wal-op-changed-last": {
        "resume_id": "rsm1:03b2d7ba612be29be17fbf17ce2ae52161507b94ad4e8b046d374ff3bac9f8a1",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dfa692c9bef57aa2d35fe5c6ac230c8ff0f4409bab41129a80743620054b245e",
    },
    "wal-digest-altered-last": {
        "resume_id": "rsm1:90a6433092036b364e783dc7832e0e257c4d5761c9d672b1063862568b1401ba",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:eabcab225cbdaf3e68b0afe5e5343ebb5380ad30d8cbe352ef61bcbab30b5be9",
    },
    "wal-missing-field-last": {
        "resume_id": "rsm1:fe467b481f9ab32069ba88d89813a500229a9a6a9b14b46f978dbc008310fd4f",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:a6bd0880aacbfa68db5157936a55ba4fbbc5b7c73e19f364faddde87e1e51849",
    },
    "wal-extra-field-last": {
        "resume_id": "rsm1:ff8f41b686a3ab936b582a9736652fba8e13b5c7c052f4060b308869b8b75d0c",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:79f6bd5ea5d299f9f3c4881abb3632518832ef8f4e32d835a1111ce0747eaf20",
    },
    "wal-garbage-text-last": {
        "resume_id": "rsm1:897217b64096f008deeda269b18be26d10a535def714b90c3d52ffc6a93eb448",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:5b4ee4b463d0b9c9acf8c7967fdb2ae4bd8204c877c7de4aad6e4253926fdc5c",
    },
    "wal-garbage-int-last": {
        "resume_id": "rsm1:548946429b2edaa0e2e6d874fd2c82c7c657edcbe4d29ac2e66e9d1d4281a1de",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:a2ad91ae99645aa6ce0626ad0385bbc739cba347a6d656562dfe540e45f024f6",
    },
    "wal-garbage-list-last": {
        "resume_id": "rsm1:06e3303c15d62ab47d6763644c757ef32cb90daca1419800255e30f0f2247f79",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:1d2e30634c609ac5606ef34daf559cbc60f49575df74a6d5cbda3c16b631e9c3",
    },
    "wal-garbage-empty-dict-last": {
        "resume_id": "rsm1:be506a928dbcd6ff5644ffb684a8ba24b340ee0e309fb17c1c2ed292d31ee34f",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:9f2ce04798225cecac3441996a745c4f3954402a3093cb4482cc6284dbbce93b",
    },
    "wal-equal-distinct-containers-last": {
        "resume_id": "rsm1:b0af8151a619126a064ed7f6fdcbf89517153df9a2b0cb632fb5708daf4dc81f",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:7d13236fefae990b402f818cf86416e467181b36f7715ee533b816fb9f626372",
    },
    "wal-depth-at-limit-last": {
        "resume_id": "rsm1:36017199c471dabcd0824bc29789403671d21483b80aa9a2ba9da35ff1b7cbc4",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:9d5a9d1dce1f77007cc4718fc5b08352d32a9b5069cdbf34c7c1d8205827359d",
    },
    "wal-entry-true-last": {
        "resume_id": "rsm1:d4a287db575b26e1209716d2cc2bddf35c87bfab30926b48a8a6e2e0e849aab8",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:0ab9b8f575311bb3669f574b58202cc0ff231aa27d5f174e0d345a4d6c462c02",
    },
    "wal-sequence-true-last": {
        "resume_id": "rsm1:f6d00962d2ca0b90540c0810f68298b97801e3e6f47a199f3fe1b031612d6d51",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:2f8dbdcbfcf2c7ff81d6fe43bad0a2d6768dfd46cfdcf0f7f612559e6b87723c",
    },
    "wal-digest-true-last": {
        "resume_id": "rsm1:6be0e2f89b89367aca3d0cac7ad93c677dafd07582631e78e43c8a5d7302561a",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dde9ddb6d7f0f7bfb02085adab790d7e0d70339809726c5c47abf3abdaf4756c",
    },
    "wal-entry-false-last": {
        "resume_id": "rsm1:84e5b5b701005ec74b130d15ec1698cd09e800929180a888140cb7fb4e8dbc11",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:1f77e04a40c9055bc9f55ece4fd55e153f437dac61a237e1ab663d2b2938ce07",
    },
    "wal-sequence-false-last": {
        "resume_id": "rsm1:0b0f79816236b45b60168cafa9f8c424c0a1312d43ffb3ef445ec8421c58682e",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:fac52e6753a848637dff73dd309b6f6354e48453f2ae3fa97a4318252a7a7298",
    },
    "wal-digest-false-last": {
        "resume_id": "rsm1:bea13722be56d0829ca51f803c505e863e36964692395c2cf589157ec849dd36",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:aec723c7433b2bbe685f2cc0110c1d1732aeb05bd66f695ab7d50f26d1b0d59f",
    },
    "wal-entry-none-last": {
        "resume_id": "rsm1:5cfc4bff039b1b602d1e744c8d65d0530b327279c0add49560919140848a377a",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:1e6a107d65684f3a8f387133410c00c4ce2776729c86d8904442da6d97fdeb96",
    },
    "wal-sequence-none-last": {
        "resume_id": "rsm1:6b8b08ffdacfc3153a745d02cd423d2ac6c4c1280613547c5bdc29dfb91e41a3",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:65ef333c572e6840fee5b0f39805aa4a820056f1b9bea74839769fdd5c703231",
    },
    "wal-digest-none-last": {
        "resume_id": "rsm1:67d7fb04f8065d3c84d0b7214c7d2f39bdfd1c3908dd5a365feeeb4b9945062c",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:786869fd0b2a909ab46541e3cfba63d2577d52f6ba6e535e47ec7f7b6f7dc346",
    },
    "wal-entry-empty-str-last": {
        "resume_id": "rsm1:bfca108c6e6c435e44e94a98c59da3f9fff2dc54c657a2844358e3b7c840c21b",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:81cfbde6908f8c55f9f578c75775a4aed2d46885f31b5f39ce6d2546ead19a97",
    },
    "wal-sequence-empty-str-last": {
        "resume_id": "rsm1:de1aa1454feb5a8c3aed549161610c6ea872f3401a8921ca5845c009f2a97389",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:d9d227d49875113304d873e119745633733f338001233b1ef4050142e4e1e4b4",
    },
    "wal-digest-empty-str-last": {
        "resume_id": "rsm1:becfe6c1143648a4cc64b5f02afaa2324e4ad9f20ddbac2a4ebf7b720e9a8ab5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:72073fa275560db3761c897b11dbc1a4de858c3155cfe89a41cd2c9584a29e67",
    },
    "wal-entry-utf8-2-byte-last": {
        "resume_id": "rsm1:bd4bc9e67a028a1df30c3304458cad4eaf8d3533c528df66f4d6a5fa528eb3bc",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:09bf2968756fcdc50f03571927437dcce15ed837f63021d20fce006a50ef406b",
    },
    "wal-sequence-utf8-2-byte-last": {
        "resume_id": "rsm1:0a55de534dce308bd94fadb99985a16aca9d213339e02c7995d343b008a83e3e",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:712eb4dfa972db489c5953b4219d582a930aa03b4ffec98438580141d9f49ce1",
    },
    "wal-digest-utf8-2-byte-last": {
        "resume_id": "rsm1:d57f422f42e56cfbf5577d8d58e07537d14f6ba6f17a81f16cbbb84f735e8118",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:57dd993748b5f1d5c4294346f33591e5e2cdfa57db9122af12b015107aa40abd",
    },
    "wal-entry-utf8-3-byte-last": {
        "resume_id": "rsm1:bdb3e4ac7012442687a3845baee6f60cb3cd51116f80122454ab5200e4337eae",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:03bbcc6b722293c6440156b5189dd5b1db07177f971e849851d775b1b2ed2f6f",
    },
    "wal-sequence-utf8-3-byte-last": {
        "resume_id": "rsm1:60cf4e4adf5b6a100d92e98549ba825eb3a1cd42fefc5ea4741ed24846ec9738",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:a20e34419b14126e71f3b1948012a6bd7f6bd4a7e840528f405a4eb018bd1d3d",
    },
    "wal-digest-utf8-3-byte-last": {
        "resume_id": "rsm1:8e8e9b18552d57eb0556d4d7ba570924860e1b49b2967fa33c21356b4ca718cb",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:64588cd33abe5801f3f6f55ffef6d9b7c50cc9760e9d75f8a05c13ca0253ec93",
    },
    "wal-entry-utf8-4-byte-last": {
        "resume_id": "rsm1:c337094436b920a839d7e2c7813d14f144c530e69a2619e18ca12320482f2b5d",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:e164802e5dd58f0e4ac552830b4f59a0f1c9c611a398397e20712ffd1e378b3d",
    },
    "wal-sequence-utf8-4-byte-last": {
        "resume_id": "rsm1:bdc1b977108f87591da00de2630fe49f9d0c2e77b0b378b0675505a8eb09218f",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:52b7da803616f7230d55b452b68e1798048877991242111a1a619e6bd61959cb",
    },
    "wal-digest-utf8-4-byte-last": {
        "resume_id": "rsm1:9c2d58d0e5e2e247086e5ee610a1b4116b60f80c09b038534f8737723f566057",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:6cf422cf654140234db645207b197dfd10be8057792f41cad75346d693533241",
    },
    "wal-entry-finite-float-last": {
        "resume_id": "rsm1:01324ce632488f854537cedf5c1dcfaa9d146e31b08c57b616bc2654e0487bd0",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:38688390a6375b98f91a5606121cc1db0fc0bd110b5525bcb38a8a2d73e7bd75",
    },
    "wal-sequence-finite-float-last": {
        "resume_id": "rsm1:ceb6f4f73dd527fb841db053f6cadb851752e9949cc105d5d0f5e41d10418c15",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:f5b92c5d94f5bfa0590d791951eb806c4b80332c5bf71290383a47839d5587d7",
    },
    "wal-digest-finite-float-last": {
        "resume_id": "rsm1:2044d7276bc4f6c11a33d38e2f464bd0e4744b2e6de098bb5457a2645409afe4",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:17e27934d492f21f816d601982d9d528b5d9376b37a7bf1f1d3dbe7221e72883",
    },
    "wal-entry-negative-zero-float-last": {
        "resume_id": "rsm1:cc2a8ecdc230ed80f993f2d1d3a9b0d90b122a402a4bae38d09b3b88fc0603ed",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:a12fe60a4ba33b2d73f4d3f91733f70f7dab5444dd9bbf0a8d72b57ffeac4ba7",
    },
    "wal-sequence-negative-zero-float-last": {
        "resume_id": "rsm1:8bdfc6d7aa7b072d2e73a6e2700b03b123f79704b1f2579d4ed4a999c72d2015",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:e4c4b3e70f1a66847ed9736b7e299bac84f98ec7e4c727d910806e68367ac82c",
    },
    "wal-digest-negative-zero-float-last": {
        "resume_id": "rsm1:f682bb5fdcb006f222f0701dd24c7823a7c4482003b0afe5ada53ad6117a93be",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:4cb163000c578f6d1ff03d88dcce00ea2603264d153b1beba0e722b334cc3d23",
    },
    "wal-entry-max-float-last": {
        "resume_id": "rsm1:48dc523dd600260fd3ff1940aad5dff8a1dfe38336380d014cbf300b001a6ab3",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:59dd533e0471693f4cf0ddf8f1d055c076361bf7f152cbfec6f29c79454a3d1a",
    },
    "wal-sequence-max-float-last": {
        "resume_id": "rsm1:12c51b766ebf72963a9b5fcf6cdfa2125d4def0a720c997937cd277932939f52",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:79935267c6dd53e080b49d1ab7365ef605284444e16596836da27d6b520532df",
    },
    "wal-digest-max-float-last": {
        "resume_id": "rsm1:70b553c3357729a46a324316efaacb23c7c6862e6c337959c1ec92c62b8d49d8",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:3332959121f2fa211371ba492a68291e9d65555b9cd9ae25c5798565d31601f0",
    },
    "wal-entry-int-at-digit-limit-last": {
        "resume_id": "rsm1:f4010991f7cfcd957ea4b577aacd84ccebb3daf06790ff0aded5cb85cb9929b3",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:797cf1fc49c4b1b73e563523b5861d6a68d38396813ef7f236d6ba9be6d220b1",
    },
    "wal-sequence-int-at-digit-limit-last": {
        "resume_id": "rsm1:1ff2e2cf19b10a4162c3980b915737b8b44e202de8b4baa604bd481fac258d11",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:489c72e8758059370b82c890f6238864894fcd058424fb51bdc6b142eb7107a7",
    },
    "wal-digest-int-at-digit-limit-last": {
        "resume_id": "rsm1:7e005beaee3a23a98d291e8ac8e4d0bc1c90d4f333a48c0ba8ce999f0dcf4938",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:449ec55c39ce8a9fa345e279c05bb0f6ce0ca4300f9c08951b5fe43d05edd2fa",
    },
    "wal-entry-negative-int-at-digit-limit-last": {
        "resume_id": "rsm1:2654c9a532b7fc5ffd45b471ee2bfc472aecb3eb2595aef87a50c7db9aafa405",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:7343f3f02735ae6fa3c38c96d0e7b359288b0da2ca7ab622ff22efbb42ec6d94",
    },
    "wal-sequence-negative-int-at-digit-limit-last": {
        "resume_id": "rsm1:715b66630db78a2a1da6d2407b07363c32937632e4042d4126cb6ae80b2f9114",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:45d00f2d9d7c8fdffe0664af6f76bf18a612fe45d1c1e5008aa17a77b2fa0ea0",
    },
    "wal-digest-negative-int-at-digit-limit-last": {
        "resume_id": "rsm1:15b47283a367a4c85eb99223b310837bd6b7ef114946c054cc9cefef8aecc9df",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:fc9a0d8b5db9212bb98c974e3869022565e86f27996d977a8296bd14aa854e10",
    },
    "tail-same-entry-twice-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-same-entry-twice-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-same-entry-twice-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-entries-share-dict-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-entries-share-dict-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-entries-share-dict-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-entries-share-list-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-entries-share-list-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-entries-share-list-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "tail-shares-durable-record-first": {
        "resume_id": "rsm1:9c076f1d9ba3aaa90816d2a2c36a6789bbb7bccd2d0576e6f3952dff672343e9",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:50d0c31c5dac2fb3c2c2eb2c8d3c2e72bebfb6a33f4c51f0ac1c5c24cefad5b4",
    },
    "tail-shares-durable-record-middle": {
        "resume_id": "rsm1:4e6bb15a4003adc872744ebeab69eaa83d0f93fed068f1c9f6a186dc11e203b3",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:dfce6629164204adee780502e4f7b1f40bf2223383cc45676b7a701b719f317e",
    },
    "tail-shares-durable-record-last": {
        "resume_id": "rsm1:c4f0aebde78c4dea4ffe2482bdd6a0e02ee345b96f4821b56cbc07fdf7aee148",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:9e35b9fb1210269f450463848e5a2bfc99d23a3b15f7f349c36a88a83ad5eb8b",
    },
    "tail-repeats-durable-entry-first": {
        "resume_id": "rsm1:d76f88e476d7521e29102b6a57db543377184133b873985570a19614950f8bab",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:e0e5c23f1173f3f86ec7d0bf47fdb13dc66931cbb511696f53a25528d6a79db3",
    },
    "tail-repeats-durable-entry-middle": {
        "resume_id": "rsm1:732f04187cff1edbcadb1aec989adaf513094688b69d7df0b14447d817de13b9",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:9cab38b6ecd5bf35c1e1852cb1227da1ec6aec661cca22060e3955277c8f1b4d",
    },
    "tail-repeats-durable-entry-last": {
        "resume_id": "rsm1:58da994ef071fb183727dbd8da9cd6dce84094d7498ea974fb9b1577c71a5b4c",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:d920ba35b27364ed985e9e4680a8c18da6e6be0df497d77762e1fc3f0f940efe",
    },
    "sink-mutates-first-entry": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-mutates-last-entry": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-mutates-first-record": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-appends-entry": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-pops-entry": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-clears-log": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-replaces-first-entry": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-reorders-entry-keys": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-adds-entry-key": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-adds-payload-key": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-adds-record-key": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-mutates-checkpoint": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-clears-request": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-adds-request-key": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-own-argument-appends": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-own-argument-clears": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-own-argument-entry": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-own-argument-adds-entry-key": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-own-argument-payload": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-own-argument-record": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-tail-sets-nested-dict-value-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-sets-nested-dict-value-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-sets-nested-dict-value-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-adds-nested-dict-key-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-adds-nested-dict-key-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-adds-nested-dict-key-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-clears-nested-dict-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-clears-nested-dict-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-clears-nested-dict-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-appends-nested-list-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-appends-nested-list-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-appends-nested-list-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-pops-nested-list-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-pops-nested-list-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-pops-nested-list-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-appends-doubly-nested-list-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-appends-doubly-nested-list-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-appends-doubly-nested-list-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-adds-entry-key-first": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-adds-entry-key-middle": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-adds-entry-key-last": {
        "resume_id": "rsm1:32ad91d2dd5c7a76aea244e1e6b1ecd98f6b4f9fea9d4e9c4bbe78d132937b1e",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a84953b5bab8a49619ea1fec21dbf0cbd814cac19a73ab907bdee00451f8bfc0",
    },
    "sink-tail-appends-list-entry-first": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-appends-list-entry-middle": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-appends-list-entry-last": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-pops-list-entry-first": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-pops-list-entry-middle": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-pops-list-entry-last": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-clears-list-entry-first": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-clears-list-entry-middle": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-clears-list-entry-last": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-appends-nested-list-in-list-entry-first": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-appends-nested-list-in-list-entry-middle": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-appends-nested-list-in-list-entry-last": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-sets-dict-in-list-entry-first": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-sets-dict-in-list-entry-middle": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-sets-dict-in-list-entry-last": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-adds-key-to-dict-in-list-entry-first": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-adds-key-to-dict-in-list-entry-middle": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-tail-adds-key-to-dict-in-list-entry-last": {
        "resume_id": "rsm1:49c474be9ffe59aa7a3976f534a48a472362be5d68561673db795157fdb0661a",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 3,
        "quarantine_token":
            "qtn1:a985ba91ec271aabccb45e9031374a36f07a91f7c81a15aaaca2d763bf70c1fd",
    },
    "sink-mutates-tail-record-digest": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-adds-tail-record-key": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-adds-tail-payload-key": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-replaces-tail-payload": {
        "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
        "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
        "resumed_count": 2,
        "discarded_count": 1,
        "quarantine_token":
            "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
    },
    "sink-mutates-last-entry-on-clean-log": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "sink-clears-log-on-clean-log": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "sink-adds-entry-key-on-clean-log": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "sink-adds-record-key-on-clean-log": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "sink-adds-request-key-on-clean-log": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
    "sink-own-argument-appends-on-clean-log": {
        "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
        "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
        "resumed_count": 3,
        "discarded_count": 0,
        "quarantine_token":
            "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
    },
}  # GENERATED
SALVAGE_EXPECT = {
    "resume_id": "rsm1:5942778f6902a4e44e60a2e344e4faeaf870621ebbd92c3cc73fa86d2c188cd5",
    "head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
    "state_id": "gs1:c532bfcfc2f83500766b1603e71f269e571bc7e774ba6ffc4341683233e65a33",
    "resumed_count": 2,
    "discarded_count": 1,
    "quarantine_token": "qtn1:dd66eaa36810fe8b438d2e18393ff2435d69fa179048b0463c86438889394afc",
}  # GENERATED
CLEAN_EXPECT = {
    "resume_id": "rsm1:c4f74eed0a169ff3b4138b58cb2eb153520f8e326f5667bbb56b326659e29b11",
    "head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
    "state_id": "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
    "resumed_count": 3,
    "discarded_count": 0,
    "quarantine_token": "qtn1:f8bfff38c0e60e031f49127908041530fa5c7220e4121dc6e81c4cc8c60eb27e",
}  # GENERATED
PROBES = _probe_builders()
PROBE_MANIFEST = (
    "request-none",
    "request-list",
    "request-text",
    "request-zero",
    "request-tuple",
    "request-dict-subclass",
    "request-empty",
    "request-extra-field",
    "request-other-field",
    "request-key-lone-surrogate",
    "request-key-checkpoint-str-subclass",
    "request-extra-key-str-subclass",
    "request-key-checkpoint-colliding-hash",
    "request-extra-key-colliding-hash",
    "request-checkpoint-none",
    "request-checkpoint-true",
    "request-checkpoint-false",
    "request-checkpoint-text",
    "request-checkpoint-float",
    "request-checkpoint-nan",
    "request-checkpoint-int-subclass",
    "request-checkpoint-str-subclass",
    "request-checkpoint-list",
    "request-checkpoint-negative",
    "request-checkpoint-huge-negative",
    "request-checkpoint-past-log-end",
    "request-checkpoint-huge",
    "request-checkpoint-past-valid-prefix",
    "request-checkpoint-0",
    "request-checkpoint-1",
    "log-none",
    "log-dict",
    "log-text",
    "log-zero",
    "log-tuple",
    "log-bytes",
    "log-list-subclass",
    "log-self-referential",
    "log-contains-log-copy",
    "log-deep",
    "log-huge-int",
    "log-clean-at-checkpoint",
    "log-clean-checkpoint-zero",
    "log-empty",
    "log-empty-checkpoint-one",
    "log-two-torn-entries",
    "log-hostile-after-torn",
    "entry-float-nan-first",
    "durable-entry-float-nan-first",
    "entry-float-inf-first",
    "durable-entry-float-inf-first",
    "entry-float-negative-inf-first",
    "durable-entry-float-negative-inf-first",
    "entry-bytes-first",
    "durable-entry-bytes-first",
    "entry-tuple-first",
    "durable-entry-tuple-first",
    "entry-set-first",
    "durable-entry-set-first",
    "entry-int-subclass-first",
    "durable-entry-int-subclass-first",
    "entry-str-subclass-first",
    "durable-entry-str-subclass-first",
    "entry-lone-surrogate-first",
    "durable-entry-lone-surrogate-first",
    "entry-int-past-digit-bound-first",
    "durable-entry-int-past-digit-bound-first",
    "entry-negative-int-past-digit-bound-first",
    "durable-entry-negative-int-past-digit-bound-first",
    "entry-dict-subclass-first",
    "durable-entry-dict-subclass-first",
    "entry-list-subclass-first",
    "durable-entry-list-subclass-first",
    "entry-key-int-first",
    "durable-entry-key-int-first",
    "entry-key-lone-surrogate-first",
    "durable-entry-key-lone-surrogate-first",
    "entry-payload-dict-subclass-first",
    "durable-entry-payload-dict-subclass-first",
    "entry-self-referential-first",
    "durable-entry-self-referential-first",
    "entry-intra-alias-first",
    "durable-entry-intra-alias-first",
    "entry-over-depth-first",
    "durable-entry-over-depth-first",
    "entry-key-sequence-str-subclass-first",
    "durable-entry-key-sequence-str-subclass-first",
    "entry-key-sequence-colliding-hash-first",
    "durable-entry-key-sequence-colliding-hash-first",
    "entry-key-op-str-subclass-first",
    "durable-entry-key-op-str-subclass-first",
    "entry-key-op-colliding-hash-first",
    "durable-entry-key-op-colliding-hash-first",
    "entry-key-entry_id-str-subclass-first",
    "durable-entry-key-entry_id-str-subclass-first",
    "entry-key-entry_id-colliding-hash-first",
    "durable-entry-key-entry_id-colliding-hash-first",
    "entry-key-prior_entry_id-str-subclass-first",
    "durable-entry-key-prior_entry_id-str-subclass-first",
    "entry-key-prior_entry_id-colliding-hash-first",
    "durable-entry-key-prior_entry_id-colliding-hash-first",
    "entry-key-payload-str-subclass-first",
    "durable-entry-key-payload-str-subclass-first",
    "entry-key-payload-colliding-hash-first",
    "durable-entry-key-payload-colliding-hash-first",
    "entry-payload-key-record-str-subclass-first",
    "durable-entry-payload-key-record-str-subclass-first",
    "entry-record-key-digest-str-subclass-first",
    "durable-entry-record-key-digest-str-subclass-first",
    "entry-payload-key-record-colliding-hash-first",
    "durable-entry-payload-key-record-colliding-hash-first",
    "entry-record-key-digest-colliding-hash-first",
    "durable-entry-record-key-digest-colliding-hash-first",
    "entry-sequence-float-nan-first",
    "durable-entry-sequence-float-nan-first",
    "entry-record-digest-float-nan-first",
    "durable-entry-record-digest-float-nan-first",
    "entry-sequence-float-inf-first",
    "durable-entry-sequence-float-inf-first",
    "entry-record-digest-float-inf-first",
    "durable-entry-record-digest-float-inf-first",
    "entry-sequence-float-negative-inf-first",
    "durable-entry-sequence-float-negative-inf-first",
    "entry-record-digest-float-negative-inf-first",
    "durable-entry-record-digest-float-negative-inf-first",
    "entry-sequence-bytes-first",
    "durable-entry-sequence-bytes-first",
    "entry-record-digest-bytes-first",
    "durable-entry-record-digest-bytes-first",
    "entry-sequence-tuple-first",
    "durable-entry-sequence-tuple-first",
    "entry-record-digest-tuple-first",
    "durable-entry-record-digest-tuple-first",
    "entry-sequence-set-first",
    "durable-entry-sequence-set-first",
    "entry-record-digest-set-first",
    "durable-entry-record-digest-set-first",
    "entry-sequence-int-subclass-first",
    "durable-entry-sequence-int-subclass-first",
    "entry-record-digest-int-subclass-first",
    "durable-entry-record-digest-int-subclass-first",
    "entry-sequence-str-subclass-first",
    "durable-entry-sequence-str-subclass-first",
    "entry-record-digest-str-subclass-first",
    "durable-entry-record-digest-str-subclass-first",
    "entry-sequence-lone-surrogate-first",
    "durable-entry-sequence-lone-surrogate-first",
    "entry-record-digest-lone-surrogate-first",
    "durable-entry-record-digest-lone-surrogate-first",
    "entry-sequence-int-past-digit-bound-first",
    "durable-entry-sequence-int-past-digit-bound-first",
    "entry-record-digest-int-past-digit-bound-first",
    "durable-entry-record-digest-int-past-digit-bound-first",
    "entry-sequence-negative-int-past-digit-bound-first",
    "durable-entry-sequence-negative-int-past-digit-bound-first",
    "entry-record-digest-negative-int-past-digit-bound-first",
    "durable-entry-record-digest-negative-int-past-digit-bound-first",
    "wal-forged-entry-id-first",
    "durable-wal-forged-entry-id-first",
    "wal-sequence-shifted-first",
    "durable-wal-sequence-shifted-first",
    "wal-prior-link-broken-first",
    "durable-wal-prior-link-broken-first",
    "wal-op-changed-first",
    "durable-wal-op-changed-first",
    "wal-digest-altered-first",
    "durable-wal-digest-altered-first",
    "wal-missing-field-first",
    "durable-wal-missing-field-first",
    "wal-extra-field-first",
    "durable-wal-extra-field-first",
    "wal-garbage-text-first",
    "durable-wal-garbage-text-first",
    "wal-garbage-int-first",
    "durable-wal-garbage-int-first",
    "wal-garbage-list-first",
    "durable-wal-garbage-list-first",
    "wal-garbage-empty-dict-first",
    "durable-wal-garbage-empty-dict-first",
    "wal-equal-distinct-containers-first",
    "durable-wal-equal-distinct-containers-first",
    "wal-depth-at-limit-first",
    "durable-wal-depth-at-limit-first",
    "wal-entry-true-first",
    "durable-wal-entry-true-first",
    "wal-sequence-true-first",
    "durable-wal-sequence-true-first",
    "wal-digest-true-first",
    "durable-wal-digest-true-first",
    "wal-entry-false-first",
    "durable-wal-entry-false-first",
    "wal-sequence-false-first",
    "durable-wal-sequence-false-first",
    "wal-digest-false-first",
    "durable-wal-digest-false-first",
    "wal-entry-none-first",
    "durable-wal-entry-none-first",
    "wal-sequence-none-first",
    "durable-wal-sequence-none-first",
    "wal-digest-none-first",
    "durable-wal-digest-none-first",
    "wal-entry-empty-str-first",
    "durable-wal-entry-empty-str-first",
    "wal-sequence-empty-str-first",
    "durable-wal-sequence-empty-str-first",
    "wal-digest-empty-str-first",
    "durable-wal-digest-empty-str-first",
    "wal-entry-utf8-2-byte-first",
    "durable-wal-entry-utf8-2-byte-first",
    "wal-sequence-utf8-2-byte-first",
    "durable-wal-sequence-utf8-2-byte-first",
    "wal-digest-utf8-2-byte-first",
    "durable-wal-digest-utf8-2-byte-first",
    "wal-entry-utf8-3-byte-first",
    "durable-wal-entry-utf8-3-byte-first",
    "wal-sequence-utf8-3-byte-first",
    "durable-wal-sequence-utf8-3-byte-first",
    "wal-digest-utf8-3-byte-first",
    "durable-wal-digest-utf8-3-byte-first",
    "wal-entry-utf8-4-byte-first",
    "durable-wal-entry-utf8-4-byte-first",
    "wal-sequence-utf8-4-byte-first",
    "durable-wal-sequence-utf8-4-byte-first",
    "wal-digest-utf8-4-byte-first",
    "durable-wal-digest-utf8-4-byte-first",
    "wal-entry-finite-float-first",
    "durable-wal-entry-finite-float-first",
    "wal-sequence-finite-float-first",
    "durable-wal-sequence-finite-float-first",
    "wal-digest-finite-float-first",
    "durable-wal-digest-finite-float-first",
    "wal-entry-negative-zero-float-first",
    "durable-wal-entry-negative-zero-float-first",
    "wal-sequence-negative-zero-float-first",
    "durable-wal-sequence-negative-zero-float-first",
    "wal-digest-negative-zero-float-first",
    "durable-wal-digest-negative-zero-float-first",
    "wal-entry-max-float-first",
    "durable-wal-entry-max-float-first",
    "wal-sequence-max-float-first",
    "durable-wal-sequence-max-float-first",
    "wal-digest-max-float-first",
    "durable-wal-digest-max-float-first",
    "wal-entry-int-at-digit-limit-first",
    "durable-wal-entry-int-at-digit-limit-first",
    "wal-sequence-int-at-digit-limit-first",
    "durable-wal-sequence-int-at-digit-limit-first",
    "wal-digest-int-at-digit-limit-first",
    "durable-wal-digest-int-at-digit-limit-first",
    "wal-entry-negative-int-at-digit-limit-first",
    "durable-wal-entry-negative-int-at-digit-limit-first",
    "wal-sequence-negative-int-at-digit-limit-first",
    "durable-wal-sequence-negative-int-at-digit-limit-first",
    "wal-digest-negative-int-at-digit-limit-first",
    "durable-wal-digest-negative-int-at-digit-limit-first",
    "entry-float-nan-middle",
    "durable-entry-float-nan-middle",
    "entry-float-inf-middle",
    "durable-entry-float-inf-middle",
    "entry-float-negative-inf-middle",
    "durable-entry-float-negative-inf-middle",
    "entry-bytes-middle",
    "durable-entry-bytes-middle",
    "entry-tuple-middle",
    "durable-entry-tuple-middle",
    "entry-set-middle",
    "durable-entry-set-middle",
    "entry-int-subclass-middle",
    "durable-entry-int-subclass-middle",
    "entry-str-subclass-middle",
    "durable-entry-str-subclass-middle",
    "entry-lone-surrogate-middle",
    "durable-entry-lone-surrogate-middle",
    "entry-int-past-digit-bound-middle",
    "durable-entry-int-past-digit-bound-middle",
    "entry-negative-int-past-digit-bound-middle",
    "durable-entry-negative-int-past-digit-bound-middle",
    "entry-dict-subclass-middle",
    "durable-entry-dict-subclass-middle",
    "entry-list-subclass-middle",
    "durable-entry-list-subclass-middle",
    "entry-key-int-middle",
    "durable-entry-key-int-middle",
    "entry-key-lone-surrogate-middle",
    "durable-entry-key-lone-surrogate-middle",
    "entry-payload-dict-subclass-middle",
    "durable-entry-payload-dict-subclass-middle",
    "entry-self-referential-middle",
    "durable-entry-self-referential-middle",
    "entry-intra-alias-middle",
    "durable-entry-intra-alias-middle",
    "entry-over-depth-middle",
    "durable-entry-over-depth-middle",
    "entry-key-sequence-str-subclass-middle",
    "durable-entry-key-sequence-str-subclass-middle",
    "entry-key-sequence-colliding-hash-middle",
    "durable-entry-key-sequence-colliding-hash-middle",
    "entry-key-op-str-subclass-middle",
    "durable-entry-key-op-str-subclass-middle",
    "entry-key-op-colliding-hash-middle",
    "durable-entry-key-op-colliding-hash-middle",
    "entry-key-entry_id-str-subclass-middle",
    "durable-entry-key-entry_id-str-subclass-middle",
    "entry-key-entry_id-colliding-hash-middle",
    "durable-entry-key-entry_id-colliding-hash-middle",
    "entry-key-prior_entry_id-str-subclass-middle",
    "durable-entry-key-prior_entry_id-str-subclass-middle",
    "entry-key-prior_entry_id-colliding-hash-middle",
    "durable-entry-key-prior_entry_id-colliding-hash-middle",
    "entry-key-payload-str-subclass-middle",
    "durable-entry-key-payload-str-subclass-middle",
    "entry-key-payload-colliding-hash-middle",
    "durable-entry-key-payload-colliding-hash-middle",
    "entry-payload-key-record-str-subclass-middle",
    "durable-entry-payload-key-record-str-subclass-middle",
    "entry-record-key-digest-str-subclass-middle",
    "durable-entry-record-key-digest-str-subclass-middle",
    "entry-payload-key-record-colliding-hash-middle",
    "durable-entry-payload-key-record-colliding-hash-middle",
    "entry-record-key-digest-colliding-hash-middle",
    "durable-entry-record-key-digest-colliding-hash-middle",
    "entry-sequence-float-nan-middle",
    "durable-entry-sequence-float-nan-middle",
    "entry-record-digest-float-nan-middle",
    "durable-entry-record-digest-float-nan-middle",
    "entry-sequence-float-inf-middle",
    "durable-entry-sequence-float-inf-middle",
    "entry-record-digest-float-inf-middle",
    "durable-entry-record-digest-float-inf-middle",
    "entry-sequence-float-negative-inf-middle",
    "durable-entry-sequence-float-negative-inf-middle",
    "entry-record-digest-float-negative-inf-middle",
    "durable-entry-record-digest-float-negative-inf-middle",
    "entry-sequence-bytes-middle",
    "durable-entry-sequence-bytes-middle",
    "entry-record-digest-bytes-middle",
    "durable-entry-record-digest-bytes-middle",
    "entry-sequence-tuple-middle",
    "durable-entry-sequence-tuple-middle",
    "entry-record-digest-tuple-middle",
    "durable-entry-record-digest-tuple-middle",
    "entry-sequence-set-middle",
    "durable-entry-sequence-set-middle",
    "entry-record-digest-set-middle",
    "durable-entry-record-digest-set-middle",
    "entry-sequence-int-subclass-middle",
    "durable-entry-sequence-int-subclass-middle",
    "entry-record-digest-int-subclass-middle",
    "durable-entry-record-digest-int-subclass-middle",
    "entry-sequence-str-subclass-middle",
    "durable-entry-sequence-str-subclass-middle",
    "entry-record-digest-str-subclass-middle",
    "durable-entry-record-digest-str-subclass-middle",
    "entry-sequence-lone-surrogate-middle",
    "durable-entry-sequence-lone-surrogate-middle",
    "entry-record-digest-lone-surrogate-middle",
    "durable-entry-record-digest-lone-surrogate-middle",
    "entry-sequence-int-past-digit-bound-middle",
    "durable-entry-sequence-int-past-digit-bound-middle",
    "entry-record-digest-int-past-digit-bound-middle",
    "durable-entry-record-digest-int-past-digit-bound-middle",
    "entry-sequence-negative-int-past-digit-bound-middle",
    "durable-entry-sequence-negative-int-past-digit-bound-middle",
    "entry-record-digest-negative-int-past-digit-bound-middle",
    "durable-entry-record-digest-negative-int-past-digit-bound-middle",
    "wal-forged-entry-id-middle",
    "durable-wal-forged-entry-id-middle",
    "wal-sequence-shifted-middle",
    "durable-wal-sequence-shifted-middle",
    "wal-prior-link-broken-middle",
    "durable-wal-prior-link-broken-middle",
    "wal-op-changed-middle",
    "durable-wal-op-changed-middle",
    "wal-digest-altered-middle",
    "durable-wal-digest-altered-middle",
    "wal-missing-field-middle",
    "durable-wal-missing-field-middle",
    "wal-extra-field-middle",
    "durable-wal-extra-field-middle",
    "wal-garbage-text-middle",
    "durable-wal-garbage-text-middle",
    "wal-garbage-int-middle",
    "durable-wal-garbage-int-middle",
    "wal-garbage-list-middle",
    "durable-wal-garbage-list-middle",
    "wal-garbage-empty-dict-middle",
    "durable-wal-garbage-empty-dict-middle",
    "wal-equal-distinct-containers-middle",
    "durable-wal-equal-distinct-containers-middle",
    "wal-depth-at-limit-middle",
    "durable-wal-depth-at-limit-middle",
    "wal-entry-true-middle",
    "durable-wal-entry-true-middle",
    "wal-sequence-true-middle",
    "durable-wal-sequence-true-middle",
    "wal-digest-true-middle",
    "durable-wal-digest-true-middle",
    "wal-entry-false-middle",
    "durable-wal-entry-false-middle",
    "wal-sequence-false-middle",
    "durable-wal-sequence-false-middle",
    "wal-digest-false-middle",
    "durable-wal-digest-false-middle",
    "wal-entry-none-middle",
    "durable-wal-entry-none-middle",
    "wal-sequence-none-middle",
    "durable-wal-sequence-none-middle",
    "wal-digest-none-middle",
    "durable-wal-digest-none-middle",
    "wal-entry-empty-str-middle",
    "durable-wal-entry-empty-str-middle",
    "wal-sequence-empty-str-middle",
    "durable-wal-sequence-empty-str-middle",
    "wal-digest-empty-str-middle",
    "durable-wal-digest-empty-str-middle",
    "wal-entry-utf8-2-byte-middle",
    "durable-wal-entry-utf8-2-byte-middle",
    "wal-sequence-utf8-2-byte-middle",
    "durable-wal-sequence-utf8-2-byte-middle",
    "wal-digest-utf8-2-byte-middle",
    "durable-wal-digest-utf8-2-byte-middle",
    "wal-entry-utf8-3-byte-middle",
    "durable-wal-entry-utf8-3-byte-middle",
    "wal-sequence-utf8-3-byte-middle",
    "durable-wal-sequence-utf8-3-byte-middle",
    "wal-digest-utf8-3-byte-middle",
    "durable-wal-digest-utf8-3-byte-middle",
    "wal-entry-utf8-4-byte-middle",
    "durable-wal-entry-utf8-4-byte-middle",
    "wal-sequence-utf8-4-byte-middle",
    "durable-wal-sequence-utf8-4-byte-middle",
    "wal-digest-utf8-4-byte-middle",
    "durable-wal-digest-utf8-4-byte-middle",
    "wal-entry-finite-float-middle",
    "durable-wal-entry-finite-float-middle",
    "wal-sequence-finite-float-middle",
    "durable-wal-sequence-finite-float-middle",
    "wal-digest-finite-float-middle",
    "durable-wal-digest-finite-float-middle",
    "wal-entry-negative-zero-float-middle",
    "durable-wal-entry-negative-zero-float-middle",
    "wal-sequence-negative-zero-float-middle",
    "durable-wal-sequence-negative-zero-float-middle",
    "wal-digest-negative-zero-float-middle",
    "durable-wal-digest-negative-zero-float-middle",
    "wal-entry-max-float-middle",
    "durable-wal-entry-max-float-middle",
    "wal-sequence-max-float-middle",
    "durable-wal-sequence-max-float-middle",
    "wal-digest-max-float-middle",
    "durable-wal-digest-max-float-middle",
    "wal-entry-int-at-digit-limit-middle",
    "durable-wal-entry-int-at-digit-limit-middle",
    "wal-sequence-int-at-digit-limit-middle",
    "durable-wal-sequence-int-at-digit-limit-middle",
    "wal-digest-int-at-digit-limit-middle",
    "durable-wal-digest-int-at-digit-limit-middle",
    "wal-entry-negative-int-at-digit-limit-middle",
    "durable-wal-entry-negative-int-at-digit-limit-middle",
    "wal-sequence-negative-int-at-digit-limit-middle",
    "durable-wal-sequence-negative-int-at-digit-limit-middle",
    "wal-digest-negative-int-at-digit-limit-middle",
    "durable-wal-digest-negative-int-at-digit-limit-middle",
    "entry-float-nan-last",
    "durable-entry-float-nan-last",
    "entry-float-inf-last",
    "durable-entry-float-inf-last",
    "entry-float-negative-inf-last",
    "durable-entry-float-negative-inf-last",
    "entry-bytes-last",
    "durable-entry-bytes-last",
    "entry-tuple-last",
    "durable-entry-tuple-last",
    "entry-set-last",
    "durable-entry-set-last",
    "entry-int-subclass-last",
    "durable-entry-int-subclass-last",
    "entry-str-subclass-last",
    "durable-entry-str-subclass-last",
    "entry-lone-surrogate-last",
    "durable-entry-lone-surrogate-last",
    "entry-int-past-digit-bound-last",
    "durable-entry-int-past-digit-bound-last",
    "entry-negative-int-past-digit-bound-last",
    "durable-entry-negative-int-past-digit-bound-last",
    "entry-dict-subclass-last",
    "durable-entry-dict-subclass-last",
    "entry-list-subclass-last",
    "durable-entry-list-subclass-last",
    "entry-key-int-last",
    "durable-entry-key-int-last",
    "entry-key-lone-surrogate-last",
    "durable-entry-key-lone-surrogate-last",
    "entry-payload-dict-subclass-last",
    "durable-entry-payload-dict-subclass-last",
    "entry-self-referential-last",
    "durable-entry-self-referential-last",
    "entry-intra-alias-last",
    "durable-entry-intra-alias-last",
    "entry-over-depth-last",
    "durable-entry-over-depth-last",
    "entry-key-sequence-str-subclass-last",
    "durable-entry-key-sequence-str-subclass-last",
    "entry-key-sequence-colliding-hash-last",
    "durable-entry-key-sequence-colliding-hash-last",
    "entry-key-op-str-subclass-last",
    "durable-entry-key-op-str-subclass-last",
    "entry-key-op-colliding-hash-last",
    "durable-entry-key-op-colliding-hash-last",
    "entry-key-entry_id-str-subclass-last",
    "durable-entry-key-entry_id-str-subclass-last",
    "entry-key-entry_id-colliding-hash-last",
    "durable-entry-key-entry_id-colliding-hash-last",
    "entry-key-prior_entry_id-str-subclass-last",
    "durable-entry-key-prior_entry_id-str-subclass-last",
    "entry-key-prior_entry_id-colliding-hash-last",
    "durable-entry-key-prior_entry_id-colliding-hash-last",
    "entry-key-payload-str-subclass-last",
    "durable-entry-key-payload-str-subclass-last",
    "entry-key-payload-colliding-hash-last",
    "durable-entry-key-payload-colliding-hash-last",
    "entry-payload-key-record-str-subclass-last",
    "durable-entry-payload-key-record-str-subclass-last",
    "entry-record-key-digest-str-subclass-last",
    "durable-entry-record-key-digest-str-subclass-last",
    "entry-payload-key-record-colliding-hash-last",
    "durable-entry-payload-key-record-colliding-hash-last",
    "entry-record-key-digest-colliding-hash-last",
    "durable-entry-record-key-digest-colliding-hash-last",
    "entry-sequence-float-nan-last",
    "durable-entry-sequence-float-nan-last",
    "entry-record-digest-float-nan-last",
    "durable-entry-record-digest-float-nan-last",
    "entry-sequence-float-inf-last",
    "durable-entry-sequence-float-inf-last",
    "entry-record-digest-float-inf-last",
    "durable-entry-record-digest-float-inf-last",
    "entry-sequence-float-negative-inf-last",
    "durable-entry-sequence-float-negative-inf-last",
    "entry-record-digest-float-negative-inf-last",
    "durable-entry-record-digest-float-negative-inf-last",
    "entry-sequence-bytes-last",
    "durable-entry-sequence-bytes-last",
    "entry-record-digest-bytes-last",
    "durable-entry-record-digest-bytes-last",
    "entry-sequence-tuple-last",
    "durable-entry-sequence-tuple-last",
    "entry-record-digest-tuple-last",
    "durable-entry-record-digest-tuple-last",
    "entry-sequence-set-last",
    "durable-entry-sequence-set-last",
    "entry-record-digest-set-last",
    "durable-entry-record-digest-set-last",
    "entry-sequence-int-subclass-last",
    "durable-entry-sequence-int-subclass-last",
    "entry-record-digest-int-subclass-last",
    "durable-entry-record-digest-int-subclass-last",
    "entry-sequence-str-subclass-last",
    "durable-entry-sequence-str-subclass-last",
    "entry-record-digest-str-subclass-last",
    "durable-entry-record-digest-str-subclass-last",
    "entry-sequence-lone-surrogate-last",
    "durable-entry-sequence-lone-surrogate-last",
    "entry-record-digest-lone-surrogate-last",
    "durable-entry-record-digest-lone-surrogate-last",
    "entry-sequence-int-past-digit-bound-last",
    "durable-entry-sequence-int-past-digit-bound-last",
    "entry-record-digest-int-past-digit-bound-last",
    "durable-entry-record-digest-int-past-digit-bound-last",
    "entry-sequence-negative-int-past-digit-bound-last",
    "durable-entry-sequence-negative-int-past-digit-bound-last",
    "entry-record-digest-negative-int-past-digit-bound-last",
    "durable-entry-record-digest-negative-int-past-digit-bound-last",
    "wal-forged-entry-id-last",
    "durable-wal-forged-entry-id-last",
    "wal-sequence-shifted-last",
    "durable-wal-sequence-shifted-last",
    "wal-prior-link-broken-last",
    "durable-wal-prior-link-broken-last",
    "wal-op-changed-last",
    "durable-wal-op-changed-last",
    "wal-digest-altered-last",
    "durable-wal-digest-altered-last",
    "wal-missing-field-last",
    "durable-wal-missing-field-last",
    "wal-extra-field-last",
    "durable-wal-extra-field-last",
    "wal-garbage-text-last",
    "durable-wal-garbage-text-last",
    "wal-garbage-int-last",
    "durable-wal-garbage-int-last",
    "wal-garbage-list-last",
    "durable-wal-garbage-list-last",
    "wal-garbage-empty-dict-last",
    "durable-wal-garbage-empty-dict-last",
    "wal-equal-distinct-containers-last",
    "durable-wal-equal-distinct-containers-last",
    "wal-depth-at-limit-last",
    "durable-wal-depth-at-limit-last",
    "wal-entry-true-last",
    "durable-wal-entry-true-last",
    "wal-sequence-true-last",
    "durable-wal-sequence-true-last",
    "wal-digest-true-last",
    "durable-wal-digest-true-last",
    "wal-entry-false-last",
    "durable-wal-entry-false-last",
    "wal-sequence-false-last",
    "durable-wal-sequence-false-last",
    "wal-digest-false-last",
    "durable-wal-digest-false-last",
    "wal-entry-none-last",
    "durable-wal-entry-none-last",
    "wal-sequence-none-last",
    "durable-wal-sequence-none-last",
    "wal-digest-none-last",
    "durable-wal-digest-none-last",
    "wal-entry-empty-str-last",
    "durable-wal-entry-empty-str-last",
    "wal-sequence-empty-str-last",
    "durable-wal-sequence-empty-str-last",
    "wal-digest-empty-str-last",
    "durable-wal-digest-empty-str-last",
    "wal-entry-utf8-2-byte-last",
    "durable-wal-entry-utf8-2-byte-last",
    "wal-sequence-utf8-2-byte-last",
    "durable-wal-sequence-utf8-2-byte-last",
    "wal-digest-utf8-2-byte-last",
    "durable-wal-digest-utf8-2-byte-last",
    "wal-entry-utf8-3-byte-last",
    "durable-wal-entry-utf8-3-byte-last",
    "wal-sequence-utf8-3-byte-last",
    "durable-wal-sequence-utf8-3-byte-last",
    "wal-digest-utf8-3-byte-last",
    "durable-wal-digest-utf8-3-byte-last",
    "wal-entry-utf8-4-byte-last",
    "durable-wal-entry-utf8-4-byte-last",
    "wal-sequence-utf8-4-byte-last",
    "durable-wal-sequence-utf8-4-byte-last",
    "wal-digest-utf8-4-byte-last",
    "durable-wal-digest-utf8-4-byte-last",
    "wal-entry-finite-float-last",
    "durable-wal-entry-finite-float-last",
    "wal-sequence-finite-float-last",
    "durable-wal-sequence-finite-float-last",
    "wal-digest-finite-float-last",
    "durable-wal-digest-finite-float-last",
    "wal-entry-negative-zero-float-last",
    "durable-wal-entry-negative-zero-float-last",
    "wal-sequence-negative-zero-float-last",
    "durable-wal-sequence-negative-zero-float-last",
    "wal-digest-negative-zero-float-last",
    "durable-wal-digest-negative-zero-float-last",
    "wal-entry-max-float-last",
    "durable-wal-entry-max-float-last",
    "wal-sequence-max-float-last",
    "durable-wal-sequence-max-float-last",
    "wal-digest-max-float-last",
    "durable-wal-digest-max-float-last",
    "wal-entry-int-at-digit-limit-last",
    "durable-wal-entry-int-at-digit-limit-last",
    "wal-sequence-int-at-digit-limit-last",
    "durable-wal-sequence-int-at-digit-limit-last",
    "wal-digest-int-at-digit-limit-last",
    "durable-wal-digest-int-at-digit-limit-last",
    "wal-entry-negative-int-at-digit-limit-last",
    "durable-wal-entry-negative-int-at-digit-limit-last",
    "wal-sequence-negative-int-at-digit-limit-last",
    "durable-wal-sequence-negative-int-at-digit-limit-last",
    "wal-digest-negative-int-at-digit-limit-last",
    "durable-wal-digest-negative-int-at-digit-limit-last",
    "tail-same-entry-twice-first",
    "tail-same-entry-twice-middle",
    "tail-same-entry-twice-last",
    "tail-entries-share-dict-first",
    "tail-entries-share-dict-middle",
    "tail-entries-share-dict-last",
    "tail-entries-share-list-first",
    "tail-entries-share-list-middle",
    "tail-entries-share-list-last",
    "tail-shares-durable-record-first",
    "tail-shares-durable-record-middle",
    "tail-shares-durable-record-last",
    "tail-repeats-durable-entry-first",
    "tail-repeats-durable-entry-middle",
    "tail-repeats-durable-entry-last",
    "sink-raises-exception",
    "sink-raises-exception-on-clean-log",
    "sink-raises-keyboard-interrupt",
    "sink-raises-keyboard-interrupt-on-clean-log",
    "sink-raises-system-exit",
    "sink-raises-system-exit-on-clean-log",
    "sink-raises-generator-exit",
    "sink-raises-generator-exit-on-clean-log",
    "sink-raises-on-corrupt-source",
    "sink-raises-on-malformed-request",
    "sink-raises-on-unknown-checkpoint",
    "sink-raises-on-malformed-tail",
    "sink-returns-none",
    "sink-returns-zero",
    "sink-returns-list",
    "sink-returns-bytes",
    "sink-returns-str-subclass",
    "sink-returns-uppercase",
    "sink-returns-trailing-newline",
    "sink-returns-wrong-scheme",
    "sink-returns-bad-grammar",
    "sink-returns-unbound-token",
    "sink-returns-full-log-token",
    "sink-returns-empty-tail-token",
    "sink-returns-prefix-token",
    "sink-returns-unframed-token",
    "sink-returns-lone-surrogate",
    "sink-returns-surrogate-in-token",
    "sink-returns-tail-token-on-clean-log",
    "sink-mutates-then-raises",
    "sink-mutates-first-entry",
    "sink-mutates-last-entry",
    "sink-mutates-first-record",
    "sink-appends-entry",
    "sink-pops-entry",
    "sink-clears-log",
    "sink-replaces-first-entry",
    "sink-reorders-entry-keys",
    "sink-adds-entry-key",
    "sink-adds-payload-key",
    "sink-adds-record-key",
    "sink-mutates-checkpoint",
    "sink-clears-request",
    "sink-adds-request-key",
    "sink-own-argument-appends",
    "sink-own-argument-clears",
    "sink-own-argument-entry",
    "sink-own-argument-adds-entry-key",
    "sink-own-argument-payload",
    "sink-own-argument-record",
    "sink-tail-sets-nested-dict-value-first",
    "sink-tail-sets-nested-dict-value-middle",
    "sink-tail-sets-nested-dict-value-last",
    "sink-tail-adds-nested-dict-key-first",
    "sink-tail-adds-nested-dict-key-middle",
    "sink-tail-adds-nested-dict-key-last",
    "sink-tail-clears-nested-dict-first",
    "sink-tail-clears-nested-dict-middle",
    "sink-tail-clears-nested-dict-last",
    "sink-tail-appends-nested-list-first",
    "sink-tail-appends-nested-list-middle",
    "sink-tail-appends-nested-list-last",
    "sink-tail-pops-nested-list-first",
    "sink-tail-pops-nested-list-middle",
    "sink-tail-pops-nested-list-last",
    "sink-tail-appends-doubly-nested-list-first",
    "sink-tail-appends-doubly-nested-list-middle",
    "sink-tail-appends-doubly-nested-list-last",
    "sink-tail-adds-entry-key-first",
    "sink-tail-adds-entry-key-middle",
    "sink-tail-adds-entry-key-last",
    "sink-tail-appends-list-entry-first",
    "sink-tail-appends-list-entry-middle",
    "sink-tail-appends-list-entry-last",
    "sink-tail-pops-list-entry-first",
    "sink-tail-pops-list-entry-middle",
    "sink-tail-pops-list-entry-last",
    "sink-tail-clears-list-entry-first",
    "sink-tail-clears-list-entry-middle",
    "sink-tail-clears-list-entry-last",
    "sink-tail-appends-nested-list-in-list-entry-first",
    "sink-tail-appends-nested-list-in-list-entry-middle",
    "sink-tail-appends-nested-list-in-list-entry-last",
    "sink-tail-sets-dict-in-list-entry-first",
    "sink-tail-sets-dict-in-list-entry-middle",
    "sink-tail-sets-dict-in-list-entry-last",
    "sink-tail-adds-key-to-dict-in-list-entry-first",
    "sink-tail-adds-key-to-dict-in-list-entry-middle",
    "sink-tail-adds-key-to-dict-in-list-entry-last",
    "sink-mutates-tail-record-digest",
    "sink-adds-tail-record-key",
    "sink-adds-tail-payload-key",
    "sink-replaces-tail-payload",
    "sink-mutates-last-entry-on-clean-log",
    "sink-clears-log-on-clean-log",
    "sink-adds-entry-key-on-clean-log",
    "sink-adds-record-key-on-clean-log",
    "sink-adds-request-key-on-clean-log",
    "sink-own-argument-appends-on-clean-log",
)  # GENERATED
PROBE_COUNT = 785  # GENERATED


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    log, request, sink = build()
    engine, counter = _engine(cls, sink)
    if failure == ACCEPT:
        # SAME INSTANCE: accepts under attack, then still rejects a
        # bad request typed and still accepts a clean salvage (a
        # clean resume after the clean-log attacks)
        try:
            _, ok = _checked_resume(engine, counter, log, request,
                                    PROBE_EXPECT[name])
            bad_log, _ = _salvage_inputs()
            ok = ok and _rejects(engine, counter, bad_log,
                                 {"checkpoint_sequence": True}, MRR)
            follow, expect = (_clean_inputs, CLEAN_EXPECT) \
                if name.endswith("-on-clean-log") \
                else (_salvage_inputs, SALVAGE_EXPECT)
            _, again = _checked_resume(engine, counter, *follow(),
                                       expect)
        except BaseException:  # noqa: BLE001 - any rejection fails
            return False
        return ok and again
    try:
        return _rejects(engine, counter, log, request, failure)
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
def _fake_receipt():
    return dict(SALVAGE_EXPECT)


class AcceptsAll(ResumeEngine):
    def resume(self, log, request):
        try:
            return super().resume(log, request)
        except ResumeError:
            return _fake_receipt()


class WrongCode(ResumeEngine):
    def resume(self, log, request):
        try:
            return super().resume(log, request)
        except ResumeError as error:
            raise ResumeError(error.failure_class, "internal") from None


class SubclassError(ResumeEngine):
    """Raises a SUBCLASS of the bound error (inexact type)."""

    class _Sub(ResumeError):
        pass

    def resume(self, log, request):
        try:
            return super().resume(log, request)
        except ResumeError as error:
            raise SubclassError._Sub(error.failure_class,
                                     error.code) from None


def _remap(frm, to):
    class Remap(ResumeEngine):
        def resume(self, log, request):
            try:
                return super().resume(log, request)
            except ResumeError as error:
                if error.failure_class == frm:
                    raise ResumeError(to, FAILURE_MAPPING[to]) from None
                raise
    Remap.__name__ = f"Remap_{frm}_to_{to}"
    return Remap


class DoubleSinkCall(ResumeEngine):
    def __init__(self, sink):
        def twice(tail):
            sink(copy.deepcopy(tail))
            return sink(tail)
        super().__init__(twice)


class UnguardedSink(ResumeEngine):
    """Calls the sink once OUTSIDE the boundary, before
    validation."""

    def resume(self, log, request):
        if type(log) is list:
            try:
                peek = copy.deepcopy(log[-1:])
            except BaseException:  # noqa: BLE001
                peek = None
            if peek is not None:
                self.sink(peek)
        return super().resume(log, request)


class SkipsSinkWhenClean(ResumeEngine):
    """Never calls the sink for an empty tail (answers with the
    empty-tail token locally)."""

    def resume(self, log, request):
        real = self.sink
        if type(log) is list and type(request) is dict:
            try:
                clean = _local_prefix(log) == len(log)
            except BaseException:  # noqa: BLE001
                clean = False
            if clean:
                self.sink = quarantine_tail
        try:
            return super().resume(log, request)
        finally:
            self.sink = real


class NoCommit(ResumeEngine):
    def resume(self, log, request):
        saved = list(log) if type(log) is list else None
        receipt = super().resume(log, request)
        log[:] = saved
        return receipt


class OverTruncates(ResumeEngine):
    def resume(self, log, request):
        receipt = super().resume(log, request)
        if receipt["discarded_count"] and log:
            log.pop()
        return receipt


class RebuildsKeptEntries(ResumeEngine):
    def resume(self, log, request):
        receipt = super().resume(log, request)
        log[:] = copy.deepcopy(log)
        return receipt


class ReordersKeptEntryKeys(ResumeEngine):
    def resume(self, log, request):
        receipt = super().resume(log, request)
        for entry in log:
            if type(entry) is dict:
                items = list(entry.items())
                entry.clear()
                entry.update(reversed(items))
        return receipt


class MutatesRequestOnSuccess(ResumeEngine):
    def resume(self, log, request):
        receipt = super().resume(log, request)
        request["checkpoint_sequence"] = receipt["resumed_count"] + 1
        return receipt


class StaleResumeId(ResumeEngine):
    def resume(self, log, request):
        receipt = super().resume(log, request)
        receipt["resume_id"] = "rsm1:" + hashlib.sha256(
            receipt["head"].encode()).hexdigest()
        return receipt


class CachedResult(ResumeEngine):
    """Class-level cache: a repeated resume returns the SAME receipt
    object."""
    _cache = {}

    def resume(self, log, request):
        receipt = super().resume(log, request)
        return CachedResult._cache.setdefault(receipt["resume_id"],
                                              receipt)


class StrSubclassToken(ResumeEngine):
    """Returns the token as a str subclass (an inexact receipt
    field type)."""

    def resume(self, log, request):
        receipt = super().resume(log, request)
        receipt = dict(receipt)
        receipt["quarantine_token"] = type(
            "_Tok", (str,), {})(receipt["quarantine_token"])
        return receipt


class ReceiptFieldOrder(ResumeEngine):
    def resume(self, log, request):
        receipt = super().resume(log, request)
        return dict(reversed(list(receipt.items())))


class RawRequestPeek(ResumeEngine):
    """Reads the live request before request validation."""

    def resume(self, log, request):
        if isinstance(request, dict):
            request.get("checkpoint_sequence")
        return super().resume(log, request)


class RawEntryPeek(ResumeEngine):
    """Reads each live entry's op before admission."""

    def resume(self, log, request):
        if type(log) is list:
            for entry in log:
                if isinstance(entry, dict):
                    entry.get("op")
        return super().resume(log, request)


class RawEntryKeySet(ResumeEngine):
    def resume(self, log, request):
        if type(log) is list:
            for entry in log:
                if isinstance(entry, dict):
                    set(entry.keys()) == set()  # noqa: B015
        return super().resume(log, request)


_SOURCES = ("_scalar_ok", "_is_canonical_json", "_canon",
            "quarantine_tail", "_snapshot", "_restore",
            "_valid_prefix_length", "ResumeEngine")


def _source_mutant(name, edits, extra=None):
    """A one-guard edit of the reference resume: the reference's
    admission walker, canonical token, snapshot/restore, prefix
    search and engine sources are concatenated; each OLD must occur
    in them (first occurrence replaced). The exec namespace raises
    the BOUND error class; EXTRA names are applied AFTER exec and
    override any exec'd definition."""
    src = "\n\n".join(inspect.getsource(getattr(_reference, n))
                      for n in _SOURCES)
    for old, new in edits:
        if old not in src:
            raise AssertionError(f"{name}: edit site missing: {old!r}")
        src = src.replace(old, new, 1)
    namespace = dict(vars(_reference))
    # the mutant raises the BOUND error class, so a correct rejection
    # counts as one under any binding (reference or production)
    namespace["ResumeError"] = ResumeError

    def _bound_fail(cls):
        raise ResumeError(cls, FAILURE_MAPPING[cls])

    namespace["_fail"] = _bound_fail
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    namespace.update(extra or {})
    return namespace["ResumeEngine"]


def _no_clear_restore(acc):
    for obj, saved in acc:
        if type(obj) is dict:
            obj.update(saved)
        else:
            obj[:] = saved


def _alias_free_across(entries):
    """ONE seen set across ALL of ENTRIES: any container met twice
    (within or across entries) fails."""
    seen, stack = set(), list(entries)
    while stack:
        node = stack.pop()
        if type(node) in (list, dict):
            if id(node) in seen:
                return False
            seen.add(id(node))
            stack.extend(node.values() if type(node) is dict else node)
    return True


_TAIL_ADMIT = "if not all(_is_canonical_json(entry) for entry in tail):"
_I8, _I12 = " " * 8, " " * 12
_SM = (
    ("isinstance-request", [(
        "if type(request) is not dict:",
        "if not isinstance(request, dict):")]),
    ("no-request-key-guard", [(
        "if not all(type(key) is str for key in dict.keys(request)):",
        "if False:")]),
    ("request-extra-fields-allowed", [(
        "if set(request.keys()) != set(_REQUEST_FIELDS):",
        "if not set(_REQUEST_FIELDS) <= set(request.keys()):")]),
    ("isinstance-checkpoint", [(
        "if type(checkpoint) is not int:",
        "if not isinstance(checkpoint, int):")]),
    ("accepts-negative-checkpoint", [(
        "if checkpoint < 0:", "if checkpoint < -1:")]),
    ("rejects-zero-checkpoint", [(
        "if checkpoint < 0:", "if checkpoint < 1:")]),
    ("negative-checkpoint-as-malformed", [(
        "if checkpoint < 0:\n            _fail(\"unknown_checkpoint\")",
        "if checkpoint < 0:\n            _fail(\"malformed_resume_record\")")]),
    ("rejects-checkpoint-at-log-end", [(
        "if checkpoint > len(log):", "if checkpoint >= len(log):")]),
    ("no-durability-check", [("if k < checkpoint:", "if False:")]),
    ("durability-off-by-one", [(
        "if k < checkpoint:", "if k < checkpoint - 1:")]),
    ("rejects-checkpoint-at-prefix-end", [(
        "if k < checkpoint:", "if k <= checkpoint:")]),
    ("skips-tail-admission", [(
        "if not all(_is_canonical_json(entry) for entry in tail):",
        "if False:")]),
    ("prefix-search-skips-cap", [(
        "for k in range(cap, -1, -1):", "for k in range(cap - 1, -1, -1):")]),
    ("rejects-depth-at-limit", [(
        "if depth > MAX_DEPTH or", "if depth >= MAX_DEPTH or")]),
    ("accepts-over-depth", [(
        "if depth > MAX_DEPTH or", "if depth > MAX_DEPTH + 1 or")]),
    ("no-alias-check", [(
        "if depth > MAX_DEPTH or id(node) in seen:",
        "if depth > MAX_DEPTH:")]),
    ("isinstance-container", [(
        "if kind is list or kind is dict:",
        "if isinstance(node, (list, dict)):")]),
    ("no-key-leaf-check", [(
        "if type(key) is not str or not _scalar_ok(key):",
        "if type(key) is not str:")]),
    # the key guard is backed by the leaf check's exact-str test on
    # every key, so the key-guard mutant must relax both
    ("isinstance-str-key", [
        ("if type(key) is not str or not _scalar_ok(key):",
         "if not isinstance(key, str) or not _scalar_ok(key):"),
        ("if kind is str:", "if isinstance(obj, str):")]),
    ("rejects-bool-leaf", [(
        "if obj is None or kind is bool:", "if obj is None:")]),
    ("rejects-none-leaf", [(
        "if obj is None or kind is bool:", "if kind is bool:")]),
    ("isinstance-int-leaf", [(
        "if kind is int:", "if isinstance(obj, int):")]),
    ("rejects-int-at-digit-limit", [(
        "return len(str(abs(obj))) <= MAX_INT_DIGITS",
        "return len(str(abs(obj))) < MAX_INT_DIGITS")]),
    ("accepts-int-past-digit-bound", [(
        "return len(str(abs(obj))) <= MAX_INT_DIGITS",
        "return len(str(abs(obj))) <= MAX_INT_DIGITS + 1")]),
    ("sign-counted-as-digit", [(
        "return len(str(abs(obj))) <= MAX_INT_DIGITS",
        "return len(str(obj)) <= MAX_INT_DIGITS")]),
    ("accepts-non-finite-float", [(
        "if kind is float:\n        return math.isfinite(obj)",
        "if kind is float:\n        return True")]),
    ("rejects-float-leaf", [(
        "if kind is float:\n        return math.isfinite(obj)",
        "if kind is float:\n        return False")]),
    ("isinstance-str-leaf", [(
        "if kind is str:", "if isinstance(obj, str):")]),
    ("ascii-str-leaf", [(
        'obj.encode("utf-8")', 'obj.encode("ascii")')]),
    ("accepts-lone-surrogate", [(
        'obj.encode("utf-8")', 'obj.encode("utf-8", "surrogatepass")')]),
    ("canon-default-separators", [(
        'separators=(",", ":"), ensure_ascii=False)',
        'ensure_ascii=False)')]),
    ("canon-ascii-escapes", [(
        'separators=(",", ":"), ensure_ascii=False)',
        'separators=(",", ":"), ensure_ascii=True)')]),
    ("canon-unsorted-keys", [(
        "json.dumps(entry, sort_keys=True,", "json.dumps(entry, sort_keys=False,")]),
    ("token-unframed", [(
        'parts.append(f"{len(text)}:{text}")', "parts.append(text)")]),
    ("token-byte-length-framing", [(
        'parts.append(f"{len(text)}:{text}")',
        'parts.append(f"{len(text.encode())}:{text}")')]),
    ("narrow-sink-boundary", [(
        "except BaseException:", "except Exception:")]),
    ("isinstance-sink-output", [(
        "if type(out) is not str or", "if not isinstance(out, str) or")]),
    ("skips-token-binding", [(
        "if token != quarantine_tail(frozen_tail):", "if False:")]),
    ("shared-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_tail))", "self.sink(frozen_tail)")]),
    ("list-shallow-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_tail))",
        "self.sink(list(frozen_tail))")]),
    ("entry-shallow-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_tail))",
        "self.sink([dict(e) if type(e) is dict else e "
        "for e in frozen_tail])")]),
    ("record-shared-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_tail))",
        "self.sink([{**e, 'payload': dict(e['payload'])} "
        "if type(e) is dict and type(e.get('payload')) is dict "
        "else copy.copy(e) for e in frozen_tail])")]),
    ("shallow-tail-freeze", [(
        "frozen_tail = [json.loads(_canon(entry)) for entry in tail]",
        "frozen_tail = [copy.copy(e) for e in tail]")]),
    ("dict-only-tail-freeze", [(
        "frozen_tail = [json.loads(_canon(entry)) for entry in tail]",
        "frozen_tail = [(copy.deepcopy(e) if type(e) is dict else e) "
        "for e in tail]")]),
    ("live-tail", [(
        "frozen_tail = [json.loads(_canon(entry)) for entry in tail]",
        "frozen_tail = tail")]),
    ("no-restore", [(f"{_I12}_restore(acc)", f"{_I12}pass")]),
    ("no-list-restore", [("obj[:] = saved", "pass")]),
    ("no-request-snapshot", [(
        f"{_I8}_snapshot(request, acc, set())\n", "")]),
    ("commit-before-quarantine", [
        (f"{_I8}acc = []\n", f"{_I8}del log[k:]\n{_I8}acc = []\n"),
        (f"{_I8}del log[k:]\n{_I8}return", f"{_I8}return")]),
    ("no-final-commit", [(f"{_I8}del log[k:]\n{_I8}return",
                          f"{_I8}return")]),
    ("over-truncating-commit", [(
        f"{_I8}del log[k:]\n{_I8}return",
        f"{_I8}del log[max(k - 1, 0):]\n{_I8}return")]),
    ("resume-id-ignores-token", [(
        '{discarded}\\n{token}', '{discarded}\\n')]),
)
_SOURCE_MUTANTS = {name: _source_mutant(name, edits)
                   for name, edits in _SM}
# whole-set alias-free readings of the admission clause (the
# reference reading is per entry)
_SOURCE_MUTANTS["tail-cross-entry-alias"] = _source_mutant(
    "tail-cross-entry-alias", [(_TAIL_ADMIT, _TAIL_ADMIT[:-1]
                                + " or not _alias_free_across(tail):")],
    {"_alias_free_across": _alias_free_across})
_SOURCE_MUTANTS["log-cross-entry-alias"] = _source_mutant(
    "log-cross-entry-alias", [(_TAIL_ADMIT, _TAIL_ADMIT[:-1]
                               + " or not _alias_free_across(log):")],
    {"_alias_free_across": _alias_free_across})
_SOURCE_MUTANTS["no-clear-restore"] = _source_mutant(
    "no-clear-restore", [], {"_restore": _no_clear_restore})

# Documented EQUIVALENT reference edits (each measured green on the
# whole battery; test_equivalent_edit_sites_exist keeps the sites
# real). Not mutants: no black-box behavior tells them apart.
_EQUIVALENT_EDITS = (
    ("no-admission-cap", ("cap = index\n            break", "break"),
     "the linked WAL rejects every inadmissible entry with a typed "
     "WalError, so the pre-screen cap is defense in depth"),
    ("wal-except-widened", ("        except WalError:\n"
                            "            continue",
                            "        except Exception:\n"
                            "            continue"),
     "the linked WAL raises nothing but WalError on admitted entries"),
    ("token-match-not-fullmatch", ("_TOKEN_RE.fullmatch(out)",
                                   "_TOKEN_RE.match(out)"),
     "a token with trailing text can never equal the byte-exact "
     "local token, so the binding check rejects it anyway"),
    ("deepcopy-freeze", (
        "frozen_tail = [json.loads(_canon(entry)) for entry in tail]",
        "frozen_tail = copy.deepcopy(tail)"),
     "admitted entries are exact JSON values: a deep copy equals the "
     "canonical round trip (cross-entry shares only become shared "
     "copies, and the sink still receives its own deep copy)"),
    ("admission-key-isinstance", (
        "if type(key) is not str or not _scalar_ok(key):",
        "if not isinstance(key, str) or not _scalar_ok(key):"),
     "_scalar_ok's exact-type test still rejects every str-subclass "
     "key (the red variant relaxes both: isinstance-str-key)"),
)

MUTANTS = {
    "accepts-all": AcceptsAll,
    "wrong-code": WrongCode,
    "subclass-error": SubclassError,
    "malformed-as-unknown-checkpoint": _remap(MRR, UC),
    "unknown-checkpoint-as-malformed": _remap(UC, MRR),
    "corrupt-source-as-malformed": _remap(CS, MRR),
    "malformed-as-corrupt-source": _remap(MRR, CS),
    "divergent-as-malformed": _remap(DQ, MRR),
    "double-sink-call": DoubleSinkCall,
    "unguarded-sink": UnguardedSink,
    "skips-sink-when-clean": SkipsSinkWhenClean,
    "no-commit": NoCommit,
    "over-truncates": OverTruncates,
    "rebuilds-kept-entries": RebuildsKeptEntries,
    "reorders-kept-entry-keys": ReordersKeptEntryKeys,
    "mutates-request-on-success": MutatesRequestOnSuccess,
    "stale-resume-id": StaleResumeId,
    "cached-result": CachedResult,
    "str-subclass-token": StrSubclassToken,
    "receipt-field-order": ReceiptFieldOrder,
    "raw-request-peek": RawRequestPeek,
    "raw-entry-peek": RawEntryPeek,
    "raw-entry-key-set": RawEntryKeySet,
    **_SOURCE_MUTANTS,
}

MUTANT_TARGETS = {
    "accepts-all": "malformed:log_not_a_list",
    "wrong-code": "malformed:log_not_a_list",
    "subclass-error": "malformed:log_not_a_list",
    "malformed-as-unknown-checkpoint": "malformed:log_not_a_list",
    "unknown-checkpoint-as-malformed": "malformed:checkpoint_negative",
    "corrupt-source-as-malformed": "malformed:checkpoint_past_log_end",
    "malformed-as-corrupt-source": "malformed:log_not_a_list",
    "divergent-as-malformed": "malformed:sink_raises",
    "double-sink-call": "happy:clean_log_resumes_unchanged",
    "unguarded-sink": "happy:clean_log_resumes_unchanged",
    "skips-sink-when-clean": "happy:clean_log_resumes_unchanged",
    "no-commit": "happy:torn_fragment_discarded",
    "over-truncates": "happy:torn_fragment_discarded",
    "rebuilds-kept-entries": "happy:clean_log_resumes_unchanged",
    "reorders-kept-entry-keys": "happy:clean_log_resumes_unchanged",
    "mutates-request-on-success": "happy:clean_log_resumes_unchanged",
    "stale-resume-id": "happy:clean_log_resumes_unchanged",
    "cached-result": "happy:clean_log_resumes_unchanged",
    "str-subclass-token": "happy:clean_log_resumes_unchanged",
    "receipt-field-order": "happy:clean_log_resumes_unchanged",
    "raw-request-peek": "totality:request-key-checkpoint-str-subclass",
    "raw-entry-peek": "totality:entry-key-op-str-subclass-first",
    "raw-entry-key-set": "totality:entry-dict-subclass-first",
    "isinstance-request": "totality:request-dict-subclass",
    "no-request-key-guard": "totality:request-key-checkpoint-str-subclass",
    "request-extra-fields-allowed": "malformed:request_extra_field",
    "isinstance-checkpoint": "malformed:checkpoint_bool",
    "accepts-negative-checkpoint": "malformed:checkpoint_negative",
    "rejects-zero-checkpoint": "boundary:empty_log_genesis",
    "negative-checkpoint-as-malformed": "malformed:checkpoint_negative",
    "rejects-checkpoint-at-log-end": "happy:clean_log_resumes_unchanged",
    "no-durability-check": "malformed:durable_entry_damaged",
    "durability-off-by-one": "totality:request-checkpoint-past-valid-prefix",
    "rejects-checkpoint-at-prefix-end": "happy:clean_log_resumes_unchanged",
    "skips-tail-admission": "malformed:tail_too_deep",
    "prefix-search-skips-cap": "happy:clean_log_resumes_unchanged",
    "rejects-depth-at-limit": "boundary:tail_depth_at_limit",
    "accepts-over-depth": "malformed:tail_too_deep",
    "no-alias-check": "totality:entry-intra-alias-first",
    "isinstance-container": "totality:entry-dict-subclass-first",
    "no-key-leaf-check": "totality:entry-key-lone-surrogate-first",
    "isinstance-str-key": "totality:entry-key-sequence-str-subclass-first",
    "rejects-bool-leaf": "totality:wal-entry-true-first",
    "rejects-none-leaf": "boundary:valid_looking_entries_after_first_invalid",
    "isinstance-int-leaf": "totality:entry-int-subclass-first",
    "rejects-int-at-digit-limit": "boundary:tail_int_at_digit_limit",
    "accepts-int-past-digit-bound": "malformed:tail_int_past_digit_bound",
    "sign-counted-as-digit": "totality:wal-entry-negative-int-at-digit-limit-first",
    "accepts-non-finite-float": "totality:entry-float-nan-first",
    "rejects-float-leaf": "totality:wal-entry-finite-float-first",
    "isinstance-str-leaf": "totality:entry-str-subclass-first",
    "ascii-str-leaf": "totality:wal-entry-utf8-2-byte-first",
    "accepts-lone-surrogate": "malformed:tail_lone_surrogate",
    "canon-default-separators": "happy:torn_fragment_discarded",
    "canon-ascii-escapes": "totality:wal-entry-utf8-2-byte-first",
    "canon-unsorted-keys": "totality:request-checkpoint-0",
    "token-unframed": "happy:torn_fragment_discarded",
    "token-byte-length-framing": "totality:wal-entry-utf8-2-byte-first",
    "narrow-sink-boundary": "totality:sink-raises-keyboard-interrupt",
    "isinstance-sink-output": "totality:sink-returns-str-subclass",
    "skips-token-binding": "malformed:sink_empty_tail_token",
    "shared-sink-argument": "rollback:mutate_tail_arg_then_matching_token",
    "list-shallow-sink-argument": "totality:sink-own-argument-entry",
    "entry-shallow-sink-argument": "totality:sink-own-argument-payload",
    "record-shared-sink-argument": "totality:sink-own-argument-record",
    "live-tail": "totality:sink-mutates-last-entry",
    "no-restore": "rollback:mutate_inputs_then_raise",
    "no-list-restore": "rollback:mutate_inputs_then_raise",
    "no-request-snapshot": "rollback:mutate_inputs_then_raise",
    "commit-before-quarantine": "malformed:sink_raises",
    "no-final-commit": "happy:torn_fragment_discarded",
    "over-truncating-commit": "happy:clean_log_resumes_unchanged",
    "resume-id-ignores-token": "happy:clean_log_resumes_unchanged",
    "no-clear-restore": "rollback:mutate_inputs_then_raise",
    "shallow-tail-freeze": "totality:sink-tail-sets-nested-dict-value-first",
    "tail-cross-entry-alias": "totality:tail-entries-share-list-first",
    "log-cross-entry-alias": "totality:tail-shares-durable-record-first",
    "dict-only-tail-freeze": "totality:sink-tail-appends-list-entry-first",
}  # GENERATED-CHECKED


# -- kill-proof: substitution mutants vs the closure ---------------------------
def _payload_of(row):
    return {k: v for k, v in row.items() if k != "name"}


def _regenerated(row):
    """ROW with its pinned receipt and surviving log regenerated from
    the reference over the row's own inputs (kept as-is when the
    reference rejects), so only the semantic pins can kill the
    edit."""
    with contextlib.suppress(_reference.ResumeError):
        row["expect"] = _reference_resume("canonical", row["log"],
                                          row["request"])
        row["expect_log"] = row["log"][:row["expect"]["resumed_count"]]
    return row


def _erasures(section, row):
    """Single-edge erasures of ROW. An erasure equal to its row is
    an equivalent mutant and is dropped."""
    out = []
    if section in ("happy", "boundary", "rollback"):
        if row["request"]["checkpoint_sequence"] > 0:
            lowered = copy.deepcopy(row)
            lowered["request"]["checkpoint_sequence"] -= 1
            out.append(("checkpoint-lowered", _regenerated(lowered)))
        if row["log"]:
            dropped = copy.deepcopy(row)
            dropped["log"] = dropped["log"][:-1]
            dropped["request"]["checkpoint_sequence"] = min(
                dropped["request"]["checkpoint_sequence"],
                len(dropped["log"]))
            out.append(("last-entry-dropped", _regenerated(dropped)))
        grown = copy.deepcopy(row)
        grown["log"].append({"op": "x"})
        out.append(("torn-entry-appended", _regenerated(grown)))
    if section == "malformed":
        fixed = copy.deepcopy(row)
        parts = _repaired(row)
        fixed.update(log=parts["log"], request=parts["request"],
                     sink=parts["sink"])
        out.append(("defect-repaired", fixed))
        retold = copy.deepcopy(row)
        retold["defect"] += "."
        out.append(("defect-text-edited", retold))
    if section == "rollback":
        for other in ROLLBACK_MANIFEST:
            if other[1] != row["rejected_sink"]:
                swapped = copy.deepcopy(row)
                swapped["rejected_sink"] = other[1]
                out.append((f"sink-{other[1]}", swapped))
    return [(label, m) for label, m in out if _canon(m) != _canon(row)]


def _canon(row):
    """Type-exact row identity (True never equals 1 here)."""
    return json.dumps(row, sort_keys=True)


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


def test_probe_expects_are_closed_and_locally_derived():
    """PROBE_EXPECT covers exactly the accept probes, and every
    pinned receipt (and the base salvage / clean receipts) is the
    LOCAL linear-prefix derivation over the probe's inputs."""
    accepts = [n for n, (_, f) in PROBES.items() if f == ACCEPT]
    assert list(PROBE_EXPECT) == accepts
    for name in accepts:
        log, request, _ = PROBES[name][0]()
        _check_receipt(name, log, request, PROBE_EXPECT[name])
    _check_receipt("salvage", *_salvage_inputs(), SALVAGE_EXPECT)
    _check_receipt("clean", *_clean_inputs(), CLEAN_EXPECT)
    assert (SALVAGE_EXPECT["resumed_count"],
            SALVAGE_EXPECT["discarded_count"]) == (2, 1)
    assert (CLEAN_EXPECT["resumed_count"],
            CLEAN_EXPECT["discarded_count"]) == (3, 0)
    assert CLEAN_EXPECT["quarantine_token"] == quarantine_tail([])


def test_wal_damage_probes_salvage_to_their_entry():
    """Every wal-* accept probe salvages back to exactly the damaged
    entry (first 0, middle 1, last 2); every durable-* probe is
    corrupt_source."""
    for name, (_, failure) in PROBES.items():
        if name.startswith("wal-"):
            index = {"first": 0, "middle": 1, "last": 2}[
                name.rsplit("-", 1)[1]]
            receipt = PROBE_EXPECT[name]
            assert (receipt["resumed_count"],
                    receipt["discarded_count"]) == \
                (index, 3 - index), name
            assert (receipt["head"] == G0) == (index == 0), name
        if name.startswith("durable-"):
            assert failure == CS, name


def test_every_hostile_and_leaf_edge_probe_covers_all_positions():
    """Hostile types and leaf edges are probed at the FIRST, MIDDLE
    and LAST entry, both torn and acknowledged."""
    stems = {}
    for name in PROBES:
        head, _, pos = name.rpartition("-")
        if pos in ("first", "middle", "last"):
            stems.setdefault(head, set()).add(pos)
    assert stems and all(v == {"first", "middle", "last"}
                         for v in stems.values())
    for label, _ in _BAD_LEAVES:
        for stem in (f"entry-{label}", f"entry-sequence-{label}",
                     f"entry-record-digest-{label}"):
            assert stem in stems and f"durable-{stem}" in stems, stem
    for label, _ in _LEAF_EDGES:
        for kind in ("entry", "sequence", "digest"):
            stem = f"wal-{kind}-{label}"
            assert stem in stems and f"durable-{stem}" in stems, stem


def _digest_token(value):
    """Salvage of the base log with the LAST entry's record digest
    set to VALUE (checkpoint 2), through the BOUND engine: the
    receipt's quarantine token (must equal the reference token over
    the frozen tail)."""
    log, request = _base_log(), {"checkpoint_sequence": 2}
    log[2]["payload"]["record"]["digest"] = value
    tail = copy.deepcopy(log[2:])
    engine, _ = _engine(ResumeEngine, "canonical")
    receipt = engine.resume(log, request)
    assert (receipt["resumed_count"], receipt["discarded_count"]) == \
        (2, 1), value
    assert receipt["quarantine_token"] == quarantine_tail(tail)
    return receipt["quarantine_token"]


@pytest.mark.parametrize("pair", [
    (True, False), (True, 1), (False, 0), (None, False), ("", None),
    (1.0, 1), (0.0, -0.0), ("\u00e9", "e"), ("\u00e9", "ee"),
    ("\u20ac", "eee"), ("\U0001d11e", "eeee"), (_EDGE, -_EDGE)],
    ids=["true-false", "true-one", "false-zero", "none-false",
         "empty-str-none", "float-one-vs-int-one", "zero-vs-negative-zero",
         "utf8-2-vs-e", "utf8-2-byte-length-twin",
         "utf8-3-byte-length-twin", "utf8-4-byte-length-twin",
         "digit-edge-sign"])
def test_leaf_pair_tokens_are_distinct(pair):
    """Pinned pairs: bool leaves never collide with each other, with
    ints or with None, "" is not None, a float is not the equal int,
    and a multi-byte str is distinct from its char-length and
    byte-length ASCII twins."""
    left, right = (_digest_token(v) for v in pair)
    assert left != right, pair


def test_equivalent_edit_sites_exist():
    """Every documented equivalent edit names a real reference site
    and is not also a battery mutant."""
    src = "\n\n".join(inspect.getsource(getattr(_reference, n))
                       for n in _SOURCES)
    for name, (old, new), why in _EQUIVALENT_EDITS:
        assert old in src and old != new and why, name
        assert name not in MUTANTS, name


def test_cross_entry_sharing_is_admitted_and_framed_per_entry():
    """Per-entry admission (the contract reading): two discarded
    entries sharing a nested list or dict resume, BOTH are
    discarded, and the token frames each entry on its own."""
    for label in ("tail-entries-share-list", "tail-entries-share-dict",
                  "tail-same-entry-twice"):
        for suffix in ("first", "middle", "last"):
            name = f"{label}-{suffix}"
            log, request, _ = PROBES[name][0]()
            receipt = PROBE_EXPECT[name]
            assert (receipt["resumed_count"],
                    receipt["discarded_count"]) == (3, 3), name
            framed = ["qtn1"] + [
                f"{len(t)}:{t}" for t in (
                    json.dumps(e, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False) for e in log[3:])]
            assert receipt["quarantine_token"] == "qtn1:" + \
                hashlib.sha256("|".join(framed).encode()).hexdigest()


def test_identity_source_mutant_is_green_under_current_binding():
    """Structural guard: an unedited reference-source mutant passes
    the whole battery under the CURRENT binding, so a source mutant
    dies only for its edit - never because it raises a different
    error class than the one the probes catch."""
    assert _probe(_source_mutant("identity", [])) == []


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(ResumeEngine, executed=executed) == []
    assert executed == [f"{s}:{m[0]}" for s, man in MANIFESTS.items()
                        for m in man] + [
        f"totality:{n}" for n in PROBE_MANIFEST]


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
    assert _probe(ResumeEngine,
                  only=set(MUTANT_TARGETS.values())) == []


def test_every_row_has_a_real_erasure():
    for section in MANIFESTS:
        for row in CASES[section]:
            erasures = _erasures(section, row)
            assert erasures, (section, row["name"])
            assert all(_canon(m) != _canon(row) for _, m in erasures)


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
    rows = sum(len(CASES[s]) for s in MANIFESTS)
    assert sum(label.startswith("payload:") for label, _ in mutants) \
        == rows * (rows - 1)
    assert len(mutants) > 900
    survivors = []
    for label, m in mutants:
        try:
            _validate_closure(m)
        except (AssertionError, ValueError, KeyError, TypeError):
            continue
        survivors.append(label)
    assert survivors == [], survivors
