"""T0235: deterministic fuzz/fault battery for the production restore (store/restore.py).

Seeded generators build backup receipts from an independent model of
data/contracts/backup.yaml and restore.yaml (the bundle serialization and
the backup-id and restore-id derivations are restated here, never taken
from production) and then damage them:

- receipt: one structural edit to the receipt (every leaf type, dropped,
  added and same-arity renamed keys, str-subclass and hash-colliding
  keys, a dict subclass, count bounds) and self-consistent forgeries that
  re-derive the backup id over a changed head, count or state id;
- container: non-dict and dict-subclass receipts;
- bundle: a verified receipt (backup id re-derived) over a damaged
  bundle: reordered or duplicated records, reordered fields, framing
  edits, wrong digests, mis-keyed or unknown records, unencodable text;
- parser: a parser fault (every BaseException kind, a forged typed error
  of every restore, backup, WAL and linked graph.node class, non-dict and
  dict-subclass output, str-subclass, hash-colliding and non-str keys at
  the state and record level, dropped, added and wrong fields, missing,
  extra and swapped records);
- live: a parser that edits the caller's live receipt (keys added,
  dropped and changed, the receipt cleared) and then answers honestly,
  raises or diverges.

Every case must satisfy, against production: no raw escape (only a typed
RestoreError from the closed class set with its mapped code); the same
outcome as the contract reference engine (tests.test_t0230_restore_contract),
and where the outcome is independently known, that outcome; the receipt
identical by value, exact type, key order and object identity at every
level on every exit; a successful restore equal to the model and detached
from the parser output; no poisoning; determinism.

Mutation check: _probe runs a fixed slice of every generator against a
source mutant of store/restore.py. Every entry in MUTANTS must turn the
probe red; every entry in _EQUIVALENT_EDITS must keep it green. Mutants
run with RestoreError rebound to the production class, guarded by an
identity mutant that must pass.
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
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph import position_digest  # noqa: E402
from graph.diff import state_id  # noqa: E402
from graph.node import make_record, record_identity  # noqa: E402
from store import backup, restore, wal  # noqa: E402
from tests import test_t0230_restore_contract as _reference  # noqa: E402
from tools.restore_contract_lint import FAILURE_MAPPING  # noqa: E402
from tools.variant_runtime import VariantError  # noqa: E402

RESTORE_SOURCE = (ROOT / "store" / "restore.py").read_text()
GENESIS = "wal0:" + "0" * 64
COUNT_MAX = 2 ** 63 - 1
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1",
    "8/8/8/8/8/8/8/K1k5 w - - 0 1",
)
RECORDS = tuple(make_record("standard", fen) for fen in FENS)
KEYS = tuple(record_identity(record) for record in RECORDS)
FIELDS = ("backup_id", "head", "state_id", "entry_count", "bundle")
CLASSES = frozenset(FAILURE_MAPPING)
MR, UB, DP, DS = ("malformed_restore_record", "unverified_backup",
                  "divergent_parse", "divergent_state")
GENERATORS = ("receipt", "container", "bundle", "parser", "live")
FUZZ_SEEDS = {"receipt": 480, "container": 24, "bundle": 240, "parser": 480,
              "live": 144}
PROBE_SEEDS = 48


class _Str(str):
    pass


class _Dict(dict):
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


LEAVES = (None, True, False, 0, 1, -1, 2 ** 70, 1.5, b"x", "", "\ud800", "é",
          [], {}, ())


# -- independent model --------------------------------------------------------------

def _bundle(state):
    """Independent canonical bundle: per sorted key, the key line and the
    sorted field=value line."""
    return "".join(
        f"{key}\n" + "|".join(f"{f}={state[key][f]}" for f in sorted(state[key])) + "\n"
        for key in sorted(state))


def _backup_id(head, sid, count, bundle):
    # surrogatepass only so unencodable forgeries still get an id; verify rejects them first
    return "bck1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{bundle}".encode("utf-8", "surrogatepass")).hexdigest()


def _state_id(state):
    """Independent graph-diff state id (same serialization as the bundle)."""
    return "gs1:" + hashlib.sha256(_bundle(state).encode()).hexdigest()


def _restore_id(backup_id, sid):
    return "rst1:" + hashlib.sha256(f"{backup_id}\n{sid}".encode()).hexdigest()


def _head(rng):
    return "wal1:" + hashlib.sha256(repr(rng.random()).encode()).hexdigest()


def _state(rng, low=0, high=None):
    high = len(RECORDS) if high is None else high
    chosen = rng.sample(range(len(RECORDS)), rng.randint(low, high))
    return {KEYS[i]: dict(RECORDS[i]) for i in chosen}


def _receipt(state, rng, bundle=None, sid=None, head=None, count=None):
    bundle = _bundle(state) if bundle is None else bundle
    sid = _state_id(state) if sid is None else sid
    if count is None:
        count = 0 if not state and rng.random() < 0.7 else len(state) + rng.randint(0, 3)
        if count == 0 and state:
            count = 1
    if head is None:
        head = GENESIS if count == 0 else _head(rng)
    return {"backup_id": _backup_id(head, sid, count, bundle), "head": head,
            "state_id": sid, "entry_count": count, "bundle": bundle}


def _reforge(receipt):
    out = dict(receipt)
    out["backup_id"] = _backup_id(out["head"], out["state_id"], out["entry_count"],
                                  out["bundle"])
    return out


def _parse(bundle):
    """Independent honest parser (the exact inverse of _bundle)."""
    lines = bundle.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    state = {}
    for i in range(0, len(lines), 2):
        state[lines[i]] = dict(pair.partition("=")[::2] for pair in lines[i + 1].split("|"))
    return state


def _case_of(label, receipt, state, parser=None, expect=None):
    return {"label": label, "receipt": receipt, "state": state,
            "parser": parser, "expect": expect}


# -- generators ------------------------------------------------------------------------

def _gen_receipt(seed):
    rng = random.Random(f"receipt-{seed}")
    state = _state(rng, 1)
    good = _receipt(state, rng)
    field = FIELDS[seed % len(FIELDS)]
    kind = ("leaf", "drop", "add", "rename", "strsub", "colliding", "subclass",
            "reforge", "count-bound", "trailing")[seed // len(FIELDS) % 10]
    r = dict(good)
    expect = ("err", MR)
    if kind == "leaf":
        leaf = LEAVES[rng.randrange(len(LEAVES))]
        if field == "bundle" and type(leaf) is str:
            leaf = None
        r[field] = leaf
        if field == "entry_count" and type(leaf) is int and 0 < leaf <= COUNT_MAX:
            expect = ("err", UB)
    elif kind == "drop":
        del r[field]
    elif kind == "add":
        r["zz" if rng.random() < 0.5 else "é"] = r[field]
    elif kind == "rename":
        r = {(k[:-1] + ("x" if k[-1] != "x" else "y") if k == field else k): v
             for k, v in r.items()}
    elif kind == "strsub":
        r = {(_Str(k) if k == field else k): v for k, v in r.items()}
    elif kind == "colliding":
        r = {(_Colliding(k) if k == field else k): v for k, v in r.items()}
    elif kind == "subclass":
        r = _Dict(r)
    elif kind == "reforge":
        if field == "head":
            r["head"] = _head(rng)
            expect = ("ok",)
        elif field == "entry_count":
            r["entry_count"] = rng.choice((len(state), COUNT_MAX, len(state) + 7))
            expect = ("ok",)
        elif field == "state_id":
            r["state_id"] = _state_id({KEYS[0]: dict(RECORDS[0])}) \
                if state != {KEYS[0]: dict(RECORDS[0])} else _state_id({})
            expect = ("err", DS)
        elif field == "backup_id":
            r["backup_id"] = r["backup_id"][:-1] + ("0" if r["backup_id"][-1] != "0" else "1")
            r = dict(r)
            expect = ("err", UB)
        else:
            r["head"] = GENESIS
            expect = ("err", UB)
        if field != "backup_id":
            r = _reforge(r)
    elif kind == "count-bound":
        r["entry_count"] = rng.choice((-1, COUNT_MAX + 1, True, 0))
        if r["entry_count"] == 0:
            expect = ("err", UB)
        if field != "backup_id" and r["entry_count"] in (0,):
            r = _reforge(r)
            expect = ("err", UB)
    else:
        if field == "entry_count":
            r["entry_count"] = float(r["entry_count"])
        elif field == "bundle":
            r["bundle"] = r["bundle"] + "\n"
            r = _reforge(r)
            expect = None
        else:
            r[field] = r[field] + "\n"
    return _case_of(f"receipt:{kind}@{field}", r, state, expect=expect)


def _gen_container(seed):
    rng = random.Random(f"container-{seed}")
    good = _receipt(_state(rng), rng)
    options = (None, [], "", 0, list(good.items()), tuple(good.items()), _Dict(good),
               {}, frozenset(), b"")
    bad = options[seed % len(options)]
    return _case_of(f"container:{type(bad).__name__}", bad, None, expect=("err", MR))


BUNDLE_TAMPERS = ("swap-records", "duplicate-record", "reorder-fields", "no-trailing-newline",
                  "extra-blank-line", "wrong-digest", "mis-keyed", "unknown-variant",
                  "extra-field", "missing-field", "unencodable", "empty-lines",
                  "crlf", "space-after-sep", "drop-record-keep-sid")


def _gen_bundle(seed):
    rng = random.Random(f"bundle-{seed}")
    state = _state(rng, 2)
    tamper = BUNDLE_TAMPERS[seed % len(BUNDLE_TAMPERS)]
    lines = _bundle(state).split("\n")[:-1]
    sid = _state_id(state)
    expect = None
    k = 2 * rng.randrange(len(state))
    if tamper == "swap-records":
        lines = lines[2:4] + lines[0:2] + lines[4:]
        expect = ("err", DP)
    elif tamper == "duplicate-record":
        lines = lines[:k + 2] + lines[k:k + 2] + lines[k + 2:]
        expect = ("err", DP)
    elif tamper == "reorder-fields":
        lines[k + 1] = "|".join(reversed(lines[k + 1].split("|")))
        expect = ("err", DP)
    elif tamper == "no-trailing-newline":
        text = "\n".join(lines)
    elif tamper == "extra-blank-line":
        lines.insert(k + 2, "")
    elif tamper == "wrong-digest":
        key = lines[k]
        lines[k + 1] = lines[k + 1].replace(state[key]["digest"],
                                            "pdv1:" + "0" * 64)
        bad = {**state, key: {**state[key], "digest": "pdv1:" + "0" * 64}}
        sid = _state_id(bad)
        expect = ("err", DS)
    elif tamper == "mis-keyed":
        spare = [i for i in range(len(KEYS)) if KEYS[i] not in state][0]
        lines[k] = KEYS[spare]
        expect = ("err", DS)
    elif tamper == "unknown-variant":
        lines[k + 1] = lines[k + 1].replace("variant=standard", "variant=chess960x")
        expect = ("err", DS)
    elif tamper == "extra-field":
        lines[k + 1] += "|zz=1"
        expect = ("err", DS)
    elif tamper == "missing-field":
        lines[k + 1] = "|".join(lines[k + 1].split("|")[1:])
        expect = ("err", DS)
    elif tamper == "unencodable":
        lines[k] += "\ud800"
        expect = ("err", MR)
    elif tamper == "empty-lines":
        lines = ["", ""] + lines
        expect = ("err", DS)
    elif tamper == "crlf":
        lines[k + 1] += "\r"
        expect = ("err", DS)
    elif tamper == "space-after-sep":
        lines[k + 1] = lines[k + 1].replace("|", "| ", 1)
        expect = ("err", DS)
    else:
        lines = lines[:k] + lines[k + 2:]
        expect = ("err", DS)
    if tamper != "no-trailing-newline":
        text = "\n".join(lines) + "\n"
    receipt = _receipt(state, rng, bundle=text, sid=sid, count=len(state))
    return _case_of(f"bundle:{tamper}", receipt, state, expect=expect)


def _raise(kind):
    def parser(bundle):
        raise kind()
    return parser


def _forged(error):
    def parser(bundle):
        raise error
    return parser


DIGEST_CLASSES = sorted(yaml.safe_load(position_digest.CONTRACT.read_text())
                        ["contract"]["failures"]["mapping"])
VARIANT_CODES = ("illegal_position", "internal", "malformed_request", "unknown_variant")
FORGED = {
    **{f"forged-restore-{c}": restore.RestoreError(c, FAILURE_MAPPING[c])
       for c in sorted(FAILURE_MAPPING)},
    **{f"forged-backup-{c}": backup.BackupError(c, backup.FAILURE_MAPPING[c])
       for c in sorted(backup.FAILURE_MAPPING)},
    **{f"forged-wal-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
    **{f"forged-digest-{c}": position_digest.DigestError(c, "malformed_request")
       for c in DIGEST_CLASSES},
    **{f"forged-variant-{c}": VariantError(code=c, message="forged")
       for c in VARIANT_CODES},
    # builtins: ValueError (named in the module's own except clause, via
    # _LINKED_REJECTIONS) and TypeError (the likeliest real parser crash)
    "forged-builtin-ValueError": ValueError("forged"),
    "forged-builtin-TypeError": TypeError("forged"),
}


def _first_key(state):
    return sorted(state)[0]


def _output(name, rng):
    """(parser, expected class) for a hostile parser output."""
    def edit(fn):
        def parser(bundle):
            out = _parse(bundle)
            return fn(out)
        return parser

    def rec_edit(fn):
        return edit(lambda s: {**s, _first_key(s): fn(dict(s[_first_key(s)]))})

    table = {
        "none": (lambda b: None, DP), "list": (lambda b: list(_parse(b).items()), DP),
        "str": (lambda b: b, DP), "dict-subclass": (lambda b: _Dict(_parse(b)), DP),
        "empty": (lambda b: {}, DS),
        "key-strsub": (edit(lambda s: {(_Str(k) if k == _first_key(s) else k): v
                                       for k, v in s.items()}), DS),
        "key-colliding": (edit(lambda s: {(_Colliding(k) if k == _first_key(s) else k): v
                                          for k, v in s.items()}), DS),
        "key-int": (edit(lambda s: {**s, 1: s[_first_key(s)]}), DS),
        "record-subclass": (edit(lambda s: {**s, _first_key(s): _Dict(s[_first_key(s)])}), DS),
        "record-none": (edit(lambda s: {**s, _first_key(s): None}), DS),
        "field-strsub": (rec_edit(lambda r: {(_Str(k) if k == "digest" else k): v
                                             for k, v in r.items()}), DS),
        "field-colliding": (rec_edit(lambda r: {(_Colliding(k) if k == "digest" else k): v
                                                for k, v in r.items()}), DS),
        "field-drop": (rec_edit(lambda r: {k: v for k, v in r.items() if k != "digest"}), DS),
        "field-add": (rec_edit(lambda r: {**r, "zz": "1"}), DS),
        "field-rename": (rec_edit(lambda r: {("digesx" if k == "digest" else k): v
                                             for k, v in r.items()}), DS),
        "value-strsub": (rec_edit(lambda r: {**r, "digest": _Str(r["digest"])}), DS),
        "value-none": (rec_edit(lambda r: {**r, "variant": None}), DS),
        "value-bytes": (rec_edit(lambda r: {**r, "snapshot_fen": r["snapshot_fen"].encode()}), DS),
        "wrong-digest": (rec_edit(lambda r: {**r, "digest": "pdv1:" + "0" * 64}), DS),
        "v2-digest": (rec_edit(lambda r: {**r, "digest": "pdv2:" + r["digest"][5:]}), DS),
        "clock-fen": (rec_edit(lambda r: {**r, "snapshot_fen": r["snapshot_fen"][:-3] + "3 9"}),
                      DS),
        "missing-record": (edit(lambda s: {k: v for k, v in s.items()
                                           if k != _first_key(s)}), DS),
        "extra-record": (edit(lambda s: {**s, **{k: dict(r) for k, r in
                                                 zip(KEYS, RECORDS, strict=True)
                                                 if k not in s}}), DS),
        "swapped-records": (edit(lambda s: dict(zip(sorted(s), [s[k] for k in sorted(s)][::-1],
                                                    strict=True))), DS),
        "reversed-order": (edit(lambda s: dict(reversed(list(s.items())))), None),
    }
    parser, cls = table[name]
    return parser, (("ok",) if cls is None else ("err", cls))


OUTPUTS = ("none", "list", "str", "dict-subclass", "empty", "key-strsub", "key-colliding",
           "key-int", "record-subclass", "record-none", "field-strsub", "field-colliding",
           "field-drop", "field-add", "field-rename", "value-strsub", "value-none",
           "value-bytes", "wrong-digest", "v2-digest", "clock-fen", "missing-record",
           "extra-record", "swapped-records", "reversed-order")
PARSER_FAULTS = (*[f"raise-{k.__name__}" for k in (ValueError, KeyError, RuntimeError,
                                                    KeyboardInterrupt, SystemExit,
                                                    GeneratorExit, MemoryError,
                                                    RecursionError)],
                 *sorted(FORGED), *OUTPUTS)
_KINDS = {f"raise-{k.__name__}": k for k in (ValueError, KeyError, RuntimeError,
                                              KeyboardInterrupt, SystemExit, GeneratorExit,
                                              MemoryError, RecursionError)}


def _gen_parser(seed):
    rng = random.Random(f"parser-{seed}")
    state = _state(rng, 2)
    fault = PARSER_FAULTS[seed % len(PARSER_FAULTS)]
    if fault in _KINDS:
        parser, expect = _raise(_KINDS[fault]), ("err", DP)
    elif fault in FORGED:
        parser, expect = _forged(FORGED[fault]), ("err", DP)
    else:
        parser, expect = _output(fault, rng)
        if fault == "swapped-records" and len(state) < 2:
            expect = None
        if fault == "extra-record" and set(KEYS) <= set(state):
            expect = None  # nothing to add: the output is the honest one
    return _case_of(f"parser:{fault}", _receipt(state, rng), state,
                    parser=lambda receipt, p=parser: p, expect=expect)


SCRAMBLES = ("add-key", "drop-key", "change-bundle", "change-state-id", "change-backup-id",
             "clear", "reorder", "strsub-key", "replace-all")
ACTIONS = ("honest", "raise", "divergent")


def _scramble(kind, receipt):
    if kind == "add-key":
        receipt["zz"] = 1
    elif kind == "drop-key":
        del receipt["bundle"]
    elif kind == "change-bundle":
        receipt["bundle"] = ""
    elif kind == "change-state-id":
        receipt["state_id"] = "gs1:" + "0" * 64
    elif kind == "change-backup-id":
        receipt["backup_id"] = None
    elif kind == "clear":
        receipt.clear()
    elif kind == "reorder":
        items = list(receipt.items())[::-1]
        receipt.clear()
        receipt.update(items)
    elif kind == "strsub-key":
        receipt[_Str("head")] = receipt.pop("head")
    else:
        receipt.clear()
        receipt.update({"x": 1})


def _gen_live(seed):
    rng = random.Random(f"live-{seed}")
    state = _state(rng, 1)
    kind = SCRAMBLES[seed % len(SCRAMBLES)]
    action = ACTIONS[seed // len(SCRAMBLES) % len(ACTIONS)]

    def make(receipt):
        def parser(bundle):
            _scramble(kind, receipt)
            if action == "raise":
                raise RuntimeError("after scribbling")
            out = _parse(bundle)
            if action == "divergent":
                key = _first_key(out)
                out[key] = {**out[key], "digest": "pdv1:" + "0" * 64}
            return out
        return parser

    expect = {"honest": ("ok",), "raise": ("err", DP), "divergent": ("err", DS)}[action]
    return _case_of(f"live:{kind}/{action}", _receipt(state, rng), state, parser=make,
                    expect=expect)


GEN = {"receipt": _gen_receipt, "container": _gen_container, "bundle": _gen_bundle,
       "parser": _gen_parser, "live": _gen_live}


@functools.cache
def _case(gen, seed):
    return GEN[gen](seed)


# -- running and checking ---------------------------------------------------------

class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _deep(value):
    """Value, exact type, key order and object identity at every level."""
    if isinstance(value, dict):
        return ("d", type(value).__name__, _Pin(value),
                tuple((type(k).__name__, _Pin(k) if type(k) is _Colliding else k, _deep(v))
                      for k, v in dict.items(value)))
    if isinstance(value, (list, tuple)):
        return ("l", type(value).__name__, _Pin(value), tuple(_deep(v) for v in value))
    return ("v", type(value).__name__, value)


def _run(module, case):
    """(outcome, before, after, outputs); outcome is ("ok", result) |
    ("err", class, code) | ("raw", exception type)."""
    receipt = copy.deepcopy(case["receipt"])
    outputs = []
    inner = _parse if case["parser"] is None else case["parser"](receipt)

    def parser(bundle):
        out = inner(bundle)
        outputs.append(out)
        return out

    engine = module.RestoreEngine(parser)
    before = _deep(receipt)
    raised = None
    _Colliding.armed = True
    try:
        outcome = ("ok", engine.restore(receipt))
    except (restore.RestoreError, _reference.RestoreError) as error:
        outcome = ("err", error.failure_class, error.code)
        raised = error
    except BaseException as error:  # noqa: BLE001 - raw escape is a defect
        outcome = ("raw", type(error).__name__)
    finally:
        _Colliding.armed = False
    return outcome, before, _deep(receipt), outputs, raised


@functools.cache
def _reference_outcome(gen, seed):
    return _run(_reference, _case(gen, seed))[0]


def _model_result(receipt):
    state = _parse(receipt["bundle"])
    return {"restore_id": _restore_id(receipt["backup_id"], receipt["state_id"]),
            "backup_id": receipt["backup_id"], "state_id": receipt["state_id"],
            "state": state}


def _check(module, gen, seed):
    """Failing reasons for one case through MODULE (empty when it holds)."""
    case = _case(gen, seed)
    outcome, before, after, outputs, raised = _run(module, case)
    if outcome[0] == "raw":
        return [f"raw {outcome[1]}"]
    why = []
    if outcome[0] == "err" and (outcome[1] not in CLASSES or
                                outcome[2] != FAILURE_MAPPING.get(outcome[1])):
        why.append(f"untyped {outcome[1:]}")
    if outcome != _reference_outcome(gen, seed):
        why.append(f"reference disagrees {outcome[:2]!r}"[:160])
    expect = case["expect"]
    if expect is not None and (expect[0] != outcome[0] or (
            expect[0] == "err" and outcome[1] != expect[1])):
        why.append(f"expected {expect}, got {outcome[:2]}")
    if after != before:
        why.append("receipt not restored")
    if any(raised is forged for forged in FORGED.values()):
        why.append("forged error object passed through")
    if raised is not None and raised.__cause__ is not None:
        why.append("typed error chained with an explicit __cause__")
    if len(outputs) > 1:
        why.append("parser called more than once")
    if outcome[0] == "ok":
        result = outcome[1]
        if list(result) != ["restore_id", "backup_id", "state_id", "state"]:
            why.append("result fields out of order")
        if result != _model_result(case["receipt"]):
            why.append("result differs from the model")
        if type(result["state"]) is not dict or any(
                type(r) is not dict for r in result["state"].values()):
            why.append("result state not exact dicts")
        if outputs and (result["state"] is outputs[0] or any(
                result["state"][k] is v for k, v in dict.items(outputs[0])
                if type(k) is str and k in result["state"])):
            why.append("result aliases the parser output")
        if len(outputs) != 1:
            why.append("parser not called exactly once")
    if outcome[0] == "err" and not _unpoisoned(module):
        why.append("poisoned after rejection")
    return why


def _unpoisoned(module):
    rng = random.Random("clean")
    state = {k: dict(r) for k, r in zip(KEYS, RECORDS, strict=True)}
    receipt = _receipt(state, rng)
    try:
        return module.RestoreEngine(_parse).restore(receipt) == _model_result(receipt)
    except BaseException:  # noqa: BLE001 - any failure is poisoning
        return False


# -- fuzz tests -------------------------------------------------------------------

def _chunks(gen, size=48):
    total = FUZZ_SEEDS[gen]
    return [(gen, start, min(start + size, total)) for start in range(0, total, size)]


@pytest.mark.parametrize("gen,start,stop", [c for g in GENERATORS for c in _chunks(g)])
def test_fuzz_cases_hold(gen, start, stop):
    failures = []
    for seed in range(start, stop):
        why = _check(restore, gen, seed)
        if why:
            failures.append((_case(gen, seed)["label"], why))
    assert failures == []


@pytest.mark.parametrize("seed", range(48))
def test_happy_receipts_restore_to_the_model(seed):
    rng = random.Random(f"happy-{seed}")
    state = _state(rng)
    receipt = _receipt(state, rng)
    assert _state_id(state) == state_id(state)
    before = _deep(receipt)
    result = restore.RestoreEngine(restore.parse_bundle).restore(receipt)
    assert result == _model_result(receipt) and result["state"] == state
    assert _deep(receipt) == before


def test_boundaries():
    engine = restore.RestoreEngine(restore.parse_bundle)
    rng = random.Random("boundaries")
    empty = _receipt({}, rng, count=0)
    assert empty["head"] == GENESIS and empty["bundle"] == ""
    assert engine.restore(empty)["state"] == {}
    for key, record in zip(KEYS, RECORDS, strict=True):
        single = _receipt({key: dict(record)}, rng, count=1)
        assert engine.restore(single)["state"] == {key: record}
    full = {k: dict(r) for k, r in zip(KEYS, RECORDS, strict=True)}
    # the count ceiling is accepted and one past it is malformed
    at_max = _receipt(full, rng, count=COUNT_MAX)
    assert engine.restore(at_max)["state"] == full
    past = _reforge({**at_max, "entry_count": COUNT_MAX + 1})
    with pytest.raises(restore.RestoreError) as exc:
        engine.restore(past)
    assert exc.value.failure_class == MR
    # an empty state under a non-genesis head with count > 0 restores to {}
    assert engine.restore(_receipt({}, rng, count=3))["state"] == {}
    # a non-empty state with count 0 does not verify
    zero = _reforge({**_receipt(full, rng), "entry_count": 0, "head": GENESIS})
    with pytest.raises(restore.RestoreError) as exc:
        engine.restore(zero)
    assert exc.value.failure_class == UB


@pytest.mark.parametrize("gen", GENERATORS)
def test_determinism_by_value(gen):
    for seed in range(0, FUZZ_SEEDS[gen], 5):
        first = _run(restore, GEN[gen](seed))[0]
        again = _run(restore, GEN[gen](seed))[0]
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
    for kind in ("leaf", "drop", "add", "rename", "strsub", "colliding", "subclass",
                 "reforge", "count-bound", "trailing"):
        assert {f"receipt:{kind}@{f}" for f in FIELDS} <= labels
    assert {f"bundle:{t}" for t in BUNDLE_TAMPERS} <= labels
    assert {f"parser:{f}" for f in PARSER_FAULTS} <= labels
    assert {f"live:{k}/{a}" for k in SCRAMBLES for a in ACTIONS} <= labels
    # the probe slice alone reaches every class too
    probe = {_reference_outcome(g, s)[1] for g in GENERATORS
             for s in range(min(PROBE_SEEDS, FUZZ_SEEDS[g]))
             if _reference_outcome(g, s)[0] == "err"}
    assert probe == CLASSES


# -- mutation check ---------------------------------------------------------------

def _source_mutant(name, edits):
    """store/restore.py with EDITS (old, new) applied, each matching exactly
    once, executed as a fresh module; RestoreError and _fail are rebound to
    the production class so typed failures stay comparable."""
    source = RESTORE_SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0235_mutant_{len(edits)}")
    module.__file__ = str(ROOT / "store" / "restore.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.RestoreError = restore.RestoreError

    def _fail(failure_class):
        raise restore.RestoreError(failure_class, FAILURE_MAPPING[failure_class])

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


_BE = '        except BaseException:\n            _fail("divergent_parse")'

MUTANTS = {
    "boundary-passthrough": [(_BE, "        except RestoreError:\n            raise\n" + _BE)],
    "boundary-narrow": [(_BE, _BE.replace("BaseException", "Exception"))],
    **{f"boundary-passes-{name}": [(_BE, f"        except {name}:\n            raise\n" + _BE)]
       for name in ("ValueError", "TypeError")},
    "boundary-cause-chain": [(_BE, "        except RestoreError as e:\n"
                                   '            raise RestoreError("divergent_parse", '
                                   'FAILURE_MAPPING["divergent_parse"]) from e\n' + _BE)],
    **{f"boundary-pass-restore-{c}": [(_BE, "        except RestoreError as e:\n"
                                        f'            if e.failure_class == "{c}": raise\n'
                                        '            _fail("divergent_parse")\n' + _BE)]
       for c in sorted(FAILURE_MAPPING)},
    **{f"boundary-pass-backup-{c}": [(_BE, "        except _backup.BackupError as e:\n"
                                       f'            if e.failure_class == "{c}": raise\n'
                                       '            _fail("divergent_parse")\n' + _BE)]
       for c in sorted(backup.FAILURE_MAPPING)},
    **{f"boundary-pass-wal-{c}": [(_BE, "        except _backup._wal.WalError as e:\n"
                                    f'            if e.failure_class == "{c}": raise\n'
                                    '            _fail("divergent_parse")\n' + _BE)]
       for c in sorted(wal.FAILURE_MAPPING)},
    **{f"boundary-pass-digest-{c}": [(_BE, "        except DigestError as e:\n"
                                       f'            if e.failure_class == "{c}": raise\n'
                                       '            _fail("divergent_parse")\n' + _BE)]
       for c in DIGEST_CLASSES},
    **{f"boundary-pass-variant-{c}": [(_BE, "        except VariantError as e:\n"
                                        f'            if e.code == "{c}": raise\n'
                                        '            _fail("divergent_parse")\n' + _BE)]
       for c in VARIANT_CODES},
    "boundary-pass-linked": [(_BE, "        except _LINKED_REJECTIONS:\n"
                                   "            raise\n" + _BE)],
    "output-isinstance": [("        if type(out) is not dict:",
                           "        if not isinstance(out, dict):")],
    "output-type-off": [("        if type(out) is not dict:\n"
                         "            _fail(\"divergent_parse\")\n",
                         "")],
    "verify-off": [("            _BACKUP.verify(receipt)\n", "            pass\n")],
    "verify-all-malformed": [('            if err.failure_class == "malformed_backup_record":\n'
                              '                _fail("malformed_restore_record")\n'
                              '            _fail("unverified_backup")',
                              '            _fail("malformed_restore_record")')],
    "verify-all-unverified": [('            if err.failure_class == "malformed_backup_record":\n'
                               '                _fail("malformed_restore_record")\n', '')],
    "no-receipt-restore": [("            receipt.clear()\n            receipt.update(saved)",
                            "            pass")],
    "receipt-no-clear": [("            receipt.clear()\n            receipt.update(saved)",
                          "            receipt.update(saved)")],
    "restore-only-on-failure": [("        try:\n            parsed = self._parse(frozen_bundle)\n"
                                 "        finally:\n            receipt.clear()\n"
                                 "            receipt.update(saved)",
                                 "        try:\n            parsed = self._parse(frozen_bundle)\n"
                                 "        except BaseException:\n            receipt.clear()\n"
                                 "            receipt.update(saved)\n            raise")],
    "key-isinstance": [("            if type(key) is not str or type(record) is not dict:",
                        "            if not isinstance(key, str) or type(record) is not dict:")],
    "state-key-check-off": [("            if type(key) is not str or type(record) is not dict:\n"
                             "                _fail(\"divergent_state\")\n", "")],
    "identity-binding-off": [("            if _record_identity(record) != key:",
                              "            if _record_identity(record) is None:")],
    "field-key-guard-off": [("            not all(type(key) is str for key in "
                             "dict.keys(record)) or \\\n",
                             "")],
    "field-set-len": [("            set(dict.keys(record)) != set(_RECORD_FIELDS):",
                       "            len(record) != len(_RECORD_FIELDS):")],
    "value-isinstance": [("type(record[field]) is not str for field in _RECORD_FIELDS",
                          "not isinstance(record[field], str) for field in _RECORD_FIELDS")],
    "record-equality-off": [("    if dict(record) != dict(derived):\n        return None\n", "")],
    "state-id-check-off": [("        if state_id(state) != frozen_state_id:\n"
                            "            _fail(\"divergent_state\")\n", "")],
    "round-trip-off": [("        if _backup.serialize_bundle(state) != frozen_bundle:\n"
                        "            _fail(\"divergent_parse\")\n", "")],
    "round-trip-class": [("        if _backup.serialize_bundle(state) != frozen_bundle:\n"
                          "            _fail(\"divergent_parse\")",
                          "        if _backup.serialize_bundle(state) != frozen_bundle:\n"
                          "            _fail(\"divergent_state\")")],
    "state-aliases-output": [("            state[key] = dict(record)",
                              "            state[key] = record")],
    "restore-id-swap": [('        f"{backup_id}\\n{sid}".encode()).hexdigest()',
                         '        f"{sid}\\n{backup_id}".encode()).hexdigest()')],
    "result-order": [('                "backup_id": frozen_backup_id,\n'
                      '                "state_id": frozen_state_id,',
                      '                "state_id": frozen_state_id,\n'
                      '                "backup_id": frozen_backup_id,')],
}

# edits that must NOT change behavior; each is asserted green
_EQUIVALENT_EDITS = (
    # an extra field passes the key-set check but the exact record equality
    # with the linked derivation rejects it the same way
    ("field-set-superset", ("            set(dict.keys(record)) != set(_RECORD_FIELDS):",
                            "            not set(dict.keys(record)) >= set(_RECORD_FIELDS):")),
    # _record_identity repeats the exact-dict check, so a dict-subclass
    # record is still divergent_state
    ("record-isinstance", ("            if type(key) is not str or type(record) is not dict:",
                           "            if type(key) is not str or not isinstance(record, dict):")),
    # the parser runs before anything can touch the receipt, so the live
    # bundle is still the frozen one at that point
    ("parse-live-bundle", ("            parsed = self._parse(frozen_bundle)",
                           '            parsed = self._parse(receipt["bundle"])')),
    # the receipt is restored before the state-id check reads it
    ("state-id-live", ("        if state_id(state) != frozen_state_id:",
                       '        if state_id(state) != receipt["state_id"]:')),
)


def test_identity_source_mutant_is_green():
    module = _source_mutant("identity", [])
    assert module.RestoreEngine is not restore.RestoreEngine
    assert _probe(module) == []


@pytest.mark.parametrize("name,edit", _EQUIVALENT_EDITS,
                         ids=[name for name, _ in _EQUIVALENT_EDITS])
def test_equivalent_edits_stay_green(name, edit):
    assert _probe(_source_mutant(name, [edit])) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red(name):
    assert _probe(_source_mutant(name, MUTANTS[name])) != [], name




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": [1]}
    before = _deep(value)
    value["k"] = [1]
    value["k"] = [1]
    assert _deep(value) != before
