"""T0244: deterministic fuzz/fault battery for the production rollback (store/rollback.py).

Seeded generators build WAL logs and rollback requests from an independent
model of data/contracts/wal.yaml and rollback.yaml (the entry-id, archive-
token and rollback-id derivations are restated here, never taken from
production) and then damage them:

- request: one edit to the request (every target leaf type, int subclass,
  bounds from both sides, dropped, added and same-arity renamed keys,
  str-subclass and hash-colliding keys, a dict subclass), over a clean or
  a corrupt log;
- container: non-list and list-subclass logs;
- source: one log tamper (id and prior flips, sequence shifts, bool
  sequences, drops, swaps, duplicates, unknown ops, re-derived forgeries,
  extra, str-subclass and colliding keys, dict subclasses) with an in-range
  or out-of-range target;
- archiver: an archiver fault at the first, a middle, or the last target
  (every BaseException kind, a forged typed error of every rollback and
  WAL class, non-str, str-subclass, unencodable and grammar-breaking
  output, a grammar-valid token of another tail);
- live: an archiver that edits the caller's live log and request and
  then answers honestly, raises or diverges.

Every case must satisfy, against production: no raw escape (only a typed
RollbackError from the closed class set with its mapped code, never the
forged object); the same outcome as the contract reference engine
(tests.test_t0239_rollback_contract), and where the outcome is
independently known, that outcome; a rejected call leaves log and request
identical by value, exact type, key order and object identity at every
level; a successful rollback truncates exactly the tail, keeps every
surviving entry by identity, leaves the request untouched and equals the
model receipt; one archiver call on a detached copy; no poisoning;
determinism.

Mutation check: _probe runs a fixed slice of every generator against a
source mutant of store/rollback.py. Every entry in MUTANTS must turn the
probe red; every entry in _EQUIVALENT_EDITS must keep it green.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import random
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph.node import make_record, record_identity  # noqa: E402
from store import rollback, wal  # noqa: E402
from tests import test_t0239_rollback_contract as _reference  # noqa: E402
from tools.rollback_contract_lint import FAILURE_MAPPING  # noqa: E402

ROLLBACK_SOURCE = (ROOT / "store" / "rollback.py").read_text()
GENESIS = "wal0:" + "0" * 64
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
MR, CS, UT, DA = ("malformed_rollback_record", "corrupt_source", "unknown_target",
                  "divergent_archive")
GENERATORS = ("request", "container", "source", "archiver", "live")
FUZZ_SEEDS = {"request": 240, "container": 24, "source": 240, "archiver": 336, "live": 144}
PROBE_SEEDS = 48


class _Str(str):
    pass


class _Int(int):
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


def _token(tail):
    """Independent archive token (data/contracts/rollback.yaml)."""
    parts = ["arc1"]
    for e in tail:
        r = e["payload"]["record"]
        for v in (e["sequence"], e["op"], e["entry_id"], e["prior_entry_id"],
                  e["payload"]["identity"], r["variant"], r["digest"], r["snapshot_fen"]):
            parts.append(f"{len(str(v))}:{v}")
    return "arc1:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


def _rollback_id(from_head, to_head, count, token):
    return "rbk1:" + hashlib.sha256(
        f"{from_head}\n{to_head}\n{count}\n{token}".encode()).hexdigest()


def _model_receipt(log, target):
    tail = log[target:]
    from_head = log[-1]["entry_id"] if log else GENESIS
    to_head = log[target - 1]["entry_id"] if target else GENESIS
    token = _token(tail)
    return {"rollback_id": _rollback_id(from_head, to_head, len(tail), token),
            "from_head": from_head, "to_head": to_head,
            "truncated_count": len(tail), "archive_token": token}


def _target(seed, rng, n):
    """First (genesis), middle and last (no-op) targets in turn, then random."""
    return (0, n // 2, n, rng.randint(0, n))[seed % 4]


def _case_of(label, log, request, archiver=None, expect=None, ops=None):
    return {"label": label, "log": log, "request": request, "archiver": archiver,
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
    target = rng.choice((0, len(ops) // 2, len(ops), len(ops) + 5, -1))
    return _case_of(f"source:{kind}@{i}", log, {"target_sequence": target},
                    expect=("err", CS), ops=ops)


LEAVES = (None, True, False, 1.0, 2.5, "1", b"1", _Str("1"), [], {}, (), 2 ** 70, -(2 ** 70))


def _gen_request(seed):
    rng = random.Random(f"request-{seed}")
    ops = _ops(rng)
    log = _model_log(ops)
    n = len(log)
    kinds = ("leaf", "int-subclass", "drop", "add", "rename", "strsub", "colliding",
             "subclass", "none", "list", "below", "above", "edge-low", "edge-high",
             "valid")
    kind = kinds[seed % len(kinds)]
    good = {"target_sequence": _target(seed // len(kinds), rng, n)}
    expect = ("err", MR)
    request = dict(good)
    if kind == "leaf":
        request["target_sequence"] = LEAVES[seed // len(kinds) % len(LEAVES)]
        if type(request["target_sequence"]) is int:
            expect = ("err", UT)
    elif kind == "int-subclass":
        request["target_sequence"] = _Int(good["target_sequence"])
    elif kind == "drop":
        request = {}
    elif kind == "add":
        request["zz" if rng.random() < 0.5 else "é"] = 1
    elif kind == "rename":
        request = {"target_sequencx": good["target_sequence"]}
    elif kind == "strsub":
        request = {_Str("target_sequence"): good["target_sequence"]}
    elif kind == "colliding":
        request = {_Colliding("target_sequence"): good["target_sequence"]}
    elif kind == "subclass":
        request = _Dict(good)
    elif kind == "none":
        request = None
    elif kind == "list":
        request = [("target_sequence", good["target_sequence"])]
    elif kind == "below":
        request["target_sequence"] = rng.choice((-1, -2, -(2 ** 70)))
        expect = ("err", UT)
    elif kind == "above":
        request["target_sequence"] = n + rng.choice((1, 2, 2 ** 70))
        expect = ("err", UT)
    elif kind == "edge-low":
        request["target_sequence"] = 0
        expect = ("ok",)
    elif kind == "edge-high":
        request["target_sequence"] = n
        expect = ("ok",)
    else:
        expect = ("ok",)
    corrupt = expect == ("err", MR) and seed // len(kinds) % 2 == 1
    if corrupt:
        log[0]["sequence"] += 1
    return _case_of(f"request:{kind}{'/corrupt-log' if corrupt else ''}", log, request,
                    expect=expect, ops=None if corrupt else ops)


def _gen_container(seed):
    rng = random.Random(f"container-{seed}")
    good = _model_log(_ops(rng))
    options = (None, {}, "", 0, tuple(good), _List(good), iter(()), b"")
    bad = options[seed % len(options)]
    if type(bad).__name__ == "generator" or not isinstance(bad, (list, tuple, dict, str,
                                                                 bytes, int, type(None))):
        bad = frozenset()
    return _case_of(f"container:{type(bad).__name__}", bad, {"target_sequence": 0},
                    expect=("err", MR))


def _raise(kind):
    def archiver(tail):
        raise kind()
    return archiver


def _forged(error):
    def archiver(tail):
        raise error
    return archiver


FORGED = {
    **{f"forged-rollback-{c}": rollback.RollbackError(c, FAILURE_MAPPING[c])
       for c in sorted(FAILURE_MAPPING)},
    **{f"forged-wal-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
    # builtins the module names in its own except clauses (UnicodeEncodeError)
    # and the likeliest real archiver crash (TypeError), raised as real objects
    "forged-builtin-UnicodeEncodeError": UnicodeEncodeError("utf-8", "x", 0, 1, "forged"),
    "forged-builtin-TypeError": TypeError("forged"),
}
_KINDS = {f"raise-{k.__name__}": k for k in (ValueError, KeyError, RuntimeError,
                                              KeyboardInterrupt, SystemExit, GeneratorExit,
                                              MemoryError, RecursionError)}


def _flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


OUTPUTS = {
    "none": lambda t: None,
    "bytes": lambda t: _token(t).encode(),
    "int": lambda t: 1,
    "list": lambda t: [_token(t)],
    "str-subclass": lambda t: _Str(_token(t)),
    "upper": lambda t: _token(t).upper(),
    "trailing-newline": lambda t: _token(t) + "\n",
    "leading-space": lambda t: " " + _token(t),
    "short": lambda t: _token(t)[:-1],
    "surrogate": lambda t: _token(t)[:-1] + "\ud800",
    "wrong-prefix": lambda t: "arc2:" + _token(t)[5:],
    "grammar-valid-flip": lambda t: _flip(_token(t)),
    "token-of-shorter-tail": lambda t: _token(t[1:]),
    "token-of-longer-tail": lambda t: _token(t + t[-1:]),
    "token-of-empty-tail": lambda t: _token([]),
    "honest": lambda t: _token(t),
    "scribble-arg": lambda t: _scribble_arg(t),
}


def _scribble_arg(tail):
    """Answer honestly, then edit the argument at every level: harmless only
    when the argument is detached from everything the engine still reads."""
    token = _token(tail)
    for entry in tail:
        entry["sequence"] = 99
        entry["payload"]["record"]["digest"] = "x"
        entry["payload"]["identity"] = "y"
        entry["zz"] = 1
    tail.append({"foreign": True})
    return token
ARCHIVER_FAULTS = (*_KINDS, *sorted(FORGED), *OUTPUTS)


def _gen_archiver(seed):
    rng = random.Random(f"archiver-{seed}")
    ops = _ops(rng)
    log = _model_log(ops)
    fault = ARCHIVER_FAULTS[seed % len(ARCHIVER_FAULTS)]
    target = _target(seed // len(ARCHIVER_FAULTS), rng, len(log))
    tail_len = len(log) - target
    if fault in _KINDS:
        archiver, expect = _raise(_KINDS[fault]), ("err", DA)
    elif fault in FORGED:
        archiver, expect = _forged(FORGED[fault]), ("err", DA)
    else:
        archiver = OUTPUTS[fault]
        expect = ("ok",) if fault in ("honest", "scribble-arg") else ("err", DA)
        if fault in ("token-of-shorter-tail", "token-of-longer-tail") and tail_len == 0 \
                or fault == "token-of-empty-tail" and tail_len == 0:
            expect = ("ok",) if fault == "token-of-empty-tail" else None
    return _case_of(f"archiver:{fault}@{target}/{len(log)}", log,
                    {"target_sequence": target},
                    archiver=lambda log_, req_, a=archiver: a, expect=expect, ops=ops)


SCRAMBLES = ("log-append", "log-delete", "log-clear", "entry-edit", "record-clear",
             "request-target", "request-add", "request-clear", "log-reverse")
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
    elif kind == "request-target":
        request["target_sequence"] = 0
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
    target = (1, len(log) // 2, len(log))[seed // (len(SCRAMBLES) * len(ACTIONS)) % 3]

    def make(live_log, live_request):
        def archiver(tail):
            _scramble(kind, live_log, live_request)
            if action == "raise":
                raise RuntimeError("after scribbling")
            token = _token(tail)
            return _flip(token) if action == "divergent" else token
        return archiver

    expect = {"honest": ("ok",), "raise": ("err", DA), "divergent": ("err", DA)}[action]
    return _case_of(f"live:{kind}/{action}@{target}", log, {"target_sequence": target},
                    archiver=make, expect=expect, ops=ops)


GEN = {"request": _gen_request, "container": _gen_container, "source": _gen_source,
       "archiver": _gen_archiver, "live": _gen_live}


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
    inner = _token if case["archiver"] is None else case["archiver"](log, request)
    calls = []

    def archiver(tail):
        calls.append((tail, copy.deepcopy(tail)))
        return inner(tail)

    engine = module.RollbackEngine(archiver)
    before = (_deep(log), _deep(request))
    raised = None
    _Colliding.armed = True
    try:
        outcome = ("ok", engine.rollback(log, request))
    except (rollback.RollbackError, _reference.RollbackError) as error:
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
    if raised is not None and any(raised.__cause__ is forged for forged in FORGED.values()):
        why.append("forged error chained as __cause__")
    if outcome != _reference_outcome(gen, seed):
        why.append(f"reference disagrees {outcome[:2]!r}"[:160])
    expect = case["expect"]
    if expect is not None and (expect[0] != outcome[0] or (
            expect[0] == "err" and outcome[1] != expect[1])):
        why.append(f"expected {expect}, got {outcome[:2]}")
    if len(calls) > 1:
        why.append("archiver called more than once")
    if outcome[0] == "err":
        if after != before:
            why.append("inputs not restored")
    else:
        target = case["request"]["target_sequence"]
        model = _model_log(case["ops"])
        if after[1] != before[1]:
            why.append("request changed")
        if log != model[:target] or after[0][3] != before[0][3][:target]:
            why.append("log not truncated to exactly the surviving prefix")
        if outcome[1] != _model_receipt(model, target):
            why.append("receipt differs from the model")
        if list(outcome[1]) != ["rollback_id", "from_head", "to_head", "truncated_count",
                                "archive_token"]:
            why.append("receipt fields out of order")
        if len(calls) != 1:
            why.append("archiver not called exactly once")
        elif calls[0][1] != model[target:] or any(
                a is b or a["payload"] is b["payload"] or
                a["payload"]["record"] is b["payload"]["record"]
                for a, b in zip(calls[0][0], log[target:] + model[target:], strict=False)):
            why.append("archiver argument not the detached tail")
    if outcome[0] == "err" and not _unpoisoned(module):
        why.append("poisoned after rejection")
    return why


def _unpoisoned(module):
    ops = [("put", 0), ("put", 1), ("delete", 0)]
    log = _model_log(ops)
    try:
        out = module.RollbackEngine(_token).rollback(log, {"target_sequence": 1})
    except BaseException:  # noqa: BLE001 - any failure is poisoning
        return False
    return out == _model_receipt(_model_log(ops), 1) and log == _model_log(ops)[:1]


# -- fuzz tests -------------------------------------------------------------------

def _chunks(gen, size=48):
    total = FUZZ_SEEDS[gen]
    return [(gen, start, min(start + size, total)) for start in range(0, total, size)]


@pytest.mark.parametrize("gen,start,stop", [c for g in GENERATORS for c in _chunks(g)])
def test_fuzz_cases_hold(gen, start, stop):
    failures = []
    for seed in range(start, stop):
        why = _check(rollback, gen, seed)
        if why:
            failures.append((_case(gen, seed)["label"], why))
    assert failures == []


@pytest.mark.parametrize("seed", range(48))
def test_happy_rollbacks_match_the_model_at_every_target(seed):
    rng = random.Random(f"happy-{seed}")
    ops = _ops(rng, 0, 12)
    for target in range(len(ops) + 1):
        log = _model_log(ops)
        survivors = list(log[:target])
        request = {"target_sequence": target}
        out = rollback.RollbackEngine(rollback.archive_tail).rollback(log, request)
        assert out == _model_receipt(_model_log(ops), target)
        assert log == _model_log(ops)[:target]
        assert all(a is b for a, b in zip(log, survivors, strict=True))
        assert request == {"target_sequence": target}
        assert wal.WalEngine(wal.canonical_payload).replay(log)["applied"] == target


def test_boundaries():
    engine = rollback.RollbackEngine(rollback.archive_tail)
    # empty log: only target 0, a no-op with the empty-tail token
    log = []
    assert engine.rollback(log, {"target_sequence": 0}) == _model_receipt([], 0)
    assert log == []
    for bad in (-1, 1):
        with pytest.raises(rollback.RollbackError) as exc:
            engine.rollback([], {"target_sequence": bad})
        assert exc.value.failure_class == UT
    # a 64-entry log: genesis, one step back, and the tip
    ops = [("put" if i % 3 else "delete", i % len(FENS)) for i in range(64)]
    for target in (0, 1, 63, 64):
        log = _model_log(ops)
        assert engine.rollback(log, {"target_sequence": target}) == \
            _model_receipt(_model_log(ops), target)
        assert len(log) == target
    # accept at the ceiling (len), reject one past it
    log = _model_log(ops)
    with pytest.raises(rollback.RollbackError) as exc:
        engine.rollback(log, {"target_sequence": 65})
    assert exc.value.failure_class == UT and log == _model_log(ops)


@pytest.mark.parametrize("gen", GENERATORS)
def test_determinism_by_value(gen):
    for seed in range(0, FUZZ_SEEDS[gen], 5):
        first = _run(rollback, GEN[gen](seed))[0]
        again = _run(rollback, GEN[gen](seed))[0]
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
    assert {f"archiver:{f}" for f in ARCHIVER_FAULTS} <= heads
    assert {f"live:{k}" for k in SCRAMBLES} <= heads
    assert any(label.endswith("/corrupt-log") for label in labels)
    # archiver faults at genesis, a middle and the tip (empty tail) targets
    spots = {("genesis" if t == "0" else "tip" if t == n else "middle")
             for label in labels if label.startswith("archiver:")
             for t, n in [label.split("@")[1].split("/")]}
    assert spots == {"genesis", "middle", "tip"}
    probe = {_reference_outcome(g, s)[1] for g in GENERATORS
             for s in range(min(PROBE_SEEDS, FUZZ_SEEDS[g]))
             if _reference_outcome(g, s)[0] == "err"}
    assert probe == CLASSES


# -- mutation check ---------------------------------------------------------------

def _source_mutant(name, edits):
    """store/rollback.py with EDITS (old, new) applied, each matching exactly
    once, executed as a fresh module; RollbackError and _fail are rebound to
    the production class so typed failures stay comparable."""
    source = ROLLBACK_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0244_mutant_{len(edits)}")
    module.__file__ = str(ROOT / "store" / "rollback.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.RollbackError = rollback.RollbackError

    def _fail(failure_class):
        raise rollback.RollbackError(failure_class, FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _probe(module):
    failures = []
    for gen in GENERATORS:
        for seed in range(min(PROBE_SEEDS, FUZZ_SEEDS[gen])):
            try:
                why = _check(module, gen, seed)
            except BaseException as error:  # noqa: BLE001
                why = [f"check raised {type(error).__name__}"]
            if why:
                failures.append(_case(gen, seed)["label"])
    return failures


_BE = '        except BaseException:\n            _fail("divergent_archive")'
_OUT = "        if type(out) is not str or _TOKEN_RE.fullmatch(out) is None:"
_REQ = ("        if type(request) is not dict or \\\n"
        "                not all(type(key) is str for key in dict.keys(request)) or \\\n"
        "                set(dict.keys(request)) != _REQUEST_FIELDS:")

MUTANTS = {
    "boundary-passthrough": [(_BE, "        except RollbackError:\n            raise\n" + _BE)],
    "boundary-narrow": [(_BE, _BE.replace("BaseException", "Exception"))],
    **{f"boundary-passes-{name}": [(_BE, f"        except {name}:\n            raise\n" + _BE)]
       for name in ("UnicodeEncodeError", "TypeError")},
    "boundary-cause-chain": [(_BE, "        except BaseException as e:\n"
                                   '            raise RollbackError("divergent_archive", '
                                   'FAILURE_MAPPING["divergent_archive"]) from e')],
    **{f"boundary-pass-rollback-{c}": [(_BE, "        except RollbackError as e:\n"
                                         f'            if e.failure_class == "{c}": raise\n'
                                         '            _fail("divergent_archive")\n' + _BE)]
       for c in sorted(FAILURE_MAPPING)},
    **{f"boundary-pass-wal-{c}": [(_BE, "        except _wal.WalError as e:\n"
                                    f'            if e.failure_class == "{c}": raise\n'
                                    '            _fail("divergent_archive")\n' + _BE)]
       for c in sorted(wal.FAILURE_MAPPING)},
    "boundary-wal-as-corrupt-source": [(_BE, "        except _wal.WalError:\n"
                                             '            _fail("corrupt_source")\n' + _BE)],
    "output-isinstance": [(_OUT, _OUT.replace("type(out) is not str",
                                              "not isinstance(out, str)"))],
    "output-type-off": [(_OUT, "        if _TOKEN_RE.fullmatch(out) is None:")],
    "binding-off": [("            if token != archive_tail(frozen_tail):\n"
                     "                _fail(\"divergent_archive\")\n", "")],
    "shared-archiver-argument": [("self.archiver(copy.deepcopy(frozen_tail))",
                                  "self.archiver(frozen_tail)")],
    "archiver-gets-live-tail": [("self.archiver(copy.deepcopy(frozen_tail))",
                                 "self.archiver(log[len(log) - len(frozen_tail):])")],
    "log-type-off": [("        if type(log) is not list:\n"
                      "            _fail(\"malformed_rollback_record\")\n", "")],
    "request-isinstance": [(_REQ, _REQ.replace("type(request) is not dict",
                                               "not isinstance(request, dict)"))],
    "request-key-guard-off": [(_REQ, _REQ.replace(
        "                not all(type(key) is str for key in dict.keys(request)) or \\\n", ""))],
    "request-key-len": [(_REQ, _REQ.replace("set(dict.keys(request)) != _REQUEST_FIELDS",
                                            "len(request) != 1"))],
    "request-key-superset": [(_REQ, _REQ.replace(
        "set(dict.keys(request)) != _REQUEST_FIELDS",
        "not set(dict.keys(request)) >= _REQUEST_FIELDS"))],
    "target-isinstance": [("        if type(target) is not int:",
                           "        if not isinstance(target, int):")],
    "target-type-off": [("        if type(target) is not int:\n"
                         "            _fail(\"malformed_rollback_record\")\n", "")],
    "source-check-off": [("            self._wal.replay(log)\n", "            pass\n")],
    "source-narrow": [("        except _wal.WalError:\n            _fail(\"corrupt_source\")",
                       "        except _wal.WalError as e:\n"
                       '            if e.failure_class != "corrupt_chain": raise\n'
                       '            _fail("corrupt_source")')],
    "target-before-source": [("        try:\n            self._wal.replay(log)\n"
                              "        except _wal.WalError:\n"
                              "            _fail(\"corrupt_source\")\n"
                              "        if target < 0 or target > len(log):\n"
                              "            _fail(\"unknown_target\")\n",
                              "        if target < 0 or target > len(log):\n"
                              "            _fail(\"unknown_target\")\n"
                              "        try:\n            self._wal.replay(log)\n"
                              "        except _wal.WalError:\n"
                              "            _fail(\"corrupt_source\")\n")],
    "request-after-source": [("        target = request[\"target_sequence\"]\n"
                              "        if type(target) is not int:\n"
                              "            _fail(\"malformed_rollback_record\")\n"
                              "        try:\n            self._wal.replay(log)\n"
                              "        except _wal.WalError:\n"
                              "            _fail(\"corrupt_source\")\n",
                              "        try:\n            self._wal.replay(log)\n"
                              "        except _wal.WalError:\n"
                              "            _fail(\"corrupt_source\")\n"
                              "        target = request[\"target_sequence\"]\n"
                              "        if type(target) is not int:\n"
                              "            _fail(\"malformed_rollback_record\")\n")],
    "target-lower-bound-off": [("        if target < 0 or target > len(log):",
                                "        if target > len(log):")],
    "target-upper-ge": [("        if target < 0 or target > len(log):",
                         "        if target < 0 or target >= len(log):")],
    "target-upper-off": [("        if target < 0 or target > len(log):",
                          "        if target < 0:")],
    "no-log-restore": [("            _wal.restore(log, container, saved)\n", "")],
    "no-request-restore": [("            request.clear()\n            request.update(saved_req)",
                            "            pass")],
    "request-no-clear": [("            request.clear()\n            request.update(saved_req)",
                          "            request.update(saved_req)")],
    "restore-only-on-failure": [("        finally:\n"
                                 "            _wal.restore(log, container, saved)",
                                 "        except BaseException:\n"
                                 "            _wal.restore(log, container, saved)")],
    "truncate-off": [("        del log[target:]\n", "")],
    "truncate-offby1": [("        del log[target:]\n", "        del log[target + 1:]\n")],
    "to-head-offby1": [('        to_head = frozen[target - 1]["entry_id"] if target else GENESIS',
                        '        to_head = frozen[target]["entry_id"] if target < len(frozen) '
                        "else GENESIS")],
    "from-head-first": [('        from_head = frozen[-1]["entry_id"] if frozen else GENESIS',
                         '        from_head = frozen[0]["entry_id"] if frozen else GENESIS')],
    "token-no-length-frame": [('            parts.append(f"{len(text)}:{text}")',
                               "            parts.append(text)")],
    "token-drop-prior": [('                      entry["prior_entry_id"], '
                          'entry["payload"]["identity"],',
                          '                      entry["payload"]["identity"],')],
    "rollback-id-swap": [('f"{from_head}\\n{to_head}\\n{truncated_count}',
                          'f"{to_head}\\n{from_head}\\n{truncated_count}')],
    "receipt-order": [('                "from_head": from_head,\n'
                       '                "to_head": to_head,',
                       '                "to_head": to_head,\n'
                       '                "from_head": from_head,')],
}

# edits that must NOT change behavior; each is asserted green
_EQUIVALENT_EDITS = (
    # the log is restored before the count reads it
    ("count-from-live-log", ("        count = len(frozen) - target",
                             "        count = len(log) - target")),
)


def test_identity_source_mutant_is_green():
    module = _source_mutant("identity", [])
    assert module.RollbackEngine is not rollback.RollbackEngine
    assert _probe(module) == []


@pytest.mark.parametrize("name,edit", _EQUIVALENT_EDITS,
                         ids=[name for name, _ in _EQUIVALENT_EDITS])
def test_equivalent_edits_stay_green(name, edit):
    assert _probe(_source_mutant(name, [edit])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(_source_mutant(name, MUTANTS[name])) != [], name
