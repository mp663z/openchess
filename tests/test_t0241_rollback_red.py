"""T0241 permanent red battery for store-rollback engines.

The battery drives EVERY row of the T0240 conformance fixture
(tests/fixtures/rollback/cases.json), a closed set of totality
probes and a pinned tail-order probe through an engine class
constructed with a tail archiver:

- happy/boundary: the pinned receipt exactly (exact field types),
  the log truncated IN PLACE to exactly the surviving prefix (same
  list object, same prefix entry objects, each _snap-identical),
  the request untouched by value AND identity, deterministic by
  value over fresh copies (never the same object), and exactly ONE
  archiver call with a DETACHED copy of the exact tail, in order;
- malformed: the original input rejects with the pinned failure
  class and its mapped code, log and request untouched, the
  archiver never called unless the class is divergent_archive, and
  the declared minimal repair is accepted with the receipt the
  reference derives for the repaired input;
- rollback: a rejection leaves log and request untouched, then the
  valid follow-up (on the SAME engine instance when the archiver is
  unchanged) returns the pinned receipt and truncates exactly the
  tail;
- totality: hostile requests, request keys/values, hostile logs,
  entries, entry keys/values, payloads, records, dict/list
  subclasses, cyclic/deep/huge values, and hostile archiver
  behavior (raising any BaseException, non-str/str-subclass/
  bad-grammar/unbound/unencodable output, archivers that mutate the
  caller's log, request or their own argument mid-call, or keep
  what they are handed) each reject with the pinned failure class
  or, for accept-probes, return the pinned receipt with exactly the
  tail removed - any other BaseException escaping is a failure.

Standalone-red convention (T0151, T0178, T0187, T0196, T0232): the
battery is permanently GREEN against the contract-derived reference
engine from tests.test_t0239_rollback_contract and every mutant
below is RED. The production task switches the binding by
replacing ONLY the two binding lines below with the production
RollbackEngine / RollbackError names; no assertion changes.

Fixture closure: ordered per-section manifests, per-row semantic
pins and closed per-tag edge/defect-locus checks over the ORIGINAL
row data (an unknown tag raises), and a ROW_DIGESTS whole-row
sha256 table whose key set equals the manifests.
test_closure_kills_substitution_mutants proves substitution
mutants are killed with the digest table live AND with digests
neutralized."""

from __future__ import annotations

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

from tests import test_t0239_rollback_contract as _reference  # noqa: E402
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
from tests.test_t0239_rollback_contract import archive_tail  # noqa: E402
from tests.test_t0240_rollback_fixture import (  # noqa: E402
    ORACLES,
    _assert_malformed_scenario,
    _repaired,
)
from tools.rollback_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
)

# -- binding switch: the production task replaces ONLY these two
RollbackEngine = _reference.RollbackEngine
RollbackError = _reference.RollbackError

FIXTURE = (Path(__file__).parent / "fixtures" / "rollback"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECEIPT_FIELDS = ("rollback_id", "from_head", "to_head",
                  "truncated_count", "archive_token")
ENTRY_FIELDS = ("sequence", "op", "entry_id", "prior_entry_id",
                "payload")
RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
MRR = "malformed_rollback_record"
UT = "unknown_target"
CS = "corrupt_source"
DA = "divergent_archive"
# failure classes raised BEFORE the archiver may be called
PRE_ARCHIVE = frozenset({MRR, UT, CS})

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, edge tag, archiver, pins) where pins =
# (log length, target sequence, truncated count, ops of the log,
# per-entry snapshot position: S=STARTPOS, K=KINGS, E=AFTER_E4)
HAPPY_MANIFEST = (
    ("rollback-single-entry-tail", "single-entry-tail", "honest",
     (3, 2, 1, ("put", "put", "put"), ("S", "K", "E"))),
    ("rollback-partial-tail", "partial-tail", "honest",
     (3, 1, 2, ("put", "put", "put"), ("S", "K", "E"))),
    ("rollback-put-delete-put", "put-delete-put", "honest",
     (3, 1, 2, ("put", "delete", "put"), ("K", "K", "S"))),
)
BOUNDARY_MANIFEST = (
    ("rollback-entire-log", "entire-log", "honest",
     (3, 0, 3, ("put", "put", "put"), ("S", "K", "E"))),
    ("rollback-empty-log", "empty-log", "honest",
     (0, 0, 0, (), ())),
    ("rollback-zero-tail-noop", "zero-tail-noop", "honest",
     (3, 3, 0, ("put", "put", "put"), ("S", "K", "E"))),
)
# malformed: (name, defect-locus tag, failure class, archiver,
# repair form)
MALFORMED_MANIFEST = (
    ("log-not-a-list", "non-list-source-log", MRR, "honest",
     "replace_log"),
    ("request-missing-field", "missing-request-field", MRR, "honest",
     "replace_request"),
    ("request-extra-field", "extra-request-field", MRR, "honest",
     "replace_request"),
    ("target-non-int", "non-int-target", MRR, "honest",
     "set_request_field"),
    ("target-bool", "bool-target", MRR, "honest",
     "set_request_field"),
    ("target-negative", "target-negative", UT, "honest",
     "set_request_field"),
    ("target-beyond-length", "target-beyond-length", UT, "honest",
     "set_request_field"),
    ("sequence-gap", "sequence-gap", CS, "honest", "replace_log"),
    ("tampered-entry-id", "tampered-entry-id", CS, "honest",
     "replace_log"),
    ("unregistered-op", "unregistered-op", CS, "honest",
     "replace_log"),
    ("oracle-raises", "oracle-raises", DA, "raising", "set_oracle"),
    ("oracle-non-str-output", "oracle-non-str-output", DA,
     "non_str_output", "set_oracle"),
    ("oracle-bad-grammar", "oracle-bad-grammar", DA, "bad_grammar",
     "set_oracle"),
    ("oracle-unbound-token", "oracle-unbound-token", DA,
     "unbound_token", "set_oracle"),
    ("oracle-lone-surrogate", "oracle-lone-surrogate", DA,
     "lone_surrogate", "set_oracle"),
)
# rollback: (name, rejection tag, failure class, archiver,
# follow-up archiver)
ROLLBACK_MANIFEST = (
    ("rejected-rollback-raising-archiver-then-valid-rollback",
     "raising-archiver", DA, "raising", "honest"),
    ("rejected-rollback-unknown-target-then-valid-rollback",
     "unknown-target", UT, "honest", "honest"),
    ("rejected-rollback-corrupt-source-then-valid-rollback",
     "corrupt-source", CS, "honest", "honest"),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:rollback-single-entry-tail":
        "ee7b5db297b7c5cde4843bebc6079ec8e78810edd66884b556272c7d3a9c1b48",
    "happy:rollback-partial-tail":
        "40a4833b0e7644be442349c130355cf87a60570abf235e1ce66f99ffc65209c8",
    "happy:rollback-put-delete-put":
        "ebd15f69bf3699d3945fde2868067466feb66f2985b52e8375038e78be3e316a",
    "boundary:rollback-entire-log":
        "daa6c4171d3d75775fa76539d05c66290dae97b391a254abdf176e5d4c108a51",
    "boundary:rollback-empty-log":
        "cc9efa4aeb742ab8315cc24eecb53f307374b422aaaaf1fb0c46ac6207728a47",
    "boundary:rollback-zero-tail-noop":
        "7a912c0f91b94b0e5a9feb9b13a502b89c21ada540e2dca9962b31a1dce7b49a",
    "malformed:log-not-a-list":
        "85a0ca720a3ede221ebc4f4f641ed542a413d0e33c1fa3c19d1e17d0672d55a7",
    "malformed:request-missing-field":
        "8274f6c26475ce3d541a79aedc0ec61fa5ebd986ed7a37e9fc06e89f957cd8da",
    "malformed:request-extra-field":
        "ca579f4e1aac166b6f2d2d1d7f49f79ef361ada91a95fba70fb44ac120528832",
    "malformed:target-non-int":
        "11c973cfab2f4252642dc89abb1aed670e135d690c32eb5ceb9e7cc797ba1e11",
    "malformed:target-bool":
        "5aa23e321018460009eb79af4415713ef2d157f5d58920db9cae02a0b1e1be28",
    "malformed:target-negative":
        "c7d2267a6757a52d3b98e93d2344e4a2559afb7aa4b531edd5321c059cb5b47e",
    "malformed:target-beyond-length":
        "98b685b0a2f81424898d5f962cb4595f94f37ffc78ace33446fb90832791341b",
    "malformed:sequence-gap":
        "2e0a169192e19abe2f984858e28f9727cecdadc28c5ed39b2f0e05f4dff6a875",
    "malformed:tampered-entry-id":
        "5044b7b251af50f638a659b67db390cfa23e7c0874d5d0ff267c01f7e3153a63",
    "malformed:unregistered-op":
        "463dd907cc07fb0c7ff83b9cf3fe0611d63ccae7b41ab87b81525a7901685e18",
    "malformed:oracle-raises":
        "40e57936b52b9f82cf75ab8fa28c1ae5cc21cc863006d05c3ac64b35e6144b88",
    "malformed:oracle-non-str-output":
        "9e8e8c7d3a8ca69bb8a1b918a28b186979b941fee1ea59dff5f1ffa149c14704",
    "malformed:oracle-bad-grammar":
        "7090dd3008555bb39ece75ae181a74e4f5291bde6487816f98e0a3978f120295",
    "malformed:oracle-unbound-token":
        "ff3a391fe5b5607489a86fb8c9443506e28b35f4f5a10bacfb145eeb3931d34c",
    "malformed:oracle-lone-surrogate":
        "f2e9b1241d504615677ace1785fc55a6df6ea1622a8d0ac5d686489380008b16",
    "rollback:rejected-rollback-raising-archiver-then-valid-rollback":
        "1d9c331008001348f30b524541c482d9acfba184e7729b1f8be8c88b3c2eac33",
    "rollback:rejected-rollback-unknown-target-then-valid-rollback":
        "5f0153c5cf0098c23e2191f0767475c31551f4c7a283e842bf0305f183d2abde",
    "rollback:rejected-rollback-corrupt-source-then-valid-rollback":
        "891f6caafed2b01dc2b260fbdac698ef9ab10f864b54e7e4f9dd02a296a12d07",
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


def _rollback_id(from_head, to_head, count, token):
    return "rbk1:" + hashlib.sha256(
        f"{from_head}\n{to_head}\n{count}\n{token}".encode()
    ).hexdigest()


def _reference_rollback(archiver, log, request):
    return _reference.RollbackEngine(ORACLES[archiver]).rollback(
        copy.deepcopy(log), copy.deepcopy(request))


def _reference_failure(archiver, log, request):
    try:
        _reference_rollback(archiver, log, request)
    except _reference.RollbackError as error:
        return error.failure_class
    return None


def _check_receipt(name, log, request, expect):
    """The pinned receipt realizes the contract over the ORIGINAL
    log and request: heads and count from the exact log positions,
    the token the canonical local tail derivation, the id
    derived."""
    assert type(log) is list and _wal_verifies(log), name
    assert type(request) is dict and \
        list(request) == ["target_sequence"], name
    target = request["target_sequence"]
    assert type(target) is int and 0 <= target <= len(log), name
    assert set(expect) == set(RECEIPT_FIELDS), name
    assert expect["from_head"] == (log[-1]["entry_id"] if log
                                   else GENESIS), name
    assert expect["to_head"] == (log[target - 1]["entry_id"]
                                 if target else GENESIS), name
    assert type(expect["truncated_count"]) is int and \
        expect["truncated_count"] == len(log) - target, name
    assert expect["archive_token"] == archive_tail(log[target:]), name
    assert expect["rollback_id"] == _rollback_id(
        expect["from_head"], expect["to_head"],
        expect["truncated_count"], expect["archive_token"]), name


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


def _check_edge(case, tag, oracle, pins):
    """One closed branch per happy/boundary edge tag; an unknown
    tag raises."""
    name = case["name"]
    log, request, expect = case["log"], case["request"], case["expect"]
    assert case["oracle"] == oracle, name
    _check_receipt(name, log, request, expect)
    target = request["target_sequence"]
    assert (len(log), target, expect["truncated_count"], _ops(log),
            _positions(log)) == pins, name
    if tag in ("single-entry-tail", "partial-tail", "put-delete-put"):
        assert 0 < target < len(log), name
        assert expect["from_head"] != expect["to_head"], name
        assert GENESIS not in (expect["from_head"],
                               expect["to_head"]), name
        if tag == "put-delete-put":
            ident = log[0]["payload"]["identity"]
            assert log[1]["payload"]["identity"] == ident, name
            assert log[2]["payload"]["identity"] != ident, name
    elif tag == "entire-log":
        assert target == 0 and log, name
        assert expect["to_head"] == GENESIS != expect["from_head"], name
    elif tag == "empty-log":
        assert log == [] and target == 0, name
        assert expect["from_head"] == expect["to_head"] == GENESIS, name
    elif tag == "zero-tail-noop":
        assert target == len(log) > 0, name
        assert expect["from_head"] == expect["to_head"] == \
            log[-1]["entry_id"], name
    else:
        raise AssertionError(f"unknown edge tag {tag!r}")


def _check_rollback(case, tag, failure, oracle, then_oracle):
    name = case["name"]
    assert case["expect_failure"] == failure, name
    assert case["oracle"] == oracle, name
    assert case["then_oracle"] == then_oracle, name
    _check_receipt(name, case["then_log"], case["then_request"],
                   case["expect"])
    assert _reference_failure(oracle, case["log"], case["request"]) \
        == failure, name
    assert _reference_rollback(then_oracle, case["then_log"],
                               case["then_request"]) == \
        case["expect"], name
    log, then_log = case["log"], case["then_log"]
    req, then_req = case["request"], case["then_request"]
    if tag == "raising-archiver":
        assert log == then_log and req == then_req, name
        try:
            ORACLES[oracle](log[req["target_sequence"]:])
        except Exception:  # noqa: BLE001
            pass
        else:
            raise AssertionError(name)
    elif tag == "unknown-target":
        assert log == then_log and req != then_req, name
        assert list(req) == list(then_req) == ["target_sequence"], name
        target = req["target_sequence"]
        assert type(target) is int and not 0 <= target <= len(log), name
    elif tag == "corrupt-source":
        assert req == then_req and log != then_log, name
        assert len(log) == len(then_log), name
        assert not _wal_verifies(log) and _wal_verifies(then_log), name
    else:
        raise AssertionError(f"unknown rollback tag {tag!r}")


def _row(section, name, cases=None):
    for row in (cases or CASES)[section]:
        if row["name"] == name:
            return row


def _check_row(section, case, meta):
    if section in ("happy", "boundary"):
        _, tag, oracle, pins = meta
        _check_edge(case, tag, oracle, pins)
    elif section == "malformed":
        _, tag, failure, oracle, form = meta
        assert case["expect_failure"] == failure, case["name"]
        assert case["oracle"] == oracle, case["name"]
        assert list(case["minimal_repair"]) == [form], case["name"]
        assert case["scenario"] == tag, case["name"]
        _assert_malformed_scenario(case, tag)
        assert _reference_failure(oracle, case["log"],
                                  case["request"]) == failure, \
            case["name"]
    else:
        _, tag, failure, oracle, then_oracle = meta
        _check_rollback(case, tag, failure, oracle, then_oracle)


def _validate_closure(cases):
    """Ordered names, whole-row digests and per-tag semantic
    checks for every executed section; the digest table's key set
    equals the manifests."""
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
    """Wraps an archiver; records every call's argument BY VALUE
    and the container ids it was handed."""

    def __init__(self, archiver):
        self.archiver, self.calls = archiver, []

    def __call__(self, tail):
        try:
            value = copy.deepcopy(tail)
        except BaseException:  # noqa: BLE001
            value = None
        self.calls.append((value, _containers(tail)))
        return self.archiver(tail)


def _engine(cls, archiver):
    counter = _Counting(ORACLES[archiver] if isinstance(archiver, str)
                        else archiver)
    return cls(counter), counter


def _receipt_ok(result, expect):
    return (type(result) is dict and result == expect
            and set(result) == set(RECEIPT_FIELDS)
            and type(result["truncated_count"]) is int
            and all(type(result[f]) is str for f in RECEIPT_FIELDS
                    if f != "truncated_count"))


def _checked_rollback(engine, counter, log, request, expect):
    """One successful rollback: pinned receipt, the SAME log list
    truncated to exactly its prefix (same, untouched prefix entry
    objects), request untouched, exactly one archiver call with a
    detached by-value copy of the exact tail in order."""
    target = request["target_sequence"]
    prefix = [_snap(entry) for entry in log[:target]]
    req_snap = _snap(request)
    tail = copy.deepcopy(log[target:])
    live = _containers(log) | _containers(request)
    n_before = len(counter.calls)
    result = engine.rollback(log, request)
    calls = counter.calls[n_before:]
    ok = (_receipt_ok(result, expect)
          and type(log) is list and len(log) == target
          and [_snap(entry) for entry in log] == prefix
          and _snap(request) == req_snap
          and len(calls) == 1 and calls[0][0] == tail
          and not calls[0][1] & live)
    return result, ok


def _rollback_ok(cls, case, prefix="", engine=None):
    """Pinned receipt and exact truncation, determinism BY VALUE
    (a repeat never returns the same object), one archiver call."""
    archiver = case[prefix + "oracle"]
    if engine is None:
        engine = _engine(cls, archiver)
    results = []
    for eng, counter in (engine, _engine(cls, archiver)):
        result, ok = _checked_rollback(
            eng, counter, copy.deepcopy(case[prefix + "log"]),
            copy.deepcopy(case[prefix + "request"]), case["expect"])
        if not ok:
            return False
        results.append(result)
    return results[1] is not results[0]


def _rejects(engine, counter, log, request, failure):
    inputs = (log, request)
    snap = _snap(inputs)
    n_before = len(counter.calls)
    try:
        engine.rollback(log, request)
    except RollbackError as error:
        calls = len(counter.calls) - n_before
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and error.code in ERROR_ENUM
                and _snap(inputs) == snap
                and calls == (0 if failure in PRE_ARCHIVE else 1))
    return False


def _malformed_ok(cls, case):
    eng, counter = _engine(cls, case["oracle"])
    if not _rejects(eng, counter, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    fixed = _repaired(case)
    expect = _reference_rollback(fixed["oracle"], fixed["log"],
                                 fixed["request"])
    eng, counter = _engine(cls, fixed["oracle"])
    _, ok = _checked_rollback(eng, counter, copy.deepcopy(fixed["log"]),
                              copy.deepcopy(fixed["request"]), expect)
    return ok


def _rollback_row_ok(cls, case):
    engine = _engine(cls, case["oracle"])
    if not _rejects(*engine, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    same = case["then_oracle"] == case["oracle"]
    return _rollback_ok(cls, case, "then_", engine if same else None)


_RUNNERS = {"happy": _rollback_ok, "boundary": _rollback_ok,
            "malformed": _malformed_ok, "rollback": _rollback_row_ok}


# -- pinned tail-order probe ---------------------------------------------------
def _order_inputs():
    """Four entries, rolled back to 1: the archiver must see the
    three-entry tail in log order, and the token binds that
    order."""
    return (_log_of(("put", STARTPOS), ("put", KINGS),
                    ("put", AFTER_E4), ("delete", KINGS)),
            {"target_sequence": 1})


ORDER_EXPECT = {
    "rollback_id":
        "rbk1:6058716019d42ad6e8cc2a69d400a51c4b621f0a965d25f3f1f1e963a9091809",
    "from_head":
        "wal1:6b3c7e3b9b8230bd63c74ee16157dea70c289d4a19317a2b00a1103bf20dd3ed",
    "to_head":
        "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
    "truncated_count": 3,
    "archive_token":
        "arc1:a80035a88a00e7ed73b78341b17b076f84f539c2f913a2025d964aef77d6972e",
}  # GENERATED
ORDER_LABEL = "probe:tail-order"


def _order_ok(cls):
    engine, counter = _engine(cls, "honest")
    _, ok = _checked_rollback(engine, counter, *_order_inputs(),
                              ORDER_EXPECT)
    return ok


# -- closed totality probes ----------------------------------------------------




def _probe_inputs():
    """A valid three-entry log rolled back to 1; every probe
    corrupts one component (or attacks through the archiver)."""
    return (_log_of(("put", STARTPOS), ("put", KINGS),
                    ("put", AFTER_E4)),
            {"target_sequence": 1})


def _rekey(mapping, key, new_key):
    return {new_key if k == key else k: v for k, v in mapping.items()}


_HOSTILE_CONTAINERS = (("none", None), ("true", True), ("zero", 0),
                       ("float", 1.5), ("text", "text"))
_FORGED = "wal1:" + "f" * 64


class _IntSub(int):
    pass


def _req(fn):
    def build():
        log, request = _probe_inputs()
        return log, fn(request), "honest"
    return build


def _log(fn):
    def build():
        log, request = _probe_inputs()
        return fn(log), request, "honest"
    return build


def _entry(fn, index):
    """Replace the entry at INDEX (0: surviving prefix, -1: tail)
    with FN(copy)."""
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


def _archiver(archiver):
    def build():
        log, request = _probe_inputs()
        return log, request, archiver
    return build


def _raises(exc):
    def archiver(tail):
        raise exc
    return archiver


def _honest_then(fn):
    return lambda tail: fn(archive_tail(tail))


_HOSTILE_ARCHIVERS = {
    "raises-value-error": _raises(ValueError("x")),
    "raises-runtime-error": _raises(RuntimeError("x")),
    "raises-keyboard-interrupt": _raises(KeyboardInterrupt()),
    "raises-system-exit": _raises(SystemExit(1)),
    "raises-generator-exit": _raises(GeneratorExit()),
    "returns-none": lambda t: None,
    "returns-int": lambda t: 7,
    "returns-list": lambda t: [archive_tail(t)],
    "returns-bytes": _honest_then(str.encode),
    "returns-str-subclass": _honest_then(lambda s: _SKV(s)),
    "returns-bad-grammar": lambda t: "bad",
    "returns-uppercase-hex": _honest_then(str.upper),
    "returns-trailing-newline": _honest_then(lambda s: s + "\n"),
    "returns-lone-surrogate": _honest_then(lambda s: s[:-1] + "\ud800"),
    "returns-unbound-token": lambda t: "arc1:" + "f" * 64,
    "returns-empty-tail-token": lambda t: archive_tail([]),
    "returns-reversed-tail-token": lambda t: archive_tail(t[::-1]),
    "returns-prefix-tail-token": lambda t: archive_tail(t[:-1]),
}


class _SKV(str):
    """str subclass VALUE: equal and hashing like its text, but not
    an exact built-in str."""


# Accept-probes: the archiver attacks the caller's OWN log and
# request (built-in methods only) or its own argument, then returns
# the honest token for the tail it was handed. The rollback must
# still return exactly PROBE_EXPECT and truncate exactly the tail
# of the ORIGINAL log, prefix entries untouched.
ACCEPT = None
_LIVE_ATTACKS = ("mutates-surviving-entry", "mutates-tail-entry",
                 "appends-entry", "pops-entry", "clears-log",
                 "reorders-entry-keys", "mutates-request-target",
                 "clears-request", "mutates-own-argument-entry",
                 "mutates-own-argument-payload",
                 "mutates-own-argument-record")


def _attack(kind, log, request, tail):
    if kind in ("mutates-surviving-entry", "mutates-then-raises"):
        dict.__setitem__(log[0], "entry_id", _FORGED)
    if kind == "mutates-tail-entry":
        dict.__setitem__(log[-1], "op", "delete")
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
    if kind in ("mutates-request-target", "mutates-then-raises"):
        dict.__setitem__(request, "target_sequence", 0)
    if kind == "clears-request":
        dict.clear(request)
    # own-argument attacks, one per depth: an engine sharing ANY
    # level of the frozen tail with the archiver is caught
    if kind == "mutates-own-argument-entry":
        for entry in tail:
            dict.__setitem__(entry, "entry_id", _FORGED)
    if kind == "mutates-own-argument-payload":
        for entry in tail:
            dict.__setitem__(entry["payload"], "identity", "x")
    if kind == "mutates-own-argument-record":
        for entry in tail:
            dict.__setitem__(entry["payload"]["record"], "digest", "x")


def _live(kind):
    def build():
        log, request = _probe_inputs()

        def archiver(tail):
            token = archive_tail(tail)
            _attack(kind, log, request, tail)
            if kind == "mutates-then-raises":
                raise ValueError("mutate then explode")
            return token
        return log, request, archiver
    return build


def _entry_family(out, index, suffix):
    """Entry/payload/record probes against the entry at INDEX:
    0 is in the surviving PREFIX, -1 in the rolled-back TAIL - the
    contract is fail-closed-typed over the FULL log."""
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

def _probe_builders():
    """name -> (builder returning (log, request, archiver), pinned
    failure class or ACCEPT)."""
    out = {}
    for label, value in _HOSTILE_CONTAINERS + (("list", []),):
        out[f"request-{label}"] = (
            _req(lambda r, v=value: copy.deepcopy(v)), MRR)
    out["request-empty"] = (_req(lambda r: {}), MRR)
    out["request-dict-subclass"] = (_req(_DictSub), MRR)
    for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
        out[f"request-key-target_sequence-{label}"] = (
            _req(lambda r, c=cls: _rekey(r, "target_sequence",
                                         c("target_sequence"))), MRR)
    out["request-key-none"] = (_req(lambda r: {**r, None: 1}), MRR)
    out["request-extra-key"] = (_req(lambda r: {**r, "force": True}),
                                MRR)
    for label, value in (("none", None), ("true", True),
                         ("false", False), ("float", 1.0),
                         ("text", "1"), ("list", [1]), ("dict", {}),
                         ("int-subclass", _IntSub(1))):
        out[f"request-value-{label}"] = (
            _req(lambda r, v=value: {"target_sequence":
                                     copy.deepcopy(v)}), MRR)
    for label, value in (("negative", -1), ("beyond-length", 4),
                         ("huge-int", 10 ** 5000),
                         ("huge-negative", -(10 ** 5000))):
        out[f"request-value-{label}"] = (
            _req(lambda r, v=value: {"target_sequence": v}), UT)
    for label, value in _HOSTILE_CONTAINERS + (("dict", {}),
                                               ("tuple", ())):
        out[f"log-{label}"] = (_log(lambda g, v=value: copy.deepcopy(v)),
                               MRR)
    out["log-list-subclass"] = (_log(_ListSub), MRR)
    out["log-duplicate-entry"] = (_log(lambda g: g + [g[-1]]), CS)
    out["log-reversed"] = (_log(lambda g: g[::-1]), CS)
    out["log-self-referential"] = (_log(_cyclic), CS)
    for index, suffix in ((0, "prefix"), (-1, "tail")):
        _entry_family(out, index, suffix)
    for label, archiver in _HOSTILE_ARCHIVERS.items():
        out[f"archiver-{label}"] = (_archiver(archiver), DA)
    for kind in _LIVE_ATTACKS:
        out[f"archiver-{kind}"] = (_live(kind), ACCEPT)
    out["archiver-mutates-then-raises"] = (_live("mutates-then-raises"),
                                           DA)
    return out


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
    "rollback_id":
        "rbk1:8e7f6a4c5cf1deffebf5bdaa927afb93d681e3473860d90c5635022663a7ea80",
    "from_head":
        "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
    "to_head":
        "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
    "truncated_count": 2,
    "archive_token":
        "arc1:f99a81cb8a2aaefcfbcb7429668386b5ddd5dbc8a8731f330025354b28164113",
}  # GENERATED
PROBES = _probe_builders()
PROBE_MANIFEST = (
    "request-none",
    "request-true",
    "request-zero",
    "request-float",
    "request-text",
    "request-list",
    "request-empty",
    "request-dict-subclass",
    "request-key-target_sequence-str-subclass",
    "request-key-target_sequence-colliding-hash",
    "request-key-none",
    "request-extra-key",
    "request-value-none",
    "request-value-true",
    "request-value-false",
    "request-value-float",
    "request-value-text",
    "request-value-list",
    "request-value-dict",
    "request-value-int-subclass",
    "request-value-negative",
    "request-value-beyond-length",
    "request-value-huge-int",
    "request-value-huge-negative",
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
    "entry-none-prefix",
    "entry-zero-prefix",
    "entry-list-prefix",
    "entry-text-prefix",
    "entry-dict-subclass-prefix",
    "entry-extra-field-prefix",
    "entry-missing-sequence-prefix",
    "entry-key-sequence-str-subclass-prefix",
    "entry-key-sequence-colliding-hash-prefix",
    "entry-missing-op-prefix",
    "entry-key-op-str-subclass-prefix",
    "entry-key-op-colliding-hash-prefix",
    "entry-missing-entry_id-prefix",
    "entry-key-entry_id-str-subclass-prefix",
    "entry-key-entry_id-colliding-hash-prefix",
    "entry-missing-prior_entry_id-prefix",
    "entry-key-prior_entry_id-str-subclass-prefix",
    "entry-key-prior_entry_id-colliding-hash-prefix",
    "entry-missing-payload-prefix",
    "entry-key-payload-str-subclass-prefix",
    "entry-key-payload-colliding-hash-prefix",
    "entry-value-op-str-subclass-prefix",
    "entry-value-entry_id-str-subclass-prefix",
    "entry-value-prior_entry_id-str-subclass-prefix",
    "entry-value-sequence-int-subclass-prefix",
    "entry-value-sequence-bool-prefix",
    "entry-value-sequence-text-prefix",
    "entry-value-sequence-huge-int-prefix",
    "payload-dict-subclass-prefix",
    "payload-key-identity-str-subclass-prefix",
    "payload-key-identity-colliding-hash-prefix",
    "payload-key-record-str-subclass-prefix",
    "payload-key-record-colliding-hash-prefix",
    "payload-value-identity-str-subclass-prefix",
    "record-dict-subclass-prefix",
    "record-key-variant-str-subclass-prefix",
    "record-key-variant-colliding-hash-prefix",
    "record-value-variant-str-subclass-prefix",
    "record-key-digest-str-subclass-prefix",
    "record-key-digest-colliding-hash-prefix",
    "record-value-digest-str-subclass-prefix",
    "record-key-snapshot_fen-str-subclass-prefix",
    "record-key-snapshot_fen-colliding-hash-prefix",
    "record-value-snapshot_fen-str-subclass-prefix",
    "record-value-self-referential-prefix",
    "record-value-deep-nested-prefix",
    "record-value-huge-int-prefix",
    "entry-none-tail",
    "entry-zero-tail",
    "entry-list-tail",
    "entry-text-tail",
    "entry-dict-subclass-tail",
    "entry-extra-field-tail",
    "entry-missing-sequence-tail",
    "entry-key-sequence-str-subclass-tail",
    "entry-key-sequence-colliding-hash-tail",
    "entry-missing-op-tail",
    "entry-key-op-str-subclass-tail",
    "entry-key-op-colliding-hash-tail",
    "entry-missing-entry_id-tail",
    "entry-key-entry_id-str-subclass-tail",
    "entry-key-entry_id-colliding-hash-tail",
    "entry-missing-prior_entry_id-tail",
    "entry-key-prior_entry_id-str-subclass-tail",
    "entry-key-prior_entry_id-colliding-hash-tail",
    "entry-missing-payload-tail",
    "entry-key-payload-str-subclass-tail",
    "entry-key-payload-colliding-hash-tail",
    "entry-value-op-str-subclass-tail",
    "entry-value-entry_id-str-subclass-tail",
    "entry-value-prior_entry_id-str-subclass-tail",
    "entry-value-sequence-int-subclass-tail",
    "entry-value-sequence-bool-tail",
    "entry-value-sequence-text-tail",
    "entry-value-sequence-huge-int-tail",
    "payload-dict-subclass-tail",
    "payload-key-identity-str-subclass-tail",
    "payload-key-identity-colliding-hash-tail",
    "payload-key-record-str-subclass-tail",
    "payload-key-record-colliding-hash-tail",
    "payload-value-identity-str-subclass-tail",
    "record-dict-subclass-tail",
    "record-key-variant-str-subclass-tail",
    "record-key-variant-colliding-hash-tail",
    "record-value-variant-str-subclass-tail",
    "record-key-digest-str-subclass-tail",
    "record-key-digest-colliding-hash-tail",
    "record-value-digest-str-subclass-tail",
    "record-key-snapshot_fen-str-subclass-tail",
    "record-key-snapshot_fen-colliding-hash-tail",
    "record-value-snapshot_fen-str-subclass-tail",
    "record-value-self-referential-tail",
    "record-value-deep-nested-tail",
    "record-value-huge-int-tail",
    "archiver-raises-value-error",
    "archiver-raises-runtime-error",
    "archiver-raises-keyboard-interrupt",
    "archiver-raises-system-exit",
    "archiver-raises-generator-exit",
    "archiver-returns-none",
    "archiver-returns-int",
    "archiver-returns-list",
    "archiver-returns-bytes",
    "archiver-returns-str-subclass",
    "archiver-returns-bad-grammar",
    "archiver-returns-uppercase-hex",
    "archiver-returns-trailing-newline",
    "archiver-returns-lone-surrogate",
    "archiver-returns-unbound-token",
    "archiver-returns-empty-tail-token",
    "archiver-returns-reversed-tail-token",
    "archiver-returns-prefix-tail-token",
    "archiver-mutates-surviving-entry",
    "archiver-mutates-tail-entry",
    "archiver-appends-entry",
    "archiver-pops-entry",
    "archiver-clears-log",
    "archiver-reorders-entry-keys",
    "archiver-mutates-request-target",
    "archiver-clears-request",
    "archiver-mutates-own-argument-entry",
    "archiver-mutates-own-argument-payload",
    "archiver-mutates-own-argument-record",
    "archiver-mutates-then-raises",
)  # GENERATED
PROBE_COUNT = 159  # GENERATED


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    log, request, archiver = build()
    engine, counter = _engine(cls, archiver)
    if failure is ACCEPT:
        # SAME INSTANCE: accepts under attack, then still rejects a
        # bad request typed and still accepts a clean rollback
        try:
            _, ok = _checked_rollback(engine, counter, log, request,
                                      PROBE_EXPECT)
            bad_log, _ = _probe_inputs()
            ok = ok and _rejects(engine, counter, bad_log,
                                 {"target_sequence": 4}, UT)
            _, again = _checked_rollback(engine, counter,
                                         *_probe_inputs(), PROBE_EXPECT)
        except BaseException:  # noqa: BLE001 - any rejection fails
            return False
        return ok and again
    try:
        return _rejects(engine, counter, log, request, failure)
    except BaseException:  # noqa: BLE001 - a raw escape is the defect
        return False


def _probe(cls, executed=None, first_only=False, only=None):
    """Every manifest row, every totality probe and the order probe
    through CLS; returns failing labels (only the first when
    FIRST_ONLY). Untrusted-component boundary: BaseException is
    caught."""
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
    jobs.append((ORDER_LABEL, lambda: _order_ok(cls)))
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
    return {"rollback_id": "rbk1:" + "0" * 64, "from_head": GENESIS,
            "to_head": GENESIS, "truncated_count": 0,
            "archive_token": archive_tail([])}


class AcceptsAll(RollbackEngine):
    def rollback(self, log, request):
        try:
            return super().rollback(log, request)
        except RollbackError:
            return _fake_receipt()


class WrongCode(RollbackEngine):
    def rollback(self, log, request):
        try:
            return super().rollback(log, request)
        except RollbackError as error:
            raise RollbackError(error.failure_class, "internal") \
                from None


def _remap(frm, to):
    class Remap(RollbackEngine):
        def rollback(self, log, request):
            try:
                return super().rollback(log, request)
            except RollbackError as error:
                if error.failure_class == frm:
                    raise RollbackError(to, FAILURE_MAPPING[to]) \
                        from None
                raise
    Remap.__name__ = f"Remap_{frm}_to_{to}"
    return Remap


class DoubleArchiverCall(RollbackEngine):
    def __init__(self, archiver):
        def twice(tail):
            archiver(copy.deepcopy(tail))
            return archiver(tail)
        super().__init__(twice)


def _peek_tail(log, request):
    if type(log) is list and type(request) is dict and \
            type(request.get("target_sequence")) is int:
        return copy.deepcopy(log[request["target_sequence"]:])
    return None


class UnguardedArchiver(RollbackEngine):
    """Calls the archiver once OUTSIDE the boundary, before
    validation."""

    def rollback(self, log, request):
        tail = _peek_tail(log, request)
        if tail is not None:
            self.archiver(tail)
        return super().rollback(log, request)


class ArchivesBeforeValidation(RollbackEngine):
    """Guarded, but archives before source validation
    (validate-first broken)."""

    def rollback(self, log, request):
        tail = _peek_tail(log, request)
        if tail is not None:
            try:
                self.archiver(tail)
            except BaseException:  # noqa: BLE001
                return super().rollback(log, request)
        return super().rollback(log, request)


class NoCommit(RollbackEngine):
    def rollback(self, log, request):
        saved = list(log) if type(log) is list else None
        out = super().rollback(log, request)
        log[:] = saved
        return out


class OverTruncate(RollbackEngine):
    def rollback(self, log, request):
        out = super().rollback(log, request)
        if log:
            log.pop()
        return out


class UnderTruncate(RollbackEngine):
    def rollback(self, log, request):
        saved = list(log) if type(log) is list else []
        out = super().rollback(log, request)
        if len(saved) > len(log):
            log.append(saved[len(log)])
        return out


class StaleRollbackId(RollbackEngine):
    def rollback(self, log, request):
        out = super().rollback(log, request)
        out["rollback_id"] = "rbk1:" + hashlib.sha256(
            out["archive_token"].encode()).hexdigest()
        return out


class SwappedHeads(RollbackEngine):
    def rollback(self, log, request):
        out = super().rollback(log, request)
        out["from_head"], out["to_head"] = out["to_head"], \
            out["from_head"]
        return out


class MutatesRequestOnSuccess(RollbackEngine):
    def rollback(self, log, request):
        out = super().rollback(log, request)
        request["target_sequence"] = len(log) + 1
        return out


class ReordersPrefixKeys(RollbackEngine):
    def rollback(self, log, request):
        out = super().rollback(log, request)
        for entry in log:
            items = list(entry.items())
            entry.clear()
            entry.update(reversed(items))
        return out


class CopiesPrefix(RollbackEngine):
    """Replaces the surviving entries with equal deep copies (the
    caller's entry objects are lost)."""

    def rollback(self, log, request):
        out = super().rollback(log, request)
        log[:] = copy.deepcopy(log)
        return out


class CachedResult(RollbackEngine):
    """Class-level cache: a repeated rollback returns the SAME
    receipt object."""
    _cache = {}

    def rollback(self, log, request):
        out = super().rollback(log, request)
        return CachedResult._cache.setdefault(out["rollback_id"], out)


class LiveArchiverArgument(RollbackEngine):
    """Hands the archiver the caller's LIVE tail entries instead of
    a detached copy."""

    def rollback(self, log, request):
        original = self.archiver
        tail = _peek_tail(log, request)
        live = log[request["target_sequence"]:] if tail is not None \
            else None
        self.archiver = (lambda t: original(live)) if live is not None \
            else original
        try:
            return super().rollback(log, request)
        finally:
            self.archiver = original


class RawRequestKeySet(RollbackEngine):
    def rollback(self, log, request):
        if isinstance(request, dict):
            set(request.keys()) == {"target_sequence"}  # noqa: B015
        return super().rollback(log, request)


class RawEntryKeySet(RollbackEngine):
    def rollback(self, log, request):
        if type(log) is list:
            for entry in log:
                if isinstance(entry, dict):
                    set(entry.keys()) == set(ENTRY_FIELDS)  # noqa: B015
        return super().rollback(log, request)


class RawOnNonListLog(RollbackEngine):
    def rollback(self, log, request):
        len(log[0:])
        return super().rollback(log, request)


def _live_target(log, request):
    if type(log) is list and type(request) is dict and \
            type(request.get("target_sequence")) is int:
        target = request["target_sequence"]
        if 0 < target <= len(log):
            return target
    return None


class PrefixHeadPeek(RollbackEngine):
    """Reads the new head from the caller's LIVE surviving entry
    before validation (no exact-type guard on the prefix)."""

    def rollback(self, log, request):
        target = _live_target(log, request)
        if target is not None:
            log[target - 1]["entry_id"]  # noqa: B018
        return super().rollback(log, request)


class RawPrefixKeySet(RollbackEngine):
    """Compares the surviving entries' raw key sets before the
    exact-str key guard."""

    def rollback(self, log, request):
        target = _live_target(log, request)
        if target is not None:
            for entry in log[:target]:
                if isinstance(entry, dict):
                    set(entry.keys()) == set(ENTRY_FIELDS)  # noqa: B015
        return super().rollback(log, request)


def _source_mutant(name, edits):
    """A one-guard edit of the reference engine: each OLD must
    occur in the reference source (first occurrence replaced)."""
    src = inspect.getsource(_reference.RollbackEngine)
    for old, new in edits:
        if old not in src:
            raise AssertionError(f"{name}: edit site missing: {old!r}")
        src = src.replace(old, new, 1)
    namespace = dict(vars(_reference))
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return namespace["RollbackEngine"]


_I12 = " " * 12
NoLogRestore = _source_mutant("no-log-restore", [(
    f"{_I12}WalEngine._restore_log(log, saved_container,\n"
    f"{_I12}                       saved_entries)\n", "")])
NoRequestRestore = _source_mutant("no-request-restore", [(
    f"{_I12}request.clear()\n{_I12}request.update(saved_req)\n",
    f"{_I12}pass\n")])
LiveRequestReread = _source_mutant("live-request-reread", [(
    f"{_I12}token = self._archive(frozen_tail)\n",
    f"{_I12}token = self._archive(frozen_tail)\n"
    f"{_I12}target = request[\"target_sequence\"]\n")])
LiveLogBinding = _source_mutant("live-log-binding", [(
    "if token != archive_tail(frozen_tail):",
    "if token != archive_tail(WalEngine._freeze_log(log)[target:]):")])
NarrowArchiverBoundary = _source_mutant("narrow-archiver-boundary", [(
    "except BaseException:", "except Exception:")])
IsinstanceArchiverOutput = _source_mutant(
    "isinstance-archiver-output", [(
        "if type(out) is not str or", "if not isinstance(out, str) or")])
SkipsTailBinding = _source_mutant("skips-tail-binding", [(
    "if token != archive_tail(frozen_tail):", "if False:")])
SkipsSourceValidation = _source_mutant("skips-source-validation", [(
    "_WAL.replay(log)", "pass")])
SharedArchiverArgument = _source_mutant("shared-archiver-argument", [(
    "out = self.archiver(copy.deepcopy(frozen_tail))",
    "out = self.archiver(frozen_tail)")])
EntryShallowArchiverArgument = _source_mutant(
    "entry-shallow-archiver-argument", [(
        "out = self.archiver(copy.deepcopy(frozen_tail))",
        "out = self.archiver([dict(e) for e in frozen_tail])")])
RecordSharedArchiverArgument = _source_mutant(
    "record-shared-archiver-argument", [(
        "out = self.archiver(copy.deepcopy(frozen_tail))",
        "out = self.archiver([{**e, \"payload\": dict(e[\"payload\"])}"
        " for e in frozen_tail])")])

MUTANTS = {
    "accepts-all": AcceptsAll,
    "wrong-code": WrongCode,
    "unknown-as-malformed": _remap(UT, MRR),
    "malformed-as-unknown": _remap(MRR, UT),
    "corrupt-as-malformed": _remap(CS, MRR),
    "archive-as-corrupt": _remap(DA, CS),
    "double-archiver-call": DoubleArchiverCall,
    "unguarded-archiver": UnguardedArchiver,
    "archives-before-validation": ArchivesBeforeValidation,
    "no-commit": NoCommit,
    "over-truncate": OverTruncate,
    "under-truncate": UnderTruncate,
    "stale-rollback-id": StaleRollbackId,
    "swapped-heads": SwappedHeads,
    "mutates-request-on-success": MutatesRequestOnSuccess,
    "reorders-prefix-keys": ReordersPrefixKeys,
    "copies-prefix": CopiesPrefix,
    "cached-result": CachedResult,
    "live-archiver-argument": LiveArchiverArgument,
    "raw-request-key-set": RawRequestKeySet,
    "raw-entry-key-set": RawEntryKeySet,
    "raw-on-non-list-log": RawOnNonListLog,
    "no-log-restore": NoLogRestore,
    "no-request-restore": NoRequestRestore,
    "live-request-reread": LiveRequestReread,
    "live-log-binding": LiveLogBinding,
    "narrow-archiver-boundary": NarrowArchiverBoundary,
    "isinstance-archiver-output": IsinstanceArchiverOutput,
    "skips-tail-binding": SkipsTailBinding,
    "skips-source-validation": SkipsSourceValidation,
    "shared-archiver-argument": SharedArchiverArgument,
    "entry-shallow-archiver-argument": EntryShallowArchiverArgument,
    "record-shared-archiver-argument": RecordSharedArchiverArgument,
    "prefix-head-peek": PrefixHeadPeek,
    "raw-prefix-key-set": RawPrefixKeySet,
}

MUTANT_TARGETS = {
    "accepts-all": "malformed:log-not-a-list",
    "wrong-code": "malformed:log-not-a-list",
    "unknown-as-malformed": "malformed:target-negative",
    "malformed-as-unknown": "malformed:log-not-a-list",
    "corrupt-as-malformed": "malformed:sequence-gap",
    "archive-as-corrupt": "malformed:oracle-raises",
    "double-archiver-call": "happy:rollback-single-entry-tail",
    "unguarded-archiver": "happy:rollback-single-entry-tail",
    "archives-before-validation": "happy:rollback-single-entry-tail",
    "no-commit": "happy:rollback-single-entry-tail",
    "over-truncate": "happy:rollback-single-entry-tail",
    "under-truncate": "happy:rollback-single-entry-tail",
    "stale-rollback-id": "happy:rollback-single-entry-tail",
    "swapped-heads": "happy:rollback-single-entry-tail",
    "mutates-request-on-success": "happy:rollback-single-entry-tail",
    "reorders-prefix-keys": "happy:rollback-single-entry-tail",
    "copies-prefix": "happy:rollback-single-entry-tail",
    "cached-result": "happy:rollback-single-entry-tail",
    "live-archiver-argument": "happy:rollback-single-entry-tail",
    "raw-request-key-set": "totality:request-dict-subclass",
    "raw-entry-key-set": "totality:entry-dict-subclass-prefix",
    "raw-on-non-list-log": "malformed:log-not-a-list",
    "no-log-restore": "totality:archiver-mutates-surviving-entry",
    "no-request-restore": "totality:archiver-mutates-request-target",
    "live-request-reread": "totality:archiver-mutates-request-target",
    "live-log-binding": "totality:archiver-mutates-tail-entry",
    "narrow-archiver-boundary": "totality:archiver-raises-keyboard-interrupt",
    "isinstance-archiver-output": "totality:archiver-returns-str-subclass",
    "skips-tail-binding": "malformed:oracle-unbound-token",
    "skips-source-validation": "malformed:sequence-gap",
    "shared-archiver-argument": "totality:archiver-mutates-own-argument-entry",
    "entry-shallow-archiver-argument":
        "totality:archiver-mutates-own-argument-payload",
    "record-shared-archiver-argument":
        "totality:archiver-mutates-own-argument-record",
    "prefix-head-peek": "totality:entry-dict-subclass-prefix",
    "raw-prefix-key-set": "totality:entry-key-entry_id-str-subclass-prefix",
}  # GENERATED-CHECKED


# -- kill-proof: substitution mutants vs the closure ---------------------------
def _payload(row):
    return {k: v for k, v in row.items() if k != "name"}


def _erasures(section, row):
    """Single-edge erasures of ROW with the pinned receipt
    regenerated from the reference so only the closure can kill
    them. An erasure equal to its row is an equivalent mutant and
    is dropped."""
    out = []
    if section in ("happy", "boundary"):
        for label, target in (("target-zero", 0),
                              ("target-tip", len(row["log"]))):
            m = copy.deepcopy(row)
            m["request"] = {"target_sequence": target}
            m["expect"] = _reference_rollback(m["oracle"], m["log"],
                                              m["request"])
            out.append((label, m))
        regrown = copy.deepcopy(row)
        regrown["log"] = _log_of(("put", KINGS), ("put", AFTER_E4),
                                 ("put", STARTPOS))
        regrown["expect"] = _reference_rollback(
            regrown["oracle"], regrown["log"], regrown["request"])
        out.append(("log-regrown", regrown))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        rep = _repaired(row)
        fixed.update(log=rep["log"], request=rep["request"],
                     oracle=rep["oracle"])
        out.append(("defect-repaired", fixed))
    else:
        unfixed = copy.deepcopy(row)
        unfixed.update(then_log=copy.deepcopy(row["log"]),
                       then_request=copy.deepcopy(row["request"]),
                       then_oracle=row["oracle"])
        out.append(("follow-up-not-fixed", unfixed))
        swapped = copy.deepcopy(row)
        swapped["oracle"], swapped["then_oracle"] = \
            row["then_oracle"], row["oracle"]
        out.append(("oracles-swapped", swapped))
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
    rows = [_payload(r) for s in MANIFESTS for r in CASES[s]]
    assert all(a != b for i, a in enumerate(rows)
               for b in rows[i + 1:])


def test_probe_manifest_closed_and_ordered():
    assert list(PROBES) == list(PROBE_MANIFEST)
    assert len(PROBE_MANIFEST) == PROBE_COUNT


def test_order_probe_is_discriminating():
    log, request = _order_inputs()
    _check_receipt(ORDER_LABEL, log, request, ORDER_EXPECT)
    tail = log[request["target_sequence"]:]
    assert len(tail) == 3
    assert archive_tail(tail) != archive_tail(tail[::-1])
    assert len({e["op"] for e in tail}) == 2


def test_probe_expect_is_the_reference_receipt():
    _check_receipt("probe", *_probe_inputs(), PROBE_EXPECT)
    assert PROBE_EXPECT["truncated_count"] == 2


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(RollbackEngine, executed=executed) == []
    assert executed == [f"{s}:{m[0]}" for s, man in MANIFESTS.items()
                        for m in man] + [
        f"totality:{n}" for n in PROBE_MANIFEST] + [ORDER_LABEL]


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_red(name):
    assert _probe(MUTANTS[name], first_only=True), \
        f"{name}: mutant passed battery"


def test_mutant_targets_closed():
    assert set(MUTANT_TARGETS) == set(MUTANTS)


def test_mutants_fail_on_their_target_rows():
    for name, label in MUTANT_TARGETS.items():
        failures = _probe(MUTANTS[name], only={label})
        assert [":".join(f.split(":")[:2]) for f in failures] == \
            [label], (name, failures)


def test_reference_green_on_every_mutant_target():
    assert _probe(RollbackEngine, only=set(MUTANT_TARGETS.values())) \
        == []


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
    assert len(mutants) > 450
    survivors = []
    for label, m in mutants:
        try:
            _validate_closure(m)
        except (AssertionError, ValueError, KeyError, TypeError):
            continue
        survivors.append(label)
    assert survivors == [], survivors
