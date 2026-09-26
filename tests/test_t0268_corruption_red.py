"""T0268 permanent red battery for store-corruption engines.

The battery drives EVERY row of the T0267 conformance fixture
(tests/fixtures/corruption/cases.json), a closed set of totality
probes and pinned accept probes through an engine class
constructed with a quarantine sink:

- happy/boundary: the pinned receipt exactly (exact field types);
  clean: the log and request _snap-identical and the sink never
  called; salvaged: the SAME log list truncated to exactly the
  verified prefix, every kept entry the SAME object and
  _snap-identical, exactly ONE sink call with a DETACHED by-value
  copy of the corrupt suffix; the request untouched by value AND
  identity; nothing of the receipt aliased with any input;
  determinism by value over fresh copies on the SAME instance
  (never the same object); and idempotency (rescanning the
  salvaged log is clean at the pinned head and count, the sink not
  called again);
- malformed: the original input rejects with the pinned failure
  class and its mapped code, log and request untouched, the sink
  called only for divergent_quarantine (exactly once), and the
  declared minimal repair is accepted with the receipt the
  reference derives for it;
- rollback: a rejection leaves log and request untouched, then the
  valid follow-up (on the SAME engine instance when the sink is
  unchanged) returns the pinned receipt;
- totality: hostile requests and max_loss values, hostile logs,
  out-of-domain values and in-domain WAL corruption in the FIRST,
  MIDDLE AND LAST entry at every nesting depth, str-subclass and
  colliding-hash keys, dict/list subclasses, cycles, aliases,
  depth and int-range edges, and hostile sink behavior (raising
  any BaseException, non-str/str-subclass/bad-grammar/unbound
  output, sinks that mutate the caller's log or request or their
  own argument at every depth mid-call - overwriting, clearing,
  popping, appending AND adding a foreign key - or mutate then
  raise) each reject with the pinned failure class or, for accept
  probes, return the pinned receipt with exactly the pinned commit
  and, on the SAME instance, still reject a bad request typed and
  still accept a clean salvage - any other BaseException escaping
  is a failure.

Standalone-red convention (T0151, T0178, T0187, T0196, T0232,
T0241, T0250, T0259): the battery is permanently GREEN against the
contract-derived reference engine from
tests.test_t0266_corruption_contract and every mutant below is
RED. The production task switches the binding by replacing ONLY
the two binding lines below with the production
CorruptionEngine / CorruptionError names; no assertion changes.
Reference-source mutants raise the BOUND error class (see
_source_mutant), guarded by an identity mutant that must pass.

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

from tests import test_t0266_corruption_contract as _reference  # noqa: E402
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
from tests.test_t0266_corruption_contract import (  # noqa: E402
    quarantine_suffix,
)
from tests.test_t0267_corruption_fixture import (  # noqa: E402
    BOUNDARY_MANIFEST as _T0267_BOUNDARY,
)
from tests.test_t0267_corruption_fixture import (  # noqa: E402
    HAPPY_MANIFEST as _T0267_HAPPY,
)
from tests.test_t0267_corruption_fixture import (  # noqa: E402
    MALFORMED_MANIFEST as _T0267_MALFORMED,
)
from tests.test_t0267_corruption_fixture import (  # noqa: E402
    ORACLES,
    _assert_boundary_edge,
    _assert_cardinalities,
    _assert_happy_edge,
    _assert_malformed_scenario,
    _assert_then_relation,
    _fail_closed,
    _repaired,
    _validate_log_shape,
    _validate_receipt_shape,
    _validate_repair,
    _validate_request_shape,
)
from tests.test_t0267_corruption_fixture import (  # noqa: E402
    ROLLBACK_MANIFEST as _T0267_ROLLBACK,
)
from tools.corruption_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
    MAX_DEPTH,
    MAX_INT_BITS,
)

# -- binding (the production task replaces ONLY these two lines) ---------------
CorruptionEngine = __import__("store.corruption").corruption.CorruptionEngine
CorruptionError = __import__("store.corruption").corruption.CorruptionError

FIXTURE = (Path(__file__).parent / "fixtures" / "corruption"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECEIPT_FIELDS = ("scan_id", "verdict", "verified_head",
                  "verified_count", "quarantined_count",
                  "quarantine_token")
MCR = "malformed_corruption_record"
EL = "excessive_loss"
DQ = "divergent_quarantine"
# failure classes raised BEFORE the sink may be called
PRE_SINK = frozenset({MCR, EL})

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, t0267 cardinalities (oracle, verdict,
# verified_count, quarantined_count), pins) where pins = (log
# length, max_loss, per-entry position S=STARTPOS K=KINGS
# E=AFTER_E4 - for a non-dict or record-less entry, per-entry op
# or entry type name)
HAPPY_MANIFEST = (
    ("clean-three-entry-log", ("honest", "clean", 3, 0),
     (3, 0, ("S", "K", "E"), ("put", "put", "put"))),
    ("salvage-tampered-tail-entry", ("honest", "salvaged", 2, 1),
     (3, 1, ("S", "K", "E"), ("put", "put", "put"))),
    ("salvage-torn-write-mid-log", ("honest", "salvaged", 1, 2),
     (3, 2, ("S", "-", "E"), ("put", "-", "put"))),
    ("salvage-garbage-appended", ("honest", "salvaged", 2, 1),
     (3, 4, ("S", "K", "-"), ("put", "put", "str"))),
)
BOUNDARY_MANIFEST = (
    ("clean-empty-log", ("honest", "clean", 0, 0),
     (0, 0, (), ())),
    ("salvage-to-genesis", ("honest", "salvaged", 0, 3),
     (3, 3, ("S", "K", "E"), ("put", "put", "put"))),
    ("loss-exactly-at-bound", ("honest", "salvaged", 1, 2),
     (3, 2, ("S", "K", "E"), ("put", "put", "put"))),
    ("int-at-range-edge-in-suffix", ("honest", "salvaged", 2, 1),
     (3, 1, ("S", "K", "E"), ("put", "put", "put"))),
    ("depth-at-limit-in-suffix", ("honest", "salvaged", 2, 1),
     (3, 1, ("S", "K", "E"), ("put", "put", "put"))),
)
# malformed: (name, failure class, oracle, repair form, closed
# scenario tag)
MALFORMED_MANIFEST = (
    ("log-not-a-list", MCR, "honest", "replace_log",
     "non-list-source-log"),
    ("request-missing-field", MCR, "honest", "replace_request",
     "missing-request-field"),
    ("request-extra-field", MCR, "honest", "replace_request",
     "extra-request-field"),
    ("max-loss-non-int", MCR, "honest", "set_request_field",
     "non-int-max-loss"),
    ("max-loss-bool", MCR, "honest", "set_request_field",
     "bool-max-loss"),
    ("max-loss-negative", MCR, "honest", "set_request_field",
     "negative-max-loss"),
    ("log-float-leaf", MCR, "honest", "replace_log", "float-leaf"),
    ("log-int-over-range", MCR, "honest", "replace_log",
     "int-over-range"),
    ("log-over-depth", MCR, "honest", "replace_log", "over-depth"),
    ("loss-one-over-bound", EL, "honest", "set_request_field",
     "excessive-loss-by-one"),
    ("loss-zero-bound-on-corrupt-log", EL, "honest",
     "set_request_field", "excessive-loss-zero-bound"),
    ("sink-raises", DQ, "raising", "set_oracle", "sink-raises"),
    ("sink-raises-keyboard-interrupt", DQ,
     "raising_keyboard_interrupt", "set_oracle",
     "sink-raises-base-exception"),
    ("sink-non-str-output", DQ, "non_str_output", "set_oracle",
     "sink-non-str-output"),
    ("sink-bad-grammar", DQ, "bad_grammar", "set_oracle",
     "sink-bad-grammar"),
    ("sink-unbound-token", DQ, "unbound_token", "set_oracle",
     "sink-unbound-token"),
    ("sink-lone-surrogate", DQ, "lone_surrogate", "set_oracle",
     "sink-lone-surrogate"),
)
# rollback: (name, failure class, oracle, follow-up oracle,
# follow-up (verified_count, quarantined_count), then_log length,
# follow-up max_loss, closed then-relation)
_RB = "rejected-"
ROLLBACK_MANIFEST = (
    (_RB + "salvage-raising-sink-then-valid-salvage", DQ, "raising",
     "honest", (2, 1), 3, 1, "same-inputs"),
    (_RB + "salvage-keyboard-interrupt-sink-then-valid-salvage", DQ,
     "raising_keyboard_interrupt", "honest", (2, 2), 4, 2,
     "same-inputs"),
    (_RB + "salvage-excessive-loss-then-valid-salvage", EL, "honest",
     "honest", (1, 2), 3, 2, "raised-max-loss"),
    (_RB + "scan-out-of-domain-then-valid-salvage", MCR, "honest",
     "honest", (2, 1), 3, 1, "leaf-repaired-log"),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:clean-three-entry-log":
        "8db26a840d1c24ecbc737c5d746a8f9780f598635d30b8bd0708ab0ba198bb9f",
    "happy:salvage-tampered-tail-entry":
        "1bae79c089da04f3a6833424ebe0562d2440dc6fdb5f2fc30187d57fdd2d5350",
    "happy:salvage-torn-write-mid-log":
        "a5a989dc1e1bcdaa849eba9c2f44037eb7a4ad8c3372f00cb080879949f2cde8",
    "happy:salvage-garbage-appended":
        "6ec5355ef48098d4e74bcc790143425c2447814bc8cb514a8cd0fdd337e225a5",
    "boundary:clean-empty-log": "4e1aab3edb0641a139374f8184b179e21274c988aec7b0c325125c27c905b231",
    "boundary:salvage-to-genesis":
        "7a2f1bf89a0e010b3041a45c7a36c39735d0cefca246643345cd5892798e5850",
    "boundary:loss-exactly-at-bound":
        "64efb9bca3ef9d57ba401a7796580d2f5db641691efb88091feaf18ce56588d9",
    "boundary:int-at-range-edge-in-suffix":
        "8bca94b6e707e1bbce10e37e1845601b83d7f4abde50f0f7a5382eec1364474c",
    "boundary:depth-at-limit-in-suffix":
        "093a412583988bec47158587b97c3650910307c917acdc2c96d1beb4fb391073",
    "malformed:log-not-a-list": "c92d1ca1f6af4aa2b4a2b049e5511378c9c905ea406dc7d4de6c6ab2cff253b2",
    "malformed:request-missing-field":
        "7a18728ef03c2e073c23afa053806f7630c2b4bad3e6ca1d5de389cac382c0d1",
    "malformed:request-extra-field":
        "e544667d844bfda331a9ae617bd2e9125223163ab0ef69ad7025f1624c787289",
    "malformed:max-loss-non-int":
        "3523a5e90299c4eeb1145309b864ed25387304c7937ba641786155c6323bc7fa",
    "malformed:max-loss-bool": "3cd9a191bf34db886f1b73f29c8f43a61dc2fefe75d6e2f79473e30430367c39",
    "malformed:max-loss-negative":
        "72534e522a0e708351221ff64bf7f1f9d2610445a21812a676695bd5b854b5a0",
    "malformed:log-float-leaf": "30ad4c5ff9c65e74192340bf65f12f3dc02c994f50c27f328b01e10e093404dd",
    "malformed:log-int-over-range":
        "3d823bbef8edcaedde245be2935c4d17df8bad0e7e7385af02064561fd469a40",
    "malformed:log-over-depth": "c305391ab373ddcb00b51725086170e17feb880b5dbc528ade7f512240a6fef3",
    "malformed:loss-one-over-bound":
        "a6a9671b8e902b03415e1fc2b142b02debcc92168d3c1da9a81fe701e5658886",
    "malformed:loss-zero-bound-on-corrupt-log":
        "d923e1a0f0ce3acbec06ca073c6b6992e7d218b0fca7b95c80fd09abbdfff1e1",
    "malformed:sink-raises": "46521f3656d91e9bcafeedd3cce2cf8444c6b4637ca9af8fc420420852e91fe0",
    "malformed:sink-raises-keyboard-interrupt":
        "3309518d1d25003b7ef38e3d333b16bf265542129187bde66e5f27e84c83e31c",
    "malformed:sink-non-str-output":
        "aa844ae25e8f9b258f6684b20ad586e9de2b57114258693f8f06640e8aba34a0",
    "malformed:sink-bad-grammar":
        "13ce01c6821d301c9a1c9b237bd9bbf2d3c2b5640f1631e6d3dc188275e06308",
    "malformed:sink-unbound-token":
        "d50aeb376a9bdc3a6db5c28dedd8509d909a28a63d4c88b70d827e6aed7c14f8",
    "malformed:sink-lone-surrogate":
        "e9f8ffeece6ac05ed3ccf2bc4a11e70590cad9b83576441820e102ce68fc5550",
    "rollback:rejected-salvage-raising-sink-then-valid-salvage":
        "b892f994d4e6ff2b9d6ca87e85ed99e264e579b04108c3e3c64dd108ef0a6701",
    "rollback:rejected-salvage-keyboard-interrupt-sink-then-valid-salvage":
        "c21839ce5f6e9cc269243ec7a9fb376bb62aed2fc4502f6048fef6ca974fa3d3",
    "rollback:rejected-salvage-excessive-loss-then-valid-salvage":
        "0a7f22e0fb74a26ea6d31c95cacf9860d8262a9d6ef938723e1942af38a0496f",
    "rollback:rejected-scan-out-of-domain-then-valid-salvage":
        "888da62928fb1df17743e37f8259db8d644bf674fd4aec3b5217e1133b080ded",
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
def _wal_head(log):
    """Head of LOG under the linked WAL, or None."""
    if not log:
        return GENESIS
    try:
        return WalEngine(canonical_payload).replay(
            copy.deepcopy(log))["head"]
    except WalError:
        return None


def _local_prefix(log):
    """(count, head) of the longest WAL-verified prefix by LINEAR
    search - independent of the reference's binary search."""
    count, head = 0, GENESIS
    for k in range(1, len(log) + 1):
        h = _wal_head(log[:k])
        if h is None:
            break
        count, head = k, h
    return count, head


def _reference_scan(oracle, log, request):
    return _reference.CorruptionEngine(ORACLES[oracle]).scan(
        copy.deepcopy(log), copy.deepcopy(request))


def _reference_failure(oracle, log, request):
    try:
        _reference_scan(oracle, log, request)
    except _reference.CorruptionError as error:
        return error.failure_class
    return None


def _check_receipt(name, log, request, expect):
    """The pinned receipt realizes the contract over the ORIGINAL
    inputs: the verified prefix and head by a LOCAL linear WAL
    search, the loss within max_loss, the token the local canonical
    suffix derivation (None when clean), the scan id derived from
    every field."""
    assert set(expect) == set(RECEIPT_FIELDS), name
    count, head = _local_prefix(log)
    lost = len(log) - count
    assert lost <= request["max_loss"], name
    verdict = "clean" if lost == 0 else "salvaged"
    token = None if lost == 0 else quarantine_suffix(log[count:])
    assert (expect["verdict"], expect["verified_head"],
            expect["verified_count"], expect["quarantined_count"],
            expect["quarantine_token"]) == \
        (verdict, head, count, lost, token), name
    assert expect["scan_id"] == \
        _reference.CorruptionEngine._derive_scan_id(
            verdict, head, count, lost, token), name


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


def _pins(log, request):
    return (len(log), request["max_loss"],
            tuple(_position(e) for e in log),
            tuple(_shape(e) for e in log))


def _check_ok(section, case, cards, pins):
    name = case["name"]
    log, request = case["log"], case["request"]
    assert (_T0267_HAPPY if section == "happy"
            else _T0267_BOUNDARY)[name] == cards, name
    _validate_log_shape(log, name)
    _validate_request_shape(request, name)
    _validate_receipt_shape(case["expect"], name)
    _assert_cardinalities(case, cards)
    _fail_closed(_assert_boundary_edge if section == "boundary"
                 else _assert_happy_edge, case)
    _check_receipt(name, log, request, case["expect"])
    assert _pins(log, request) == pins, name


def _check_malformed(case, meta):
    name, failure, oracle, form, tag = meta
    assert case["expect_failure"] == failure, name
    assert case["oracle"] == oracle, name
    assert list(case["minimal_repair"]) == [form], name
    assert case["scenario"] == tag, name
    assert _T0267_MALFORMED[name] == (failure, oracle, form,
                                      case["defect"], tag), name
    _validate_repair(case)
    _assert_malformed_scenario(case, tag)
    assert _reference_failure(oracle, case["log"],
                              case["request"]) == failure, name
    fixed = _repaired(case)
    _check_receipt(name, fixed["log"], fixed["request"],
                   _reference_scan(fixed["oracle"], fixed["log"],
                                   fixed["request"]))


def _check_rollback(case, meta):
    (name, failure, oracle, then_oracle, counts, then_len, then_loss,
     relation) = meta
    assert case["expect_failure"] == failure, name
    assert case["oracle"] == oracle, name
    assert case["then_oracle"] == then_oracle, name
    assert _T0267_ROLLBACK[name] == (failure, oracle, then_oracle,
                                     *counts, then_len, relation), name
    _validate_request_shape(case["request"], name)
    _validate_log_shape(case["then_log"], name)
    _validate_request_shape(case["then_request"], name)
    _validate_receipt_shape(case["expect"], name)
    assert _reference_failure(oracle, case["log"],
                              case["request"]) == failure, name
    _check_receipt(name, case["then_log"], case["then_request"],
                   case["expect"])
    assert case["expect"]["verdict"] == "salvaged", name
    assert (case["expect"]["verified_count"],
            case["expect"]["quarantined_count"]) == counts, name
    assert len(case["then_log"]) == then_len, name
    assert case["then_request"]["max_loss"] == then_loss, name
    _assert_then_relation(case, relation)


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
    """Wraps a sink; records every call's suffix BY VALUE and the
    container ids it was handed."""

    def __init__(self, sink):
        self.sink, self.calls = sink, []

    def __call__(self, suffix):
        try:
            value = copy.deepcopy(suffix)
        except BaseException:  # noqa: BLE001
            value = None
        self.calls.append((value, _containers(suffix)))
        return self.sink(suffix)


def _engine(cls, oracle):
    counter = _Counting(ORACLES[oracle] if isinstance(oracle, str)
                        else oracle)
    return cls(counter), counter


def _receipt_ok(receipt, expect):
    return (type(receipt) is dict and receipt == expect
            and list(receipt) == list(RECEIPT_FIELDS)
            and all(type(receipt[f]) is str
                    for f in ("scan_id", "verdict", "verified_head"))
            and type(receipt["verified_count"]) is int
            and type(receipt["quarantined_count"]) is int
            and (receipt["quarantine_token"] is None
                 or type(receipt["quarantine_token"]) is str))


def _checked_scan(engine, counter, log, request, expect):
    """One successful scan: the pinned receipt; clean - log
    _snap-identical, no sink call; salvaged - the SAME log list
    truncated to the verified prefix, every kept entry the SAME
    object and _snap-identical, exactly one sink call with a
    detached by-value copy of the corrupt suffix; request
    untouched; nothing of the receipt aliased with any input."""
    log_id = id(log)
    count = expect["verified_count"]
    kept_ids = [id(entry) for entry in log[:count]]
    kept = [_snap(entry) for entry in log[:count]]
    whole = _snap(log)
    req_snap = _snap(request)
    want_suffix = copy.deepcopy(log[count:])
    live = _containers(log) | _containers(request)
    n_before = len(counter.calls)
    receipt = engine.scan(log, request)
    calls = counter.calls[n_before:]
    ok = (_receipt_ok(receipt, expect)
          and id(log) == log_id and type(log) is list
          and _snap(request) == req_snap
          and _not_aliased(receipt, (log, request)))
    if not ok:
        return receipt, False
    if expect["verdict"] == "clean":
        return receipt, _snap(log) == whole and calls == []
    ok = (len(log) == count
          and [id(entry) for entry in log] == kept_ids
          and [_snap(entry) for entry in log] == kept
          and len(calls) == 1 and calls[0][0] == want_suffix
          and type(calls[0][0]) is list
          and not calls[0][1] & live)
    return receipt, ok


def _clean_after(expect):
    """The clean receipt a rescan of the salvaged log must
    return."""
    head, count = expect["verified_head"], expect["verified_count"]
    return {"scan_id": _reference.CorruptionEngine._derive_scan_id(
                "clean", head, count, 0, None),
            "verdict": "clean", "verified_head": head,
            "verified_count": count, "quarantined_count": 0,
            "quarantine_token": None}


def _scan_ok(cls, case, prefix="", engine=None):
    """Pinned receipt and commit, determinism BY VALUE on the SAME
    instance (a repeat over fresh copies never returns the same
    object), and idempotency: rescanning the committed log with
    the same request is clean at the pinned head, no sink call."""
    oracle = case[prefix + "oracle"]
    eng, counter = engine or _engine(cls, oracle)
    results, logs = [], []
    for _ in range(2):
        log = copy.deepcopy(case[prefix + "log"])
        receipt, ok = _checked_scan(
            eng, counter, log, copy.deepcopy(case[prefix + "request"]),
            case["expect"])
        if not ok:
            return False
        results.append(receipt)
        logs.append(log)
    if results[1] is results[0]:
        return False
    _, ok = _checked_scan(eng, counter, logs[1],
                          copy.deepcopy(case[prefix + "request"]),
                          _clean_after(case["expect"]))
    return ok


def _rejects(engine, counter, log, request, failure):
    inputs = (log, request)
    snap = _snap(inputs)
    n_before = len(counter.calls)
    try:
        engine.scan(log, request)
    except CorruptionError as error:
        calls = len(counter.calls) - n_before
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and error.code in ERROR_ENUM
                and _snap(inputs) == snap
                and calls == (0 if failure in PRE_SINK else 1))
    return False


def _malformed_ok(cls, case):
    eng, counter = _engine(cls, case["oracle"])
    if not _rejects(eng, counter, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    fixed = _repaired(case)
    expect = _reference_scan(fixed["oracle"], fixed["log"],
                             fixed["request"])
    if fixed["oracle"] != case["oracle"]:
        eng, counter = _engine(cls, fixed["oracle"])
    _, ok = _checked_scan(eng, counter, copy.deepcopy(fixed["log"]),
                          copy.deepcopy(fixed["request"]), expect)
    return ok


def _rollback_row_ok(cls, case):
    engine = _engine(cls, case["oracle"])
    if not _rejects(*engine, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    same = case["then_oracle"] == case["oracle"]
    return _scan_ok(cls, case, "then_", engine if same else None)


_RUNNERS = {"happy": _scan_ok, "boundary": _scan_ok,
            "malformed": _malformed_ok, "rollback": _rollback_row_ok}


# -- closed totality probes ----------------------------------------------------
_FORGED = "wal1:" + "f" * 64
_BITS = 2 ** MAX_INT_BITS  # bit_length MAX_INT_BITS + 1: out of range


def _base_log():
    return _log_of(("put", STARTPOS), ("put", KINGS), ("put", AFTER_E4))


def _salvage_inputs():
    """Salvage path: a three-entry log whose tail entry id is
    forged; max_loss 1 - every probe corrupts one component (or
    attacks through the sink)."""
    log = _base_log()
    log[2]["entry_id"] = _FORGED
    return log, {"max_loss": 1}


def _clean_inputs():
    return _base_log(), {"max_loss": 0}


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
    """Salvage log, honest sink, request replaced by FN(request)."""
    def build():
        log, request = _salvage_inputs()
        return log, fn(request), "honest"
    return build


def _log(fn, max_loss=3):
    def build():
        return fn(_base_log()), {"max_loss": max_loss}, "honest"
    return build


def _entry(fn, index, max_loss=3):
    """Base log with entry INDEX replaced by FN(entry, log)."""
    def edit(log):
        log[index] = fn(log[index], log)
        return log
    return _log(edit, max_loss)


def _sink(sink, inputs=None):
    def build():
        log, request = (inputs or _salvage_inputs)()
        return log, request, sink
    return build


def _set(path, value):
    """fn(entry, log) setting entry[path...] = VALUE(entry, log)
    on a copy-free walk of the live entry."""
    def fn(entry, log):
        node = entry
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value(entry, log) if callable(value) else value
        return entry
    return fn


# the digest VALUE sits at nesting depth 5: log(1) > entry(2) >
# payload(3) > record(4) > digest(5)
_DEPTH_OF_DIGEST = 5

ACCEPT = "accept"
_LIVE_ATTACKS = (
    "mutates-first-entry", "mutates-last-entry", "mutates-first-record",
    "appends-entry", "pops-entry", "clears-log", "replaces-first-entry",
    "reorders-entry-keys", "adds-entry-key", "adds-payload-key",
    "adds-record-key", "mutates-max-loss", "clears-request",
    "adds-request-key", "own-argument-appends", "own-argument-clears",
    "own-argument-entry", "own-argument-adds-entry-key",
    "own-argument-payload", "own-argument-record")


def _attack(kind, log, request, suffix):
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
    if kind in ("mutates-max-loss", "mutates-then-raises"):
        dict.__setitem__(request, "max_loss", 0)
    if kind == "clears-request":
        dict.clear(request)
    if kind == "adds-request-key":
        dict.__setitem__(request, "x", 1)
    # own-argument attacks, one per depth: an engine sharing ANY
    # level of the frozen suffix with the sink is caught
    if kind == "own-argument-appends":
        list.append(suffix, "x")
    if kind == "own-argument-clears":
        list.clear(suffix)
    if kind == "own-argument-entry":
        dict.__setitem__(suffix[0], "op", "x")
    if kind == "own-argument-adds-entry-key":
        dict.__setitem__(suffix[0], "x", 1)
    if kind == "own-argument-payload":
        dict.__setitem__(suffix[0]["payload"], "identity", "x")
    if kind == "own-argument-record":
        dict.__setitem__(suffix[0]["payload"]["record"], "digest", "x")


def _live(kind):
    def build():
        log, request = _salvage_inputs()

        def sink(suffix):
            token = quarantine_suffix(suffix)
            _attack(kind, log, request, suffix)
            if kind == "mutates-then-raises":
                raise ValueError("mutate then explode")
            return token
        return log, request, sink
    return build


def _raises(exc):
    def sink(suffix):
        raise exc("hostile sink")
    return sink


def _returns(value):
    def sink(suffix):
        return value(suffix) if callable(value) else value
    return sink


class _TokenSub(str):
    pass


_ENTRY_KEYS = ("sequence", "op", "entry_id", "prior_entry_id",
               "payload")
_RECORD_PATH = ("payload", "record")


# in-domain leaf edges the canonical encoding must frame exactly
_LEAF_EDGES = (("true", True), ("false", False), ("empty-str", ""),
               ("utf8-2-byte", "\u00e9"), ("utf8-3-byte", "\u20ac"),
               ("utf8-4-byte", "\U0001d11e"))


def _entry_family(out, index, suffix):
    """Entry probes against log entry INDEX: 0 is the FIRST, 1 the
    MIDDLE, 2 the LAST - the scan domain and WAL judgment cover the
    FULL log, at every nesting depth."""
    def entry(fn, max_loss=3):
        return _entry(fn, index, max_loss)

    # out of the closed scan domain: malformed at every depth
    for label, value in (("float", 1.0), ("bytes", b"x"),
                         ("tuple", ()), ("int-subclass", _IntSub(1)),
                         ("str-subclass", SK("x"))):
        out[f"entry-{label}-{suffix}"] = (
            entry(lambda e, g, v=value: v), MCR)
    out[f"entry-dict-subclass-{suffix}"] = (entry(
        lambda e, g: _DictSub(e)), MCR)
    out[f"entry-list-subclass-{suffix}"] = (entry(
        lambda e, g: _ListSub([1])), MCR)
    for key in _ENTRY_KEYS:
        for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
            out[f"entry-key-{key}-{label}-{suffix}"] = (entry(
                lambda e, g, k=key, c=cls: _rekey(e, k, c(k))), MCR)
    out[f"entry-key-int-{suffix}"] = (entry(
        lambda e, g: {**e, 7: "x"}), MCR)
    for label, value in (("float", 1.5), ("bytes", b"1"),
                         ("int-subclass", _IntSub(1)),
                         ("int-over-range", _BITS),
                         ("negative-int-over-range", -_BITS),
                         ("tuple", (1,))):
        out[f"entry-sequence-{label}-{suffix}"] = (
            entry(_set(("sequence",), value)), MCR)
    for label, value in (("str-subclass", SK("put")),
                         ("lone-surrogate", "\ud800")):
        out[f"entry-op-{label}-{suffix}"] = (
            entry(_set(("op",), value)), MCR)
    out[f"entry-payload-dict-subclass-{suffix}"] = (entry(
        _set(("payload",), lambda e, g: _DictSub(e["payload"]))), MCR)
    for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
        out[f"entry-payload-key-record-{label}-{suffix}"] = (entry(
            _set(("payload",), lambda e, g, c=cls:
                 _rekey(e["payload"], "record", c("record")))), MCR)
        out[f"entry-record-key-digest-{label}-{suffix}"] = (entry(
            _set(_RECORD_PATH, lambda e, g, c=cls:
                 _rekey(e["payload"]["record"], "digest",
                        c("digest")))), MCR)
    for label, value in (("float", 0.5), ("lone-surrogate", "\ud800"),
                         ("bytes", b"d"), ("int-over-range", _BITS),
                         ("str-subclass", SK("d"))):
        out[f"entry-record-digest-{label}-{suffix}"] = (
            entry(_set(_RECORD_PATH + ("digest",), value)), MCR)
    out[f"entry-self-referential-{suffix}"] = (entry(
        _set(_RECORD_PATH + ("digest",), lambda e, g: e)), MCR)
    out[f"entry-aliases-other-entry-{suffix}"] = (entry(
        _set(("payload",),
             lambda e, g: g[(index + 1) % 3]["payload"])), MCR)
    out[f"entry-over-depth-{suffix}"] = (entry(
        _set(_RECORD_PATH + ("digest",),
             _nest(MAX_DEPTH - _DEPTH_OF_DIGEST + 1))), MCR)
    # inside the domain, WAL-corrupt: salvaged back to INDEX (an
    # accept probe with its own pinned receipt), or excessive loss
    # one below the bound
    lost = 3 - index
    corruptions = (
        ("forged-entry-id", _set(("entry_id",), _FORGED)),
        ("sequence-shifted", _set(("sequence",), lambda e, g:
                                  e["sequence"] + 1)),
        ("prior-link-broken", _set(("prior_entry_id",), _FORGED)),
        ("op-changed", _set(("op",), "delete")),
        ("digest-altered", _set(_RECORD_PATH + ("digest",),
                                "pdv1:" + "0" * 64)),
        ("missing-field", lambda e, g: {k: v for k, v in e.items()
                                        if k != "prior_entry_id"}),
        ("extra-field", lambda e, g: {**e, "note": "x"}),
        ("garbage-text", lambda e, g: "garbage"),
        ("garbage-none", lambda e, g: None),
        ("garbage-list", lambda e, g: []),
        ("garbage-empty-dict", lambda e, g: {}),
        ("depth-at-limit", _set(_RECORD_PATH + ("digest",),
                                _nest(MAX_DEPTH - _DEPTH_OF_DIGEST))),
        ("int-at-range-edge", _set(("sequence",), _BITS - 1)),
        ("negative-int-at-range-edge", _set(("sequence",),
                                            -(_BITS - 1))),
        # bool and str leaf edges of the canonical encoding, inside
        # the domain: True / False are distinct leaves (never ints,
        # never each other); "" is a str; multi-byte UTF-8 is framed
        # by its BYTE length
        ("sequence-true", _set(("sequence",), True)),
        ("sequence-false", _set(("sequence",), False)),
        ("entry-true", lambda e, g: True),
        ("entry-false", lambda e, g: False))
    corruptions += tuple(
        (f"digest-{label}", _set(_RECORD_PATH + ("digest",), value))
        for label, value in _LEAF_EDGES)
    for label, fn in corruptions:
        out[f"wal-{label}-{suffix}"] = (entry(fn, lost), ACCEPT)
    out[f"wal-forged-entry-id-{suffix}-over-bound"] = (
        entry(_set(("entry_id",), _FORGED), lost - 1), EL)


def _probe_builders():
    out = {}
    for label, value in (("none", None), ("list", [["max_loss", 1]]),
                         ("text", "max_loss"), ("zero", 0),
                         ("tuple", (("max_loss", 1),))):
        out[f"request-{label}"] = (_request(lambda r, v=value: v), MCR)
    out["request-dict-subclass"] = (_request(_DictSub), MCR)
    out["request-empty"] = (_request(lambda r: {}), MCR)
    out["request-extra-field"] = (_request(
        lambda r: {**r, "note": "x"}), MCR)
    out["request-other-field"] = (_request(lambda r: {"loss": 1}), MCR)
    for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
        out[f"request-key-max_loss-{label}"] = (_request(
            lambda r, c=cls: _rekey(r, "max_loss", c("max_loss"))), MCR)
    for label, value in (("none", None), ("true", True),
                         ("false", False), ("text", "1"),
                         ("float", 1.0), ("negative", -1),
                         ("huge-negative", -(10 ** 5000)),
                         ("int-subclass", _IntSub(1)),
                         ("str-subclass", SK("1")), ("list", [1])):
        out[f"request-max-loss-{label}"] = (_request(
            lambda r, v=value: {"max_loss": v}), MCR)
    out["request-max-loss-huge"] = (_request(
        lambda r: {"max_loss": 10 ** 5000}), ACCEPT)
    out["request-max-loss-zero-on-corrupt-log"] = (_request(
        lambda r: {"max_loss": 0}), EL)
    for label, value in (("none", None), ("dict", {}), ("text", "x"),
                         ("zero", 0), ("tuple", ()), ("bytes", b"")):
        out[f"log-{label}"] = (_log(lambda g, v=value: v), MCR)
    out["log-list-subclass"] = (_log(lambda g: _ListSub(g)), MCR)
    out["log-self-referential"] = (_log(lambda g: g + [g]), MCR)
    out["log-aliased-entry"] = (_log(lambda g: g + [g[0]]), MCR)
    out["log-deep"] = (_log(lambda g: g + [_deep()]), MCR)
    out["log-huge-int"] = (_log(lambda g: g + [10 ** 5000]), MCR)
    out["log-clean-max-loss-zero"] = (_log(lambda g: g, 0), ACCEPT)
    out["log-empty"] = (_log(lambda g: [], 0), ACCEPT)
    for index, suffix in ((0, "first"), (1, "middle"), (2, "last")):
        _entry_family(out, index, suffix)
    for label, exc in (("exception", ValueError),
                       ("keyboard-interrupt", KeyboardInterrupt),
                       ("system-exit", SystemExit),
                       ("generator-exit", GeneratorExit)):
        out[f"sink-raises-{label}"] = (_sink(_raises(exc)), DQ)
        # the sink is never called on a clean scan or before a
        # malformed / excessive-loss rejection
        out[f"sink-raises-{label}-on-clean-log"] = (
            _sink(_raises(exc), _clean_inputs), ACCEPT)
    out["sink-raises-on-excessive-loss"] = (_sink(
        _raises(ValueError), lambda: (_salvage_inputs()[0],
                                      {"max_loss": 0})), EL)
    out["sink-raises-on-malformed-request"] = (_sink(
        _raises(ValueError), lambda: (_salvage_inputs()[0],
                                      {"max_loss": -1})), MCR)
    for label, value in (
            ("none", None), ("zero", 0), ("list", []),
            ("bytes", lambda s: quarantine_suffix(s).encode()),
            ("str-subclass",
             lambda s: _TokenSub(quarantine_suffix(s))),
            ("uppercase", lambda s: quarantine_suffix(s).upper()),
            ("trailing-newline", lambda s: quarantine_suffix(s) + "\n"),
            ("bad-grammar", "qrn1:zz"),
            ("unbound-token", "qrn1:" + "f" * 64),
            ("full-log-token",
             lambda s: quarantine_suffix(_salvage_inputs()[0])),
            ("empty-suffix-token", lambda s: quarantine_suffix([])),
            ("prefix-token",
             lambda s: quarantine_suffix(_salvage_inputs()[0][:2])),
            ("lone-surrogate", "\ud800"),
            ("surrogate-in-token", "qrn1:" + "a" * 63 + "\ud800")):
        out[f"sink-returns-{label}"] = (_sink(_returns(value)), DQ)
    out["sink-mutates-then-raises"] = (_live("mutates-then-raises"), DQ)
    for kind in _LIVE_ATTACKS:
        out[f"sink-{kind}"] = (_live(kind), ACCEPT)
    return out


PROBE_EXPECT = {
    "request-max-loss-huge": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "log-clean-max-loss-zero": {
        "scan_id": "crp1:37b79e6b0315c081ecc8e1d13d54fd3a65db27d36ed35e1e903efd392a1a891e",
        "verdict": "clean",
        "verified_head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "verified_count": 3,
        "quarantined_count": 0,
        "quarantine_token": None,
    },
    "log-empty": {
        "scan_id": "crp1:0b9df618dc14bc5791718878b37f7a0dd5a38522c264b59fbd91253a29a4ee26",
        "verdict": "clean",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 0,
        "quarantine_token": None,
    },
    "wal-forged-entry-id-first": {
        "scan_id": "crp1:d850b59a24d6dc359536cafb55e69d74874ea2479dacf7aa5c2c7dac57364174",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:16743bedb2a470d0be969650c31b68b56150e3803d10a967ae5531ced6e5abfe",
    },
    "wal-sequence-shifted-first": {
        "scan_id": "crp1:f32b1e6faac0412b5fe9233e4c21fe1e6094e2988e876f8065546e821a562a18",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:67eb430d09c53a9335a1f16127b16306d36a2bb74895a42e02b74525482af33a",
    },
    "wal-prior-link-broken-first": {
        "scan_id": "crp1:7d32954c7af8a184e9f84c4e7ac49745f70f1a7e4bfc011a4abd4b5be9e2035f",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:2fc444d32f9d0be7435f444d98c7eb41f44c3ae164d3e904fcae0bdc461eef55",
    },
    "wal-op-changed-first": {
        "scan_id": "crp1:2d6be85f2788f7fb5d3ccfb487dc8c5904fb2d5b2aa3a0d70f6b4bddab24446c",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:30c9990b7b2e264572583caa5b53ec3c8f4339b2440709fb4f32cc6823fe61af",
    },
    "wal-digest-altered-first": {
        "scan_id": "crp1:2a8e3af855af58c21ada66cc2960fba2fdb5a15fc1891f35f0554600cea25d11",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:f48f137cf688d5aeafdc6d241a370390423c2ccd0c49e7d84d619a645a812b4e",
    },
    "wal-missing-field-first": {
        "scan_id": "crp1:6220ebf63047da89dfe0b2b0857da3bbeff064356ab7ae9a7cd393cbc1a26e5d",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:d87ab75e33bb6fe3071f2772e9faead1952b34a396ec50ffd31e07ddab7d0ce2",
    },
    "wal-extra-field-first": {
        "scan_id": "crp1:5ad0fc5a72a300c148a447a25fe4aacc21e86e0c9ac0b1ec951d9663b250cb6d",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:882542decd0d148d0f11c9d4091c20b496139c5ccb4cb5be8460a7c89b731139",
    },
    "wal-garbage-text-first": {
        "scan_id": "crp1:8fb796ac018c0f17f2cdcf6e21197efdcb0a5a1904cf4f8d412ca6d64dc92561",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:12259d986f8c75b09be4fbb6851f3c5538e34a49915174107070a2a241c6c13c",
    },
    "wal-garbage-none-first": {
        "scan_id": "crp1:b8e128e61a0f7257520b058c9eb256409d6048f48642c29310532f3fb04dff4c",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:3cf34f552f9f006d336f9cdee20fa66d2fa6dc53ab869f5f80b7cf56c03cf61d",
    },
    "wal-garbage-list-first": {
        "scan_id": "crp1:d572458cf5c561a5fde8cc01c6a5a6a4ea61fe8c198334676cbbf27c769b032d",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:8bdc94f3c82110fe3ee416cc84b34fc29410c2e2c1bb9956d9931e5ade069214",
    },
    "wal-garbage-empty-dict-first": {
        "scan_id": "crp1:8dd38e57d9d0a93ac0d587a2e0f2380512366d44955f00a05943203bdfd8a29d",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:415ab9da2dcb384cfaf1cda3151c6734344c2c6cc4fd27febe85cffd81e2a45b",
    },
    "wal-depth-at-limit-first": {
        "scan_id": "crp1:ebbb3ee7aa763c868537b2244a4671d961ae61ac670bf66e3d0b4321f334e807",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:7d74b15e53bb3fef7fe824c3d3924e17031260ff42600a3f18bd946612ee4c0e",
    },
    "wal-int-at-range-edge-first": {
        "scan_id": "crp1:1bc30e4ddbde82f14991ca98447752e7c2d58249ad00900d174b9fc7b841cc94",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:6d30e0c5a15e6487bb09d41910a962006285a7e00b6c4ee0a881e8a5da950b44",
    },
    "wal-negative-int-at-range-edge-first": {
        "scan_id": "crp1:c044d1090061e347ceaec9183a82e42ea1934373941a18200409a690f2d55eb0",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:86a2e275d3a752c1ac04cee561397bb264b63b0e6d6438d5297bd24f04a9063c",
    },
    "wal-sequence-true-first": {
        "scan_id": "crp1:348d32ebd133a06b658dd5032503453caab7879f42acc69b20a25650575eb7b0",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:916165d293e105f5624671e26a2d73af7f1240aa9d1f76206085509a1e74ef97",
    },
    "wal-sequence-false-first": {
        "scan_id": "crp1:66b6118cbfb1fa7ad9fd359282e551d7e512b77d1410a26778c882273dd38f54",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:0e607a2720a1154b3882e662ac14a356b9673f4311ad78633b42195bbb7af8ea",
    },
    "wal-entry-true-first": {
        "scan_id": "crp1:655714d197d59e0e69dbfda29e81335171b9b03449abf7f5d71538ba3dcbcff2",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:b6124e41bfd6f9f0f698b85a5a3ea05b18d710a8536e1b834820855dfd24f13f",
    },
    "wal-entry-false-first": {
        "scan_id": "crp1:a7d5e7dfb49123f9d7a5ef73fdf23645045e49784fd1de548b053013680b2b03",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:8e1707ad6d001e60859b16bdd6e90d5ee133966121d90d1517db959de391ea97",
    },
    "wal-digest-true-first": {
        "scan_id": "crp1:51dc14c7fdf9eca80408dae2319e0338df3f9c1acfe0fb530c4f6b75fa71e075",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:48a70599a140fb03b3fcb531f7c91cabfe1a65f6d9f4bcd934b8fcf4c48818e2",
    },
    "wal-digest-false-first": {
        "scan_id": "crp1:639c849be6794850424eecd0126f7a8629dcede8a4bd643b0e8fd804357a1c36",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:5ef78659be30894fd4992cf94327f8cc97beaca937899328039ba59c7c8a8528",
    },
    "wal-digest-empty-str-first": {
        "scan_id": "crp1:4dcab37be49e72c0b77efa512b6a51afde7a7242eb5120251186dc720cf5bdb0",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:a349109eb2ca6304fc896605c96b723cf3501a30ce8cd2d740c9d2f001034bdd",
    },
    "wal-digest-utf8-2-byte-first": {
        "scan_id": "crp1:367a12ef6e739d76048a24527b5a70ad5c6ba2a5c2b9249f85f09092554c5461",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:4089c2801a2e74d3fe5862642b0ff8e8ca056fac4a4dc1aad992d5b47951310d",
    },
    "wal-digest-utf8-3-byte-first": {
        "scan_id": "crp1:9a4a2b9cdebf7a70eafa9459e15056b73bcdf96d4031eb8865f684b947efd8fc",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:4a33919178002b289892034abf017c97b7e614aff4baa3ea62b6f45ceba30bc2",
    },
    "wal-digest-utf8-4-byte-first": {
        "scan_id": "crp1:d6509bfb7649fa1e19d7440598dc10ab5b8764072691fd7c159fa2822e3c18de",
        "verdict": "salvaged",
        "verified_head": "wal0:0000000000000000000000000000000000000000000000000000000000000000",
        "verified_count": 0,
        "quarantined_count": 3,
        "quarantine_token":
            "qrn1:2847918c8dff138126d44a5f62012b35eb05934fa242f5c20c7c37ca222773eb",
    },
    "wal-forged-entry-id-middle": {
        "scan_id": "crp1:a3af332bcfc228355126c9a94f88554d7e4c35ad4881b27d23c5c70aa5ad2798",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:f82d525a9700c0ec9cac5605fae17d51c35b9cd8e2e48d005fc7720ff28233c1",
    },
    "wal-sequence-shifted-middle": {
        "scan_id": "crp1:a5c1acd9dbbfb5db9bbfb8b4df73a1d5b4bdfce011e2ec121c52b60266ebac8e",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:fc79bb8d1f27b8d77a517a66b7625a05484fdd4308ce9e7c0965e0c70f32b05a",
    },
    "wal-prior-link-broken-middle": {
        "scan_id": "crp1:d91f6af0d8c9278091f2484dec2c2848a241f9728f644a9d65756c259be0bd12",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:37fdbda86c9df5cc4297d680c606b84b8ccd1831c8ea7ac2be041f31170adc13",
    },
    "wal-op-changed-middle": {
        "scan_id": "crp1:14bace1a753dbeee4cbd2f3008ef5bb76958de49342001f6eab8aff79f332537",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:bd19acd37ea75f0a98d115d1f15407fc6df4c01fa85d5b1e9d2e4ac527c774e2",
    },
    "wal-digest-altered-middle": {
        "scan_id": "crp1:3afaf937dbb79b1a3ddd71569ae9e0f660d0e51c90604f016a17f1314794b400",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:3fd9c6592008f2b0d4c18019baee8835e72153218fcf6a36b9fc8d035b7a974a",
    },
    "wal-missing-field-middle": {
        "scan_id": "crp1:cb7269c0fe5c15b0e0f62467852e7fa8c720f12a7ecd46bb2d25495d22fc59d6",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:c4120265470c3c9bcdf42c9b937383ffcdbf063087490caf95f862dfbc73043d",
    },
    "wal-extra-field-middle": {
        "scan_id": "crp1:57a874fe5a90368014efb6926ce2676fdb8fbd291fc17ab6db181108d08ade20",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:d33a39e3b29bda11a2f348f91255ccd45aa4f540ad094dc1ca44d24b52efeac4",
    },
    "wal-garbage-text-middle": {
        "scan_id": "crp1:2bc663066812a01906306ed49cff46d10d8c3d58397a189140fa83fb29e37bc2",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:d3962a7acf5bd499b0186c45b1650a5b21f15fef2caa8b96184b7d55e43ab3a5",
    },
    "wal-garbage-none-middle": {
        "scan_id": "crp1:e5bd484d8649b6f754d29c14d5dbc26e8641e290d7b3490c56b399351e77bd0a",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:b35c82d12b2ea5f0a1249c374eba34c401a79dade4dbeba3993de47af3819194",
    },
    "wal-garbage-list-middle": {
        "scan_id": "crp1:9ff93752c2e82f0061f1f053871333399b4b5939892ec91e892edc95c7b5c269",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:0131450a47d58df607459ca12184b19a9ed2f201201498c7ba4c09f8aa6d786e",
    },
    "wal-garbage-empty-dict-middle": {
        "scan_id": "crp1:051e15720b12f3c4f9c103eba76131e288f3556201630c37c0994495f7e7cc43",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:0fcf164ba298d16b31b9b1c49ba6afe102ca1eea00190baf7c0214cc192d9d46",
    },
    "wal-depth-at-limit-middle": {
        "scan_id": "crp1:08698bb7c4e6cce5815965d3f962a2afbdb92d4d2f350b6eeb7507fef63280a5",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:97bb946b2d700191b67dd38cd54c59a6b1d0b942a13f7e838acdc46cf3a27b82",
    },
    "wal-int-at-range-edge-middle": {
        "scan_id": "crp1:153fc03dde6eafc9c042936756f866b45180e885b5d5f24e8a6b4d6f82a3b6ea",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:9ab32610db5466cf59bef02ed39f6b73f3f259c702dff89333894b48f0c70e52",
    },
    "wal-negative-int-at-range-edge-middle": {
        "scan_id": "crp1:af501edf53fab5e1460f9515174140a520f2aa300d61b7a25f68b44ca0870a45",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:d1e15dcc8474910c96bbf4d6506e3995e25bc6ac9f8c3e2133760b5790e4c57d",
    },
    "wal-sequence-true-middle": {
        "scan_id": "crp1:e63e102fd507fcd4d9260bc8c70bb7fc580319319e4efc8eac43b3cab63d7037",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:81d998acd6fffd9a392093983f3422747dd2ba4fec245f57b1a829b43304653a",
    },
    "wal-sequence-false-middle": {
        "scan_id": "crp1:39ac93123878a31bc40bd46378bac20f0d964d56b16632170b0ee0ea9ed5107d",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:6b6efde8f55bf07ef9af33e5cae02e50e410ee922c463bdb1e823c6f4f9998a2",
    },
    "wal-entry-true-middle": {
        "scan_id": "crp1:0f672379be5301ad07727c9f12ce8c8332d34d60212f384bcbc0ce8a9c4ef887",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:663aa31aff78c38ed622670688a29b49d69916caf20e6d2b6cb517f93ac1f022",
    },
    "wal-entry-false-middle": {
        "scan_id": "crp1:2ac5a5aff26a88d228546c3d930a7c751be0806f91eafe1fd12a511aadf29a28",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:c44ea14dd49f02121217a865c62d5445b11528be79d07c84b933fd92852e31d0",
    },
    "wal-digest-true-middle": {
        "scan_id": "crp1:98c6eef659beac86a0b50598a53c4d9c083e2a9fdf7d84f5eabc381346a5d74f",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:8cf28f78d583fbbe09ee2c0fcfd46d0efb09f2c4f3371a3b75cb1ec6b2c54d73",
    },
    "wal-digest-false-middle": {
        "scan_id": "crp1:333d53610566957c69c9523126bb40bea4bca45a2e7faadc02bd644571884f47",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:0ef9358838fcd40448c3ef0251b963f14fd2e9a19d3769086729582fa1ef3dfe",
    },
    "wal-digest-empty-str-middle": {
        "scan_id": "crp1:298248bb4a6b4cb1a31ab662ab1ce35305794f307cee787d7fcdf4693236dfa8",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:6b63ba450cbc2c1e86a3744f55e99f00af189d664e913d11ffe45e63a595d013",
    },
    "wal-digest-utf8-2-byte-middle": {
        "scan_id": "crp1:9c3bf4de650ed83ad9ef5c2580339e6c8d99db6ec51b5c8bbe46534103f7c75b",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:2cb5270fb91606e84a56886eb9e8d75f990f8cb7de397838055e59f51a429f13",
    },
    "wal-digest-utf8-3-byte-middle": {
        "scan_id": "crp1:2e3beab469ff3ba973ac732aa9eb33130dcb3ed807eaa996e540a12329a0d9a7",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:c868ca8fffb7c6721078926c0b1d85696bf190cd2873dbe98be4dca17f4a2649",
    },
    "wal-digest-utf8-4-byte-middle": {
        "scan_id": "crp1:726f19d2ab09d88899f4b7e71e28719f6902f877cff92e60911dde3b48602641",
        "verdict": "salvaged",
        "verified_head": "wal1:f27b07632252d1f0da48068c9c20c2ccc9d93e8ca0c638befe90f62ba5518412",
        "verified_count": 1,
        "quarantined_count": 2,
        "quarantine_token":
            "qrn1:2274e7ffa230451031db976b1625e6f813321691b20f0cbca312e8dd07550b72",
    },
    "wal-forged-entry-id-last": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "wal-sequence-shifted-last": {
        "scan_id": "crp1:5fa1051b3bcfc7540f3bcf4d1b9109bfb81eda41bd82b734cb63d9db631f6c51",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:f5dfaad76b71127bccad3c3f4b4dac8c31ec386cf5a579f9124b8f6ee49ae3eb",
    },
    "wal-prior-link-broken-last": {
        "scan_id": "crp1:ac45268416e404055b763e51794d6e9a891333a8594bf4da1b34ea9f66674f2e",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:2f41d71d9b58489375e76109f068f85bafbcbc098a688bfe9f25fff637e97628",
    },
    "wal-op-changed-last": {
        "scan_id": "crp1:c56d2f0fbc7133bb351daa3f613db08b8273d7c4a7037aed80eee9b0747f80f0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:d695f65bf1f0cae499fa2c08726e7169660ba5601e2b7886b9cbf98bda12c935",
    },
    "wal-digest-altered-last": {
        "scan_id": "crp1:bec80164546795e22358cf7124c10360272a358415c7fe0a77659b18a11de705",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:7d731e3789b10ecbd049cd3e73310234e079ddf8356f132b92889fe48f7149d5",
    },
    "wal-missing-field-last": {
        "scan_id": "crp1:b5a254aefa1e7ea3c5cafb9eaaeb190a38513fbfc7f91fc0e2264ebb479a74d8",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:a3b824b27e144db1b8c76ae937b39e7e2fde8d9a6490284c1ad26bd5989bb339",
    },
    "wal-extra-field-last": {
        "scan_id": "crp1:3e5704c323c5d1ee6179ff37ecf834b32b49483c7d7303753271da5dc38e5e80",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:79f49522a460886d016d8d411a7843e724d74da598f819ad1387100a0d936ebc",
    },
    "wal-garbage-text-last": {
        "scan_id": "crp1:0718e8f8ea61e11b9f8acdac4a38ed9e2bb9061009db3a374ac34dca53215a92",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:bed53937fe01e04f589eaf7b8b6e81d701895405887bd23a53755ec5dc218f19",
    },
    "wal-garbage-none-last": {
        "scan_id": "crp1:16bf0f0b0339a4468e71cda0d03ad9a86825c25e3eeb710acb341a9d2e5ea861",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:3d53d949ce30a5cfcb022b1e63b5e5ebd3d123ef5228374fb291e0182660b64b",
    },
    "wal-garbage-list-last": {
        "scan_id": "crp1:c85f2feae472cfc21422e5472bc0b25e7f8583684273b4595e28953a70b79f86",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:40ab74a897fc86e460ed3263f47105ad7e0ca0e5799c9ee4038041f2345438eb",
    },
    "wal-garbage-empty-dict-last": {
        "scan_id": "crp1:07d7ee8cb32bc209f4917f43331ab7f6454dec05b708b0986ac94a2085165168",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:2c608344b5f2ad836d1e4c5d0f0485ccfd9e8c55572109e831c1c02b4b3d7925",
    },
    "wal-depth-at-limit-last": {
        "scan_id": "crp1:db45a15ad148e2d744f4251f56cd19d35432f75638efac26560cb656998af5e0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:111482447f33b6d2c1c13cb20af3aa87cbdddf118f4d400dabc813f8acbdd792",
    },
    "wal-int-at-range-edge-last": {
        "scan_id": "crp1:e7c676c30b537bbce39fb0dcc1b1363551e5ef08ae054399ff8a6f51c58e7eb0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:ea5546ae1c5005b905cf9eceed85f1c79153b74e607eea0b77aae3733884a16f",
    },
    "wal-negative-int-at-range-edge-last": {
        "scan_id": "crp1:fa7745c2bc56d0fa5f509611518f8dd943ef021ac4117af8dbf9561fac8b709e",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:425de97f0445386b2e7ebd4c6761c730999d70fe9537f533e275fc7bdffe6c41",
    },
    "wal-sequence-true-last": {
        "scan_id": "crp1:9b0a5b88029dc851117be9919d36a9ba466dd37f03d2ca6fa97e1d06669c2767",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:59decb3f956ba22825911fda876140670a84183f200a82ab9620a3fa4bfbf442",
    },
    "wal-sequence-false-last": {
        "scan_id": "crp1:71fabf003ac8091986874294f209d3c12c7f63d9ac1558f8389e176144bfa032",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:76b57f30b0598fc6d372c727016ef9fd66130ea50eab5e717897e39aae4ce425",
    },
    "wal-entry-true-last": {
        "scan_id": "crp1:10d586ae5112523f2a83ad4a8a698ee8a2e03357a28422c481026b3eb951fb74",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:2cd8373119c5601046e0b9a4384d70fc2847e173ee1bd617d00d49eccd36bf20",
    },
    "wal-entry-false-last": {
        "scan_id": "crp1:f238af851a612c8b4cd789605336fae3cb76135f30875383d909bebdb65fdc6c",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:e5dd6bac11d6e9470efdf6af3e592129482b5350ae0994e62a6082e71f40d9af",
    },
    "wal-digest-true-last": {
        "scan_id": "crp1:ce7368910e31d92166bba74a4b8e8b13c3e9e41b02eaf94223908e80b728851c",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:9b0b7a898e87c51024743878a60fec751c34e6e2acd376cc689024583fd45dfe",
    },
    "wal-digest-false-last": {
        "scan_id": "crp1:c3b29651a342ad436669505b4fc5b6036accb997b314ceeff6025a843ed318b0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:bbee85cd753227adfbe751d3fc9ce5beaec85fdba07c29346110a83bc08111fe",
    },
    "wal-digest-empty-str-last": {
        "scan_id": "crp1:4064cdfb70c7c832b227f5ab6142b9abaec2c4fc9c0e61d5021efdcdb7739b77",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:f0c978d44d6bc6f6f4ab3dc73c549a6ad372ef3d715e47ff0ffdde554aab18c3",
    },
    "wal-digest-utf8-2-byte-last": {
        "scan_id": "crp1:ae77b76ea4b31ab2af79c92ae36c501aca973cc1c947d049197d4472e83e215d",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:0cb72c008f5db7670d6cfc180791dc4f33acd6be69b6754e0b53813e07ae79c6",
    },
    "wal-digest-utf8-3-byte-last": {
        "scan_id": "crp1:9813870c3fd74ea9c2cb8a6dc3b585c1d1f5cf40cacf6188a624c20c9afd8c74",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:e9e31b072867d51e4c03610e70f73c431589db9aede15d07d8ce1e39d9b2f443",
    },
    "wal-digest-utf8-4-byte-last": {
        "scan_id": "crp1:40b4b5f14d7b697b36ae14370b6d521da123339f25c77c50a9ad2bc8570ed28c",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:c0dc8baf1445821691da67b03a9a725855bb7f5acc1cd67cd2b0229cb380ecca",
    },
    "sink-raises-exception-on-clean-log": {
        "scan_id": "crp1:37b79e6b0315c081ecc8e1d13d54fd3a65db27d36ed35e1e903efd392a1a891e",
        "verdict": "clean",
        "verified_head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "verified_count": 3,
        "quarantined_count": 0,
        "quarantine_token": None,
    },
    "sink-raises-keyboard-interrupt-on-clean-log": {
        "scan_id": "crp1:37b79e6b0315c081ecc8e1d13d54fd3a65db27d36ed35e1e903efd392a1a891e",
        "verdict": "clean",
        "verified_head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "verified_count": 3,
        "quarantined_count": 0,
        "quarantine_token": None,
    },
    "sink-raises-system-exit-on-clean-log": {
        "scan_id": "crp1:37b79e6b0315c081ecc8e1d13d54fd3a65db27d36ed35e1e903efd392a1a891e",
        "verdict": "clean",
        "verified_head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "verified_count": 3,
        "quarantined_count": 0,
        "quarantine_token": None,
    },
    "sink-raises-generator-exit-on-clean-log": {
        "scan_id": "crp1:37b79e6b0315c081ecc8e1d13d54fd3a65db27d36ed35e1e903efd392a1a891e",
        "verdict": "clean",
        "verified_head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
        "verified_count": 3,
        "quarantined_count": 0,
        "quarantine_token": None,
    },
    "sink-mutates-first-entry": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-mutates-last-entry": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-mutates-first-record": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-appends-entry": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-pops-entry": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-clears-log": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-replaces-first-entry": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-reorders-entry-keys": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-adds-entry-key": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-adds-payload-key": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-adds-record-key": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-mutates-max-loss": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-clears-request": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-adds-request-key": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-own-argument-appends": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-own-argument-clears": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-own-argument-entry": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-own-argument-adds-entry-key": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-own-argument-payload": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
    "sink-own-argument-record": {
        "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
        "verdict": "salvaged",
        "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
        "verified_count": 2,
        "quarantined_count": 1,
        "quarantine_token":
            "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
    },
}  # GENERATED
SALVAGE_EXPECT = {
    "scan_id": "crp1:355572c91c37f5cfabda7ccfab7f7f337b07b4bd69155f96d8662c2549b233d0",
    "verdict": "salvaged",
    "verified_head": "wal1:aa7680033301d2cbc27223d9d03150f370263f9f803cbdf1a02f138ff9c22eb4",
    "verified_count": 2,
    "quarantined_count": 1,
    "quarantine_token": "qrn1:b7ecd3a79d5ba1b40e48b458f0a844f9c1961b04e61a0daef7288d75925bb648",
}  # GENERATED
CLEAN_EXPECT = {
    "scan_id": "crp1:37b79e6b0315c081ecc8e1d13d54fd3a65db27d36ed35e1e903efd392a1a891e",
    "verdict": "clean",
    "verified_head": "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
    "verified_count": 3,
    "quarantined_count": 0,
    "quarantine_token": None,
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
    "request-key-max_loss-str-subclass",
    "request-key-max_loss-colliding-hash",
    "request-max-loss-none",
    "request-max-loss-true",
    "request-max-loss-false",
    "request-max-loss-text",
    "request-max-loss-float",
    "request-max-loss-negative",
    "request-max-loss-huge-negative",
    "request-max-loss-int-subclass",
    "request-max-loss-str-subclass",
    "request-max-loss-list",
    "request-max-loss-huge",
    "request-max-loss-zero-on-corrupt-log",
    "log-none",
    "log-dict",
    "log-text",
    "log-zero",
    "log-tuple",
    "log-bytes",
    "log-list-subclass",
    "log-self-referential",
    "log-aliased-entry",
    "log-deep",
    "log-huge-int",
    "log-clean-max-loss-zero",
    "log-empty",
    "entry-float-first",
    "entry-bytes-first",
    "entry-tuple-first",
    "entry-int-subclass-first",
    "entry-str-subclass-first",
    "entry-dict-subclass-first",
    "entry-list-subclass-first",
    "entry-key-sequence-str-subclass-first",
    "entry-key-sequence-colliding-hash-first",
    "entry-key-op-str-subclass-first",
    "entry-key-op-colliding-hash-first",
    "entry-key-entry_id-str-subclass-first",
    "entry-key-entry_id-colliding-hash-first",
    "entry-key-prior_entry_id-str-subclass-first",
    "entry-key-prior_entry_id-colliding-hash-first",
    "entry-key-payload-str-subclass-first",
    "entry-key-payload-colliding-hash-first",
    "entry-key-int-first",
    "entry-sequence-float-first",
    "entry-sequence-bytes-first",
    "entry-sequence-int-subclass-first",
    "entry-sequence-int-over-range-first",
    "entry-sequence-negative-int-over-range-first",
    "entry-sequence-tuple-first",
    "entry-op-str-subclass-first",
    "entry-op-lone-surrogate-first",
    "entry-payload-dict-subclass-first",
    "entry-payload-key-record-str-subclass-first",
    "entry-record-key-digest-str-subclass-first",
    "entry-payload-key-record-colliding-hash-first",
    "entry-record-key-digest-colliding-hash-first",
    "entry-record-digest-float-first",
    "entry-record-digest-lone-surrogate-first",
    "entry-record-digest-bytes-first",
    "entry-record-digest-int-over-range-first",
    "entry-record-digest-str-subclass-first",
    "entry-self-referential-first",
    "entry-aliases-other-entry-first",
    "entry-over-depth-first",
    "wal-forged-entry-id-first",
    "wal-sequence-shifted-first",
    "wal-prior-link-broken-first",
    "wal-op-changed-first",
    "wal-digest-altered-first",
    "wal-missing-field-first",
    "wal-extra-field-first",
    "wal-garbage-text-first",
    "wal-garbage-none-first",
    "wal-garbage-list-first",
    "wal-garbage-empty-dict-first",
    "wal-depth-at-limit-first",
    "wal-int-at-range-edge-first",
    "wal-negative-int-at-range-edge-first",
    "wal-sequence-true-first",
    "wal-sequence-false-first",
    "wal-entry-true-first",
    "wal-entry-false-first",
    "wal-digest-true-first",
    "wal-digest-false-first",
    "wal-digest-empty-str-first",
    "wal-digest-utf8-2-byte-first",
    "wal-digest-utf8-3-byte-first",
    "wal-digest-utf8-4-byte-first",
    "wal-forged-entry-id-first-over-bound",
    "entry-float-middle",
    "entry-bytes-middle",
    "entry-tuple-middle",
    "entry-int-subclass-middle",
    "entry-str-subclass-middle",
    "entry-dict-subclass-middle",
    "entry-list-subclass-middle",
    "entry-key-sequence-str-subclass-middle",
    "entry-key-sequence-colliding-hash-middle",
    "entry-key-op-str-subclass-middle",
    "entry-key-op-colliding-hash-middle",
    "entry-key-entry_id-str-subclass-middle",
    "entry-key-entry_id-colliding-hash-middle",
    "entry-key-prior_entry_id-str-subclass-middle",
    "entry-key-prior_entry_id-colliding-hash-middle",
    "entry-key-payload-str-subclass-middle",
    "entry-key-payload-colliding-hash-middle",
    "entry-key-int-middle",
    "entry-sequence-float-middle",
    "entry-sequence-bytes-middle",
    "entry-sequence-int-subclass-middle",
    "entry-sequence-int-over-range-middle",
    "entry-sequence-negative-int-over-range-middle",
    "entry-sequence-tuple-middle",
    "entry-op-str-subclass-middle",
    "entry-op-lone-surrogate-middle",
    "entry-payload-dict-subclass-middle",
    "entry-payload-key-record-str-subclass-middle",
    "entry-record-key-digest-str-subclass-middle",
    "entry-payload-key-record-colliding-hash-middle",
    "entry-record-key-digest-colliding-hash-middle",
    "entry-record-digest-float-middle",
    "entry-record-digest-lone-surrogate-middle",
    "entry-record-digest-bytes-middle",
    "entry-record-digest-int-over-range-middle",
    "entry-record-digest-str-subclass-middle",
    "entry-self-referential-middle",
    "entry-aliases-other-entry-middle",
    "entry-over-depth-middle",
    "wal-forged-entry-id-middle",
    "wal-sequence-shifted-middle",
    "wal-prior-link-broken-middle",
    "wal-op-changed-middle",
    "wal-digest-altered-middle",
    "wal-missing-field-middle",
    "wal-extra-field-middle",
    "wal-garbage-text-middle",
    "wal-garbage-none-middle",
    "wal-garbage-list-middle",
    "wal-garbage-empty-dict-middle",
    "wal-depth-at-limit-middle",
    "wal-int-at-range-edge-middle",
    "wal-negative-int-at-range-edge-middle",
    "wal-sequence-true-middle",
    "wal-sequence-false-middle",
    "wal-entry-true-middle",
    "wal-entry-false-middle",
    "wal-digest-true-middle",
    "wal-digest-false-middle",
    "wal-digest-empty-str-middle",
    "wal-digest-utf8-2-byte-middle",
    "wal-digest-utf8-3-byte-middle",
    "wal-digest-utf8-4-byte-middle",
    "wal-forged-entry-id-middle-over-bound",
    "entry-float-last",
    "entry-bytes-last",
    "entry-tuple-last",
    "entry-int-subclass-last",
    "entry-str-subclass-last",
    "entry-dict-subclass-last",
    "entry-list-subclass-last",
    "entry-key-sequence-str-subclass-last",
    "entry-key-sequence-colliding-hash-last",
    "entry-key-op-str-subclass-last",
    "entry-key-op-colliding-hash-last",
    "entry-key-entry_id-str-subclass-last",
    "entry-key-entry_id-colliding-hash-last",
    "entry-key-prior_entry_id-str-subclass-last",
    "entry-key-prior_entry_id-colliding-hash-last",
    "entry-key-payload-str-subclass-last",
    "entry-key-payload-colliding-hash-last",
    "entry-key-int-last",
    "entry-sequence-float-last",
    "entry-sequence-bytes-last",
    "entry-sequence-int-subclass-last",
    "entry-sequence-int-over-range-last",
    "entry-sequence-negative-int-over-range-last",
    "entry-sequence-tuple-last",
    "entry-op-str-subclass-last",
    "entry-op-lone-surrogate-last",
    "entry-payload-dict-subclass-last",
    "entry-payload-key-record-str-subclass-last",
    "entry-record-key-digest-str-subclass-last",
    "entry-payload-key-record-colliding-hash-last",
    "entry-record-key-digest-colliding-hash-last",
    "entry-record-digest-float-last",
    "entry-record-digest-lone-surrogate-last",
    "entry-record-digest-bytes-last",
    "entry-record-digest-int-over-range-last",
    "entry-record-digest-str-subclass-last",
    "entry-self-referential-last",
    "entry-aliases-other-entry-last",
    "entry-over-depth-last",
    "wal-forged-entry-id-last",
    "wal-sequence-shifted-last",
    "wal-prior-link-broken-last",
    "wal-op-changed-last",
    "wal-digest-altered-last",
    "wal-missing-field-last",
    "wal-extra-field-last",
    "wal-garbage-text-last",
    "wal-garbage-none-last",
    "wal-garbage-list-last",
    "wal-garbage-empty-dict-last",
    "wal-depth-at-limit-last",
    "wal-int-at-range-edge-last",
    "wal-negative-int-at-range-edge-last",
    "wal-sequence-true-last",
    "wal-sequence-false-last",
    "wal-entry-true-last",
    "wal-entry-false-last",
    "wal-digest-true-last",
    "wal-digest-false-last",
    "wal-digest-empty-str-last",
    "wal-digest-utf8-2-byte-last",
    "wal-digest-utf8-3-byte-last",
    "wal-digest-utf8-4-byte-last",
    "wal-forged-entry-id-last-over-bound",
    "sink-raises-exception",
    "sink-raises-exception-on-clean-log",
    "sink-raises-keyboard-interrupt",
    "sink-raises-keyboard-interrupt-on-clean-log",
    "sink-raises-system-exit",
    "sink-raises-system-exit-on-clean-log",
    "sink-raises-generator-exit",
    "sink-raises-generator-exit-on-clean-log",
    "sink-raises-on-excessive-loss",
    "sink-raises-on-malformed-request",
    "sink-returns-none",
    "sink-returns-zero",
    "sink-returns-list",
    "sink-returns-bytes",
    "sink-returns-str-subclass",
    "sink-returns-uppercase",
    "sink-returns-trailing-newline",
    "sink-returns-bad-grammar",
    "sink-returns-unbound-token",
    "sink-returns-full-log-token",
    "sink-returns-empty-suffix-token",
    "sink-returns-prefix-token",
    "sink-returns-lone-surrogate",
    "sink-returns-surrogate-in-token",
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
    "sink-mutates-max-loss",
    "sink-clears-request",
    "sink-adds-request-key",
    "sink-own-argument-appends",
    "sink-own-argument-clears",
    "sink-own-argument-entry",
    "sink-own-argument-adds-entry-key",
    "sink-own-argument-payload",
    "sink-own-argument-record",
)  # GENERATED
PROBE_COUNT = 273  # GENERATED


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    log, request, sink = build()
    engine, counter = _engine(cls, sink)
    if failure == ACCEPT:
        # SAME INSTANCE: accepts under attack, then still rejects a
        # bad request typed and still accepts a clean salvage (a
        # clean scan for the never-called raising sinks)
        try:
            _, ok = _checked_scan(engine, counter, log, request,
                                  PROBE_EXPECT[name])
            bad_log, _ = _salvage_inputs()
            ok = ok and _rejects(engine, counter, bad_log,
                                 {"max_loss": -1}, MCR)
            follow, expect = (_clean_inputs, CLEAN_EXPECT) \
                if name.endswith("-on-clean-log") \
                else (_salvage_inputs, SALVAGE_EXPECT)
            _, again = _checked_scan(engine, counter, *follow(), expect)
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


class AcceptsAll(CorruptionEngine):
    def scan(self, log, request):
        try:
            return super().scan(log, request)
        except CorruptionError:
            return _fake_receipt()


class WrongCode(CorruptionEngine):
    def scan(self, log, request):
        try:
            return super().scan(log, request)
        except CorruptionError as error:
            raise CorruptionError(error.failure_class,
                                  "internal") from None


def _remap(frm, to):
    class Remap(CorruptionEngine):
        def scan(self, log, request):
            try:
                return super().scan(log, request)
            except CorruptionError as error:
                if error.failure_class == frm:
                    raise CorruptionError(
                        to, FAILURE_MAPPING[to]) from None
                raise
    Remap.__name__ = f"Remap_{frm}_to_{to}"
    return Remap


class DoubleSinkCall(CorruptionEngine):
    def __init__(self, sink):
        def twice(suffix):
            sink(copy.deepcopy(suffix))
            return sink(suffix)
        super().__init__(twice)


class UnguardedSink(CorruptionEngine):
    """Calls the sink once OUTSIDE the boundary, before
    validation."""

    def scan(self, log, request):
        if type(log) is list:
            try:
                peek = copy.deepcopy(log[-1:])
            except BaseException:  # noqa: BLE001
                peek = None
            if peek is not None:
                self.sink(peek)
        return super().scan(log, request)


class SinkOnClean(CorruptionEngine):
    def scan(self, log, request):
        receipt = super().scan(log, request)
        if receipt["verdict"] == "clean":
            self.sink([])
        return receipt


class NoCommit(CorruptionEngine):
    def scan(self, log, request):
        saved = list(log) if type(log) is list else None
        receipt = super().scan(log, request)
        log[:] = saved
        return receipt


class OverTruncates(CorruptionEngine):
    def scan(self, log, request):
        receipt = super().scan(log, request)
        if receipt["verdict"] == "salvaged" and log:
            log.pop()
        return receipt


class RebuildsKeptEntries(CorruptionEngine):
    def scan(self, log, request):
        receipt = super().scan(log, request)
        log[:] = copy.deepcopy(log)
        return receipt


class ReordersKeptEntryKeys(CorruptionEngine):
    def scan(self, log, request):
        receipt = super().scan(log, request)
        for entry in log:
            if type(entry) is dict:
                items = list(entry.items())
                entry.clear()
                entry.update(reversed(items))
        return receipt


class MutatesRequestOnSuccess(CorruptionEngine):
    def scan(self, log, request):
        receipt = super().scan(log, request)
        request["max_loss"] = 99
        return receipt


class StaleScanId(CorruptionEngine):
    def scan(self, log, request):
        receipt = super().scan(log, request)
        receipt["scan_id"] = "crp1:" + hashlib.sha256(
            receipt["verdict"].encode()).hexdigest()
        return receipt


class CachedResult(CorruptionEngine):
    """Class-level cache: a repeated scan returns the SAME receipt
    object."""
    _cache = {}

    def scan(self, log, request):
        receipt = super().scan(log, request)
        return CachedResult._cache.setdefault(receipt["scan_id"],
                                              receipt)


class StrSubclassToken(CorruptionEngine):
    """Returns the salvage token as a str subclass (an inexact
    receipt field type)."""

    def scan(self, log, request):
        receipt = super().scan(log, request)
        if log and receipt["verdict"] == "salvaged":
            receipt = dict(receipt)
            receipt["quarantine_token"] = type(
                "_Tok", (str,), {})(receipt["quarantine_token"])
        return receipt


class RawRequestPeek(CorruptionEngine):
    """Reads the live request before request validation."""

    def scan(self, log, request):
        if isinstance(request, dict):
            request.get("max_loss")
        return super().scan(log, request)


class RawEntryPeek(CorruptionEngine):
    """Reads each live entry's op before domain validation."""

    def scan(self, log, request):
        if type(log) is list:
            for entry in log:
                if isinstance(entry, dict):
                    entry.get("op")
        return super().scan(log, request)


class RawEntryKeySet(CorruptionEngine):
    def scan(self, log, request):
        if type(log) is list:
            for entry in log:
                if isinstance(entry, dict):
                    set(entry.keys()) == set()  # noqa: B015
        return super().scan(log, request)


_SOURCES = ("_encode", "canonical_encoding", "quarantine_suffix",
            "_snapshot", "_restore", "_prefix_head", "verified_prefix",
            "CorruptionEngine")


def _source_mutant(name, edits, extra=None):
    """A one-guard edit of the reference scan: the reference's
    domain encoder, prefix search, snapshot/restore and engine
    sources are concatenated; each OLD must occur in them (first
    occurrence replaced). The exec namespace raises the BOUND
    error class; EXTRA names are applied last and override any
    exec'd definition."""
    src = "\n\n".join(inspect.getsource(getattr(_reference, n))
                      for n in _SOURCES)
    for old, new in edits:
        if old not in src:
            raise AssertionError(f"{name}: edit site missing: {old!r}")
        src = src.replace(old, new, 1)
    namespace = dict(vars(_reference))
    # the mutant raises the BOUND error class, so a correct rejection
    # counts as one under any binding (reference or production)
    namespace["CorruptionError"] = CorruptionError

    def _bound_fail(cls):
        raise CorruptionError(cls, FAILURE_MAPPING[cls])

    namespace["_fail"] = _bound_fail
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    namespace.update(extra or {})
    return namespace["CorruptionEngine"]


def _no_clear_restore(saved):
    for obj, contents in saved:
        if type(obj) is list:
            obj[:] = contents
        else:
            obj.update(contents)


_I8, _I12 = " " * 8, " " * 12
_SM = (
    ("no-request-key-guard", [(
        "any(type(key) is not str for key in dict.keys(request)) \\\n"
        "                or ", "")]),
    ("isinstance-max-loss", [(
        "type(max_loss) is not int", "not isinstance(max_loss, int)")]),
    ("rejects-zero-max-loss", [("max_loss < 0", "max_loss < 1")]),
    ("non-strict-loss-bound", [(
        "if lost > max_loss:", "if lost >= max_loss:")]),
    ("no-loss-bound", [("if lost > max_loss:", "if False:")]),
    ("loss-check-after-quarantine", [
        ("if lost > max_loss:", "if False:"),
        (f"{_I8}# COMMIT LAST", f"{_I8}if lost > max_loss:\n"
         f"{_I12}_fail(\"excessive_loss\")\n{_I8}# COMMIT LAST")]),
    ("skips-domain-check", [(
        "frozen, _ = canonical_encoding(log)",
        "frozen = copy.deepcopy(log)")]),
    ("rejects-depth-at-limit", [(
        "if depth > MAX_DEPTH:", "if depth >= MAX_DEPTH:")]),
    ("accepts-over-depth", [(
        "if depth > MAX_DEPTH:", "if depth > MAX_DEPTH + 1:")]),
    ("rejects-int-at-range-edge", [(
        "value.bit_length() > MAX_INT_BITS",
        "value.bit_length() >= MAX_INT_BITS")]),
    ("accepts-int-over-range", [(
        "value.bit_length() > MAX_INT_BITS",
        "value.bit_length() > MAX_INT_BITS + 1")]),
    ("accepts-lone-surrogate", [(
        'raw = value.encode("utf-8")',
        'raw = value.encode("utf-8", "surrogatepass")')]),
    ("isinstance-int-leaf", [(
        "if kind is int:", "if isinstance(value, int):")]),
    ("isinstance-str-leaf", [(
        "if kind is str:", "if isinstance(value, str):")]),
    # the key guard is backed by the leaf encoder's exact-str check
    # on every key, so the key-guard mutant must relax both
    ("isinstance-str-key", [
        ("if type(key) is not str:", "if not isinstance(key, str):"),
        ("if kind is str:", "if isinstance(value, str):")]),
    ("rejects-bool-leaf", [("if kind is bool:", "if False:")]),
    ("bool-collision", [(
        'out.append(b"t" if value else b"f")', 'out.append(b"t")')]),
    ("rejects-empty-str", [(
        'raw = value.encode("utf-8")',
        'raw = value.encode("utf-8")\n'
        f'{_I12}if not raw:\n'
        f'{_I12}    raise _OutOfDomain("empty")')]),
    ("ascii-str-encode", [(
        'raw = value.encode("utf-8")', 'raw = value.encode("ascii")')]),
    ("char-length-str-framing", [(
        'out.append(b"s%d:" % len(raw) + raw)',
        'out.append(b"s%d:" % len(value) + raw)')]),
    ("no-alias-check", [("if ident in seen:", "if False:")]),
    ("no-restore", [(f"{_I12}_restore(saved)\n", "")]),
    ("no-request-restore", [(f"{_I12}request.update(saved_req)\n", "")]),
    ("no-request-clear", [(f"{_I12}request.clear()\n", "")]),
    ("no-list-restore", [("obj[:] = contents", "pass")]),
    ("narrow-sink-boundary", [(
        "except BaseException:", "except Exception:")]),
    ("isinstance-sink-output", [(
        "if type(out) is not str or", "if not isinstance(out, str) or")]),
    ("skips-suffix-binding", [(
        "if token != quarantine_suffix(frozen_suffix):", "if False:")]),
    ("shared-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_suffix))",
        "self.sink(frozen_suffix)")]),
    ("list-shallow-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_suffix))",
        "self.sink(list(frozen_suffix))")]),
    ("entry-shallow-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_suffix))",
        "self.sink([dict(e) if type(e) is dict else e "
        "for e in frozen_suffix])")]),
    ("record-shared-sink-argument", [(
        "self.sink(copy.deepcopy(frozen_suffix))",
        "self.sink([{**e, 'payload': dict(e['payload'])} "
        "if type(e) is dict and type(e.get('payload')) is dict "
        "else e for e in frozen_suffix])")]),
    ("live-suffix", [(
        "frozen_suffix = frozen[count:]", "frozen_suffix = log[count:]")]),
    ("commit-before-quarantine", [
        (f"{_I8}saved = _snapshot(log, [])\n",
         f"{_I8}del log[count:]\n{_I8}saved = _snapshot(log, [])\n"),
        (f"{_I8}del log[count:]\n{_I8}return", f"{_I8}return")]),
    ("no-final-commit", [(f"{_I8}del log[count:]\n", "")]),
    ("sink-called-when-clean", [("if lost == 0:", "if lost == 0 and False:")]),
    ("coarse-prefix-search", [(
        "while bad - good > 1:", "while bad - good > 2:")]),
    ("scan-id-ignores-token", [(
        "f\"{token if token is not None else '-'}\"", "\"-\"")]),
)
_SOURCE_MUTANTS = {name: _source_mutant(name, edits)
                   for name, edits in _SM}
_SOURCE_MUTANTS["no-clear-restore"] = _source_mutant(
    "no-clear-restore", [], {"_restore": _no_clear_restore})

MUTANTS = {
    "accepts-all": AcceptsAll,
    "wrong-code": WrongCode,
    "malformed-as-excessive-loss": _remap(MCR, EL),
    "excessive-loss-as-malformed": _remap(EL, MCR),
    "divergent-as-malformed": _remap(DQ, MCR),
    "excessive-loss-as-divergent": _remap(EL, DQ),
    "double-sink-call": DoubleSinkCall,
    "unguarded-sink": UnguardedSink,
    "sink-on-clean": SinkOnClean,
    "no-commit": NoCommit,
    "over-truncates": OverTruncates,
    "rebuilds-kept-entries": RebuildsKeptEntries,
    "reorders-kept-entry-keys": ReordersKeptEntryKeys,
    "mutates-request-on-success": MutatesRequestOnSuccess,
    "stale-scan-id": StaleScanId,
    "cached-result": CachedResult,
    "str-subclass-token": StrSubclassToken,
    "raw-request-peek": RawRequestPeek,
    "raw-entry-peek": RawEntryPeek,
    "raw-entry-key-set": RawEntryKeySet,
    **_SOURCE_MUTANTS,
}

MUTANT_TARGETS = {
    "accepts-all": "malformed:log-not-a-list",
    "wrong-code": "malformed:log-not-a-list",
    "malformed-as-excessive-loss": "malformed:log-not-a-list",
    "excessive-loss-as-malformed": "malformed:loss-one-over-bound",
    "divergent-as-malformed": "malformed:sink-raises",
    "excessive-loss-as-divergent": "malformed:loss-one-over-bound",
    "double-sink-call": "happy:salvage-tampered-tail-entry",
    "unguarded-sink": "happy:clean-three-entry-log",
    "sink-on-clean": "happy:clean-three-entry-log",
    "no-commit": "happy:salvage-tampered-tail-entry",
    "over-truncates": "happy:salvage-tampered-tail-entry",
    "rebuilds-kept-entries": "happy:clean-three-entry-log",
    "reorders-kept-entry-keys": "happy:clean-three-entry-log",
    "mutates-request-on-success": "happy:clean-three-entry-log",
    "stale-scan-id": "happy:clean-three-entry-log",
    "cached-result": "happy:clean-three-entry-log",
    "str-subclass-token": "happy:salvage-tampered-tail-entry",
    "raw-request-peek": "totality:request-key-max_loss-str-subclass",
    "raw-entry-peek": "totality:entry-key-op-str-subclass-first",
    "raw-entry-key-set": "totality:entry-dict-subclass-first",
    "no-request-key-guard": "totality:request-key-max_loss-str-subclass",
    "isinstance-max-loss": "malformed:max-loss-bool",
    "rejects-zero-max-loss": "happy:clean-three-entry-log",
    "non-strict-loss-bound": "happy:salvage-tampered-tail-entry",
    "no-loss-bound": "malformed:loss-one-over-bound",
    "loss-check-after-quarantine": "malformed:loss-one-over-bound",
    "skips-domain-check": "malformed:log-float-leaf",
    "rejects-depth-at-limit": "boundary:depth-at-limit-in-suffix",
    "accepts-over-depth": "malformed:log-over-depth",
    "rejects-int-at-range-edge": "boundary:int-at-range-edge-in-suffix",
    "accepts-int-over-range": "malformed:log-int-over-range",
    "accepts-lone-surrogate": "totality:entry-op-lone-surrogate-first",
    "isinstance-int-leaf": "totality:entry-int-subclass-first",
    "isinstance-str-leaf": "totality:entry-str-subclass-first",
    "no-alias-check": "totality:log-self-referential",
    "no-restore": "totality:sink-mutates-then-raises",
    "no-request-restore": "happy:salvage-tampered-tail-entry",
    "no-request-clear": "totality:sink-adds-request-key",
    "no-list-restore": "totality:sink-mutates-then-raises",
    "narrow-sink-boundary": "malformed:sink-raises-keyboard-interrupt",
    "isinstance-sink-output": "totality:sink-returns-str-subclass",
    "skips-suffix-binding": "malformed:sink-unbound-token",
    "shared-sink-argument": "totality:sink-own-argument-appends",
    "list-shallow-sink-argument": "totality:sink-own-argument-entry",
    "entry-shallow-sink-argument": "totality:sink-own-argument-payload",
    "record-shared-sink-argument": "totality:sink-own-argument-record",
    "live-suffix": "totality:sink-mutates-last-entry",
    "commit-before-quarantine": "boundary:salvage-to-genesis",
    "no-final-commit": "happy:salvage-tampered-tail-entry",
    "sink-called-when-clean": "happy:clean-three-entry-log",
    "coarse-prefix-search": "happy:salvage-tampered-tail-entry",
    "scan-id-ignores-token": "happy:salvage-tampered-tail-entry",
    "no-clear-restore": "totality:sink-reorders-entry-keys",
    "isinstance-str-key": "totality:entry-key-sequence-str-subclass-first",
    "rejects-bool-leaf": "totality:wal-sequence-true-middle",
    "bool-collision": "totality:wal-digest-false-last",
    "rejects-empty-str": "totality:wal-digest-empty-str-last",
    "ascii-str-encode": "totality:wal-digest-utf8-2-byte-last",
    "char-length-str-framing": "totality:wal-digest-utf8-3-byte-last",
}  # GENERATED-CHECKED


# -- kill-proof: substitution mutants vs the closure ---------------------------
def _payload_of(row):
    return {k: v for k, v in row.items() if k != "name"}


def _regenerated(row):
    """ROW with its pinned receipt regenerated from the reference
    over the row's own inputs (kept as-is when the reference
    rejects), so only the semantic pins can kill the edit."""
    with contextlib.suppress(_reference.CorruptionError):
        row["expect"] = _reference_scan(row["oracle"], row["log"],
                                        row["request"])
    return row


def _erasures(section, row):
    """Single-edge erasures of ROW. An erasure equal to its row is
    an equivalent mutant and is dropped."""
    out = []
    if section in ("happy", "boundary"):
        raised = copy.deepcopy(row)
        raised["request"]["max_loss"] += 5
        out.append(("max-loss-raised", _regenerated(raised)))
        if row["log"]:
            dropped = copy.deepcopy(row)
            dropped["log"] = dropped["log"][:-1]
            out.append(("last-entry-dropped", _regenerated(dropped)))
        grown = copy.deepcopy(row)
        grown["log"].append("garbage")
        grown["request"]["max_loss"] += 1
        out.append(("garbage-appended", _regenerated(grown)))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        parts = _repaired(row)
        fixed.update(log=parts["log"], request=parts["request"],
                     oracle=parts["oracle"])
        out.append(("defect-repaired", fixed))
    else:
        tame = copy.deepcopy(row)
        tame["oracle"] = "honest"
        out.append(("oracle-honest", tame))
        swapped = copy.deepcopy(row)
        swapped["then_oracle"] = row["oracle"]
        out.append(("then-oracle-swapped", swapped))
        repeated = copy.deepcopy(row)
        repeated["then_request"] = copy.deepcopy(row["request"])
        out.append(("follow-up-repeats-rejected-request", repeated))
        shorter = copy.deepcopy(row)
        shorter["then_log"] = shorter["then_log"][:-1]
        out.append(("follow-up-log-shortened", shorter))
    return [(label, m) for label, m in out if _canon(m) != _canon(row)]


def _canon(row):
    """Type-exact row identity (True never equals 1 here)."""
    return json.dumps(row, sort_keys=True)


def _iter_substitution_mutants():
    labels = [(s, r["name"]) for s in MANIFESTS for r in CASES[s]]
    for sa, na in labels:
        for sb, nb in labels:
            if (sa, na) == (sb, nb):
                continue
            m = copy.deepcopy(CASES)
            i = [r["name"] for r in m[sa]].index(na)
            src = next(r for r in CASES[sb] if r["name"] == nb)
            m[sa][i] = dict(copy.deepcopy(src), name=na)
            yield (f"payload:{sa}:{na}<-{sb}:{nb}", m)
    for section in MANIFESTS:
        rows = CASES[section]
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                m = copy.deepcopy(CASES)
                m[section][i]["name"], m[section][j]["name"] = \
                    rows[j]["name"], rows[i]["name"]
                yield (f"name-swap:{section}:{i}:{j}", m)
        for label, fn in (("reversed", lambda r: r.reverse()),
                          ("dropped", lambda r: r.pop()),
                          ("duplicated",
                           lambda r: r.append(copy.deepcopy(r[0])))):
            m = copy.deepcopy(CASES)
            fn(m[section])
            yield (f"{label}:{section}", m)
        for i, row in enumerate(CASES[section]):
            for label, mutant in _erasures(section, row):
                m = copy.deepcopy(CASES)
                m[section][i] = mutant
                yield (f"erased:{section}:{row['name']}:{label}", m)
    return


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
    assert SALVAGE_EXPECT["verdict"] == "salvaged"
    assert CLEAN_EXPECT["verdict"] == "clean"


def test_wal_corruption_probes_salvage_to_their_entry():
    """Every wal-* accept probe salvages back to exactly the
    corrupted entry (first 0, middle 1, last 2)."""
    for name, receipt in PROBE_EXPECT.items():
        if name.startswith("wal-"):
            index = {"first": 0, "middle": 1, "last": 2}[
                name.rsplit("-", 1)[1]]
            assert (receipt["verdict"], receipt["verified_count"],
                    receipt["quarantined_count"]) == \
                ("salvaged", index, 3 - index), name


def _digest_token(value):
    """Salvage of _salvage_inputs() with the LAST entry's record
    digest set to VALUE, through the BOUND engine: the receipt's
    quarantine token (must equal the reference token over the
    frozen suffix)."""
    log, request = _base_log(), {"max_loss": 1}
    log[2]["payload"]["record"]["digest"] = value
    suffix = copy.deepcopy(log[2:])
    engine, counter = _engine(CorruptionEngine, "honest")
    receipt = engine.scan(log, request)
    assert (receipt["verdict"], receipt["verified_count"],
            receipt["quarantined_count"]) == ("salvaged", 2, 1), value
    assert receipt["quarantine_token"] == quarantine_suffix(suffix)
    return receipt["quarantine_token"]


@pytest.mark.parametrize("pair", [
    (True, False), (True, 1), (False, 0), ("", None),
    ("\u00e9", "e"), ("\u00e9", "ee"), ("\u20ac", "eee"),
    ("\U0001d11e", "eeee")],
    ids=["true-false", "true-one", "false-zero", "empty-str-none",
         "utf8-2-vs-e", "utf8-2-byte-length-twin",
         "utf8-3-byte-length-twin", "utf8-4-byte-length-twin"])
def test_leaf_pair_tokens_are_distinct(pair):
    """Pinned pairs: bool leaves never collide with each other or
    with ints, "" is not None, and a multi-byte str is distinct from
    its char-length and byte-length ASCII twins."""
    left, right = (_digest_token(v) for v in pair)
    assert left != right, pair


def test_identity_source_mutant_is_green_under_current_binding():
    """Structural guard: an unedited reference-source mutant passes
    the whole battery under the CURRENT binding, so a source mutant
    dies only for its edit - never because it raises a different
    error class than the one the probes catch."""
    assert _probe(_source_mutant("identity", [])) == []


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(CorruptionEngine, executed=executed) == []
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
    assert _probe(CorruptionEngine,
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
    count = 0
    survivors = []
    for label, m in _iter_substitution_mutants():
        count += 1
        try:
            _validate_closure(m)
        except (AssertionError, ValueError, KeyError, TypeError):
            continue
        survivors.append(label)
    assert count > 1000
    assert survivors == [], survivors
