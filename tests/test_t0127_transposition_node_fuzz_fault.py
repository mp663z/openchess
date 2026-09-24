"""T0127 transposition-node fuzz/fault campaign against the production
runtime (graph.transposition_node, T0125).

Where T0126 pins properties over seeded inserts, this task runs a larger
classified campaign with these duties:

1. CLASSIFICATION: every campaign case (insert, merge of a source, or an
   operation under a faulty digest oracle) ends exactly one of accept -
   the returned record and the whole table equal an independent model
   (one node per identity: variant, the linked digest of the hand-pinned
   snapshot, snapshot with identity en passant and clocks 0 1) - or
   reject - NodeError with the failure class the case's single
   corruption predicts, its mapped code, and no __cause__/__context__.
   Any other exception is a CRASH and fails the suite with the case
   recorded.
2. ROLLBACK: every reject leaves the table bit-identical (bucket contents
   and bucket list objects).
3. BOUNDARY FORGES: the digest oracle is the module's untrusted
   boundary. Every NodeError class, FenError, DigestError and ValueError
   (the classes the module raises and those in its own except clauses),
   BaseException-only and KeyboardInterrupt raisers and every malformed
   output shape are injected on insert, on merge and on validate_record;
   each must fail closed as a fresh malformed_node_record, never as the
   forged object. An oracle that fails midway through a merge's staged
   inserts and one that rewrites the caller's live source leave the
   table as the contract says.
4. FAULT INJECTION: one-edit production mutants run through the same
   detectors; each is caught by its own check in the deterministic
   sweep alone, and the seeded campaign flags each one too.
5. DETERMINISM: the campaign is pure in its seed; the classified log's
   SHA-256 is pinned.

The deterministic sweep enumerates every corruption sub-variant (seeded
draws never decide coverage), insert precedence, merge precedence (the
whole-source freeze pass, then per-record validation in order, both
before any staging) and shared-bucket rows under a constant digest.

Insert returns, and records() hands out, the stored record objects by
contract (T0125/T0126 pin insert-or-return-existing), so detachment is
not a property of this module and is not asserted here.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import random
import types
from pathlib import Path

import pytest

from graph import position_digest
from graph import transposition_node as tn
from graph.fen import FenError

DOCS = tn.load_docs()
CASES = 700
MNR, MP, UV = "malformed_node_record", "malformed_position", "unknown_variant"
CLASSES = {MNR, MP, UV}
FIELDS = ("variant", "digest", "snapshot_fen")

# input FEN -> hand-pinned canonical snapshot (identity en passant, clocks 0 1)
POOL = {
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1": (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    ),
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1": (
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
    ),
    "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e6 0 2": (
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1"
    ),
    "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1": (
        "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
    ),
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1": "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1": "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1": (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1"
    ),
    "4k3/P7/8/8/8/8/8/4K3 w - - 0 1": "4k3/P7/8/8/8/8/8/4K3 w - - 0 1",
    "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1": "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1",
}
FENS = list(POOL)
SNAPS = sorted(set(POOL.values()))
START, AFTER_E4, AFTER_E4_E5, LEGAL_EP, KINGS, KINGS_B, START_KQ, PROMO_FROM, PROMO_TO = FENS
_FIXED = position_digest.digest_fen("standard", KINGS)


class _S(str):
    pass


class _D(dict):
    pass


class _L(list):
    pass


class _Base(BaseException):
    pass


def _real(variant, fen):
    return position_digest.digest_fen(variant, fen)


def _const(variant, fen):
    return _FIXED


# -- independent model --------------------------------------------------------------


def _node(snap, digest_fn=_real):
    return {"variant": "standard", "digest": digest_fn("standard", snap), "snapshot_fen": snap}


def _rows(model):
    return sorted(("standard", r["digest"], r["snapshot_fen"]) for r in model.values())


def _canon(fen):
    """The hand-pinned snapshot of a pool position with legal clocks, else None."""
    if type(fen) is not str:
        return None
    parts = fen.split(" ")
    if len(parts) != 6 or not all(p.isascii() and p.isdigit() for p in parts[4:]):
        return None
    if len(parts[4]) > 6 or len(parts[5]) > 6 or int(parts[5]) < 1:
        return None
    for raw, snap in POOL.items():
        if raw.split(" ")[:4] == parts[:4]:
            return None if parts[3] != "-" and parts[4] != "0" else snap
    return None


def _valid_insert(variant, fen):
    return type(variant) is str and variant == "standard" and _canon(fen) is not None


def _valid_record(rec):
    return (
        type(rec) is dict
        and all(type(k) is str for k in dict.keys(rec))
        and set(dict.keys(rec)) == set(FIELDS)
        and all(type(rec[f]) is str for f in FIELDS)
        and rec["variant"] == "standard"
        and rec["snapshot_fen"] in SNAPS
        and rec["digest"] == _real("standard", rec["snapshot_fen"])
    )


# -- generators -------------------------------------------------------------------------


def _clock(rng, fen):
    parts = fen.split(" ")
    parts[4] = "0" if parts[3] != "-" else str(rng.randint(0, 99))
    parts[5] = str(rng.randint(1, 300))
    return " ".join(parts)


BAD_VARIANTS = ("chess960", "Standard", "", _S("standard"), None, 1)
BAD_FEN_TYPES = (None, 1, START.encode(), _S(START))


def _bad_fens(base=START):
    return [
        "x",
        "",
        "not a fen",
        "8/8/8 w - - 0 1",
        base.rsplit(" ", 1)[0] + " 0",
        base.rsplit(" ", 1)[0] + " " + "9" * 5000,
        AFTER_E4.replace(" 0 1", " 5 1"),
        base + " ",
        base.replace(" ", "  ", 1),
        base.replace(" w ", " x "),
    ]


def _bad_snapshots():
    """Snapshot corruptions a record can carry; every one is malformed."""
    rows = {
        "clocks": KINGS[: -len("0 1")] + "3 9",
        "fullmove": KINGS[: -len("0 1")] + "0 2",
        "phantom-ep": AFTER_E4,
        "clock-5000": KINGS[:-1] + "9" * 5000,
    }
    rows.update({f"parse:{i}": f for i, f in enumerate(_bad_fens(KINGS))})
    return rows


def _bad_digests(snap):
    good = _real("standard", snap)
    other = _real("standard", next(s for s in SNAPS if s != snap))
    return {
        "upper": good.upper(),
        "short": good[:-1],
        "trailing-newline": good + "\n",
        "prefix": "pdv2:" + good[5:],
        "other-position": other,
        "empty": "",
    }


def _corrupt_insert(rng):
    fen = _clock(rng, rng.choice(FENS))
    kind = rng.randrange(3)
    if kind == 0:
        return (rng.choice(BAD_VARIANTS), fen), UV
    if kind == 1:
        return ("standard", rng.choice(BAD_FEN_TYPES)), MP
    return ("standard", rng.choice(_bad_fens(fen))), MP


def _corrupt_record(rng):
    rec = _node(rng.choice(SNAPS))
    kind = rng.randrange(9)
    if kind == 0:
        return rng.choice([None, "x", list(rec.values()), _D(rec)]), MNR
    if kind == 1:
        rec.pop(rng.choice(FIELDS))
        return rec, MNR
    if kind == 2:
        return {**rec, "note": "x"}, MNR
    if kind == 3:
        field = rng.choice(FIELDS)
        return {(_S(k) if k == field else k): v for k, v in rec.items()}, MNR
    if kind == 4:
        field = rng.choice(FIELDS)
        return {**rec, field: rng.choice([None, 1, rec[field].encode(), _S(rec[field])])}, MNR
    if kind == 5:
        return {**rec, "variant": rng.choice(["chess960", "Standard", ""])}, UV
    if kind == 6:
        return {
            **rec,
            "digest": rng.choice(sorted(_bad_digests(rec["snapshot_fen"]).values())),
        }, MNR
    snap = rng.choice(sorted(_bad_snapshots().values()))
    return {**rec, "snapshot_fen": snap, "digest": _real_or_fixed(snap)}, MNR


def _real_or_fixed(snap):
    try:
        return _real("standard", snap)
    except Exception:  # noqa: BLE001 - an unparseable snapshot keeps a valid-format digest
        return _FIXED


def _forges():
    """(label, oracle, forged holder) for every boundary forge and bad output."""
    raisers = {
        "ValueError": lambda: ValueError("x"),
        "Base": lambda: _Base("x"),
        "KeyboardInterrupt": lambda: KeyboardInterrupt(),
        "FenError": lambda: FenError("malformed_fen", "malformed_request"),
        "DigestError": lambda: position_digest.DigestError("x", "malformed_request"),
    }
    for cls in sorted(tn.FAILURE_MAPPING):
        raisers[f"NodeError:{cls}"] = lambda c=cls: tn.NodeError(c, tn.FAILURE_MAPPING[c])
    out = []
    for label, make in raisers.items():
        holder = [None]

        def oracle(variant, fen, make=make, holder=holder):
            holder[0] = make()
            raise holder[0]

        out.append((f"raise:{label}", oracle, holder))
    shapes = {
        "none": None,
        "bytes": _FIXED.encode(),
        "subclass": _S(_FIXED),
        "short": _FIXED[:-1],
        "upper": _FIXED.upper(),
        "trailing-newline": _FIXED + "\n",
        "int": 7,
    }
    for label, value in shapes.items():
        out.append((f"return:{label}", lambda v, f, value=value: value, [None]))
    return out


# -- outcome and state ------------------------------------------------------------------


def _outcome(call, forged=None):
    try:
        return ("accept", call())
    except BaseException as exc:  # noqa: BLE001 - a raw escape is the defect
        if type(exc).__name__ == "NodeError" and type(exc).__module__ in (
            tn.__name__,
            "transposition_node_mutant",
        ):
            if exc.__cause__ is not None or exc.__context__ is not None:
                return ("chained", exc.failure_class)
            if forged is not None and exc is forged[0]:
                return ("forged", exc.failure_class)
            return ("reject", exc.failure_class, exc.code)
        return ("crash", type(exc).__name__)


def _state(t):
    return copy.deepcopy(t.buckets), {k: id(v) for k, v in t.buckets.items()}, t.serialize()


class _Src:
    def __init__(self, records):
        self._r = records

    def records(self):
        return self._r


def _reject(out, cls, before, t, tag):
    if out[0] != "reject":
        kind = "crash" if out[0] == "crash" else "missing-rejection"
        return f"{tag} {cls} {out[0]}", f"{kind}:{cls}:{tag}:{out[:2]}"
    if out[1] != cls:
        return f"{tag} {cls} {out[1]}", f"wrong-class:{cls}:{tag}:{out[1]}"
    if out[2] != tn.FAILURE_MAPPING[cls]:
        return f"{tag} {cls}", f"wrong-code:{cls}:{tag}"
    if _state(t) != before:
        return f"{tag} {cls}", f"rollback:{tag}"
    return f"{tag} reject {cls}", None


# -- the seeded campaign ----------------------------------------------------------------


def _case(mod, rng, t, model):
    op = rng.randrange(7)
    before = _state(t)
    line = f"op{op}"
    if op in (0, 1):  # valid insert (new, or reinsert with other clocks)
        raw = rng.choice(FENS)
        snap = POOL[raw]
        line = "insert-reinsert accept" if snap in model else "insert-new accept"
        model.setdefault(snap, _node(snap))
        out = _outcome(lambda: t.insert("standard", _clock(rng, raw)))
        if out != ("accept", model[snap]):
            return f"insert-valid {out[0]}", f"accept-mismatch:{out[:2]}"
    elif op == 2:
        args, cls = _corrupt_insert(rng)
        if _valid_insert(*args):
            return "generator", "corruption-was-valid"
        line, problem = _reject(_outcome(lambda: t.insert(*args)), cls, before, t, "insert-bad")
        if problem:
            return line, problem
    elif op == 3:  # merge of a valid source
        recs = [_node(rng.choice(SNAPS)) for _ in range(rng.randint(0, 4))]
        out = _outcome(lambda: t.merge(_Src(copy.deepcopy(recs))))
        if out[0] != "accept" or out[1] is not t:
            return f"merge-valid {out[0]}", f"accept-mismatch:{out[:2]}"
        new = {r["snapshot_fen"] for r in recs} - set(model)
        for r in recs:
            model.setdefault(r["snapshot_fen"], r)
        line = f"merge-valid accept +{len(new)}"
    elif op == 4:  # merge with one corrupted record among valid records
        rec, cls = _corrupt_record(rng)
        if _valid_record(rec):
            return "generator", "corruption-was-valid"
        recs = [_node(rng.choice(SNAPS)) for _ in range(rng.randint(0, 3))]
        recs.insert(rng.randint(0, len(recs)), rec)
        line, problem = _reject(_outcome(lambda: t.merge(_Src(recs))), cls, before, t, "merge-bad")
        if problem:
            return line, problem
    elif op == 5:  # a source that is not an exact list
        good = [_node(rng.choice(SNAPS))]
        src = rng.choice([tuple(good), _L(good), None, {"r": good[0]}, "records", iter(good)])
        line, problem = _reject(
            _outcome(lambda: t.merge(_Src(src))), MNR, before, t, "merge-source"
        )
        if problem:
            return line, problem
    else:  # a faulty oracle on the campaign table itself
        label, oracle, holder = rng.choice(_forges())
        saved, t.digest_fn = t.digest_fn, oracle
        try:
            if rng.random() < 0.5:
                raw = rng.choice(FENS)
                out = _outcome(lambda: t.insert("standard", _clock(rng, raw)), holder)
                tag = "oracle-insert"
            else:
                out = _outcome(lambda: t.merge(_Src([_node(rng.choice(SNAPS))])), holder)
                tag = "oracle-merge"
        finally:
            t.digest_fn = saved
        line, problem = _reject(out, MNR, before, t, tag)
        line += f" {label}"
        if problem:
            return line, problem
    if t.serialize() != _rows(model):
        return line, "table-model-mismatch"
    return line, None


# -- the deterministic sweep ------------------------------------------------------------


def _sweep_rows(fn):
    """(label, kind, arg, class); class None is an accept row whose arg
    carries the expected final rows. Every row runs on a fresh table that
    holds the KINGS node."""
    good = _node(POOL[START], fn)
    uv = {**good, "variant": "chess960"}
    missing = {k: v for k, v in good.items() if k != "digest"}
    rows = [
        ("ins:variant+fen-type", "insert", ("chess960", None), UV),
        ("ins:variant+parse", "insert", (_S("standard"), "x"), UV),
        ("ins:fen-type", "insert", ("standard", None), MP),
        ("ins:parse", "insert", ("standard", "x"), MP),
        ("merge:uv-then-missing", "merge", [uv, missing], UV),
        ("merge:missing-then-uv", "merge", [missing, uv], MNR),
        ("merge:uv-then-non-dict", "merge", [uv, None], MNR),
        ("merge:uv-then-dict-subclass", "merge", [uv, _D(good)], MNR),
        ("merge:new-then-bad", "merge", [good, missing], MNR),
        ("merge:list-subclass", "merge", _L([good]), MNR),
        ("merge:tuple", "merge", (good,), MNR),
        ("merge:dict-subclass-record", "merge", [_D(good)], MNR),
        (
            "merge:subclass-key",
            "merge",
            [{(_S(k) if k == "digest" else k): v for k, v in good.items()}],
            MNR,
        ),
        (
            "merge:renamed-field",
            "merge",
            [{("dig" if k == "digest" else k): v for k, v in good.items()}],
            MNR,
        ),
    ]
    for v in BAD_VARIANTS:
        rows.append((f"ins:variant:{v!r}", "insert", (v, START), UV))
    for f in BAD_FEN_TYPES:
        rows.append((f"ins:fen-type:{type(f).__name__}", "insert", ("standard", f), MP))
    for i, f in enumerate(_bad_fens()):
        rows.append((f"ins:parse:{i}", "insert", ("standard", f), MP))
    for field in FIELDS:
        for value in (None, 1, good[field].encode(), _S(good[field])):
            rows.append(
                (
                    f"merge:field-{field}:{type(value).__name__}",
                    "merge",
                    [{**good, field: value}],
                    MNR,
                )
            )
    for v in ("chess960", "Standard", ""):
        rows.append((f"merge:variant:{v!r}", "merge", [{**good, "variant": v}], UV))
    for label, d in _bad_digests(POOL[START]).items():
        if fn is _const and label == "other-position":
            continue
        rows.append((f"merge:digest-{label}", "merge", [{**good, "digest": d}], MNR))
    for label, snap in _bad_snapshots().items():
        rec = {
            **good,
            "snapshot_fen": snap,
            "digest": _FIXED if fn is _const else _real_or_fixed(snap),
        }
        rows.append((f"merge:snapshot-{label}", "merge", [rec], MNR))
    # accept rows: reinsert with other clocks, a new node in the same bucket
    kings = _node(POOL[KINGS], fn)
    start = _node(POOL[START], fn)
    rows.append(("ins:reinsert-other-clocks", "insert", ("standard", KINGS[:-3] + "7 40"), [kings]))
    rows.append(("ins:new-same-bucket", "insert", ("standard", START), [kings, start]))
    rows.append(("merge:new-and-reinsert", "merge", [start, kings, start], [kings, start]))
    return rows


def _midway_oracle(fail_at):
    """A constant-digest oracle that fails on call number FAIL_AT."""
    calls = [0]

    def oracle(variant, fen):
        calls[0] += 1
        if calls[0] == fail_at:
            raise ValueError("midway")
        return _FIXED

    return oracle


NFIELDS = ("variant", "digest", "snapshot_fen")

# -- hostile types at every input boundary ------------------------------------------

HOSTILE = []  # user dunder calls observed during a hostile call


class _LogList(list):
    """A list subclass that logs any of its own accessors being used."""

    def __iter__(self):
        HOSTILE.append("iter")
        return list.__iter__(self)

    def __len__(self):
        HOSTILE.append("len")
        return list.__len__(self)

    def __getitem__(self, i):
        HOSTILE.append("getitem")
        return list.__getitem__(self, i)


class _LyingDict(dict):
    """A dict subclass whose own accessors log and lie about its content."""

    def __getitem__(self, key):
        HOSTILE.append(f"getitem:{key}")
        return dict.__getitem__(self, key)

    def keys(self):
        HOSTILE.append("keys")
        return list(NFIELDS)

    def __iter__(self):
        HOSTILE.append("iter")
        return iter(NFIELDS)

    def items(self):
        HOSTILE.append("items")
        return dict.items(self)

    def __len__(self):
        HOSTILE.append("len")
        return 3


class _EqRaises(str):
    def __eq__(self, other):
        HOSTILE.append("eq")
        raise RuntimeError("eq")

    def __ne__(self, other):
        HOSTILE.append("ne")
        raise RuntimeError("ne")

    __hash__ = str.__hash__


def _colliding(target):
    class _Collides(str):
        def __hash__(self):
            HOSTILE.append("hash")
            return hash(target)

        def __eq__(self, other):
            HOSTILE.append("eq")
            return str.__eq__(self, other)

        def __ne__(self, other):
            HOSTILE.append("ne")
            return str.__ne__(self, other)

    return _Collides


KEY_FORMS = ("plain", "eq-raises", "hash-collides")


def _hstr(form, text, target):
    return {"plain": _S, "eq-raises": _EqRaises, "hash-collides": _colliding(target)}[form](text)


def _inert(value):
    """Bytes of a (possibly hostile) argument read only through the base
    types' own methods, so no user dunder runs."""
    if isinstance(value, list):
        return "[" + ",".join(_inert(v) for v in list.__iter__(value)) + "]"
    if isinstance(value, dict):
        pairs = sorted((json.dumps(k), type(k).__name__, _inert(v)) for k, v in dict.items(value))
        return f"{type(value).__name__}{{{pairs}}}"
    return f"{type(value).__name__}:{json.dumps(value)}"


def _hostile_rows():
    """(label, kind, builder, class). Builders run per call so each row
    gets fresh hostile objects; kind is insert, merge or validate."""
    good = _node(POOL[START])
    ins = ("standard", START)
    rows = []
    for form in KEY_FORMS:
        for i, (name, cls) in enumerate((("variant_id", UV), ("fen_text", MP))):

            def build(form=form, i=i):
                args = list(ins)
                args[i] = _hstr(form, args[i], "standard")
                return tuple(args)

            rows.append((f"ins:{name}:{form}", "insert", build, cls))
    for where in ("merge", "validate"):
        if where == "merge":
            rows.append(
                ("merge:source:list-subclass", "merge", lambda: _LogList([dict(good)]), MNR)
            )
            rows.append(("merge:source:list-subclass-empty", "merge", lambda: _LogList(), MNR))

            def box(rec):
                return [rec]
        else:

            def box(rec):
                return rec

        rows.append((f"{where}:record:dict-subclass", where, lambda box=box: box(_D(good)), MNR))
        rows.append(
            (f"{where}:record:lying-dict", where, lambda box=box: box(_LyingDict(good)), MNR)
        )
        for field in NFIELDS:
            for form in KEY_FORMS:
                rows.append(
                    (
                        f"{where}:key-{field}:{form}",
                        where,
                        lambda box=box, field=field, form=form: box(
                            {
                                (_hstr(form, k, "variant") if k == field else k): v
                                for k, v in good.items()
                            }
                        ),
                        MNR,
                    )
                )
                rows.append(
                    (
                        f"{where}:value-{field}:{form}",
                        where,
                        lambda box=box, field=field, form=form: box(
                            {**good, field: _hstr(form, good[field], good[field])}
                        ),
                        MNR,
                    )
                )
    return rows


def _hostile_sweep(mod):
    """Every hostile row: the typed class the contract gives, an empty
    hostile-call log, the argument unchanged and the table unchanged."""
    for label, kind, build, cls in _hostile_rows():
        t = mod.NodeTable(DOCS)
        t.insert("standard", KINGS)
        before = _state(t)
        arg = build()
        snapshot = _inert(list(arg) if kind == "insert" else arg)
        HOSTILE.clear()  # construction may hash; only the production call counts
        if kind == "insert":
            out = _outcome(lambda t=t, arg=arg: t.insert(*arg))
        elif kind == "merge":
            out = _outcome(lambda t=t, arg=arg: t.merge(_Src(arg)))
        else:
            out = _outcome(lambda arg=arg: mod.validate_record(*DOCS, arg))
        calls = list(HOSTILE)
        tag = f"hostile:{label}"
        line, problem = _reject(out, cls, before, t, tag)
        if problem is None and calls:
            problem = f"hostile-call:{tag}:{calls}"
        if problem is None and _inert(list(arg) if kind == "insert" else arg) != snapshot:
            problem = f"input-changed:{tag}"
        yield line, problem


def _sweep(mod):
    for fn, dname in ((_real, "real"), (_const, "const")):
        for label, kind, arg, cls in _sweep_rows(fn):
            t = mod.NodeTable(DOCS, fn)
            t.insert("standard", KINGS)
            before = _state(t)
            if kind == "insert":
                out = _outcome(lambda t=t, arg=arg: t.insert(*arg))
            else:
                out = _outcome(lambda t=t, arg=arg: t.merge(_Src(copy.deepcopy(arg))))
            tag = f"sweep:{label}:{dname}"
            if cls is None or type(cls) is list:
                want = sorted(("standard", r["digest"], r["snapshot_fen"]) for r in cls)
                ok = out[0] == "accept" and t.serialize() == want
                if ok and kind == "insert":
                    ok = out[1] == next(r for r in cls if r["snapshot_fen"] == _canon(arg[1]))
                yield (f"{tag} accept", None if ok else f"accept-mismatch:{tag}:{out[:2]}")
                continue
            yield _reject(out, cls, before, t, tag)
    # an oracle failing during the staged inserts (after every record validated):
    # the first staged insert lands in the existing constant bucket
    t = mod.NodeTable(DOCS, _const)
    t.insert("standard", KINGS)
    before = _state(t)
    recs = [_node(POOL[START], _const), _node(POOL[START_KQ], _const)]
    t.digest_fn = _midway_oracle(len(recs) + 2)  # validate x2, insert 1 ok, insert 2 fails
    out = _outcome(lambda: t.merge(_Src(copy.deepcopy(recs))))
    t.digest_fn = _const
    yield _reject(out, MNR, before, t, "sweep:merge:oracle-fails-midway")
    # an oracle that rewrites the caller's live source during validation
    live = [_node(POOL[START])]

    def rewriting(variant, fen):
        live[0]["snapshot_fen"] = POOL[START_KQ]
        live[0]["digest"] = _real("standard", POOL[START_KQ])
        return _real(variant, fen)

    t = mod.NodeTable(DOCS)
    t.digest_fn = rewriting
    out = _outcome(lambda: t.merge(_Src(live)))
    t.digest_fn = _real
    want = [("standard", _real("standard", POOL[START]), POOL[START])]
    ok = out[0] == "accept" and t.serialize() == want
    yield (
        "sweep:merge:live-source-rewrite accept",
        None if ok else "toctou:sweep:merge:live-source-rewrite",
    )
    for label, oracle, holder in _forges():
        for where in ("insert", "merge", "validate"):
            t = mod.NodeTable(DOCS)
            t.insert("standard", KINGS)
            before = _state(t)
            t.digest_fn = oracle
            if where == "insert":
                out = _outcome(lambda t=t: t.insert("standard", START_KQ), holder)
            elif where == "merge":
                out = _outcome(lambda t=t: t.merge(_Src([_node(POOL[START_KQ])])), holder)
            else:
                out = _outcome(
                    lambda oracle=oracle: mod.validate_record(*DOCS, _node(POOL[START_KQ]), oracle),
                    holder,
                )
            t.digest_fn = _real
            yield _reject(out, MNR, before, t, f"forge:{label}:{where}")


def _campaign(mod, seed=0, cases=CASES):
    rng = random.Random(seed)
    t, model = mod.NodeTable(DOCS), {}
    log, problems = [], []
    for line, problem in _sweep(mod):
        log.append(line)
        if problem:
            problems.append((-1, problem))
    for line, problem in _hostile_sweep(mod):
        log.append(line)
        if problem:
            problems.append((-1, problem))
    for i in range(cases):
        line, problem = _case(mod, rng, t, model)
        log.append(f"{i} {line}")
        if problem:
            problems.append((i, problem))
    log.append(f"rows {len(model)}")
    return log, problems


# -- tests ------------------------------------------------------------------------------


def test_r1_campaign_uses_no_test_helpers():
    modules = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)


_CLEAN = {}


def _clean_campaign():
    if not _CLEAN:
        _CLEAN["run"] = _campaign(tn)
    return _CLEAN["run"]


def test_campaign_clean_run():
    log, problems = _clean_campaign()
    assert problems == []
    body = [line.split(" ", 1)[1] for line in log if line.split(" ", 1)[0].isdigit()]
    kinds = {line.split(" ")[0] for line in body}
    for tag in (
        "insert-new",
        "insert-reinsert",
        "insert-bad",
        "merge-valid",
        "merge-bad",
        "merge-source",
        "oracle-insert",
        "oracle-merge",
    ):
        assert tag in kinds, tag
    assert {line.split(" ")[2] for line in body if " reject " in line} == CLASSES
    assert int(log[-1].split()[1]) == len(SNAPS)


def test_every_boundary_class_is_forged():
    labels = {label for label, _o, _h in _forges()}
    assert {f"raise:NodeError:{c}" for c in tn.FAILURE_MAPPING} <= labels
    caught = set()
    for node in ast.walk(ast.parse(Path(tn.__file__).read_text())):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            elts = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            caught.update(ast.unparse(e).split(".")[-1] for e in elts)
    assert caught == {"BaseException", "FenError", "ValueError"}
    assert {"raise:FenError", "raise:ValueError", "raise:Base", "raise:KeyboardInterrupt"} <= labels


def test_determinism_pinned_log():
    a, _ = _clean_campaign()
    b, _ = _campaign(tn)
    assert a == b
    assert hashlib.sha256("\n".join(a).encode()).hexdigest() == PINNED_LOG_SHA256


PINNED_LOG_SHA256 = "3a1cdafb4864b78ed6238a723268cd46548d46bb8732548b312b3380d8cd98f8"


# -- fault injection: one-edit production mutants ---------------------------------------

MUTANTS = [
    (
        "exact-dict-isinstance",
        "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "    return isinstance(obj, dict) and all(type(k) is str for k in dict.keys(obj))",
    ),
    (
        "validate-key-set-before-exact-check",
        "    if not _exact_dict(record) or set(dict.keys(record)) != set(\n"
        '            nc["record"]["fields"]):',
        "    if set(dict.keys(record)) != set(\n"
        '            nc["record"]["fields"]) or not _exact_dict(record):',
    ),
    (
        "variant-membership-without-type-check",
        "    if type(variant_id) is not str or variant_id not in _variant_ids(vc):",
        "    if variant_id not in _variant_ids(vc):",
    ),
    (
        "exact-dict-drops-key-check",
        "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "    return type(obj) is dict",
    ),
    (
        "freeze-accepts-any-mapping",
        "            if not _exact_dict(rec):\n"
        '                _fail(self.nc, "malformed_node_record")\n',
        "",
    ),
    (
        "validate-shape-by-length",
        "    if not _exact_dict(record) or set(dict.keys(record)) != set(\n"
        '            nc["record"]["fields"]):',
        '    if not _exact_dict(record) or len(record) != len(nc["record"]["fields"]):',
    ),
    (
        "variant-field-type-unchecked",
        "    if type(variant) is not str or type(digest) is not str or \\\n",
        "    if type(digest) is not str or \\\n",
    ),
    (
        "clocks-unchecked",
        '    if fen_named["halfmove_clock"] != "0" or \\\n'
        '            fen_named["fullmove_number"] != "1":',
        "    if False:",
    ),
    (
        "ep-identity-unchecked",
        '    if fen_named["en_passant"] != named["en_passant"]:',
        "    if False:",
    ),
    (
        "digest-recompute-dropped",
        "    if digest != _oracle_digest(nc, dc, digest_fn, variant, snapshot):",
        "    if False:",
    ),
    (
        "variant-subclass-accepted",
        "    if type(variant_id) is not str or variant_id not in _variant_ids(vc):",
        "    if variant_id not in _variant_ids(vc):",
    ),
    (
        "oracle-boundary-exception",
        "    except BaseException:  # noqa: BLE001 - untrusted oracle boundary",
        "    except Exception:  # noqa: BLE001 - untrusted oracle boundary",
    ),
    (
        "oracle-output-isinstance",
        "    if type(out) is not str or re.fullmatch(",
        "    if not isinstance(out, str) or re.fullmatch(",
    ),
    (
        "oracle-output-match",
        "    if type(out) is not str or re.fullmatch(",
        "    if type(out) is not str or re.match(",
    ),
    (
        "reinsert-by-bucket",
        "            if self._identity(existing) == new_identity:",
        "            if True:",
    ),
    (
        "reinsert-appends-duplicate",
        "                return existing\n",
        "                pass\n",
    ),
    (
        "merge-source-isinstance",
        "        if type(source) is not list:",
        "        if not isinstance(source, (list, tuple)):",
    ),
    (
        "merge-skips-validation",
        "        for rec in frozen:\n"
        "            validate_record(self.nc, self.vc, self.dc, self.ec, self.fc,\n"
        "                            rec, self.digest_fn)\n",
        "",
    ),
    (
        "merge-unfrozen",
        "            frozen.append(dict(rec))",
        "            frozen.append(rec)",
    ),
    (
        "merge-commits-in-place",
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}",
        "        staged.buckets = self.buckets",
    ),
    (
        "merge-shares-bucket-lists",
        "        staged.buckets = {key: list(bucket)\n"
        "                          for key, bucket in self.buckets.items()}",
        "        staged.buckets = dict(self.buckets)",
    ),
]

OWN_CHECK = {
    "exact-dict-isinstance": f"missing-rejection:{MNR}:hostile:merge:record:dict-subclass",
    "validate-key-set-before-exact-check": "hostile-call:hostile:validate:key-",
    "variant-membership-without-type-check": f"crash:{UV}:hostile:ins:variant_id:eq-raises",
    "exact-dict-drops-key-check": f"missing-rejection:{MNR}:sweep:merge:subclass-key",
    "freeze-accepts-any-mapping": f"missing-rejection:{MNR}:sweep:merge:dict-subclass-record",
    "validate-shape-by-length": f"crash:{MNR}:sweep:merge:renamed-field",
    "variant-field-type-unchecked": f"wrong-class:{MNR}:sweep:merge:field-variant:NoneType",
    "clocks-unchecked": f"missing-rejection:{MNR}:sweep:merge:snapshot-clocks",
    "ep-identity-unchecked": f"missing-rejection:{MNR}:sweep:merge:snapshot-phantom-ep",
    "digest-recompute-dropped": f"missing-rejection:{MNR}:sweep:merge:digest-other-position",
    "variant-subclass-accepted": f"missing-rejection:{UV}:sweep:ins:variant:'standard'",
    "oracle-boundary-exception": f"crash:{MNR}:forge:raise:Base",
    "oracle-output-isinstance": f"missing-rejection:{MNR}:forge:return:subclass:insert",
    "oracle-output-match": f"missing-rejection:{MNR}:forge:return:trailing-newline:insert",
    "reinsert-by-bucket": "accept-mismatch:sweep:ins:new-same-bucket:const",
    "reinsert-appends-duplicate": "accept-mismatch:sweep:ins:reinsert-other-clocks",
    "merge-source-isinstance": f"missing-rejection:{MNR}:sweep:merge:tuple",
    "merge-skips-validation": f"missing-rejection:{MNR}:sweep:merge:new-then-bad",
    "merge-unfrozen": "toctou:sweep:merge:live-source-rewrite",
    "merge-commits-in-place": "rollback:sweep:merge:oracle-fails-midway",
    "merge-shares-bucket-lists": "rollback:sweep:merge:oracle-fails-midway",
}


def _mutant(old, new):
    src = Path(tn.__file__).read_text()
    assert src.count(old) == 1, old
    mod = types.ModuleType("transposition_node_mutant")
    mod.__file__ = tn.__file__
    exec(compile(src.replace(old, new), tn.__file__, "exec"), mod.__dict__)  # noqa: S102
    return mod


@pytest.mark.parametrize("name,old,new", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_injected_fault_detected_by_its_own_check(name, old, new):
    mod = _mutant(old, new)
    # the own check fires in the deterministic sweep alone ...
    _, problems = _campaign(mod, cases=0)
    assert any(p.startswith(OWN_CHECK[name]) for _i, p in problems), (name, problems[:3])
    # ... and the seeded campaign still flags the mutant
    _, problems = _campaign(mod, cases=300)
    assert problems, name


# Edits that no longer change behavior: the linked parse_fen is total since
# the T0089 'graph.fen: total parse_fen' commit (a non-str or str-subclass
# payload and an over-limit counter both fail as FenError malformed_fen).
# Precedence check: between the guard and parse_fen nothing reads the FEN or
# fails with another class (_make_record passes it straight to _parse, whose
# handler maps FenError to malformed_position), so each edit gives the same
# class, an empty hostile-call log and unchanged input. The guards stay as
# defense in depth. Each edit must keep the sweep and the seeded campaign green.
EQUIVALENT_EDITS = {
    "parse-catches-fen-error-only": (
        "    except (FenError, ValueError):",
        "    except FenError:",
    ),
    "position-subclass-accepted": (
        "    if type(fen_text) is not str:",
        "    if not isinstance(fen_text, str):",
    ),
}


@pytest.mark.parametrize("name", list(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name):
    old, new = EQUIVALENT_EDITS[name]
    log, problems = _campaign(_mutant(old, new))
    assert problems == [], (name, problems[:3])
    assert log == _clean_campaign()[0], name
