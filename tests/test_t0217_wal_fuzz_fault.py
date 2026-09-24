"""T0217: deterministic fuzz/fault battery for the production WAL (store/wal.py).

Seeded generators build logs from an independent model of
data/contracts/wal.yaml (the entry-id derivation is restated here, never
taken from production) and then damage them:

- shape: one structural edit at a first, middle or last entry, at the
  entry, payload, record or leaf level (every leaf type, dropped and
  added keys, dict subclasses, str-subclass and hash-colliding keys);
- chain: id flips, relinks, self-consistent re-derived forgeries only
  the link or sequence check can see, bool sequences, swaps, drops,
  duplicates, op and payload swaps;
- request: the same edits on an append request;
- container: non-list and list-subclass logs;
- canon: a canonicalizer fault on one chosen call (every BaseException
  kind, non-str, str-subclass, unencodable and divergent output, a
  scribbled argument);
- live: a canonicalizer that edits the caller's live log and request on
  one chosen call (keys added at every level, cleared records, container
  inserts, deletes and reversals) and then answers honestly, raises or
  diverges.

Every case must satisfy, against production: no raw escape (only a typed
WalError from the closed class set with its mapped code); the same
outcome as the contract reference engine (tests.test_t0212_wal_contract),
and where the outcome is independently known (happy logs, faults), that
outcome; atomicity (a rejected call and every replay leave log and
request identical by value, key order and object identity at every
level; a successful append adds exactly one detached entry); no
poisoning (a fresh engine still replays a clean log to the model state
after the rejection); determinism.

Mutation check: _probe runs a fixed slice of every generator against a
source mutant of store/wal.py. Every entry in MUTANTS is a one-guard edit
that must turn the probe red. Every entry in _EQUIVALENT_EDITS is an edit
that must keep it green, and the test asserts exactly that. Mutants run
with WalError rebound to the production class, guarded by an identity
mutant that must pass.
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

from graph.diff import state_id  # noqa: E402
from graph.node import make_record, record_identity  # noqa: E402
from store import wal  # noqa: E402
from tests import test_t0212_wal_contract as _reference  # noqa: E402
from tools.wal_contract_lint import FAILURE_MAPPING  # noqa: E402

WAL_SOURCE = (ROOT / "store" / "wal.py").read_text()
GENESIS = "wal0:" + "0" * 64
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
IDENTITIES = tuple(record_identity(record) for record in RECORDS)
CLASSES = frozenset(FAILURE_MAPPING)
MWE, UO, SC, CC, DC = ("malformed_wal_entry", "unknown_operation",
                       "sequence_conflict", "corrupt_chain",
                       "divergent_canonicalization")
GENERATORS = ("shape", "chain", "request", "container", "canon", "live")
FUZZ_SEEDS = {"shape": 480, "chain": 240, "request": 240, "container": 24,
              "canon": 240, "live": 240}
PROBE_SEEDS = 48


class _Str(str):
    pass


class _Dict(dict):
    pass


class _List(list):
    pass


class _Colliding:
    """Hashes like a real field name; while armed (only around the engine
    call) comparing it raises, so a comparison before the exact-str key
    guard escapes raw."""

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

    def __repr__(self):
        return f"_Colliding({self.name!r})"


# every leaf type a verifier probes: bool, empty and multi-byte str, an
# unencodable str, bytes, float, a huge int, containers and a str subclass
LEAVES = (None, True, False, 0, 1, -1, 2 ** 70, 1.5, float("nan"), b"put",
          "", "\u00e9", "\U0001d11e", "\ud800", (), [], {}, _Str("put"),
          "put", "delete", "wal1:" + "0" * 64, GENESIS)


# -- the independent model --------------------------------------------------------

def _honest(identity, record):
    """The contract's canonical payload, restated."""
    return (f"{identity}\n{record['variant']}\n{record['digest']}\n"
            f"{record['snapshot_fen']}")


def _entry_id(sequence, op, identity, record, prior):
    canonical = _honest(identity, record)
    return "wal1:" + hashlib.sha256(
        f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


def _entry(sequence, op, index, prior):
    record = dict(RECORDS[index])
    return {"entry_id": _entry_id(sequence, op, IDENTITIES[index], record,
                                  prior),
            "sequence": sequence, "op": op,
            "payload": {"identity": IDENTITIES[index], "record": record},
            "prior_entry_id": prior}


def _model_log(ops):
    log, prior = [], GENESIS
    for position, (op, index) in enumerate(ops, start=1):
        log.append(_entry(position, op, index, prior))
        prior = log[-1]["entry_id"]
    return log


def _fold(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _replay_model(ops):
    log = _model_log(ops)
    state = _fold(ops)
    return {"state": state, "state_id": state_id(state),
            "head": log[-1]["entry_id"] if log else GENESIS,
            "applied": len(ops)}


def _request(op, index):
    return {"op": op, "payload": {"identity": IDENTITIES[index],
                                  "record": dict(RECORDS[index])}}


def _ops(rng, low=1, high=9):
    return [(rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
            for _ in range(rng.randrange(low, high))]


def _index(seed, rng, n):
    """First, middle and last entries in turn, then random."""
    return (0, n // 2, n - 1, rng.randrange(n))[seed % 4]


def _reforge(entry):
    """Re-derive the id of a (tampered) entry so it is self-consistent."""
    payload = entry["payload"]
    entry["entry_id"] = _entry_id(entry["sequence"], entry["op"],
                                  payload["identity"], payload["record"],
                                  entry["prior_entry_id"])


# -- generators -------------------------------------------------------------------

def _paths(value, prefix=()):
    """Every dict path in VALUE, the container itself first."""
    yield prefix
    if type(value) is dict:
        for key in list(value):
            yield from _paths(value[key], prefix + (key,))


def _edit_at(root, path, rng):
    """One structural edit at PATH under ROOT (a dict)."""
    parent, key, node = None, None, root
    for step in path:
        parent, key, node = node, step, node[step]
    kinds = ["leaf"]
    if type(node) is dict:
        kinds += ["add", "add-colliding", "subclass", "rekey"]
        if node:
            kinds += ["rename"]
    if parent is not None:
        kinds += ["drop"]
    if type(node) is str:
        kinds += ["strsub"]
    kind = rng.choice(kinds)
    if kind == "leaf":
        value = copy.deepcopy(rng.choice(LEAVES))
        if parent is None:
            return kind, value
        parent[key] = value
    elif kind == "drop":
        del parent[key]
    elif kind == "add":
        node[rng.choice(("extra", "", "\u00e9", "\U0001d11e"))] = \
            copy.deepcopy(rng.choice(LEAVES))
    elif kind == "add-colliding":
        field = rng.choice(list(node) or ["op"])
        node[_Colliding(field)] = 1
    elif kind == "rename":
        # a PLAIN exact-str key at the same arity: only an exact
        # key-set comparison (not a length check) can see it
        _rename(node, rng.choice(list(node)), rng)
    elif kind == "rekey":
        if node:
            field = rng.choice(list(node))
            value = node.pop(field)
            node[rng.choice((_Str, _Colliding))(field)] = value
        else:
            node[_Str("op")] = 1
    elif kind == "subclass":
        if parent is None:
            return kind, _Dict(node)
        parent[key] = _Dict(node)
    else:
        parent[key] = _Str(node)
    return kind, root


def _rename(node, field, rng):
    """Replace FIELD with a plain str key, same arity, same value,
    keeping the key position."""
    new = next(k for k in (rng.choice((field + "x", "", "\u00e9" + field)),
                           field + "x", field + "xx") if k not in node)
    _rename_to(node, field, new)


def _rename_to(node, field, new):
    items = [(new if key == field else key, value)
             for key, value in list(node.items())]
    node.clear()
    node.update(items)


def _gen_shape(seed):
    rng = random.Random(("shape", seed).__repr__())
    ops = _ops(rng)
    log = _model_log(ops)
    index = _index(seed, rng, len(log))
    path = rng.choice(list(_paths(log[index])))
    kind, log[index] = _edit_at(log[index], path, rng)
    how = ("replay", "append")[seed % 2 if seed % 8 < 4 else rng.randrange(2)]
    return {"label": f"shape:{seed}:{kind}@{index}:{'/'.join(map(repr, path))}",
            "log": log, "request": _request("put", rng.randrange(len(FENS))),
            "how": how, "canon": None, "expect": None, "ops": ops}


CHAIN_TAMPERS = ("flip-id", "relink-genesis", "relink-other", "reforged-prior",
                 "shift-sequence", "reforged-sequence", "bool-sequence",
                 "swap", "drop", "duplicate", "op-switch",
                 "reforged-op-switch", "record-swap", "identity-swap",
                 "reforged-record-swap", "truncate", "unknown-op",
                 "bad-id-grammar", "bad-prior-grammar", "entry-subclass",
                 "payload-subclass", "record-subclass", "str-subclass-op",
                 "str-subclass-identity", "str-subclass-fen",
                 "colliding-entry-key", "colliding-payload-key",
                 "colliding-record-key", "renamed-entry-key",
                 "renamed-payload-key", "renamed-record-key",
                 "entry-id-newline", "prior-newline",
                 "entry-id-leading-space", "prior-leading-space",
                 "renamed-sequence", "renamed-identity", "renamed-digest")


def _gen_chain(seed):
    rng = random.Random(("chain", seed).__repr__())
    ops = _ops(rng, 2, 10)
    log = _model_log(ops)
    n = len(log)
    index = _index(seed, rng, n)
    tamper = CHAIN_TAMPERS[seed % len(CHAIN_TAMPERS)]
    entry = log[index]
    expect = None
    # a record and identity that are really different from the entry's
    other = (IDENTITIES.index(entry["payload"]["identity"]) + 1) % len(FENS)
    if tamper == "flip-id":
        tail = "0" if entry["entry_id"][-1] != "0" else "1"
        entry["entry_id"] = entry["entry_id"][:-1] + tail
        expect = ("err", CC)
    elif tamper == "relink-genesis":
        if index == 0:
            index, entry = n - 1, log[n - 1]
        entry["prior_entry_id"] = GENESIS
        expect = ("err", CC)
    elif tamper == "relink-other":
        entry["prior_entry_id"] = "wal1:" + "ab" * 32
        expect = ("err", CC)
    elif tamper == "reforged-prior":
        entry["prior_entry_id"] = "wal1:" + "cd" * 32
        _reforge(entry)
        expect = ("err", CC)
    elif tamper == "shift-sequence":
        entry["sequence"] += rng.choice((1, -1, 7))
        expect = ("err", SC)
    elif tamper == "reforged-sequence":
        entry["sequence"] += rng.choice((1, -1))
        _reforge(entry)
        expect = ("err", SC)
    elif tamper == "bool-sequence":
        index, entry = 0, log[0]
        entry["sequence"] = True
        _reforge(entry)
        expect = ("err", MWE)
    elif tamper == "swap":
        j = index + 1 if index + 1 < n else index - 1
        log[index], log[j] = log[j], log[index]
    elif tamper == "drop":
        del log[index]
        if index == len(log):
            # dropping the last entry is a valid truncation
            ops = ops[:index]
    elif tamper == "duplicate":
        log.insert(index, copy.deepcopy(entry))
    elif tamper in ("op-switch", "reforged-op-switch"):
        entry["op"] = "delete" if entry["op"] == "put" else "put"
        if tamper == "reforged-op-switch":
            _reforge(entry)
            for later in log[index + 1:]:
                later["prior_entry_id"] = log[log.index(later) - 1]["entry_id"]
                _reforge(later)
            ops = [(e["op"], IDENTITIES.index(e["payload"]["identity"]))
                   for e in log]
            expect = ("ok", None)
        else:
            expect = ("err", CC)
    elif tamper in ("record-swap", "reforged-record-swap"):
        entry["payload"]["record"] = dict(RECORDS[other])
        if tamper == "reforged-record-swap":
            _reforge(entry)
        expect = ("err", MWE)
    elif tamper == "identity-swap":
        entry["payload"]["identity"] = IDENTITIES[other]
        expect = ("err", MWE)
    elif tamper == "unknown-op":
        entry["op"] = rng.choice(("upsert", "", "PUT", "\u00e9"))
        _reforge(entry)
        expect = ("err", UO)
    elif tamper == "bad-id-grammar":
        entry["entry_id"] = rng.choice(("wal2:", "wal1:", "WAL1:")) + \
            entry["entry_id"][5:].upper()
        expect = ("err", MWE)
    elif tamper == "bad-prior-grammar":
        entry["prior_entry_id"] = entry["prior_entry_id"][:-1]
        expect = ("err", MWE)
    elif tamper.endswith("-subclass"):
        payload = entry["payload"]
        if tamper == "entry-subclass":
            log[index] = _Dict(entry)
        elif tamper == "payload-subclass":
            entry["payload"] = _Dict(payload)
        else:
            payload["record"] = _Dict(payload["record"])
        expect = ("err", MWE)
    elif tamper.startswith("str-subclass"):
        payload = entry["payload"]
        if tamper == "str-subclass-op":
            entry["op"] = _Str(entry["op"])
        elif tamper == "str-subclass-identity":
            payload["identity"] = _Str(payload["identity"])
        else:
            payload["record"]["snapshot_fen"] = _Str(
                payload["record"]["snapshot_fen"])
        expect = ("err", MWE)
    elif tamper in ("renamed-entry-key", "renamed-payload-key",
                    "renamed-record-key"):
        node = {"renamed-entry-key": entry,
                "renamed-payload-key": entry["payload"],
                "renamed-record-key": entry["payload"]["record"]}[tamper]
        _rename(node, rng.choice(list(node)), rng)
        expect = ("err", MWE)
    elif tamper in ("renamed-sequence", "renamed-identity", "renamed-digest"):
        # the exact probes: sequencx, identitx, digesx at the same arity
        field = tamper.split("-")[1]
        node = {"sequence": entry, "identity": entry["payload"],
                "digest": entry["payload"]["record"]}[field]
        _rename_to(node, field, field[:-1] + "x")
        expect = ("err", MWE)
    elif tamper in ("entry-id-leading-space", "prior-leading-space"):
        field = "entry_id" if tamper == "entry-id-leading-space" \
            else "prior_entry_id"
        entry[field] = " " + entry[field]
        expect = ("err", MWE)
    elif tamper in ("entry-id-newline", "prior-newline"):
        # grammars end in $, which also matches before a final newline:
        # only fullmatch rejects these
        field = "entry_id" if tamper == "entry-id-newline" \
            else "prior_entry_id"
        entry[field] += "\n"
        expect = ("err", MWE)
    elif tamper.startswith("colliding"):
        node = {"colliding-entry-key": entry,
                "colliding-payload-key": entry["payload"],
                "colliding-record-key": entry["payload"]["record"]}[tamper]
        field = rng.choice(list(node))
        node[_Colliding(field)] = node.pop(field)
        expect = ("err", MWE)
    else:
        del log[index + 1:]
        ops = ops[:index + 1]
        expect = ("ok", None)
    how = ("replay", "append")[(seed // len(CHAIN_TAMPERS)) % 2]
    return {"label": f"chain:{seed}:{tamper}@{index}", "log": log,
            "request": _request("delete", rng.randrange(len(FENS))),
            "how": how, "canon": None, "expect": expect, "ops": ops}


def _gen_request(seed):
    rng = random.Random(("request", seed).__repr__())
    ops = _ops(rng, 0, 6)
    log = _model_log(ops)
    request = _request(rng.choice(("put", "delete")), rng.randrange(len(FENS)))
    mode = seed % 16
    if mode == 0:
        kind, request = "leaf", copy.deepcopy(rng.choice(LEAVES))
    elif mode == 1:
        # cycled, not drawn, so the str-subclass op is always probed
        request["op"] = (_Str("put"), "Put", "\u00e9", "", "upsert", "put ",
                         "delete\x00")[seed // 16 % 7]
        kind = f"op={request['op']!r}"
    elif mode == 2:
        request["payload"]["record"] = dict(RECORDS[
            (IDENTITIES.index(request["payload"]["identity"]) + 1)
            % len(FENS)])
        kind = "identity-record-mismatch"
    elif mode == 3:
        path = rng.choice(list(_paths(request)))
        kind, request = _edit_at(request, path, rng)
        kind += "@" + "/".join(map(repr, path))
    elif mode == 8:
        node = rng.choice((request, request["payload"],
                           request["payload"]["record"]))
        field = rng.choice(list(node))
        _rename(node, field, rng)
        kind = f"renamed-{field}"
    elif mode == 9:
        kind, request = "request-subclass", _Dict(request)
    elif mode in (10, 11):
        # the exact probes: {"op", "payloax"} and {"oq", "payload"}
        field, new = (("payload", "payloax"), ("op", "oq"))[mode - 10]
        _rename_to(request, field, new)
        kind = f"renamed-{field}-{new}"
    elif mode in (12, 13, 14):
        # a strictly larger plain-str key set: request-level with an ASCII
        # and a non-ASCII extra key, then payload-level (superset probes)
        node, key = ((request, "extra"), (request, "\u00e9xtra"),
                     (request["payload"], "extra"))[mode - 12]
        node[key] = 1
        kind = ("request-extra", "request-extra-nonascii",
                "payload-extra")[mode - 12]
    elif mode == 15:
        # a log entry with a strictly larger key set (entry superset probe)
        if not ops:
            ops = [("put", rng.randrange(len(FENS)))]
            log = _model_log(ops)
        log[_index(seed // 16, rng, len(log))]["extra"] = 1
        kind = "entry-extra"
    else:
        payload = request["payload"]
        kind = ("payload-subclass", "record-subclass", "identity-str-subclass",
                "colliding-key")[mode - 4]
        if mode == 4:
            request["payload"] = _Dict(payload)
        elif mode == 5:
            payload["record"] = _Dict(payload["record"])
        elif mode == 6:
            payload["identity"] = _Str(payload["identity"])
        else:
            node = rng.choice((request, payload, payload["record"]))
            field = rng.choice(list(node))
            node[_Colliding(field)] = node.pop(field)
    return {"label": f"request:{seed}:{kind}", "log": log, "request": request,
            "how": "append", "canon": None, "expect": None, "ops": ops}


def _gen_container(seed):
    rng = random.Random(("container", seed).__repr__())
    ops = _ops(rng)
    log = _model_log(ops)
    builders = (tuple, lambda x: None, lambda x: {"entries": x},
                lambda x: "log", _List, lambda x: 0)
    build = builders[seed % len(builders)]
    how = ("replay", "append")[(seed // len(builders)) % 2]
    return {"label": f"container:{seed}", "log": build(log),
            "request": _request("put", 0), "how": how, "canon": None,
            "expect": ("err", MWE), "ops": ops}


def _raise(kind):
    def fault(identity, record):
        raise kind("fault")
    return fault


FAULTS = {
    "ValueError": _raise(ValueError),
    "KeyboardInterrupt": _raise(KeyboardInterrupt),
    "SystemExit": _raise(SystemExit),
    "GeneratorExit": _raise(GeneratorExit),
    "RecursionError": _raise(RecursionError),
    "none": lambda identity, record: None,
    "bytes": lambda identity, record: _honest(identity, record).encode(),
    "int": lambda identity, record: 7,
    "str-subclass": lambda identity, record: _Str(_honest(identity, record)),
    "unencodable": lambda identity, record: _honest(identity, record) + "\ud800",
    "divergent": lambda identity, record: _honest(identity, record) + " ",
    "empty": lambda identity, record: "",
}


def _scribble_arg(identity, record):
    out = _honest(identity, record)
    record["variant"] = "chess960"
    record["extra"] = 1
    record.pop("digest")
    return out


FAULTS["scribble-arg"] = _scribble_arg


def _forged(failure_class):
    """A canonicalizer raising a PRE-BUILT typed WalError of another
    class: the boundary must still report divergent_canonicalization,
    never pass the forged class through."""
    error = wal.WalError(failure_class, FAILURE_MAPPING[failure_class])

    def fault(identity, record):
        raise error
    return fault


for _cls in sorted(FAILURE_MAPPING):
    if _cls != "divergent_canonicalization":
        FAULTS[f"forged-{_cls}"] = _forged(_cls)


def _on_call(k, fault):
    """Honest on every call but the K-th (0-based), where FAULT answers."""
    def factory(log, request):
        calls = [0]

        def canon(identity, record):
            calls[0] += 1
            if calls[0] - 1 == k:
                return fault(identity, record)
            return _honest(identity, record)
        return canon
    return factory


def _fault_expect(fault_name, k, n, how, ops):
    """Independent expectation of a single-call fault."""
    if fault_name == "scribble-arg":
        return ("ok", None)
    if fault_name in ("divergent", "empty"):
        return ("ok", "divergent-id") if how == "append" and k == n \
            else ("err", CC)
    return ("err", DC)


def _gen_canon(seed):
    rng = random.Random(("canon", seed).__repr__())
    ops = _ops(rng, 0, 8)
    n = len(ops)
    how = ("replay", "append")[seed % 2]
    calls = n + (how == "append")
    if calls == 0:
        how, calls = "append", 1
    names = sorted(FAULTS)
    name = names[(seed // 2) % len(names)]
    k = (0, calls - 1, calls // 2, rng.randrange(calls))[(seed // 26) % 4]
    return {"label": f"canon:{seed}:{name}@call{k}/{calls}:{how}",
            "log": _model_log(ops), "request": _request("put", seed % len(FENS)),
            "how": how, "canon": _on_call(k, FAULTS[name]),
            "expect": _fault_expect(name, k, n, how, ops), "ops": ops}


SCRAMBLES = ("entry-add", "payload-add", "record-add", "record-clear",
             "entry-drop", "log-insert", "log-delete", "log-reverse",
             "log-clear", "request-add", "request-payload-add",
             "request-record-add", "request-op", "request-record-clear")
ACTIONS = ("honest", "raise", "divergent")


def _scramble(kind, log, request, rng):
    """Edit the caller's LIVE inputs (never the detached argument)."""
    if kind.startswith("request"):
        payload = request["payload"]
        target = {"request-add": request, "request-payload-add": payload,
                  "request-record-add": payload["record"]}.get(kind)
        if target is not None:
            target[rng.choice(("zz", "\u00e9", "op"))+"_live"] = 1
        elif kind == "request-op":
            request["op"] = "delete" if request["op"] == "put" else "put"
        else:
            payload["record"].clear()
        return
    if not log:
        log.append({"forged": True})
        return
    entry = log[rng.randrange(len(log))]
    if kind == "entry-add":
        entry["zz_live"] = 1
    elif kind == "payload-add":
        entry["payload"]["zz_live"] = 1
    elif kind == "record-add":
        entry["payload"]["record"]["zz_live"] = 1
    elif kind == "record-clear":
        entry["payload"]["record"].clear()
    elif kind == "entry-drop":
        entry.pop("prior_entry_id")
    elif kind == "log-insert":
        log.insert(rng.randrange(len(log) + 1), {"forged": True})
    elif kind == "log-delete":
        del log[rng.randrange(len(log))]
    elif kind == "log-reverse":
        log.reverse()
        log.append(copy.deepcopy(log[0]))
    else:
        log.clear()


def _live(k, kind, action, seed):
    def factory(log, request):
        calls = [0]
        rng = random.Random(("live-scramble", seed).__repr__())

        def canon(identity, record):
            calls[0] += 1
            out = _honest(identity, record)
            if calls[0] - 1 != k:
                return out
            _scramble(kind, log, request, rng)
            if action == "raise":
                raise KeyboardInterrupt("after scribbling")
            return out + " " if action == "divergent" else out
        return canon
    return factory


def _gen_live(seed):
    rng = random.Random(("live", seed).__repr__())
    ops = _ops(rng, 1, 8)
    n = len(ops)
    how = ("replay", "append")[seed % 2]
    calls = n + (how == "append")
    kind = SCRAMBLES[(seed // 2) % len(SCRAMBLES)]
    if how == "replay" and kind.startswith("request"):
        # replay has no request; the scramble hits the live log instead
        kind = SCRAMBLES[(seed // 2) % 9]
    action = ACTIONS[(seed // 28) % 3]
    k = (0, calls - 1, rng.randrange(calls))[(seed // 84) % 3]
    expect = {"honest": ("ok", None), "raise": ("err", DC),
              "divergent": _fault_expect("divergent", k, n, how, ops)}[action]
    return {"label": f"live:{seed}:{kind}:{action}@call{k}/{calls}:{how}",
            "log": _model_log(ops), "request": _request("put", seed % len(FENS)),
            "how": how, "canon": _live(k, kind, action, seed),
            "expect": expect, "ops": ops}


GEN = {"shape": _gen_shape, "chain": _gen_chain, "request": _gen_request,
       "container": _gen_container, "canon": _gen_canon, "live": _gen_live}


@functools.cache
def _case(gen, seed):
    return GEN[gen](seed)


# -- running and checking ---------------------------------------------------------

def _deep(value):
    """Value, exact type, key order and object identity at every level."""
    if type(value) in (dict, _Dict):
        return ("d", type(value).__name__, id(value),
                tuple((key, _deep(item)) for key, item in dict.items(value)))
    if type(value) in (list, _List):
        return ("l", type(value).__name__, id(value),
                tuple(_deep(item) for item in list.__iter__(value)))
    if type(value) is float and value != value:
        return ("nan",)
    return ("v", type(value).__name__, value)


def _run(module, case):
    """(outcome, before, after, log, request) of CASE through MODULE on a
    private deep copy; outcome is ("ok", result) | ("err", class, code)
    | ("raw", exception type)."""
    log = copy.deepcopy(case["log"])
    request = copy.deepcopy(case["request"])
    canon = case["canon"](log, request) if case["canon"] else _honest
    engine = module.WalEngine(canon)
    before = (_deep(log), _deep(request))
    _Colliding.armed = True
    try:
        if case["how"] == "replay":
            outcome = ("ok", engine.replay(log))
        else:
            outcome = ("ok", engine.append(log, request))
    except (wal.WalError, _reference.WalError) as error:
        outcome = ("err", error.failure_class, error.code)
    except BaseException as error:  # noqa: BLE001 - raw escape is a defect
        outcome = ("raw", type(error).__name__)
    finally:
        _Colliding.armed = False
    return outcome, before, (_deep(log), _deep(request)), log, request


@functools.cache
def _reference_outcome(gen, seed):
    return _run(_reference, _case(gen, seed))[0]


def _check(module, gen, seed):
    """Failing reasons for one case through MODULE (empty when it holds)."""
    case = _case(gen, seed)
    outcome, before, after, log, request = _run(module, case)
    why = []
    if outcome[0] == "raw":
        return [f"raw {outcome[1]}"]
    if outcome[0] == "err" and (outcome[1] not in CLASSES or outcome[2]
                                != FAILURE_MAPPING.get(outcome[1])):
        why.append(f"untyped {outcome[1:]}")
    if outcome != _reference_outcome(gen, seed):
        why.append(f"reference disagrees {outcome[:2]!r}"[:160])
    expect = case["expect"]
    if expect is not None and (expect[0] != outcome[0] or (
            expect[0] == "err" and outcome[1] != expect[1])):
        why.append(f"expected {expect}, got {outcome[:2]}")
    if outcome[0] == "err" or case["how"] == "replay":
        if after != before:
            why.append("inputs not restored")
    else:
        why += _append_ok_problems(case, outcome[1], before, after, log,
                                   request)
    if outcome[0] == "ok" and case["how"] == "replay" and \
            outcome[1] != _replay_model(case["ops"]):
        why.append("replay differs from the model")
    if outcome[0] == "err" and not _unpoisoned(module, case["ops"]):
        why.append("poisoned after rejection")
    return why


def _append_ok_problems(case, entry, before, after, log, request):
    why = []
    if after[1] != before[1]:
        why.append("request changed")
    old = before[0][3]
    new = after[0][3]
    if new[:-1] != old or len(new) != len(old) + 1:
        why.append("existing entries not preserved")
        return why
    committed = log[-1]
    if committed != entry or committed is entry or \
            committed["payload"] is entry["payload"] or \
            committed["payload"]["record"] is entry["payload"]["record"]:
        why.append("returned entry not a detached copy")
    if type(request) is dict and type(request.get("payload")) is dict and (
            committed["payload"] is request["payload"] or
            committed["payload"]["record"] is request["payload"].get("record")):
        why.append("committed entry aliases the request")
    ops = case["ops"]
    n = len(ops)
    prior = log[-2]["entry_id"] if n else GENESIS
    want = _entry(n + 1, request["op"],
                  IDENTITIES.index(request["payload"]["identity"]), prior)
    if case["expect"] == ("ok", "divergent-id"):
        if committed["entry_id"] == want["entry_id"] or \
                {**committed, "entry_id": want["entry_id"]} != want:
            why.append("divergent final call did not change only the id")
        elif _outcome_of(log) != ("err", CC):
            why.append("divergent id not rejected by a later replay")
    elif committed != want:
        why.append("appended entry differs from the model")
    return why


def _outcome_of(log):
    try:
        wal.WalEngine(_honest).replay(copy.deepcopy(log))
    except wal.WalError as error:
        return ("err", error.failure_class)
    return ("ok",)


def _unpoisoned(module, ops):
    """A fresh engine of MODULE still replays the clean model log (the
    same records the rejected case used) to the model state."""
    clean = ops or [("put", 0)]
    try:
        return module.WalEngine(_honest).replay(_model_log(clean)) == \
            _replay_model(clean)
    except BaseException:  # noqa: BLE001 - any failure is poisoning
        return False


# -- fuzz tests -------------------------------------------------------------------

def _chunks(gen, size=48):
    total = FUZZ_SEEDS[gen]
    return [(gen, start, min(start + size, total))
            for start in range(0, total, size)]


@pytest.mark.parametrize("gen,start,stop",
                         [c for g in GENERATORS for c in _chunks(g)])
def test_fuzz_cases_hold(gen, start, stop):
    failures = []
    for seed in range(start, stop):
        why = _check(wal, gen, seed)
        if why:
            failures.append((_case(gen, seed)["label"], why))
    assert failures == []


@pytest.mark.parametrize("seed", range(64))
def test_happy_logs_match_the_model_on_every_prefix(seed):
    rng = random.Random(("happy", seed).__repr__())
    ops = _ops(rng, 0, 25)
    engine = wal.WalEngine(wal.canonical_payload)
    log = []
    for position, (op, index) in enumerate(ops, start=1):
        request = _request(op, index)
        entry = engine.append(log, request)
        assert entry == _model_log(ops[:position])[-1]
        assert request == _request(op, index)
    assert log == _model_log(ops)
    for k in range(len(ops) + 1):
        prefix = log[:k]
        before = _deep(prefix)
        assert engine.replay(prefix) == _replay_model(ops[:k])
        assert _deep(prefix) == before


def test_boundaries():
    engine = wal.WalEngine(wal.canonical_payload)
    assert engine.replay([]) == _replay_model([])
    for index in range(len(FENS)):
        for op in ("put", "delete"):
            log = []
            assert engine.append(log, _request(op, index)) == \
                _entry(1, op, index, GENESIS)
            assert engine.replay(log) == _replay_model([(op, index)])
    long_ops = [("put" if i % 3 else "delete", i % len(FENS))
                for i in range(64)]
    assert engine.replay(_model_log(long_ops)) == _replay_model(long_ops)
    churn = [("put", 0), ("delete", 0), ("delete", 0), ("put", 0), ("put", 0)]
    result = engine.replay(_model_log(churn))
    assert result == _replay_model(churn)
    assert result["state_id"] == _replay_model([("put", 0)])["state_id"]


@pytest.mark.parametrize("gen", GENERATORS)
def test_determinism_by_value(gen):
    for seed in range(0, FUZZ_SEEDS[gen], 7):
        first = _run(wal, GEN[gen](seed))[0]
        again = _run(wal, GEN[gen](seed))[0]
        assert first == again, _case(gen, seed)["label"]


def test_corpus_reaches_every_class_every_position_and_both_calls():
    classes, positions, hows, oks = set(), set(), set(), 0
    for gen in GENERATORS:
        for seed in range(PROBE_SEEDS):
            outcome = _reference_outcome(gen, seed)
            hows.add((gen, _case(gen, seed)["how"]))
            if outcome[0] == "err":
                classes.add(outcome[1])
            else:
                oks += 1
        for seed in range(min(PROBE_SEEDS, FUZZ_SEEDS[gen])):
            label = _case(gen, seed)["label"]
            if "@" in label and gen in ("shape", "chain"):
                index = int(label.split("@")[1].split(":")[0])
                n = len(_case(gen, seed)["ops"])
                positions.add((gen, "first" if index == 0 else
                               "last" if index >= n - 1 else "middle"))
    assert classes == CLASSES
    assert oks >= 20
    assert {(g, p) for g in ("shape", "chain")
            for p in ("first", "middle", "last")} <= positions
    assert {(g, h) for g in ("shape", "chain", "canon", "live", "container")
            for h in ("replay", "append")} <= hows
    assert {k for g in ("shape", "request") for s in range(FUZZ_SEEDS[g])
            for k in ("leaf", "drop", "add", "add-colliding", "subclass",
                      "rekey", "rename", "strsub")
            if f":{k}@" in _case(g, s)["label"]} == {
        "leaf", "drop", "add", "add-colliding", "subclass", "rekey", "rename",
        "strsub"}


# -- mutation check ---------------------------------------------------------------

def _source_mutant(name, edits):
    """store/wal.py with EDITS (old, new) applied, each matching exactly
    once, executed as a fresh module; WalError and _fail are rebound to
    the production class so typed failures stay comparable."""
    source = WAL_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0217_mutant_{len(edits)}")
    module.__file__ = str(ROOT / "store" / "wal.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.WalError = wal.WalError

    def _fail(failure_class):
        raise wal.WalError(failure_class, FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


def _probe(module):
    """Labels of the probe slice MODULE fails."""
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


def _isinstance(expr, kind):
    return (f"type({expr}) is not {kind}", f"not isinstance({expr}, {kind})")


_RESTORE_LOOP = ("    for entry, e_copy, payload, p_copy, record, r_copy"
                 " in saved:\n")

MUTANTS = {
    "no-sequence-check": [("    if sequence != position:\n",
                           "    if False:\n")],
    "sequence-gt": [("    if sequence != position:\n",
                     "    if sequence > position:\n")],
    "no-prior-link-check": [('    if entry["prior_entry_id"] != prior_tip:\n',
                             "    if False:\n")],
    "no-id-grammar": [("grammar.fullmatch(entry[field]) is None",
                       "False")],
    "id-grammar-match": [("grammar.fullmatch(entry[field])",
                          "grammar.match(entry[field])")],
    "id-grammar-search": [("grammar.fullmatch(entry[field])",
                           "grammar.search(entry[field])")],
    "record-key-len": [("set(dict.keys(record)) != set(_RECORD_FIELDS)",
                        "len(record) != len(_RECORD_FIELDS)")],
    "record-key-subset": [("set(dict.keys(record)) != set(_RECORD_FIELDS)",
                           "not set(dict.keys(record)) <= set(_RECORD_FIELDS)")],
    "payload-key-len": [("set(dict.keys(payload)) != set(_OPS[op])",
                         "len(payload) != len(_OPS[op])")],
    "entry-key-len": [("set(dict.keys(entry)) != set(_FIELDS)",
                       "len(entry) != len(_FIELDS)")],
    "request-key-len": [('set(dict.keys(request)) != {"op", "payload"}',
                         "len(request) != 2")],
    "request-key-superset": [('set(dict.keys(request)) != {"op", "payload"}',
                              'not set(dict.keys(request)) >= {"op", "payload"}')],
    "payload-key-superset": [("set(dict.keys(payload)) != set(_OPS[op])",
                              "not set(dict.keys(payload)) >= set(_OPS[op])")],
    "entry-key-superset": [("set(dict.keys(entry)) != set(_FIELDS)",
                            "not set(dict.keys(entry)) >= set(_FIELDS)")],
    "forged-error-passthrough": [(
        "        except BaseException:\n            _fail("
        '"divergent_canonicalization")',
        "        except WalError:\n            raise\n"
        "        except BaseException:\n            _fail("
        '"divergent_canonicalization")')],
    "no-entry-unknown-op": [('    if op not in _OPS:\n'
                             '        _fail("unknown_operation")\n'
                             '    sequence = entry["sequence"]\n',
                             '    sequence = entry["sequence"]\n')],
    "entry-op-isinstance": [('    op = entry["op"]\n    if type(op) is not str:',
                             '    op = entry["op"]\n    if not isinstance(op, str):')],
    "request-op-isinstance": [('        op = request["op"]\n'
                               '        if type(op) is not str:',
                               '        op = request["op"]\n'
                               '        if not isinstance(op, str):')],
    "sequence-isinstance": [_isinstance("sequence", "int")],
    "entry-isinstance": [_isinstance("entry", "dict")],
    "payload-isinstance": [_isinstance("payload", "dict")],
    "record-isinstance": [_isinstance("record", "dict")],
    "request-isinstance": [_isinstance("request", "dict")],
    "identity-isinstance": [_isinstance("identity", "str")],
    "record-field-isinstance": [("any(type(record[field]) is not str",
                                 "any(not isinstance(record[field], str)")],
    "id-field-isinstance": [("type(entry[field]) is not str",
                             "not isinstance(entry[field], str)")],
    "no-key-guard": [("    return all(type(key) is str for key in dict.keys(mapping))",
                      "    return True")],
    "no-request-key-guard": [("        if type(request) is not dict or not "
                              "_exact_str_keys(request) or",
                              "        if type(request) is not dict or")],
    "no-record-equality": [("    if dict(record) != dict(derived):", "    if False:")],
    "no-identity-binding": [('    if _validate_record(payload["record"]) != identity:',
                             '    if _validate_record(payload["record"]) and False:')],
    "narrow-boundary": [("        except BaseException:\n            _fail("
                         '"divergent_canonicalization")',
                         "        except Exception:\n            _fail("
                         '"divergent_canonicalization")')],
    "output-isinstance": [_isinstance("out", "str")],
    "no-utf8-check": [('            out.encode("utf-8")\n', "            pass\n")],
    "shared-argument": [("self.canonicalizer(identity, dict(record))",
                         "self.canonicalizer(identity, record)")],
    "no-rederive-check": [('                    entry["entry_id"]:\n'
                           '                _fail("corrupt_chain")',
                           '                    entry["entry_id"] and False:\n'
                           '                _fail("corrupt_chain")')],
    "restore-no-record-clear": [(_RESTORE_LOOP + "        record.clear()\n",
                                 _RESTORE_LOOP)],
    "restore-no-entry-clear": [("        entry.clear()\n        entry.update(e_copy)",
                                "        entry.update(e_copy)")],
    "restore-no-container": [("    log[:] = container\n", "    pass\n")],
    "append-no-request-clear": [("            request.clear()\n", "")],
    "append-no-payload-clear": [("            payload.clear()\n", "")],
    "append-no-record-clear": [("            record.clear()\n", "")],
    "staged-aliases-request": [('                    "record": dict(frozen_req["payload"]'
                                '["record"])},',
                                '                    "record": record},')],
    "returns-committed": [("        log.append(staged)\n        return _freeze_entry(staged)",
                           "        log.append(staged)\n        return staged")],
    "delete-noop": [("                    state.pop(identity, None)",
                     "                    pass")],
    "replay-no-log-type": [('        if type(log) is not list:\n            _fail('
                            '"malformed_wal_entry")\n        _validate_log(log)\n'
                            "        container, saved = snapshot(log)\n        frozen",
                            "        _validate_log(log)\n"
                            "        container, saved = snapshot(log)\n        frozen")],
    "append-no-log-type": [('        if type(log) is not list:\n            _fail('
                            '"malformed_wal_entry")\n        if type(request)',
                            "        if type(request)")],
    "replay-reads-live-log": [("            tip = self._rederive(frozen)\n"
                               "            state = {}",
                               "            tip = self._rederive(list(log))\n"
                               "            state = {}")],
    "append-sequence-from-live-log": [("            sequence = len(frozen) + 1",
                                       "            sequence = len(log) + 1")],
    "no-append-unknown-op": [('        if op not in _OPS:\n            _fail("unknown_operation")\n'
                              '        _validate_payload(op, request["payload"])',
                              '        _validate_payload(op, request["payload"])')],
}

# edits that must NOT change behavior; each is asserted green
_EQUIVALENT_EDITS = (
    ("no-memo-cache", ("_linked = functools.lru_cache(maxsize=4096)(_derive_linked)",
                       "_linked = _derive_linked")),
    ("memo-bound-zero", ("_MEMO_MAX_FEN = 256", "_MEMO_MAX_FEN = 0")),
    ("record-fields-reversed", ("set(dict.keys(record)) != set(_RECORD_FIELDS)",
                                "set(dict.keys(record)) != set(_RECORD_FIELDS[::-1])")),
    ("snapshot-slice-copy", ("    return list(log), saved", "    return log[:], saved")),
    # an extra record key passes this check but the exact record
    # equality with the linked derivation rejects it the same way
    ("record-key-superset", ("set(dict.keys(record)) != set(_RECORD_FIELDS)",
                             "not set(_RECORD_FIELDS) <= set(dict.keys(record))")),
    # the linked graph.node runtime raises VariantError/DigestError for
    # every bad record found (empty, unencodable, huge-clock, trailing
    # newline, bad variant ...); none reaches it as a plain ValueError
    ("linked-rejections-without-valueerror", (
        "_LINKED_REJECTIONS = (VariantError, DigestError, ValueError)",
        "_LINKED_REJECTIONS = (VariantError, DigestError)")),
)


def test_identity_source_mutant_is_green():
    module = _source_mutant("identity", [])
    assert module.WalEngine is not wal.WalEngine
    assert _probe(module) == []


@pytest.mark.parametrize("name,edit", _EQUIVALENT_EDITS,
                         ids=[name for name, _ in _EQUIVALENT_EDITS])
def test_equivalent_edits_stay_green(name, edit):
    assert _probe(_source_mutant(name, [edit])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(_source_mutant(name, MUTANTS[name])) != [], name
