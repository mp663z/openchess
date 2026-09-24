"""T0253: deterministic fuzz/fault battery for the production export (store/export.py).

Seeded generators build WAL logs and export requests from an independent
model of data/contracts/wal.yaml and export.yaml (the entry-id, state-id,
canonical document and export-id derivations are restated here, never taken
from production) and then damage them:

- request: one edit to the request (every format leaf type, str subclass,
  near-miss and out-of-list strings, dropped, added and same-arity renamed
  keys, str-subclass and hash-colliding keys, a dict subclass), over a clean
  or a corrupt log;
- container: non-list and list-subclass logs;
- source: one log tamper (id and prior flips, sequence shifts, bool
  sequences, drops, swaps, duplicates, unknown ops, re-derived forgeries,
  extra, str-subclass and colliding keys, dict subclasses);
- exporter: an exporter fault over an empty log, a short log and a random
  log (every BaseException kind, a forged typed error of every class the
  module or its linked modules can raise, non-str, str-subclass, unencodable
  and canonically divergent output, an honest answer, an honest answer that
  then scribbles on its argument);
- live: an exporter that edits the caller's live log and request and then
  answers honestly, raises or diverges.

Every case must satisfy, against production: no raw escape (only a typed
ExportError from the closed class set with its mapped code, never the forged
object); the same outcome as the contract reference engine
(tests.test_t0248_export_contract), and where the outcome is independently
known, that outcome; log and request identical by value, exact type, key
order and object identity at every level on every exit; no exporter call on
a request or source rejection; on success exactly one exporter call on a
detached copy equal to the model state, and the model receipt; no poisoning;
determinism.

Mutation check: _probe runs a fixed slice of every generator against a
source mutant of store/export.py. Every entry in MUTANTS must turn the probe
red; every entry in _EQUIVALENT_EDITS must keep it green.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import random
import sys
import types
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph import diff as _diff  # noqa: E402
from graph import position_digest as _digest  # noqa: E402
from graph.node import make_record, record_identity  # noqa: E402
from store import export, wal  # noqa: E402
from tests import test_t0248_export_contract as _reference  # noqa: E402
from tools import variant_runtime as _variant  # noqa: E402
from tools.export_contract_lint import FAILURE_MAPPING  # noqa: E402
from tools.variant_contract_lint import ContractError  # noqa: E402

EXPORT_SOURCE = (ROOT / "store" / "export.py").read_text()
GENESIS = "wal0:" + "0" * 64
FMT = "jsonl-v1"
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "8/8/8/8/8/8/8/K1k5 w - - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
IDENTITIES = tuple(record_identity(record) for record in RECORDS)
CLASSES = frozenset(FAILURE_MAPPING)
MR, UF, CS, DE = ("malformed_export_request", "unsupported_format", "corrupt_source",
                  "divergent_export")
GENERATORS = ("request", "container", "source", "exporter", "live")
FUZZ_SEEDS = {"request": 256, "container": 24, "source": 216, "exporter": 0, "live": 108}
PROBE_SEEDS = 48  # per generator; the exporter slice covers every fault once


class _Str(str):
    pass


class _Dict(dict):
    pass


class _List(list):
    pass


class _Colliding:
    """Hashes like a real name; while armed (only around the engine call)
    comparing it raises, so any comparison before an exact-str key guard
    escapes raw."""

    armed = False

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __deepcopy__(self, memo):
        return self

    def __eq__(self, other):
        if _Colliding.armed:
            raise RuntimeError("hostile __eq__")
        return other is self

    def __ne__(self, other):
        return not self.__eq__(other)


# -- the independent model --------------------------------------------------------

def _entry_id(sequence, op, identity, record, prior):
    canonical = (f"{identity}\n{record['variant']}\n{record['digest']}\n"
                 f"{record['snapshot_fen']}")
    return "wal1:" + hashlib.sha256(
        f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


def _model_log(ops):
    log, prior = [], GENESIS
    for sequence, (op, index) in enumerate(ops, start=1):
        record = dict(RECORDS[index])
        log.append({"entry_id": _entry_id(sequence, op, IDENTITIES[index], record, prior),
                    "sequence": sequence, "op": op,
                    "payload": {"identity": IDENTITIES[index], "record": record},
                    "prior_entry_id": prior})
        prior = log[-1]["entry_id"]
    return log


def _ops(rng, low=1, high=9):
    return [(rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
            for _ in range(rng.randrange(low, high))]


def _model_state(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _state_id(state):
    """Independent canonical state digest (wal.yaml state_id)."""
    text = "".join(f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key]))
                   + "\n" for key in sorted(state))
    return "gs1:" + hashlib.sha256(text.encode()).hexdigest()


def _document(state, fmt=FMT):
    """Independent canonical jsonl-v1 rendering (export.yaml semantics)."""
    return "".join(json.dumps({"identity": key, "record": dict(state[key])}, sort_keys=True,
                              separators=(",", ":"), ensure_ascii=False) + "\n"
                   for key in sorted(state))


def _model_receipt(ops):
    log = _model_log(ops)
    state = _model_state(ops)
    head = log[-1]["entry_id"] if log else GENESIS
    sid, count, doc = _state_id(state), len(state), _document(state)
    export_id = "exp1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{FMT}\n{doc}".encode()).hexdigest()
    return {"export_id": export_id, "head": head, "state_id": sid, "record_count": count,
            "format": FMT, "document": doc}


def _case_of(label, log, request, exporter=None, expect=None, ops=None):
    return {"label": label, "log": log, "request": request, "exporter": exporter,
            "expect": expect, "ops": ops}


# -- generators ------------------------------------------------------------------------

SOURCE_TAMPERS = ("flip-id", "flip-prior", "sequence-shift", "sequence-bool", "drop",
                  "swap", "duplicate", "unknown-op", "op-flip-reforged", "digest-swap",
                  "extra-key", "strsub-key", "colliding-key", "entry-subclass",
                  "payload-subclass", "record-colliding-key", "identity-strsub",
                  "entry-not-dict")


def _reforge(entry):
    p = entry["payload"]
    entry["entry_id"] = _entry_id(entry["sequence"], entry["op"], p["identity"],
                                  p["record"], entry["prior_entry_id"])


def _tamper(log, kind, i, rng):
    e = log[i]
    if kind == "flip-id":
        e["entry_id"] = e["entry_id"][:-1] + ("0" if e["entry_id"][-1] != "0" else "1")
    elif kind == "flip-prior":
        e["prior_entry_id"] = "wal1:" + "1" * 64
        _reforge(e)
    elif kind == "sequence-shift":
        e["sequence"] += 1
        _reforge(e)
    elif kind == "sequence-bool":
        e["sequence"] = True if e["sequence"] == 1 else 1.0 * e["sequence"]
    elif kind == "drop":
        # dropping the last entry is a valid truncation; drop an inner one
        del log[min(i, len(log) - 2)]
        if not log:
            log.append({"x": 1})
    elif kind == "swap":
        j = (i + 1) % len(log)
        if j == i:
            log.append(copy.deepcopy(e))
        else:
            log[i], log[j] = log[j], log[i]
    elif kind == "duplicate":
        log.insert(i, copy.deepcopy(e))
    elif kind == "unknown-op":
        e["op"] = "move"
        _reforge(e)
    elif kind == "op-flip-reforged":
        e["op"] = "delete" if e["op"] == "put" else "put"
        _reforge(e)
        # the forged entry is self-consistent; the next entry's prior breaks
        if i + 1 == len(log):
            e["sequence"] += 1
            _reforge(e)
    elif kind == "digest-swap":
        other = RECORDS[(IDENTITIES.index(e["payload"]["identity"]) + 1) % len(RECORDS)]
        e["payload"]["record"]["digest"] = other["digest"]
        _reforge(e)
    elif kind == "extra-key":
        e["zz"] = 1
    elif kind == "strsub-key":
        e[_Str("op")] = e.pop("op")
    elif kind == "colliding-key":
        e[_Colliding("op")] = 1
    elif kind == "entry-subclass":
        log[i] = _Dict(e)
    elif kind == "payload-subclass":
        e["payload"] = _Dict(e["payload"])
    elif kind == "record-colliding-key":
        e["payload"]["record"][_Colliding("digest")] = "x"
    elif kind == "identity-strsub":
        e["payload"]["identity"] = _Str(e["payload"]["identity"])
    else:
        log[i] = list(e.items())



def _gen_source(seed):
    rng = random.Random(f"source-{seed}")
    ops = _ops(rng, 2)
    log = _model_log(ops)
    kind = SOURCE_TAMPERS[seed % len(SOURCE_TAMPERS)]
    i = (0, len(log) // 2, len(log) - 1)[seed // len(SOURCE_TAMPERS) % 3]
    _tamper(log, kind, i, rng)
    return _case_of(f"source:{kind}@{i}", log, {"format": FMT}, expect=("err", CS), ops=ops)


# format leaves: non-str -> malformed; exact str outside the list -> unsupported
LEAVES = (None, True, 1, 0.0, b"jsonl-v1", _Str(FMT), [FMT], {FMT: 1}, (FMT,),
          "JSONL-V1", "jsonl-v2", "", "jsonl-v1 ", " jsonl-v1", "jsonl-v1\x00", "jsonl-v1\n",
          "jsonl_v1", "jsonl-v1é")


def _gen_request(seed):
    rng = random.Random(f"request-{seed}")
    ops = _ops(rng)
    log = _model_log(ops)
    kinds = ("leaf", "strsub-format", "drop", "add", "rename", "strsub", "colliding",
             "subclass", "none", "list", "unsupported", "valid", "format-dict", "keys-extra")
    kind = kinds[seed % len(kinds)]
    good = {"format": FMT}
    expect = ("err", MR)
    request = dict(good)
    if kind == "leaf":
        request["format"] = LEAVES[seed // len(kinds) % len(LEAVES)]
        if type(request["format"]) is str:
            expect = ("err", UF)
    elif kind == "strsub-format":
        request["format"] = _Str(rng.choice((FMT, "jsonl-v2")))
    elif kind == "drop":
        request = {}
    elif kind == "add":
        request["zz" if rng.random() < 0.5 else "é"] = 1
    elif kind == "rename":
        request = {"formax": FMT}
    elif kind == "strsub":
        request = {_Str("format"): FMT}
    elif kind == "colliding":
        request = {_Colliding("format"): FMT}
    elif kind == "subclass":
        request = _Dict(good)
    elif kind == "none":
        request = None
    elif kind == "list":
        request = [("format", FMT)]
    elif kind == "unsupported":
        request["format"] = rng.choice(("jsonl-v0", "csv", "JSONL-v1", "jsonl-v1\t"))
        expect = ("err", UF)
    elif kind == "format-dict":
        request["format"] = _Dict()
    elif kind == "keys-extra":
        request = {"format": FMT, _Colliding("format"): FMT}
    else:
        expect = ("ok",)
    corrupt = expect[0] == "err" and seed // len(kinds) % 2 == 1
    if corrupt:
        if not log:
            log.append({"x": 1})
        log[0]["sequence"] += 1
    return _case_of(f"request:{kind}{'/corrupt-log' if corrupt else ''}", log, request,
                    expect=expect, ops=None if corrupt else ops)


def _gen_container(seed):
    rng = random.Random(f"container-{seed}")
    good = _model_log(_ops(rng))
    options = (None, {}, "", 0, tuple(good), _List(good), frozenset(), b"")
    bad = options[seed % len(options)]
    return _case_of(f"container:{type(bad).__name__}", bad, {"format": FMT},
                    expect=("err", MR))


def _raise(kind):
    def exporter(state, fmt):
        raise kind()
    return exporter


def _forged(error):
    def exporter(state, fmt):
        raise error
    return exporter


_DIGEST_CLASSES = yaml.safe_load(_digest.CONTRACT.read_text())["contract"]["failures"]["mapping"]
FORGED = {
    **{f"forged-export-{c}": export.ExportError(c, FAILURE_MAPPING[c])
       for c in sorted(FAILURE_MAPPING)},
    **{f"forged-wal-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
    **{f"forged-diff-{c}": _diff.DiffError(c) for c in sorted(_diff.FAILURE_MAPPING)},
    **{f"forged-digest-{c}": _digest.DigestError(c, _DIGEST_CLASSES[c])
       for c in sorted(_DIGEST_CLASSES)},
    **{f"forged-variant-{c}": _variant.VariantError(
        "illegal_position" if c == "illegal_position" else "malformed_request", c, c)
       for c in sorted(_variant.FAILURE_CLASSES)},
    "forged-contract": ContractError("forged"),
    # builtins: UnicodeEncodeError (named in the module's own except clause)
    # and TypeError (the likeliest real exporter crash), raised as real objects
    "forged-builtin-UnicodeEncodeError": UnicodeEncodeError("utf-8", "x", 0, 1, "forged"),
    "forged-builtin-TypeError": TypeError("forged"),
}
_KINDS = {f"raise-{k.__name__}": k for k in (ValueError, KeyError, RuntimeError,
                                              KeyboardInterrupt, SystemExit, GeneratorExit,
                                              MemoryError, RecursionError)}


def _scribble_arg(state, fmt):
    """Answer honestly, then edit the argument at every level: harmless only
    when the argument is detached from everything the engine still reads."""
    doc = _document(state)
    for key in list(state):
        state[key]["digest"] = "x"
        state[key]["zz"] = 1
    state["foreign"] = {"variant": "x"}
    return doc


def _unsorted(state, fmt):
    return "".join(json.dumps({"identity": k, "record": dict(state[k])}, sort_keys=True,
                              separators=(",", ":")) + "\n" for k in reversed(sorted(state)))


def _extra_record(state, fmt):
    extra = dict(state)
    key = next(i for i in IDENTITIES if i not in state) if len(state) < len(IDENTITIES) \
        else "zz"
    extra[key] = dict(RECORDS[0])
    return _document(extra)


OUTPUTS = {
    "none": lambda s, f: None,
    "bytes": lambda s, f: _document(s).encode(),
    "int": lambda s, f: 0,
    "list": lambda s, f: [_document(s)],
    "str-subclass": lambda s, f: _Str(_document(s)),
    "extra-newline": lambda s, f: _document(s) + "\n",
    "no-final-newline": lambda s, f: _document(s)[:-1] if s else " ",
    "crlf": lambda s, f: _document(s).replace("\n", "\r\n") if s else "\r\n",
    "spaced": lambda s, f: _document(s).replace(",", ", ") if s else ", ",
    "reversed": _unsorted,
    "bom": lambda s, f: "\ufeff" + _document(s),
    "surrogate": lambda s, f: _document(s) + "\ud800",
    "extra-record": _extra_record,
    "drop-first-record": lambda s, f: "".join(_document(s).splitlines(True)[1:])
    if s else "\n",
    "ascii-escaped": lambda s, f: "".join(json.dumps({"identity": k, "record": dict(s[k])},
                                                     sort_keys=True, separators=(",", ":"))
                                          + "\n" for k in sorted(s)),
    "honest": lambda s, f: _document(s),
    "scribble-arg": _scribble_arg,
}
EXPORTER_FAULTS = (*_KINDS, *sorted(FORGED), *OUTPUTS)
FUZZ_SEEDS["exporter"] = len(EXPORTER_FAULTS) * 6
PROBE = {g: min(PROBE_SEEDS, FUZZ_SEEDS[g]) for g in GENERATORS}
PROBE["exporter"] = len(EXPORTER_FAULTS)


def _spot_ops(seed, rng):
    """A random log, then an emptied state (put then delete), then an empty log."""
    spot = seed // len(EXPORTER_FAULTS) % 3
    if spot == 2:
        return "empty", []
    if spot == 1:
        index = rng.randrange(len(FENS))
        return "emptied", [("put", index), ("delete", index)]
    return "live", _ops(rng, 3, 10)


def _gen_exporter(seed):
    rng = random.Random(f"exporter-{seed}")
    spot, ops = _spot_ops(seed, rng)
    log = _model_log(ops)
    fault = EXPORTER_FAULTS[seed % len(EXPORTER_FAULTS)]
    if fault in _KINDS:
        exporter, expect = _raise(_KINDS[fault]), ("err", DE)
    elif fault in FORGED:
        exporter, expect = _forged(FORGED[fault]), ("err", DE)
    else:
        exporter = OUTPUTS[fault]
        state = _model_state(ops)
        out = exporter(copy.deepcopy(state), FMT)
        expect = ("ok",) if type(out) is str and out == _document(state) else ("err", DE)
    return _case_of(f"exporter:{fault}@{spot}", log, {"format": FMT},
                    exporter=lambda log_, req_, e=exporter: e, expect=expect, ops=ops)


SCRAMBLES = ("log-append", "log-delete", "log-clear", "entry-edit", "record-clear",
             "request-format", "request-add", "request-clear", "log-reverse")
ACTIONS = ("honest", "raise", "divergent")


def _scramble(kind, log, request):
    if kind == "log-append":
        log.append({"foreign": True})
    elif kind == "log-delete":
        del log[0]
    elif kind == "log-clear":
        log.clear()
    elif kind == "entry-edit":
        log[0]["sequence"] = 99
        log[0]["zz"] = 1
        del log[0]["op"]
    elif kind == "record-clear":
        log[-1]["payload"]["record"].clear()
    elif kind == "request-format":
        request["format"] = "jsonl-v2"
    elif kind == "request-add":
        request["zz"] = 1
    elif kind == "request-clear":
        request.clear()
    else:
        log.reverse()


def _gen_live(seed):
    rng = random.Random(f"live-{seed}")
    ops = _ops(rng, 2)
    log = _model_log(ops)
    kind = SCRAMBLES[seed % len(SCRAMBLES)]
    action = ACTIONS[seed // len(SCRAMBLES) % len(ACTIONS)]

    def make(live_log, live_request):
        def exporter(state, fmt):
            _scramble(kind, live_log, live_request)
            if action == "raise":
                raise RuntimeError("after scribbling")
            doc = _document(state)
            return doc + "\n" if action == "divergent" else doc
        return exporter

    expect = {"honest": ("ok",), "raise": ("err", DE), "divergent": ("err", DE)}[action]
    return _case_of(f"live:{kind}/{action}", log, {"format": FMT}, exporter=make,
                    expect=expect, ops=ops)


GEN = {"request": _gen_request, "container": _gen_container, "source": _gen_source,
       "exporter": _gen_exporter, "live": _gen_live}


@functools.cache
def _case(gen, seed):
    return GEN[gen](seed)


# -- running and checking ---------------------------------------------------------

def _deep(value):
    """Value, exact type, key order and object identity at every level."""
    if isinstance(value, dict):
        return ("d", type(value).__name__, id(value),
                tuple((type(k).__name__, id(k) if type(k) is _Colliding else k, _deep(v))
                      for k, v in dict.items(value)))
    if isinstance(value, (list, tuple)):
        return ("l", type(value).__name__, id(value),
                tuple(_deep(v) for v in list.__iter__(value)) if isinstance(value, list)
                else tuple(_deep(v) for v in value))
    if type(value) is float and value != value:
        return ("nan",)
    return ("v", type(value).__name__, id(value) if type(value) is frozenset else value)


def _run(module, case):
    """(outcome, before, after, log, request, calls, raised)."""
    log = copy.deepcopy(case["log"])
    request = copy.deepcopy(case["request"])
    inner = _document if case["exporter"] is None else case["exporter"](log, request)
    calls = []

    def exporter(state, fmt):
        calls.append((state, copy.deepcopy(state), fmt))
        return inner(state, fmt)

    engine = module.ExportEngine(exporter)
    before = (_deep(log), _deep(request))
    raised = None
    _Colliding.armed = True
    try:
        outcome = ("ok", engine.export(log, request))
    except (export.ExportError, _reference.ExportError) as error:
        outcome = ("err", error.failure_class, error.code)
        raised = error
    except BaseException as error:  # noqa: BLE001 - raw escape is a defect
        outcome = ("raw", type(error).__name__)
    finally:
        _Colliding.armed = False
    return outcome, before, (_deep(log), _deep(request)), log, request, calls, raised


@functools.cache
def _reference_outcome(gen, seed):
    return _run(_reference, _case(gen, seed))[0]


def _records(log):
    return [e["payload"]["record"] for e in log
            if type(e) is dict and type(e.get("payload")) is dict]


def _check(module, gen, seed):
    """Failing reasons for one case through MODULE (empty when it holds)."""
    case = _case(gen, seed)
    outcome, before, after, log, request, calls, raised = _run(module, case)
    if outcome[0] == "raw":
        return [f"raw {outcome[1]}"]
    why = []
    if outcome[0] == "err" and (outcome[1] not in CLASSES or
                                outcome[2] != FAILURE_MAPPING.get(outcome[1])):
        why.append(f"untyped {outcome[1:]}")
    if any(raised is forged for forged in FORGED.values()):
        why.append("forged error object passed through")
    if raised is not None and raised.__cause__ is not None:
        why.append("typed error chained with an explicit __cause__")
    if outcome != _reference_outcome(gen, seed):
        why.append(f"reference disagrees {outcome[:2]!r}"[:160])
    expect = case["expect"]
    if expect is not None and (expect[0] != outcome[0] or (
            expect[0] == "err" and outcome[1] != expect[1])):
        why.append(f"expected {expect}, got {outcome[:2]}")
    if after != before:
        why.append("inputs not restored")
    if len(calls) > 1:
        why.append("exporter called more than once")
    if outcome[0] == "err" and outcome[1] != DE and calls:
        why.append("exporter called before a request or source rejection")
    if outcome[0] == "ok":
        model = _model_receipt(case["ops"])
        if outcome[1] != model:
            why.append("receipt differs from the model")
        if list(outcome[1]) != ["export_id", "head", "state_id", "record_count", "format",
                                "document"]:
            why.append("receipt fields out of order")
        if type(outcome[1]["document"]) is not str or type(outcome[1]["format"]) is not str:
            why.append("receipt strings not exact str")
        if len(calls) != 1:
            why.append("exporter not called exactly once")
        else:
            state, snap, fmt = calls[0]
            live = _records(log)
            if snap != _model_state(case["ops"]) or type(fmt) is not str or fmt != FMT:
                why.append("exporter argument not the model state")
            if type(state) is not dict or any(type(r) is not dict for r in snap.values()) \
                    or any(r is x for r in state.values() for x in live):
                why.append("exporter argument not a detached exact copy")
    if outcome[0] == "err" and not _unpoisoned(module):
        why.append("poisoned after rejection")
    return why


def _unpoisoned(module):
    ops = [("put", 0), ("put", 1), ("delete", 0)]
    log = _model_log(ops)
    try:
        out = module.ExportEngine(_document).export(log, {"format": FMT})
    except BaseException:  # noqa: BLE001 - any failure is poisoning
        return False
    return out == _model_receipt(ops) and log == _model_log(ops)


# -- fuzz tests -------------------------------------------------------------------

def _chunks(gen, size=48):
    total = FUZZ_SEEDS[gen]
    return [(gen, start, min(start + size, total)) for start in range(0, total, size)]


@pytest.mark.parametrize("gen,start,stop", [c for g in GENERATORS for c in _chunks(g)])
def test_fuzz_cases_hold(gen, start, stop):
    failures = []
    for seed in range(start, stop):
        why = _check(export, gen, seed)
        if why:
            failures.append((_case(gen, seed)["label"], why))
    assert failures == []


@pytest.mark.parametrize("seed", range(48))
def test_happy_exports_match_the_model_at_every_prefix(seed):
    rng = random.Random(f"happy-{seed}")
    ops = _ops(rng, 0, 12)
    engine = export.ExportEngine(export.render_document)
    for n in range(len(ops) + 1):
        log = _model_log(ops[:n])
        entries = list(log)
        request = {"format": FMT}
        out = engine.export(log, request)
        assert out == _model_receipt(ops[:n])
        assert out["document"] == _document(_model_state(ops[:n]))
        assert log == _model_log(ops[:n]) and all(
            a is b for a, b in zip(log, entries, strict=True))
        assert request == {"format": FMT}
        assert engine.export(log, request) == out


def test_boundaries():
    engine = export.ExportEngine(export.render_document)
    # empty log: genesis head, empty state, empty document
    assert engine.export([], {"format": FMT}) == _model_receipt([])
    assert _model_receipt([])["document"] == "" and _model_receipt([])["head"] == GENESIS
    # a state emptied by deletes still carries the log tip as head
    ops = [("put", 2), ("delete", 2)]
    out = engine.export(_model_log(ops), {"format": FMT})
    assert out == _model_receipt(ops) and out["record_count"] == 0
    assert out["head"] == _model_log(ops)[-1]["entry_id"]
    # every record live, and a 64-entry log
    every = [("put", i) for i in range(len(FENS))]
    assert engine.export(_model_log(every), {"format": FMT})["record_count"] == len(FENS)
    long_ops = [("put" if i % 3 else "delete", i % len(FENS)) for i in range(64)]
    assert engine.export(_model_log(long_ops), {"format": FMT}) == _model_receipt(long_ops)
    # the one listed format is accepted; its near misses are not
    for fmt in ("jsonl-v1 ", "Jsonl-v1", "jsonl-v"):
        with pytest.raises(export.ExportError) as exc:
            engine.export([], {"format": fmt})
        assert exc.value.failure_class == UF


def test_render_document_is_closed_and_canonical():
    for seed in range(24):
        state = _model_state(_ops(random.Random(f"render-{seed}"), 0, 12))
        assert export.render_document(state, FMT) == _document(state)
    for fmt in ("jsonl-v2", "", _Str("x"), "JSONL-V1"):
        with pytest.raises(ValueError):
            export.render_document({}, fmt)


@pytest.mark.parametrize("gen", GENERATORS)
def test_determinism_by_value(gen):
    for seed in range(0, FUZZ_SEEDS[gen], 5):
        first = _run(export, GEN[gen](seed))[0]
        again = _run(export, GEN[gen](seed))[0]
        assert first == again, _case(gen, seed)["label"]


def test_corpus_reaches_every_class_and_every_edit():
    classes, oks = set(), 0
    for gen in GENERATORS:
        for seed in range(FUZZ_SEEDS[gen]):
            outcome = _reference_outcome(gen, seed)
            if outcome[0] == "err":
                classes.add(outcome[1])
            else:
                oks += 1
    assert classes == CLASSES and oks >= 60
    labels = {_case(g, s)["label"] for g in GENERATORS for s in range(FUZZ_SEEDS[g])}
    heads = {label.split("@")[0].split("/")[0] for label in labels}
    assert {f"source:{t}" for t in SOURCE_TAMPERS} <= heads
    assert {f"exporter:{f}" for f in EXPORTER_FAULTS} <= heads
    assert {f"live:{k}" for k in SCRAMBLES} <= heads
    assert {label for label in labels if label.startswith("exporter:")} == {
        f"exporter:{f}@{s}" for f in EXPORTER_FAULTS for s in ("empty", "emptied", "live")}
    assert any(label.endswith("/corrupt-log") for label in labels)
    assert any(_case("request", s)["label"] == "request:unsupported/corrupt-log"
               for s in range(FUZZ_SEEDS["request"]))
    probe = {_reference_outcome(g, s)[1] for g in GENERATORS
             for s in range(PROBE[g])
             if _reference_outcome(g, s)[0] == "err"}
    assert probe == CLASSES


# -- mutation check ---------------------------------------------------------------

def _source_mutant(name, edits):
    """store/export.py with EDITS (old, new) applied, each matching exactly
    once, executed as a fresh module; ExportError and _fail are rebound to
    the production class so typed failures stay comparable."""
    source = EXPORT_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0253_mutant_{len(edits)}")
    module.__file__ = str(ROOT / "store" / "export.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.ExportError = export.ExportError

    def _fail(failure_class):
        raise export.ExportError(failure_class, FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _probe(module):
    failures = []
    for gen in GENERATORS:
        for seed in range(PROBE[gen]):
            try:
                why = _check(module, gen, seed)
            except BaseException as error:  # noqa: BLE001
                why = [f"check raised {type(error).__name__}"]
            if why:
                failures.append(_case(gen, seed)["label"])
    return failures


_BE = '        except BaseException:\n            _fail("divergent_export")'
_OUT = '        if type(out) is not str:\n            _fail("divergent_export")'
_KEYS = ("        if not all(type(key) is str for key in dict.keys(request)) or \\\n"
         "                set(dict.keys(request)) != _REQUEST_FIELDS:")
_SOURCE = ("        try:\n            replayed = self._wal.replay(log)\n"
           "        except _wal.WalError:\n            _fail(\"corrupt_source\")\n")
_FORMAT = ("        fmt = request[\"format\"]\n        if type(fmt) is not str:\n"
           "            _fail(\"malformed_export_request\")\n"
           "        if fmt not in FORMATS:\n            _fail(\"unsupported_format\")\n")
_CLOSED = "        if fmt not in FORMATS:\n            _fail(\"unsupported_format\")\n"
_CALL = ("self.exporter(\n"
         "                {key: dict(rec) for key, rec in frozen_state.items()}, fmt)")
# a forged-object passthrough for one error class: re-raise it, catch the rest
_FORGE_SINKS = {
    "export": ("ExportError", sorted(FAILURE_MAPPING)),
    "wal": ("_wal.WalError", sorted(wal.FAILURE_MAPPING)),
    "diff": ("_wal.state_id.__globals__['DiffError']", sorted(_diff.FAILURE_MAPPING)),
    "digest": ("_wal.DigestError", sorted(_DIGEST_CLASSES)),
    "variant": ("_wal.VariantError", sorted(_variant.FAILURE_CLASSES)),
}


def _passthrough(error_expr, failure_class):
    return [(_BE, f"        except {error_expr} as e:\n"
                  f'            if e.failure_class == "{failure_class}": raise\n'
                  '            _fail("divergent_export")\n' + _BE)]


MUTANTS = {
    "boundary-passthrough": [(_BE, "        except ExportError:\n            raise\n" + _BE)],
    "boundary-narrow": [(_BE, _BE.replace("BaseException", "Exception"))],
    **{f"boundary-passes-{name}": [(_BE, f"        except {name}:\n            raise\n" + _BE)]
       for name in ("UnicodeEncodeError", "TypeError")},
    "boundary-cause-chain": [(_BE, "        except BaseException as e:\n"
                                   '            raise ExportError("divergent_export", '
                                   'FAILURE_MAPPING["divergent_export"]) from e')],
    **{f"boundary-pass-{sink}-{c}": _passthrough(expr, c)
       for sink, (expr, cls) in _FORGE_SINKS.items() for c in cls},
    "boundary-pass-contract": [(_BE, "        except __import__('tools.variant_contract_lint',"
                                     " fromlist=['x']).ContractError:\n            raise\n"
                                + _BE)],
    "boundary-wal-as-corrupt-source": [(_BE, "        except _wal.WalError:\n"
                                             '            _fail("corrupt_source")\n' + _BE)],
    "output-isinstance": [(_OUT, _OUT.replace("type(out) is not str",
                                              "not isinstance(out, str)"))],
    "output-type-off": [(_OUT, "")],
    "binding-off": [("            if document != render_document(frozen_state, fmt):\n"
                     "                _fail(\"divergent_export\")\n", "")],
    "shared-exporter-argument": [(_CALL, "self.exporter(frozen_state, fmt)")],
    "shallow-exporter-argument": [(_CALL, "self.exporter(dict(frozen_state), fmt)")],
    "log-type-off": [("        if type(log) is not list or type(request) is not dict:",
                      "        if type(request) is not dict:")],
    "log-isinstance": [("        if type(log) is not list or type(request) is not dict:",
                        "        if not isinstance(log, list) or type(request) is not dict:")],
    "request-isinstance": [("        if type(log) is not list or type(request) is not dict:",
                            "        if type(log) is not list or "
                            "not isinstance(request, dict):")],
    "request-key-guard-off": [(_KEYS, _KEYS.replace(
        "        if not all(type(key) is str for key in dict.keys(request)) or \\\n"
        "                set(", "        if set("))],
    "request-key-len": [(_KEYS, _KEYS.replace("set(dict.keys(request)) != _REQUEST_FIELDS",
                                              "len(request) != 1"))],
    "request-key-superset": [(_KEYS, _KEYS.replace(
        "set(dict.keys(request)) != _REQUEST_FIELDS",
        "not set(dict.keys(request)) >= _REQUEST_FIELDS"))],
    "format-isinstance": [("        if type(fmt) is not str:",
                           "        if not isinstance(fmt, str):")],
    "format-type-off": [("        if type(fmt) is not str:\n"
                         "            _fail(\"malformed_export_request\")\n", "")],
    "format-closed-off": [(_CLOSED, "")],
    "format-prefix": [(_CLOSED, _CLOSED.replace("fmt not in FORMATS",
                                                "not fmt.strip().lower() in FORMATS"))],
    "format-after-source": [(_CLOSED + _SOURCE, _SOURCE + _CLOSED)],
    "request-after-source": [(_FORMAT + _SOURCE, _SOURCE + _FORMAT)],
    "source-narrow": [("        except _wal.WalError:\n            _fail(\"corrupt_source\")",
                       "        except _wal.WalError as e:\n"
                       '            if e.failure_class != "corrupt_chain": raise\n'
                       '            _fail("corrupt_source")')],
    "no-log-restore": [("            _wal.restore(log, container, saved)\n", "")],
    "no-request-restore": [("            request.clear()\n"
                            "            request.update(saved_request)", "            pass")],
    "request-no-clear": [("            request.clear()\n"
                          "            request.update(saved_request)",
                          "            request.update(saved_request)")],
    "restore-only-on-failure": [("        finally:\n"
                                 "            _wal.restore(log, container, saved)",
                                 "        except BaseException:\n"
                                 "            _wal.restore(log, container, saved)")],
    "count-from-log": [("        count = len(frozen_state)", "        count = len(log)")],
    "export-id-swap": [("derive_export_id(head, sid, count, fmt,",
                        "derive_export_id(sid, head, count, fmt,")],
    "export-id-no-document": [('f"{head}\\n{sid}\\n{record_count}\\n{fmt}\\n{document}"',
                               'f"{head}\\n{sid}\\n{record_count}\\n{fmt}"')],
    "receipt-head-sid": [('                "head": head,\n'
                          '                "state_id": sid,',
                          '                "head": sid,\n'
                          '                "state_id": head,')],
    "receipt-order": [('                "head": head,\n'
                       '                "state_id": sid,',
                       '                "state_id": sid,\n'
                       '                "head": head,')],
    "render-unsorted": [("        for key in sorted(state))", "        for key in state)")],
    "render-no-sort-keys": [("json.dumps({\"identity\": key, \"record\": dict(state[key])},\n"
                             "                   sort_keys=True,",
                             "json.dumps({\"record\": dict(state[key]), \"identity\": key},\n"
                             "                   sort_keys=False,")],
    "render-separators": [('separators=(",", ":"),', 'separators=(", ", ":"),')],
    "render-no-newline": [('ensure_ascii=False) + "\\n"', 'ensure_ascii=False)')],
}

# edits that must NOT change behavior; each is asserted green
_EQUIVALENT_EDITS = (
    # every reachable document is ASCII (identities are hex digests, records
    # are validated standard FENs), so escaping non-ASCII changes nothing
    ("render-ensure-ascii", ("ensure_ascii=False) + ", "ensure_ascii=True) + ")),
    # the binding compares against the canonical rendering, which is never a
    # surrogate-bearing string, so the separate encode check is redundant
    ("encode-check-off", ("        try:\n            out.encode(\"utf-8\")\n"
                          "        except UnicodeEncodeError:\n"
                          "            _fail(\"divergent_export\")\n", "")),
    # the log is restored before the count; the count is of live records
    # the engine rejects every unlisted format before rendering
    ("render-format-ignored", ("    if fmt not in FORMATS:\n"
                               "        raise ValueError(f\"unsupported format: {fmt!r}\")\n",
                               "")),
    ("count-from-replayed-state", ("        count = len(frozen_state)",
                                   "        count = len(replayed[\"state\"])")),
)


def test_identity_source_mutant_is_green():
    module = _source_mutant("identity", [])
    assert module.ExportEngine is not export.ExportEngine
    assert _probe(module) == []


@pytest.mark.parametrize("name,edit", _EQUIVALENT_EDITS,
                         ids=[name for name, _ in _EQUIVALENT_EDITS])
def test_equivalent_edits_stay_green(name, edit):
    assert _probe(_source_mutant(name, [edit])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(_source_mutant(name, MUTANTS[name])) != [], name
