"""T0250 permanent red battery for store-export engines.

The battery drives EVERY row of the T0249 conformance fixture
(tests/fixtures/export/cases.json), a closed set of totality
probes and a pinned identity-order probe through an engine class
constructed with a document exporter:

- happy/boundary: the pinned receipt exactly (exact field types),
  the log and request untouched by value AND identity (export is
  read-only: every container and key _snap-identical), determinism
  by value over fresh copies on the SAME instance (never the same
  object), and exactly ONE exporter call with a DETACHED by-value
  copy of the replayed state and the exact format;
- malformed: the original input rejects with the pinned failure
  class and its mapped code, log and request untouched, the
  exporter never called unless the class is divergent_export, and
  the declared single-locus repair is accepted with the receipt
  the reference derives for the repaired input;
- rollback: the hostile exporter bound to the caller's live inputs
  is rejected with the pinned class, log and request restored by
  value and identity, then the valid follow-up on the SAME objects
  returns the pinned receipt;
- totality: hostile requests, request keys/values, hostile logs,
  entries, entry keys/values, payloads and records (first AND last
  entry), dict/list subclasses, cyclic/deep/huge values, and
  hostile exporter behavior (raising any BaseException,
  non-str/str-subclass/unencodable/divergent output, exporters
  that mutate the caller's log, request or their own argument at
  every depth mid-call) each reject with the pinned failure class
  or, for accept-probes, return the pinned receipt with the inputs
  untouched and, on the SAME instance, still reject a bad request
  typed and still accept a clean export - any other BaseException
  escaping is a failure.

Standalone-red convention (T0151, T0178, T0187, T0196, T0232,
T0241): the battery is permanently GREEN against the
contract-derived reference engine from
tests.test_t0248_export_contract and every mutant below is RED.
The production task switches the binding by replacing ONLY the two
binding lines below with the production ExportEngine / ExportError
names; no assertion changes.

Fixture closure: ordered per-section manifests, per-row semantic
pins and closed per-tag checks over the ORIGINAL row data (an
unknown tag raises), and a ROW_DIGESTS whole-row sha256 table whose
key set equals the manifests. test_closure_kills_substitution_mutants
proves substitution mutants are killed with the digest table live
AND with digests neutralized."""

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

from tests import test_t0248_export_contract as _reference  # noqa: E402
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
)
from tests.test_t0194_migration_contract import state_id  # noqa: E402
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    WalEngine,
    WalError,
    _log_of,
    canonical_payload,
)
from tests.test_t0248_export_contract import render_document  # noqa: E402
from tests.test_t0249_export_fixture import (  # noqa: E402
    EXPORTERS,
    _apply_repair,
    _check_malformed_scenario,
    _rollback_exporter,
)
from tests.test_t0249_export_fixture import (  # noqa: E402
    MALFORMED_MANIFEST as _T0249_MALFORMED,
)
from tools.export_contract_lint import (  # noqa: E402
    ERROR_ENUM,
    FAILURE_MAPPING,
)

# -- binding switch: the production task replaces ONLY these two
ExportEngine = __import__("store.export").export.ExportEngine
ExportError = __import__("store.export").export.ExportError

FIXTURE = (Path(__file__).parent / "fixtures" / "export"
           / "cases.json")
CASES = json.loads(FIXTURE.read_text())
RECEIPT_FIELDS = ("export_id", "head", "state_id", "record_count",
                  "format", "document")
ENTRY_FIELDS = ("sequence", "op", "entry_id", "prior_entry_id",
                "payload")
RECORD_FIELDS = ("variant", "digest", "snapshot_fen")
FMT = "jsonl-v1"
MER = "malformed_export_request"
UF = "unsupported_format"
CS = "corrupt_source"
DE = "divergent_export"
# failure classes raised BEFORE the exporter may be called
PRE_EXPORT = frozenset({MER, UF, CS})

# -- closed, ORDERED manifests --------------------------------------------------
# happy/boundary: (name, exporter, pins) where pins = (ops of the
# log, per-entry snapshot position S=STARTPOS K=KINGS E=AFTER_E4,
# record_count, head kind, live positions in document order)
HAPPY_MANIFEST = (
    ("three_live_records", "canonical",
     (("put", "put", "put"), ("S", "K", "E"), 3, "wal1",
      ("K", "E", "S"))),
    ("single_record", "canonical",
     (("put",), ("K",), 1, "wal1", ("K",))),
)
BOUNDARY_MANIFEST = (
    ("empty_log_genesis", "canonical", ((), (), 0, "wal0", ())),
    ("put_then_delete_zero_records", "canonical",
     (("put", "delete"), ("S", "S"), 0, "wal1", ())),
    ("delete_leaves_one_live", "canonical",
     (("put", "put", "delete"), ("S", "K", "S"), 1, "wal1", ("K",))),
)
# malformed: (name == closed defect tag, failure class, exporter,
# repair locus)
MALFORMED_MANIFEST = (
    ("log_not_a_list", MER, "canonical", "log"),
    ("request_missing_format", MER, "canonical", "request"),
    ("request_extra_field", MER, "canonical", "request"),
    ("format_not_a_string", MER, "canonical", "request"),
    ("format_outside_closed_list", UF, "canonical", "request"),
    ("format_wrong_case", UF, "canonical", "request"),
    ("tampered_record_digest", CS, "canonical", "log"),
    ("sequence_gap", CS, "canonical", "log"),
    ("forged_entry_id", CS, "canonical", "log"),
    ("exporter_raises", DE, "raising", "exporter"),
    ("exporter_non_string", DE, "bytes", "exporter"),
    ("exporter_reversed_order", DE, "reversed", "exporter"),
    ("exporter_surrogate", DE, "surrogate", "exporter"),
    ("exporter_pretty_json", DE, "spaced", "exporter"),
    ("exporter_missing_trailing_newline", DE, "no_final_newline",
     "exporter"),
)
# rollback: (name == closed hostile-exporter tag, failure class,
# pins of the valid follow-up as for happy rows)
ROLLBACK_MANIFEST = (
    ("mutate_inputs_then_raise", DE,
     (("put", "put", "put"), ("S", "K", "E"), 3, "wal1",
      ("K", "E", "S"))),
    ("mutate_inputs_then_diverge", DE,
     (("put", "put", "put"), ("S", "K", "E"), 3, "wal1",
      ("K", "E", "S"))),
    ("mutate_state_then_diverge", DE,
     (("put", "put", "put"), ("S", "K", "E"), 3, "wal1",
      ("K", "E", "S"))),
)
MANIFESTS = {"happy": HAPPY_MANIFEST,
             "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

ROW_DIGESTS = {
    "happy:three_live_records":
        "28ee11e980c7b56c864b1772df3f6ba49e2166849452c29a331a09dae6884a60",
    "happy:single_record":
        "74ac41230bf5b090118a9daadb8f3d69429de9aa40f969521cac0d78e805b2c1",
    "boundary:empty_log_genesis":
        "c26abd85cff85c0a58f5e2f4404940f0eecea358fa222793205e7a101bac36a1",
    "boundary:put_then_delete_zero_records":
        "1fc31564561ec14179cd96e12df32710dbc5f4c6f315f4e78571f7172517eb82",
    "boundary:delete_leaves_one_live":
        "285eb91f17bd1875c19727a03b827ec14be972c6d15cdfc5de402d65d5f94073",
    "malformed:log_not_a_list":
        "8dc53646f3e322cd01a287b152cc92e73bfd9dbc7e4fb6c3219dfe7c496090d8",
    "malformed:request_missing_format":
        "54bf774624682f5d473b20af306af9852f812234b86e2a35c0c1e63ee328d99d",
    "malformed:request_extra_field":
        "9205226cc152dbe9e32a4882911ac8d5b664e02752db3650ab8552673024afd4",
    "malformed:format_not_a_string":
        "b816921283deaf734e8a4f3e278fa994ce31d0d1fc2aa1c09fd22bbf36151dae",
    "malformed:format_outside_closed_list":
        "7174a854fb72c09c9725655ffe4b4cfab5233d64f8eb3c8fbbd57e89a4f61a7a",
    "malformed:format_wrong_case":
        "111582c69eee26088258fa73a729c346469d15b97c0be60358a5eae7a098f2d6",
    "malformed:tampered_record_digest":
        "0237615c94febc0184db861bfc33a34d37cdb54d07b33c1d9e80354707c04cc0",
    "malformed:sequence_gap":
        "243351591a53a08e6b56756b0ba4113ac1c1bd115c62a71da42e7c84d8106548",
    "malformed:forged_entry_id":
        "d625cfd31b4477c02435c1e25434ecf0fd29022e3e6b952f1ad9dcc41847de85",
    "malformed:exporter_raises":
        "1923190d42f7b33eeca937cba2950fbd0842c602a1db3c84ddee03f8012aacaf",
    "malformed:exporter_non_string":
        "470ea2034701ed4b4adece1db3c5d99aec1449a56dabb52cfa6422ab075fc36d",
    "malformed:exporter_reversed_order":
        "7399beb743c0afed96a3e3057e3848e2b15cdb31891e9bd5710504f1decb36ea",
    "malformed:exporter_surrogate":
        "585c92971a1007725299bc22cc2a5ad9ee0f4db892ebc182a726fcd3cd0c1536",
    "malformed:exporter_pretty_json":
        "50e31e01b6f487cec175ddd4fa6655a849340cee7ef6a9012875bc9ce795b048",
    "malformed:exporter_missing_trailing_newline":
        "a71b5958cc62c3bc4ac94184ce890bde61d19c459f44bdd9ce50c925637319d3",
    "rollback:mutate_inputs_then_raise":
        "c803f253af31f07d6ab1379c31e0197b149da8a85d6f51a057872450eba1efbe",
    "rollback:mutate_inputs_then_diverge":
        "3d0bf6e6af6684f1b01471c0f7930cb4f62183e83c665dd23f78d84a49ea670a",
    "rollback:mutate_state_then_diverge":
        "aa45243e35b834f95f944de4744c523502cef03f42c629bd4ec163b90c1357a9",
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
def _replay(log):
    return WalEngine(canonical_payload).replay(copy.deepcopy(log))


def _wal_verifies(log):
    try:
        _replay(log)
    except WalError:
        return False
    return True


def _export_id(head, sid, count, fmt, document):
    return "exp1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{fmt}\n{document}".encode()
    ).hexdigest()


def _reference_export(exporter, log, request):
    return _reference.ExportEngine(EXPORTERS[exporter]).export(
        copy.deepcopy(log), copy.deepcopy(request))


def _reference_failure(exporter, log, request):
    try:
        _reference_export(exporter, log, request)
    except _reference.ExportError as error:
        return error.failure_class
    return None


def _check_receipt(name, log, request, expect):
    """The pinned receipt realizes the contract over the ORIGINAL
    log and request: head and state from the linked WAL replay, the
    count of live records, the exact format, the document the
    canonical local rendering, the id derived."""
    assert type(log) is list and _wal_verifies(log), name
    assert type(request) is dict and list(request) == ["format"], name
    assert request["format"] == FMT, name
    replayed = _replay(log)
    state = replayed["state"]
    assert set(expect) == set(RECEIPT_FIELDS), name
    assert expect["head"] == (log[-1]["entry_id"] if log
                              else GENESIS) == replayed["head"], name
    assert expect["state_id"] == state_id(state) == \
        replayed["state_id"], name
    assert type(expect["record_count"]) is int and \
        expect["record_count"] == len(state), name
    assert expect["format"] == FMT, name
    assert expect["document"] == render_document(state, FMT), name
    assert expect["document"].count("\n") == len(state), name
    assert expect["export_id"] == _export_id(
        expect["head"], expect["state_id"], expect["record_count"],
        expect["format"], expect["document"]), name


def _ops(log):
    return tuple(entry["op"] for entry in log)


# case-order positions: identities that sort DIFFERENTLY under
# codepoint and case-folded collation ('K' < 'b' < 'k' vs 'b' < 'K')
CASE_UPPER = "K7/8/8/8/8/8/8/7k w - - 0 1"
CASE_LOWER = "b7/8/8/8/8/8/8/K6k w - - 0 1"
_POSITIONS = {
    _log_of(("put", pos))[0]["payload"]["record"]["snapshot_fen"]: label
    for label, pos in (("S", STARTPOS), ("K", KINGS), ("E", AFTER_E4),
                       ("U", CASE_UPPER), ("L", CASE_LOWER))}


def _positions(log):
    """Per-entry snapshot position label; an unknown position
    raises KeyError."""
    return tuple(_POSITIONS[entry["payload"]["record"]["snapshot_fen"]]
                 for entry in log)


def _document_positions(document):
    return tuple(_POSITIONS[json.loads(line)["record"]["snapshot_fen"]]
                 for line in document.splitlines())


def _check_pins(name, log, expect, pins):
    ops, positions, count, head_kind, live = pins
    assert _ops(log) == ops and _positions(log) == positions, name
    assert expect["record_count"] == count, name
    assert expect["head"].split(":")[0] == head_kind, name
    assert _document_positions(expect["document"]) == live, name


def _check_ok(case, exporter, pins):
    """One closed branch per happy/boundary tag; an unknown tag
    raises."""
    name = case["name"]
    log, request, expect = case["log"], case["request"], case["expect"]
    assert case["exporter"] == exporter == "canonical", name
    assert type(case["why"]) is str and case["why"], name
    _check_receipt(name, log, request, expect)
    _check_pins(name, log, expect, pins)
    idents = [entry["payload"]["identity"] for entry in log]
    if name == "three_live_records":
        assert len(set(idents)) == 3, name
    elif name == "single_record":
        assert len(log) == 1, name
    elif name == "empty_log_genesis":
        assert log == [] and expect["head"] == GENESIS, name
        assert expect["document"] == "", name
    elif name == "put_then_delete_zero_records":
        assert idents[0] == idents[1] and expect["document"] == "", name
    elif name == "delete_leaves_one_live":
        assert idents[2] == idents[0] != idents[1], name
        assert json.loads(expect["document"])["identity"] == \
            idents[1], name
    else:
        raise AssertionError(f"unknown ok tag {name!r}")


_ROLLBACK_TAGS = ("mutate_inputs_then_raise",
                  "mutate_inputs_then_diverge",
                  "mutate_state_then_diverge")


def _check_rollback(case, failure, pins):
    name = case["name"]
    log, request, expect = case["log"], case["request"], case["expect"]
    assert type(case["why"]) is str and case["why"], name
    if name not in _ROLLBACK_TAGS:
        raise AssertionError(f"unknown rollback tag {name!r}")
    assert case["rejected_exporter"] == name, name
    assert case["expect_failure"] == failure, name
    _check_receipt(name, log, request, expect)
    _check_pins(name, log, expect, pins)
    live_log, live_req = copy.deepcopy(log), copy.deepcopy(request)
    hostile = _rollback_exporter(name, live_log, live_req)
    try:
        _reference.ExportEngine(hostile).export(live_log, live_req)
    except _reference.ExportError as error:
        assert error.failure_class == failure, name
    else:
        raise AssertionError(f"{name}: hostile exporter accepted")
    assert live_log == log and live_req == request, name


def _row(section, name, cases=None):
    (row,) = [r for r in (cases or CASES)[section]
              if r["name"] == name]
    return row


def _check_row(section, case, meta):
    if section in ("happy", "boundary"):
        _, exporter, pins = meta
        _check_ok(case, exporter, pins)
    elif section == "malformed":
        name, failure, exporter, locus = meta
        assert case["expect_failure"] == failure, name
        assert case["exporter"] == exporter, name
        assert list(case["minimal_repair"]) == [locus], name
        assert case["defect"] == _T0249_MALFORMED[name][3], name
        assert _T0249_MALFORMED[name][:3] == (failure, exporter,
                                              locus), name
        _check_malformed_scenario(case)
        assert _reference_failure(exporter, case["log"],
                                  case["request"]) == failure, name
        parts = _apply_repair(case)
        _check_receipt(name, parts["log"], parts["request"],
                       _reference_export(parts["exporter"],
                                         parts["log"],
                                         parts["request"]))
    else:
        _, failure, pins = meta
        _check_rollback(case, failure, pins)


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
    """Wraps an exporter; records every call's (state, format)
    BY VALUE and the container ids it was handed."""

    def __init__(self, exporter):
        self.exporter, self.calls = exporter, []

    def __call__(self, state, fmt):
        try:
            value = (copy.deepcopy(state), copy.deepcopy(fmt))
        except BaseException:  # noqa: BLE001
            value = None
        self.calls.append((value, _containers(state)))
        return self.exporter(state, fmt)


def _engine(cls, exporter):
    counter = _Counting(EXPORTERS[exporter] if isinstance(exporter, str)
                        else exporter)
    return cls(counter), counter


def _receipt_ok(result, expect):
    return (type(result) is dict and result == expect
            and set(result) == set(RECEIPT_FIELDS)
            and type(result["record_count"]) is int
            and all(type(result[f]) is str for f in RECEIPT_FIELDS
                    if f != "record_count"))


def _checked_export(engine, counter, log, request, expect):
    """One successful export: pinned receipt, log and request
    _snap-identical (read-only: same containers, same keys, same
    values), the receipt aliasing nothing of the inputs, and
    exactly one exporter call with a detached by-value copy of the
    replayed state and the exact format."""
    inputs = (log, request)
    snap = _snap(inputs)
    state = _replay(log)["state"]
    fmt = request["format"]
    live = _containers(inputs)
    n_before = len(counter.calls)
    result = engine.export(log, request)
    calls = counter.calls[n_before:]
    ok = (_receipt_ok(result, expect)
          and _snap(inputs) == snap
          and _not_aliased(result, inputs)
          and len(calls) == 1 and calls[0][0] == (state, fmt)
          and type(calls[0][0][1]) is str
          and not calls[0][1] & live)
    return result, ok


def _export_ok(cls, case, engine=None):
    """Pinned receipt, read-only inputs, one exporter call, and
    determinism BY VALUE on the SAME instance (a repeat over fresh
    copies never returns the same object)."""
    eng, counter = engine or _engine(cls, case["exporter"])
    results = []
    for _ in range(2):
        result, ok = _checked_export(
            eng, counter, copy.deepcopy(case["log"]),
            copy.deepcopy(case["request"]), case["expect"])
        if not ok:
            return False
        results.append(result)
    return results[1] is not results[0]


def _rejects(engine, counter, log, request, failure):
    inputs = (log, request)
    snap = _snap(inputs)
    n_before = len(counter.calls)
    try:
        engine.export(log, request)
    except ExportError as error:
        calls = len(counter.calls) - n_before
        return (error.failure_class == failure
                and error.code == FAILURE_MAPPING[failure]
                and error.code in ERROR_ENUM
                and _snap(inputs) == snap
                and calls == (0 if failure in PRE_EXPORT else 1))
    return False


def _malformed_ok(cls, case):
    eng, counter = _engine(cls, case["exporter"])
    if not _rejects(eng, counter, copy.deepcopy(case["log"]),
                    copy.deepcopy(case["request"]),
                    case["expect_failure"]):
        return False
    parts = _apply_repair(case)
    expect = _reference_export(parts["exporter"], parts["log"],
                               parts["request"])
    if parts["exporter"] != case["exporter"]:
        eng, counter = _engine(cls, parts["exporter"])
    _, ok = _checked_export(eng, counter, copy.deepcopy(parts["log"]),
                            copy.deepcopy(parts["request"]), expect)
    return ok


def _rollback_row_ok(cls, case):
    """The hostile exporter bound to the caller's live inputs is
    rejected with inputs restored by value and identity; the
    canonical follow-up on the SAME objects returns the pin."""
    log, request = copy.deepcopy(case["log"]), copy.deepcopy(case["request"])
    engine = _engine(cls, _rollback_exporter(case["name"], log, request))
    if not _rejects(*engine, log, request, case["expect_failure"]):
        return False
    _, ok = _checked_export(*_engine(cls, "canonical"), log, request,
                            case["expect"])
    return ok


_RUNNERS = {"happy": _export_ok, "boundary": _export_ok,
            "malformed": _malformed_ok, "rollback": _rollback_row_ok}


# -- pinned identity-order probe -----------------------------------------------
def _order_inputs():
    """Insertion order E, S, K: neither identity order (K, E, S)
    nor its reverse, so any engine rendering in log order, reverse
    order or any other fixed order diverges from the pin."""
    return (_log_of(("put", AFTER_E4), ("put", STARTPOS),
                    ("put", KINGS)),
            {"format": FMT})


ORDER_EXPECT = {
    "export_id":
        "exp1:08da11699ac0608572b5cb25107bd988e9191d3765fd1f1f5027914b05fdf0a5",
    "head":
        "wal1:fcefb0642082b99eeb2935afb70fbfc9290ea0d0d979828f61332cfbc3d16790",
    "state_id":
        "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
    "record_count": 3,
    "format": "jsonl-v1",
    "document":
        ("{\"identity\":\"('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')"
         "\",\"record\":{\"digest\":\"pdv1:5a52f52530c2a06135595264d05d0e93d"
         "c422ee8fb28818eef7d85a706bb949b\",\"snapshot_fen\":\"4k3/8/8/8/8/8"
         "/8/4K3 w - - 0 1\",\"variant\":\"standard\"}}\n{\"identity\":\"('s"
         "tandard', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR', 'b', 'K"
         "Qkq', '-')\",\"record\":{\"digest\":\"pdv1:736a6cefc8595ac6eafae7e"
         "e667e6490abaa30e2a0969607aee8dce93c2381ff\",\"snapshot_fen\":\"rnb"
         "qkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1\",\"varian"
         "t\":\"standard\"}}\n{\"identity\":\"('standard', 'rnbqkbnr/ppppppp"
         "p/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', 'KQkq', '-')\",\"record\":{\"di"
         "gest\":\"pdv1:66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2c"
         "ae32a8b88ffe\",\"snapshot_fen\":\"rnbqkbnr/pppppppp/8/8/8/8/PPPPPP"
         "PP/RNBQKBNR w KQkq - 0 1\",\"variant\":\"standard\"}}\n"),
}  # GENERATED
ORDER_LABEL = "probe:identity-order"


def _order_ok(cls):
    engine, counter = _engine(cls, "canonical")
    _, ok = _checked_export(engine, counter, *_order_inputs(),
                            ORDER_EXPECT)
    return ok


def _case_order_inputs():
    """Insertion order U, K, L. Codepoint identity order is K, U, L
    and case-folded order is K, L, U: the insertion order is neither
    (nor either reverse), so an engine collating identities any way
    other than exact codepoint order diverges from the pin."""
    return (_log_of(("put", CASE_UPPER), ("put", KINGS),
                    ("put", CASE_LOWER)),
            {"format": FMT})


CASE_ORDER_EXPECT = {
    "export_id":
        "exp1:2329cde23d79ba9c96e86dc83903d40412aa02990bf6d927440dfacd48b43ece",
    "head":
        "wal1:c1df9b50e80faaa0c9407ca3c92538b2a7aa8931806e886714cc1ef20dc8e25d",
    "state_id":
        "gs1:4e130aa680188c85dd097c3d399410fe168ce300911746fa71f720aaa7c41e6e",
    "record_count": 3,
    "format": "jsonl-v1",
    "document":
        ("{\"identity\":\"('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')"
         "\",\"record\":{\"digest\":\"pdv1:5a52f52530c2a06135595264d05d0e93d"
         "c422ee8fb28818eef7d85a706bb949b\",\"snapshot_fen\":\"4k3/8/8/8/8/8"
         "/8/4K3 w - - 0 1\",\"variant\":\"standard\"}}\n{\"identity\":\"('s"
         "tandard', 'K7/8/8/8/8/8/8/7k', 'w', '-', '-')\",\"record\":{\"dige"
         "st\":\"pdv1:03ec5420b8b5d182d06c862953d850c2dc75fec9a7c4fdeceef19b"
         "10a9c08de1\",\"snapshot_fen\":\"K7/8/8/8/8/8/8/7k w - - 0 1\",\"va"
         "riant\":\"standard\"}}\n{\"identity\":\"('standard', 'b7/8/8/8/8/8"
         "/8/K6k', 'w', '-', '-')\",\"record\":{\"digest\":\"pdv1:cf03166e85"
         "84a5b8a5c896205573aedba96080c3bfc97c86738a909ee6198f9d\",\"snapsho"
         "t_fen\":\"b7/8/8/8/8/8/8/K6k w - - 0 1\",\"variant\":\"standard\"}"
         "}\n"),
}  # GENERATED
CASE_ORDER_LABEL = "probe:identity-case-order"


def _case_order_ok(cls):
    engine, counter = _engine(cls, "canonical")
    _, ok = _checked_export(engine, counter, *_case_order_inputs(),
                            CASE_ORDER_EXPECT)
    return ok


# -- closed totality probes ----------------------------------------------------
def _probe_inputs():
    """A valid three-entry log (three live records); every probe
    corrupts one component (or attacks through the exporter)."""
    return (_log_of(("put", STARTPOS), ("put", KINGS),
                    ("put", AFTER_E4)),
            {"format": FMT})


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
        return log, fn(request), "canonical"
    return build


def _log(fn):
    def build():
        log, request = _probe_inputs()
        return fn(log), request, "canonical"
    return build


def _entry(fn, index):
    """Replace the entry at INDEX (0: first, 1: middle, -1: last)
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


def _exporter(exporter):
    def build():
        log, request = _probe_inputs()
        return log, request, exporter
    return build


def _raises(exc):
    def exporter(state, fmt):
        raise exc
    return exporter


def _canonical_then(fn):
    return lambda state, fmt: fn(render_document(state, fmt))


def _drop_first(state, fmt):
    return render_document({k: state[k] for k in sorted(state)[1:]},
                           fmt)


def _log_order(state, fmt):
    """Renders in dict (insertion) order instead of identity
    order."""
    return "".join(json.dumps({"identity": k, "record": dict(v)},
                              sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False) + "\n"
                   for k, v in state.items())


_HOSTILE_EXPORTERS = {
    "raises-value-error": _raises(ValueError("x")),
    "raises-runtime-error": _raises(RuntimeError("x")),
    "raises-keyboard-interrupt": _raises(KeyboardInterrupt()),
    "raises-system-exit": _raises(SystemExit(1)),
    "raises-generator-exit": _raises(GeneratorExit()),
    "returns-none": lambda s, f: None,
    "returns-int": lambda s, f: 7,
    "returns-list": _canonical_then(lambda d: [d]),
    "returns-bytes": _canonical_then(str.encode),
    "returns-str-subclass": _canonical_then(lambda d: _SKV(d)),
    "returns-empty": lambda s, f: "",
    "returns-extra-newline": _canonical_then(lambda d: d + "\n"),
    "returns-crlf": _canonical_then(lambda d: d.replace("\n", "\r\n")),
    "returns-lone-surrogate": _canonical_then(lambda d: d + "\ud800"),
    "returns-ascii-escaped": _canonical_then(
        lambda d: d.replace("'", "\\u0027")),
    "returns-dropped-record": _drop_first,
    "returns-insertion-order": _log_order,
}


class _SKV(str):
    """str subclass VALUE: equal and hashing like its text, but not
    an exact built-in str."""


# Accept-probes: the exporter attacks the caller's OWN log and
# request (built-in methods only) or its own argument at every
# depth, AFTER rendering the canonical document for the state it
# was handed. The export must still return exactly PROBE_EXPECT with
# log and request restored bit-identical.
ACCEPT = None
_LIVE_ATTACKS = ("mutates-first-entry", "mutates-last-entry",
                 "mutates-first-record", "appends-entry",
                 "pops-entry", "clears-log", "reorders-entry-keys",
                 "mutates-request-format", "adds-request-key",
                 "clears-request", "mutates-own-argument-state",
                 "mutates-own-argument-record")


def _attack(kind, log, request, state):
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
    if kind in ("mutates-request-format", "mutates-then-raises"):
        dict.__setitem__(request, "format", "csv-v1")
    if kind == "adds-request-key":
        dict.__setitem__(request, "filter", "all")
    if kind == "clears-request":
        dict.clear(request)
    # own-argument attacks, one per depth: an engine sharing ANY
    # level of the frozen state with the exporter is caught
    if kind == "mutates-own-argument-state":
        dict.pop(state, sorted(state)[0])
    if kind == "mutates-own-argument-record":
        for record in state.values():
            dict.__setitem__(record, "digest", "x")


def _live(kind):
    def build():
        log, request = _probe_inputs()

        def exporter(state, fmt):
            document = render_document(state, fmt)
            _attack(kind, log, request, state)
            if kind == "mutates-then-raises":
                raise ValueError("mutate then explode")
            return document
        return log, request, exporter
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



def _probe_builders():
    """name -> (builder returning (log, request, exporter), pinned
    failure class or ACCEPT)."""
    out = {}
    for label, value in _HOSTILE_CONTAINERS + (("list", []),
                                               ("tuple", ())):
        out[f"request-{label}"] = (
            _req(lambda r, v=value: copy.deepcopy(v)), MER)
    out["request-empty"] = (_req(lambda r: {}), MER)
    out["request-dict-subclass"] = (_req(_DictSub), MER)
    for label, cls in (("str-subclass", SK), ("colliding-hash", HK)):
        out[f"request-key-format-{label}"] = (
            _req(lambda r, c=cls: _rekey(r, "format", c("format"))), MER)
    out["request-key-none"] = (_req(lambda r: {**r, None: 1}), MER)
    out["request-extra-key"] = (_req(lambda r: {**r, "filter": "all"}),
                                MER)
    for label, value in (("none", None), ("true", True), ("int", 1),
                         ("float", 1.0), ("bytes", FMT.encode()),
                         ("list", [FMT]), ("dict", {}),
                         ("int-subclass", _IntSub(1)),
                         ("str-subclass", _SKV(FMT)),
                         ("hostile-str-subclass", SK(FMT))):
        out[f"request-value-{label}"] = (
            _req(lambda r, v=value: {"format": v}), MER)
    for label, value in (("csv", "csv-v1"), ("upper", FMT.upper()),
                         ("empty", ""), ("padded", FMT + " "),
                         ("nul", FMT + "\x00"),
                         ("surrogate", FMT + "\ud800"),
                         ("huge", "j" * 100_000)):
        out[f"request-value-{label}"] = (
            _req(lambda r, v=value: {"format": v}), UF)
    for label, value in _HOSTILE_CONTAINERS + (("dict", {}),
                                               ("tuple", ())):
        out[f"log-{label}"] = (_log(lambda g, v=value: copy.deepcopy(v)),
                               MER)
    out["log-list-subclass"] = (_log(_ListSub), MER)
    out["log-duplicate-entry"] = (_log(lambda g: g + [g[-1]]), CS)
    out["log-reversed"] = (_log(lambda g: g[::-1]), CS)
    out["log-self-referential"] = (_log(_cyclic), CS)
    for index, suffix in ((0, "first"), (1, "middle"), (-1, "last")):
        _entry_family(out, index, suffix)
    for label, exporter in _HOSTILE_EXPORTERS.items():
        out[f"exporter-{label}"] = (_exporter(exporter), DE)
    for kind in _LIVE_ATTACKS:
        out[f"exporter-{kind}"] = (_live(kind), ACCEPT)
    out["exporter-mutates-then-raises"] = (_live("mutates-then-raises"),
                                           DE)
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
    "export_id":
        "exp1:870d676601d4a2d7a0d12e5a14ed622e010cd37f8700bddf79facd3bfdca5560",
    "head":
        "wal1:db87e058ce9d9463d70218b5c77eda90cc750c42336fc9adc82fa2f5e3aef580",
    "state_id":
        "gs1:714c05acf7a590a4cb35438855c64c72f708fb4ffbfd3d0e96f44e062d91b54f",
    "record_count": 3,
    "format": "jsonl-v1",
    "document":
        ("{\"identity\":\"('standard', '4k3/8/8/8/8/8/8/4K3', 'w', '-', '-')"
         "\",\"record\":{\"digest\":\"pdv1:5a52f52530c2a06135595264d05d0e93d"
         "c422ee8fb28818eef7d85a706bb949b\",\"snapshot_fen\":\"4k3/8/8/8/8/8"
         "/8/4K3 w - - 0 1\",\"variant\":\"standard\"}}\n{\"identity\":\"('s"
         "tandard', 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR', 'b', 'K"
         "Qkq', '-')\",\"record\":{\"digest\":\"pdv1:736a6cefc8595ac6eafae7e"
         "e667e6490abaa30e2a0969607aee8dce93c2381ff\",\"snapshot_fen\":\"rnb"
         "qkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1\",\"varian"
         "t\":\"standard\"}}\n{\"identity\":\"('standard', 'rnbqkbnr/ppppppp"
         "p/8/8/8/8/PPPPPPPP/RNBQKBNR', 'w', 'KQkq', '-')\",\"record\":{\"di"
         "gest\":\"pdv1:66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2c"
         "ae32a8b88ffe\",\"snapshot_fen\":\"rnbqkbnr/pppppppp/8/8/8/8/PPPPPP"
         "PP/RNBQKBNR w KQkq - 0 1\",\"variant\":\"standard\"}}\n"),
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
    "request-key-format-str-subclass",
    "request-key-format-colliding-hash",
    "request-key-none",
    "request-extra-key",
    "request-value-none",
    "request-value-true",
    "request-value-int",
    "request-value-float",
    "request-value-bytes",
    "request-value-list",
    "request-value-dict",
    "request-value-int-subclass",
    "request-value-str-subclass",
    "request-value-hostile-str-subclass",
    "request-value-csv",
    "request-value-upper",
    "request-value-empty",
    "request-value-padded",
    "request-value-nul",
    "request-value-surrogate",
    "request-value-huge",
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
    "exporter-raises-value-error",
    "exporter-raises-runtime-error",
    "exporter-raises-keyboard-interrupt",
    "exporter-raises-system-exit",
    "exporter-raises-generator-exit",
    "exporter-returns-none",
    "exporter-returns-int",
    "exporter-returns-list",
    "exporter-returns-bytes",
    "exporter-returns-str-subclass",
    "exporter-returns-empty",
    "exporter-returns-extra-newline",
    "exporter-returns-crlf",
    "exporter-returns-lone-surrogate",
    "exporter-returns-ascii-escaped",
    "exporter-returns-dropped-record",
    "exporter-returns-insertion-order",
    "exporter-mutates-first-entry",
    "exporter-mutates-last-entry",
    "exporter-mutates-first-record",
    "exporter-appends-entry",
    "exporter-pops-entry",
    "exporter-clears-log",
    "exporter-reorders-entry-keys",
    "exporter-mutates-request-format",
    "exporter-adds-request-key",
    "exporter-clears-request",
    "exporter-mutates-own-argument-state",
    "exporter-mutates-own-argument-record",
    "exporter-mutates-then-raises",
)  # GENERATED
PROBE_COUNT = 212  # GENERATED


def _totality_ok(cls, name):
    build, failure = PROBES[name]
    log, request, exporter = build()
    engine, counter = _engine(cls, exporter)
    if failure is ACCEPT:
        # SAME INSTANCE: accepts under attack, then still rejects a
        # bad request typed and still accepts a clean export
        try:
            _, ok = _checked_export(engine, counter, log, request,
                                    PROBE_EXPECT)
            bad_log, _ = _probe_inputs()
            ok = ok and _rejects(engine, counter, bad_log,
                                 {"format": "csv-v1"}, UF)
            _, again = _checked_export(engine, counter,
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
    jobs.append((CASE_ORDER_LABEL, lambda: _case_order_ok(cls)))
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
    return {"export_id": "exp1:" + "0" * 64, "head": GENESIS,
            "state_id": state_id({}), "record_count": 0,
            "format": FMT, "document": ""}


class AcceptsAll(ExportEngine):
    def export(self, log, request):
        try:
            return super().export(log, request)
        except ExportError:
            return _fake_receipt()


class WrongCode(ExportEngine):
    def export(self, log, request):
        try:
            return super().export(log, request)
        except ExportError as error:
            raise ExportError(error.failure_class, "internal") from None


def _remap(frm, to):
    class Remap(ExportEngine):
        def export(self, log, request):
            try:
                return super().export(log, request)
            except ExportError as error:
                if error.failure_class == frm:
                    raise ExportError(to, FAILURE_MAPPING[to]) from None
                raise
    Remap.__name__ = f"Remap_{frm}_to_{to}"
    return Remap


class DoubleExporterCall(ExportEngine):
    def __init__(self, exporter):
        def twice(state, fmt):
            exporter(copy.deepcopy(state), fmt)
            return exporter(state, fmt)
        super().__init__(twice)


def _peek_state(log, request):
    if type(log) is list and type(request) is dict and \
            request.get("format") == FMT:
        try:
            return _replay(log)["state"]
        except BaseException:  # noqa: BLE001
            return None
    return None


class UnguardedExporter(ExportEngine):
    """Calls the exporter once OUTSIDE the boundary, before
    validation."""

    def export(self, log, request):
        state = _peek_state(log, request)
        if state is not None:
            self.exporter(state, FMT)
        return super().export(log, request)


class ExportsBeforeValidation(ExportEngine):
    """Guarded, but calls the exporter an extra time before source
    validation (single evaluation broken)."""

    def export(self, log, request):
        state = _peek_state(log, request)
        if state is not None:
            try:
                self.exporter(state, FMT)
            except BaseException:  # noqa: BLE001
                return super().export(log, request)
        return super().export(log, request)


class MutatesLogOnSuccess(ExportEngine):
    """Not read-only: drops the last entry after a success."""

    def export(self, log, request):
        out = super().export(log, request)
        if log:
            log.pop()
        return out


class MutatesRequestOnSuccess(ExportEngine):
    def export(self, log, request):
        out = super().export(log, request)
        request["format"] = FMT.upper()
        return out


class ReordersEntryKeys(ExportEngine):
    def export(self, log, request):
        out = super().export(log, request)
        for entry in log:
            items = list(entry.items())
            entry.clear()
            entry.update(reversed(items))
        return out


class CopiesEntries(ExportEngine):
    """Replaces the caller's entries with equal deep copies."""

    def export(self, log, request):
        out = super().export(log, request)
        log[:] = copy.deepcopy(log)
        return out


class StaleExportId(ExportEngine):
    def export(self, log, request):
        out = super().export(log, request)
        out["export_id"] = "exp1:" + hashlib.sha256(
            out["document"].encode()).hexdigest()
        return out


class LogLengthCount(ExportEngine):
    def export(self, log, request):
        out = super().export(log, request)
        out["record_count"] = len(log)
        return out


class FirstEntryHead(ExportEngine):
    def export(self, log, request):
        out = super().export(log, request)
        if log:
            out["head"] = log[0]["entry_id"]
        return out


class StrSubclassFormat(ExportEngine):
    """Returns the format as an equal str subclass, not an exact
    built-in str."""

    def export(self, log, request):
        out = super().export(log, request)
        out["format"] = _SKV(out["format"])
        return out


class CachedResult(ExportEngine):
    """Class-level cache: a repeated export returns the SAME
    receipt object."""
    _cache = {}

    def export(self, log, request):
        out = super().export(log, request)
        return CachedResult._cache.setdefault(out["export_id"], out)


class LiveStateArgument(ExportEngine):
    """Hands the exporter a state built over the caller's LIVE
    record dicts instead of a detached copy."""

    def export(self, log, request):
        original = self.exporter
        live = None
        if type(log) is list and _peek_state(log, request) is not None:
            live = {e["payload"]["identity"]: e["payload"]["record"]
                    for e in log if e["op"] == "put"}
        self.exporter = (lambda s, f: original(live, f)) \
            if live is not None else original
        try:
            return super().export(log, request)
        finally:
            self.exporter = original


def _live_head(log):
    if type(log) is list and log:
        return log[-1]
    return None


class HeadPeek(ExportEngine):
    """Reads the head from the caller's LIVE last entry before
    validation (no exact-type guard)."""

    def export(self, log, request):
        last = _live_head(log)
        if last is not None:
            last["entry_id"]  # noqa: B018
        return super().export(log, request)


class RawFirstKeySet(ExportEngine):
    """Compares the first entry's raw key set before the exact-str
    key guard."""

    def export(self, log, request):
        if type(log) is list and log and isinstance(log[0], dict):
            set(log[0].keys()) == set(ENTRY_FIELDS)  # noqa: B015
        return super().export(log, request)


class RawRequestKeySet(ExportEngine):
    def export(self, log, request):
        if isinstance(request, dict):
            set(request.keys()) == {"format"}  # noqa: B015
        return super().export(log, request)


class RawOnNonListLog(ExportEngine):
    def export(self, log, request):
        len(log[0:])
        return super().export(log, request)


class RawInteriorKeySet(ExportEngine):
    """Compares the interior entries' raw key sets before the
    exact-str key guard."""

    def export(self, log, request):
        if type(log) is list:
            for entry in log[1:-1]:
                if isinstance(entry, dict):
                    set(entry.keys()) == set(ENTRY_FIELDS)  # noqa: B015
        return super().export(log, request)


def _casefold_render(state, fmt):
    """render_document with case-folded identity collation."""
    return "".join(
        json.dumps({"identity": key, "record": dict(state[key])},
                   sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False) + "\n"
        for key in sorted(state, key=str.lower))


def _source_mutant(name, edits, extra=None):
    """A one-guard edit of the reference engine: each OLD must
    occur in the reference source (first occurrence replaced);
    EXTRA names are added to the exec namespace."""
    src = inspect.getsource(_reference.ExportEngine)
    for old, new in edits:
        if old not in src:
            raise AssertionError(f"{name}: edit site missing: {old!r}")
        src = src.replace(old, new, 1)
    namespace = dict(vars(_reference), **(extra or {}))
    exec(compile(src, f"<mutant {name}>", "exec"), namespace)  # noqa: S102
    return namespace["ExportEngine"]


_I12 = " " * 12
_ARG = "self.exporter({key: dict(rec) for key, rec in frozen_state.items()}, fmt)"
NoLogRestore = _source_mutant("no-log-restore", [(
    f"{_I12}WalEngine._restore_log(log, saved_container, saved_entries)\n",
    "")])
NoRequestRestore = _source_mutant("no-request-restore", [(
    f"{_I12}request.clear()\n{_I12}request.update(saved_req)\n",
    f"{_I12}pass\n")])
LiveRequestReread = _source_mutant("live-request-reread", [(
    f"{_I12}document = self._export(frozen_state, fmt)\n",
    f"{_I12}document = self._export(frozen_state, fmt)\n"
    f"{_I12}fmt = request.get(\"format\")\n")])
LiveLogBinding = _source_mutant("live-log-binding", [(
    "if document != render_document(frozen_state, fmt):",
    "if document != render_document(_WAL.replay(log)[\"state\"], fmt):")])
NarrowExporterBoundary = _source_mutant("narrow-exporter-boundary", [(
    "except BaseException:", "except Exception:")])
IsinstanceExporterOutput = _source_mutant(
    "isinstance-exporter-output", [(
        "if type(out) is not str:", "if not isinstance(out, str):")])
SkipsDocumentBinding = _source_mutant("skips-document-binding", [(
    "if document != render_document(frozen_state, fmt):", "if False:")])
NoRequestKeyGuard = _source_mutant("no-request-key-guard", [(
    "if not all(type(key) is str for key in dict.keys(request)):",
    "if False:")])
NoFormatTypeGuard = _source_mutant("no-format-type-guard", [(
    "if type(fmt) is not str:", "if False:")])
NoLogTypeGuard = _source_mutant("no-log-type-guard", [(
    "if type(log) is not list:", "if False:")])
SharedExporterArgument = _source_mutant("shared-exporter-argument", [(
    _ARG, "self.exporter(frozen_state, fmt)")])
CasefoldIdentityOrder = _source_mutant("casefold-identity-order", [(
    "if document != render_document(frozen_state, fmt):",
    "if document != _casefold_render(frozen_state, fmt):")],
    {"_casefold_render": _casefold_render})
RecordSharedExporterArgument = _source_mutant(
    "record-shared-exporter-argument", [(
        _ARG, "self.exporter(dict(frozen_state), fmt)")])

MUTANTS = {
    "accepts-all": AcceptsAll,
    "wrong-code": WrongCode,
    "unsupported-as-malformed": _remap(UF, MER),
    "malformed-as-unsupported": _remap(MER, UF),
    "corrupt-as-malformed": _remap(CS, MER),
    "divergent-as-corrupt": _remap(DE, CS),
    "double-exporter-call": DoubleExporterCall,
    "unguarded-exporter": UnguardedExporter,
    "exports-before-validation": ExportsBeforeValidation,
    "mutates-log-on-success": MutatesLogOnSuccess,
    "mutates-request-on-success": MutatesRequestOnSuccess,
    "reorders-entry-keys": ReordersEntryKeys,
    "copies-entries": CopiesEntries,
    "stale-export-id": StaleExportId,
    "log-length-count": LogLengthCount,
    "first-entry-head": FirstEntryHead,
    "str-subclass-format": StrSubclassFormat,
    "cached-result": CachedResult,
    "live-state-argument": LiveStateArgument,
    "head-peek": HeadPeek,
    "raw-first-key-set": RawFirstKeySet,
    "raw-request-key-set": RawRequestKeySet,
    "raw-on-non-list-log": RawOnNonListLog,
    "no-log-restore": NoLogRestore,
    "no-request-restore": NoRequestRestore,
    "live-request-reread": LiveRequestReread,
    "live-log-binding": LiveLogBinding,
    "narrow-exporter-boundary": NarrowExporterBoundary,
    "isinstance-exporter-output": IsinstanceExporterOutput,
    "skips-document-binding": SkipsDocumentBinding,
    "no-request-key-guard": NoRequestKeyGuard,
    "no-format-type-guard": NoFormatTypeGuard,
    "no-log-type-guard": NoLogTypeGuard,
    "shared-exporter-argument": SharedExporterArgument,
    "record-shared-exporter-argument": RecordSharedExporterArgument,
    "casefold-identity-order": CasefoldIdentityOrder,
    "raw-interior-key-set": RawInteriorKeySet,
}

MUTANT_TARGETS = {
    "accepts-all": "malformed:log_not_a_list",
    "wrong-code": "malformed:log_not_a_list",
    "unsupported-as-malformed": "malformed:format_outside_closed_list",
    "malformed-as-unsupported": "malformed:log_not_a_list",
    "corrupt-as-malformed": "malformed:tampered_record_digest",
    "divergent-as-corrupt": "malformed:exporter_raises",
    "double-exporter-call": "happy:three_live_records",
    "unguarded-exporter": "happy:three_live_records",
    "exports-before-validation": "happy:three_live_records",
    "mutates-log-on-success": "happy:three_live_records",
    "mutates-request-on-success": "happy:three_live_records",
    "reorders-entry-keys": "happy:three_live_records",
    "copies-entries": "happy:three_live_records",
    "stale-export-id": "happy:three_live_records",
    "log-length-count": "boundary:put_then_delete_zero_records",
    "first-entry-head": "happy:three_live_records",
    "str-subclass-format": "happy:three_live_records",
    "cached-result": "happy:three_live_records",
    "live-state-argument": "happy:three_live_records",
    "head-peek": "totality:entry-dict-subclass-last",
    "raw-first-key-set": "totality:entry-dict-subclass-first",
    "raw-request-key-set": "totality:request-dict-subclass",
    "raw-on-non-list-log": "malformed:log_not_a_list",
    "no-log-restore": "rollback:mutate_inputs_then_raise",
    "no-request-restore": "rollback:mutate_inputs_then_raise",
    "live-request-reread": "rollback:mutate_inputs_then_diverge",
    "live-log-binding": "rollback:mutate_inputs_then_diverge",
    "narrow-exporter-boundary": "totality:exporter-raises-keyboard-interrupt",
    "isinstance-exporter-output": "totality:exporter-returns-str-subclass",
    "skips-document-binding": "malformed:exporter_reversed_order",
    "no-request-key-guard": "totality:request-key-format-str-subclass",
    "no-format-type-guard": "malformed:format_not_a_string",
    "no-log-type-guard": "malformed:log_not_a_list",
    "shared-exporter-argument": "totality:exporter-mutates-own-argument-state",
    "record-shared-exporter-argument":
        "totality:exporter-mutates-own-argument-record",
    "casefold-identity-order": "probe:identity-case-order",
    "raw-interior-key-set": "totality:entry-key-entry_id-str-subclass-middle",
}  # GENERATED-CHECKED


# -- kill-proof: substitution mutants vs the closure ---------------------------
def _payload(row):
    return {k: v for k, v in row.items() if k != "name"}


def _regrown(row):
    m = copy.deepcopy(row)
    m["log"] = _log_of(("put", KINGS), ("put", AFTER_E4),
                       ("put", STARTPOS))
    m["expect"] = _reference_export("canonical", m["log"], m["request"])
    return m


def _erasures(section, row):
    """Single-edge erasures of ROW with the pinned receipt
    regenerated from the reference where a receipt exists, so only
    the closure's semantic pins can kill them. An erasure equal to
    its row is an equivalent mutant and is dropped."""
    out = []
    if section in ("happy", "boundary"):
        out.append(("log-regrown", _regrown(row)))
        if row["log"]:
            dropped = copy.deepcopy(row)
            dropped["log"] = dropped["log"][:-1]
            dropped["expect"] = _reference_export(
                "canonical", dropped["log"], dropped["request"])
            out.append(("last-entry-dropped", dropped))
        spaced = copy.deepcopy(row)
        spaced["exporter"] = "spaced"
        out.append(("exporter-swapped", spaced))
    elif section == "malformed":
        fixed = copy.deepcopy(row)
        parts = _apply_repair(row)
        fixed.update(log=parts["log"], request=parts["request"],
                     exporter=parts["exporter"])
        out.append(("defect-repaired", fixed))
    else:
        tame = copy.deepcopy(row)
        tame["rejected_exporter"] = "canonical"
        out.append(("exporter-not-hostile", tame))
        out.append(("follow-up-regrown", _regrown(row)))
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
    document = _document_positions(ORDER_EXPECT["document"])
    assert _positions(log) == ("E", "S", "K")
    assert document == ("K", "E", "S")
    assert document not in (_positions(log), _positions(log)[::-1])


def test_case_order_probe_is_discriminating():
    log, request = _case_order_inputs()
    _check_receipt(CASE_ORDER_LABEL, log, request, CASE_ORDER_EXPECT)
    ids = [entry["payload"]["identity"] for entry in log]
    assert sorted(ids) != sorted(ids, key=str.lower)
    assert sorted(ids) != sorted(ids, key=str.casefold)
    document = _document_positions(CASE_ORDER_EXPECT["document"])
    assert _positions(log) == ("U", "K", "L")
    assert document == ("K", "U", "L")
    folded = tuple(_positions(log)[ids.index(i)]
                   for i in sorted(ids, key=str.lower))
    assert folded == ("K", "L", "U") != document
    assert _positions(log) not in (document, document[::-1], folded,
                                   folded[::-1])


def test_probe_expect_is_the_reference_receipt():
    _check_receipt("probe", *_probe_inputs(), PROBE_EXPECT)
    assert PROBE_EXPECT["record_count"] == 3


def test_reference_engine_passes_battery():
    executed = []
    assert _probe(ExportEngine, executed=executed) == []
    assert executed == [f"{s}:{m[0]}" for s, man in MANIFESTS.items()
                        for m in man] + [
        f"totality:{n}" for n in PROBE_MANIFEST] + [ORDER_LABEL,
                                                   CASE_ORDER_LABEL]


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
    assert _probe(ExportEngine, only=set(MUTANT_TARGETS.values())) \
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
