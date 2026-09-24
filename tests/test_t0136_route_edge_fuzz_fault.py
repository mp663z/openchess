"""T0136 route-edge fuzz/fault campaign against the production runtime
(graph.route_edge, T0134).

Where T0135 pins properties over seeded edges, this task runs a larger
classified campaign with these duties:

1. CLASSIFICATION: every campaign case (insert, merge of a source, or an
   operation under a faulty digest oracle) ends exactly one of accept -
   the returned record and the whole table equal an independent model
   ((variant, move, from snapshot) -> to snapshot, snapshots hand-pinned
   with identity en passant and clocks 0 1) - or reject - EdgeError with
   the failure class the case's single corruption predicts, its mapped
   code, and no __cause__/__context__. Any other exception is a CRASH
   and fails the suite with the case recorded.
2. ROLLBACK: every reject leaves the destination bit-identical (bucket
   contents and bucket list objects).
3. BOUNDARY FORGES: the digest oracle is the module's untrusted
   boundary. Every EdgeError and NodeError class, FenError, DigestError
   and ValueError (the classes in route_edge's own except clauses and
   those it raises), BaseException-only and KeyboardInterrupt raisers,
   and every malformed output shape are injected on insert and on merge
   validation; each must fail closed as a fresh malformed_edge_record,
   never as the forged object.
4. FAULT INJECTION: one-edit production mutants run through the same
   campaign detectors; each must be caught by its own check.
5. DETERMINISM: the campaign is pure in its seed; the classified log's
   SHA-256 is pinned, so a generator edit cannot silently change
   coverage.

A deterministic sweep runs before the seeded cases: precedence rows
(two corruptions, the production order decides), merge first-failure
order, a shared-bucket failed merge under a constant digest, merge
sources that are list subclasses or tuples, every oracle forge, direct
validate_record rows (extra, missing and renamed keys; same-square,
off-grammar, bad-promotion and wrong-length moves), and a merge whose
oracle rewrites the caller's source record during validation (the staged
edge must be the validated one).
Every corruption generator GUARANTEES invalidity: an independent
predicate re-checks the produced input and the campaign fails if a
"corruption" is actually valid.

Insert returns, and records() hands out, the stored record objects by
contract (T0134/T0135 pin `again is got`), so detachment is not a
property of this module and is not asserted here.
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

from graph import position_digest, transposition_node
from graph import route_edge as re_
from graph.fen import FenError

DOCS = re_.load_docs()
CASES = 700
MER, MP, UV, CE = (
    "malformed_edge_record",
    "malformed_position",
    "unknown_variant",
    "conflicting_edge",
)
CLASSES = {MER, MP, UV, CE}
FILES, RANKS, PROMOS = "abcdefgh", "12345678", "qrbn"
FIELDS = ("variant", "move", "from_snapshot_fen", "to_snapshot_fen")

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
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1": (
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w Kq - 0 1"
    ),
    "4k3/P7/8/8/8/8/8/4K3 w - - 0 1": "4k3/P7/8/8/8/8/8/4K3 w - - 0 1",
    "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1": "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1",
}
FENS = list(POOL)
SNAPS = sorted(set(POOL.values()))
START, AFTER_E4, AFTER_E4_E5, LEGAL_EP, KINGS, START_KQ, PROMO_FROM, PROMO_TO = FENS
MOVES = ["e2e4", "e1e2", "a7a8q", "g1f3", "h7h8n", "a1h8", "d7d5", "b1c3"]
_FIXED = re_.digest_fen("standard", START)


class _S(str):
    pass


class _D(dict):
    pass


class _L(list):
    pass


class _Base(BaseException):
    pass


# -- independent model --------------------------------------------------------------


def _valid_move(m):
    return (
        type(m) is str
        and len(m) in (4, 5)
        and m[0] in FILES
        and m[1] in RANKS
        and m[2] in FILES
        and m[3] in RANKS
        and m[:2] != m[2:4]
        and (len(m) == 4 or m[4] in PROMOS)
    )


def _canon(fen):
    """The hand-pinned snapshot of a pool position with any legal clocks."""
    if type(fen) is not str:
        return None
    parts = fen.split(" ")
    if len(parts) != 6 or not all(p.isdigit() and p.isascii() for p in parts[4:]):
        return None
    if len(parts[4]) > 6 or len(parts[5]) > 6 or int(parts[5]) < 1:
        return None
    for raw, snap in POOL.items():
        if raw.split(" ")[:4] == parts[:4]:
            if parts[3] != "-" and parts[4] != "0":
                return None
            return snap
    return None


def _rec(move, a, b, variant="standard"):
    return {"variant": variant, "move": move, "from_snapshot_fen": a, "to_snapshot_fen": b}


def _valid_insert(variant, move, a, b):
    return (
        type(variant) is str
        and variant == "standard"
        and _valid_move(move)
        and _canon(a) is not None
        and _canon(b) is not None
    )


def _valid_record(rec):
    return (
        type(rec) is dict
        and all(type(k) is str for k in dict.keys(rec))
        and set(dict.keys(rec)) == set(FIELDS)
        and all(type(rec[f]) is str for f in FIELDS)
        and rec["variant"] == "standard"
        and _valid_move(rec["move"])
        and rec["from_snapshot_fen"] in SNAPS
        and rec["to_snapshot_fen"] in SNAPS
    )


def _model_rows(model):
    return sorted(("standard", m, a, b) for (m, a), b in model.items())


def _model_apply(model, recs):
    """Staged iterated insertion: the new model, or CE."""
    staged = dict(model)
    for r in recs:
        key = (r["move"], r["from_snapshot_fen"])
        if key in staged and staged[key] != r["to_snapshot_fen"]:
            return CE
        staged.setdefault(key, r["to_snapshot_fen"])
    return staged


# -- generators -------------------------------------------------------------------------


def _clock(rng, fen):
    parts = fen.split(" ")
    parts[4] = "0" if parts[3] != "-" else str(rng.randint(0, 99))
    parts[5] = str(rng.randint(1, 300))
    return " ".join(parts)


def _move(rng):
    if rng.random() < 0.6:
        return rng.choice(MOVES)
    while True:
        a = rng.choice(FILES) + rng.choice(RANKS)
        b = rng.choice(FILES) + rng.choice(RANKS)
        if a != b:
            return a + b + rng.choice(["", "", "", *PROMOS])


def _bad_move(rng):
    good = _move(rng)
    return rng.choice(
        [
            "z" + good[1:],
            good[0] + "9" + good[2:],
            good[:2] + "i" + good[3:],
            good[:3] + "9" + good[4:],
            good[:2] + good[:2],
            good[:4] + rng.choice("kKQx1"),
            good[: rng.choice([0, 1, 2, 3])],
            good[:4] + "qq",
            good.upper(),
            good + "\n",
            "\uff45" + good[1:],
        ]
    )


def _bad_fen(rng):
    fen = _clock(rng, rng.choice(FENS))
    return rng.choice(
        [
            "x",
            "",
            "not a fen",
            "8/8/8 w - - 0 1",
            fen[: fen.rindex(" ")] + " 0",  # fullmove 0
            fen[: fen.rindex(" ")] + " " + "9" * 5000,  # beyond the int-str limit
            AFTER_E4.replace(" 0 1", " 5 1"),  # en passant with a halfmove clock
            fen + " ",
            fen.replace(" ", "  ", 1),
        ]
    )


def _corrupt_insert(rng):
    """(args, class) with exactly one corruption."""
    variant, move = "standard", _move(rng)
    a, b = _clock(rng, rng.choice(FENS)), _clock(rng, rng.choice(FENS))
    kind = rng.randrange(6)
    if kind == 0:
        variant = rng.choice(["chess960", "Standard", "", _S("standard"), None, 1])
        return (variant, move, a, b), UV
    if kind == 1:
        return (variant, rng.choice([None, 5, b"e2e4", _S(move), ("e2", "e4")]), a, b), MER
    if kind == 2:
        return (variant, _bad_move(rng), a, b), MER
    if kind == 3:
        bad = rng.choice([None, 1, a.encode(), _S(a)])
        return ((variant, move, bad, b) if rng.random() < 0.5 else (variant, move, a, bad)), MP
    bad = _bad_fen(rng)
    return ((variant, move, bad, b) if rng.random() < 0.5 else (variant, move, a, bad)), MP


def _good_record(rng):
    return _rec(_move(rng), rng.choice(SNAPS), rng.choice(SNAPS))


def _corrupt_record(rng):
    """(record, class) - one corruption of an incoming record."""
    rec = _good_record(rng)
    kind = rng.randrange(10)
    if kind == 0:
        return rng.choice([None, "e2e4", list(rec.values()), _D(rec)]), MER
    if kind == 1:
        rec.pop(rng.choice(FIELDS))
        return rec, MER
    if kind == 2:
        return {**rec, rng.choice(["digest", "note"]): "x"}, MER
    if kind == 3:
        field = rng.choice(FIELDS)
        return {(_S(k) if k == field else k): v for k, v in rec.items()}, MER
    if kind == 4:
        field = rng.choice(FIELDS)
        value = rng.choice([None, 1, rec[field].encode(), _S(rec[field])])
        return {**rec, field: value}, MER
    if kind == 5:
        return {**rec, "variant": rng.choice(["chess960", "Standard", ""])}, UV
    if kind == 6:
        return {**rec, "move": _bad_move(rng)}, MER
    field = rng.choice(["from_snapshot_fen", "to_snapshot_fen"])
    if kind == 7:  # a parseable position whose clocks are not the pinned 0 1
        snap = rng.choice([x for x in SNAPS if x.split(" ")[3] == "-"])
        return {
            **rec,
            field: snap[: -len("0 1")] + f"{rng.randint(1, 99)} {rng.randint(2, 300)}",
        }, MER
    if kind == 8:  # the raw phantom en-passant input form, never a snapshot
        return {**rec, field: AFTER_E4}, MER
    return {**rec, field: _bad_fen(rng)}, MER


def _identity_digest(variant, fen):
    return re_.digest_fen(variant, fen)


def _forges():
    """(label, oracle, forged holder) for every boundary forge and bad output."""
    out = []
    raisers = {
        "ValueError": lambda: ValueError("x"),
        "Base": lambda: _Base("x"),
        "KeyboardInterrupt": lambda: KeyboardInterrupt(),
        "FenError": lambda: FenError("malformed_fen", "malformed_request"),
        "DigestError": lambda: position_digest.DigestError("x", "malformed_request"),
    }
    for cls in sorted(re_.FAILURE_MAPPING):
        raisers[f"EdgeError:{cls}"] = lambda c=cls: re_.EdgeError(c, re_.FAILURE_MAPPING[c])
    for cls in sorted(transposition_node.FAILURE_MAPPING):
        raisers[f"NodeError:{cls}"] = lambda c=cls: transposition_node.NodeError(
            c, transposition_node.FAILURE_MAPPING[c]
        )
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
        "upper": _FIXED.upper() if _FIXED.upper() != _FIXED else "Z" * len(_FIXED),
        "trailing-newline": _FIXED + "\n",
        "int": 7,
    }
    for label, value in shapes.items():
        out.append((f"return:{label}", lambda v, f, value=value: value, [None]))
    return out


# -- the campaign ---------------------------------------------------------------------


def _outcome(call, forged=None):
    try:
        return ("accept", call())
    except BaseException as exc:  # noqa: BLE001 - a raw escape is the defect
        if type(exc).__name__ == "EdgeError" and type(exc).__module__ in (
            re_.__name__,
            "route_edge_mutant",
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
        return (
            f"{tag} {cls} {out[0]}",
            f"{'crash' if out[0] == 'crash' else 'missing-rejection'}:{cls}:{tag}:{out[:2]}",
        )
    if out[1] != cls:
        return f"{tag} {cls} {out[1]}", f"wrong-class:{cls}:{tag}:{out[1]}"
    if out[2] != re_.FAILURE_MAPPING[cls]:
        return f"{tag} {cls}", f"wrong-code:{cls}:{tag}"
    if _state(t) != before:
        return f"{tag} {cls}", f"rollback:{tag}"
    return f"{tag} reject {cls}", None


def _case(mod, rng, t, model):
    op = rng.randrange(7)
    before = _state(t)
    line = f"op{op}"
    if op in (0, 1):  # valid insert (new, reinsert with other clocks, or conflict)
        if model and rng.random() < 0.4:
            (move, a) = rng.choice(sorted(model))
            raw_a = next(r for r, s in POOL.items() if s == a)
            b = model[(move, a)] if rng.random() < 0.6 else rng.choice(SNAPS)
            raw_b = next(r for r, s in POOL.items() if s == b)
        else:
            move, raw_a, raw_b = _move(rng), rng.choice(FENS), rng.choice(FENS)
        args = ("standard", move, _clock(rng, raw_a), _clock(rng, raw_b))
        key, to = (move, POOL[raw_a]), POOL[raw_b]
        out = _outcome(lambda: t.insert(*args))
        if key in model and model[key] != to:
            line, problem = _reject(out, CE, before, t, "insert-conflict")
            if problem:
                return line, problem
        else:
            line = "insert-reinsert accept" if key in model else "insert-new accept"
            model.setdefault(key, to)
            want = _rec(move, key[1], to)
            if out != ("accept", want):
                return f"insert-valid {out[0]}", f"accept-mismatch:{out[:2]}"
    elif op == 2:  # corrupted insert
        args, cls = _corrupt_insert(rng)
        if _valid_insert(*args):
            return "generator", "corruption-was-valid"
        line, problem = _reject(_outcome(lambda: t.insert(*args)), cls, before, t, "insert-bad")
        if problem:
            return line, problem
    elif op == 3:  # merge of a valid source (may conflict with the table or itself)
        recs = [_good_record(rng) for _ in range(rng.randint(0, 4))]
        if model and rng.random() < 0.5:
            (move, a) = rng.choice(sorted(model))
            recs.insert(rng.randint(0, len(recs)), _rec(move, a, model[(move, a)]))
        want = _model_apply(model, recs)
        out = _outcome(lambda: t.merge(_Src(copy.deepcopy(recs))))
        if want == CE:
            line, problem = _reject(out, CE, before, t, "merge-conflict")
            if problem:
                return line, problem
        else:
            if out[0] != "accept" or out[1] is not t:
                return f"merge-valid {out[0]}", f"accept-mismatch:{out[:2]}"
            line = f"merge-valid accept +{len(want) - len(model)}"
            model.clear()
            model.update(want)
    elif op == 4:  # merge with one corrupted record among stored (reinsert) records
        rec, cls = _corrupt_record(rng)
        if _valid_record(rec):
            return "generator", "corruption-was-valid"
        recs = [
            _rec(m, a, b) for (m, a), b in rng.sample(sorted(model.items()), min(3, len(model)))
        ]
        recs.insert(rng.randint(0, len(recs)), rec)
        out = _outcome(lambda: t.merge(_Src(recs)))
        line, problem = _reject(out, cls, before, t, "merge-bad")
        if problem:
            return line, problem
    elif op == 5:  # merge source that is not an exact list
        good = [_good_record(rng)]
        src = rng.choice([tuple(good), _L(good), None, {"r": good[0]}, "records", iter(good)])
        line, problem = _reject(
            _outcome(lambda: t.merge(_Src(src))), MER, before, t, "merge-source"
        )
        if problem:
            return line, problem
    else:  # a faulty digest oracle on the campaign table itself
        label, oracle, holder = rng.choice(_forges())
        saved = t.digest_fn
        t.digest_fn = oracle
        try:
            if rng.random() < 0.5:
                args = (
                    "standard",
                    _move(rng),
                    _clock(rng, rng.choice(FENS)),
                    _clock(rng, rng.choice(FENS)),
                )
                out = _outcome(lambda: t.insert(*args), holder)
                tag = "oracle-insert"
            else:
                out = _outcome(lambda: t.merge(_Src([_good_record(rng)])), holder)
                tag = "oracle-merge"
        finally:
            t.digest_fn = saved
        line, problem = _reject(out, MER, before, t, tag)
        line += f" {label}"
        if problem:
            return line, problem
    if t.serialize() != _model_rows(model):
        return line, "table-model-mismatch"
    return line, None


def _sweep_rows():
    """(label, table builder, call, class). Every row is a single call on a
    fresh destination holding one START e2e4 edge."""
    good = _rec("e2e4", POOL[START], POOL[AFTER_E4])
    kq = _rec("e2e4", POOL[START_KQ], POOL[KINGS])
    conflict = _rec("e2e4", POOL[START], POOL[KINGS])
    bad_variant = {**kq, "variant": "chess960"}
    bad_shape = {k: v for k, v in kq.items() if k != "move"}
    rows = [
        # insert precedence: variant, move type, position type, move grammar, position parse
        ("ins:variant+move-type", ("insert", ("chess960", None, START, AFTER_E4)), UV),
        ("ins:move-type+pos-type", ("insert", ("standard", None, None, AFTER_E4)), MER),
        ("ins:pos-type+move-grammar", ("insert", ("standard", "e2e9", START, None)), MP),
        ("ins:move-grammar+pos-parse", ("insert", ("standard", "e2e9", "x", AFTER_E4)), MER),
        ("ins:variant+pos-parse", ("insert", (_S("standard"), "e2e4", "x", "x")), UV),
        ("ins:pos-parse+conflict", ("insert", ("standard", "e2e4", START, "x")), MP),
        ("ins:conflict", ("insert", ("standard", "e2e4", START, KINGS)), CE),
        # merge: the first failing record decides
        ("merge:shape-then-variant", ("merge", [bad_shape, bad_variant]), MER),
        ("merge:variant-then-shape", ("merge", [bad_variant, bad_shape]), UV),
        ("merge:conflict-then-bad", ("merge", [conflict, bad_shape]), CE),
        ("merge:bad-then-conflict", ("merge", [bad_variant, conflict]), UV),
        ("merge:new-then-conflict", ("merge", [kq, conflict]), CE),
        ("merge:self-conflict", ("merge", [kq, {**kq, "to_snapshot_fen": POOL[START]}]), CE),
        ("merge:new-then-bad", ("merge", [kq, bad_shape]), MER),
        ("merge:list-subclass", ("merge", _L([kq])), MER),
        ("merge:tuple", ("merge", (kq,)), MER),
        ("merge:dict-subclass-record", ("merge", [_D(kq)]), MER),
        (
            "merge:subclass-key",
            ("merge", [{_S("move") if k == "move" else k: v for k, v in kq.items()}]),
            MER,
        ),
        (
            "merge:renamed-field",
            ("merge", [{("mov" if k == "move" else k): v for k, v in kq.items()}]),
            MER,
        ),
        ("merge:subclass-variant", ("merge", [{**kq, "variant": _S("standard")}]), MER),
        (
            "merge:non-canonical-snapshot",
            ("merge", [{**kq, "to_snapshot_fen": KINGS.replace(" 0 1", " 3 9")}]),
            MER,
        ),
        ("merge:phantom-ep-snapshot", ("merge", [{**kq, "to_snapshot_fen": AFTER_E4}]), MER),
        ("merge:good-reinsert", ("merge", [good]), None),
    ]
    # every corruption sub-variant, enumerated (seeded draws never decide coverage)
    for m in (
        "z7e8",
        "e9e8",
        "e7i8",
        "e7e9",
        "e7e7",
        "e7e8k",
        "e7e8Q",
        "e7e8qq",
        "e7e",
        "",
        "E7E8",
        "e7e8\n",
        "\uff45" + "7e8",
    ):
        rows.append((f"ins:move:{m!r}", ("insert", ("standard", m, PROMO_FROM, PROMO_TO)), MER))
    for m in (None, 5, b"e2e4", _S("e2e4"), ("e2", "e4")):
        rows.append((f"ins:move-type:{m!r}", ("insert", ("standard", m, START, AFTER_E4)), MER))
    for v in ("chess960", "Standard", "", _S("standard"), None, 1):
        rows.append((f"ins:variant:{v!r}", ("insert", (v, "e2e4", START, AFTER_E4)), UV))
    for f in (None, 1, START.encode(), _S(START)):
        rows.append((f"ins:from-type:{f!r}", ("insert", ("standard", "e2e4", f, AFTER_E4)), MP))
        rows.append((f"ins:to-type:{f!r}", ("insert", ("standard", "e2e4", START, f)), MP))
    bad_fens = [
        "x",
        "",
        "not a fen",
        "8/8/8 w - - 0 1",
        START[:-1] + "0",
        START[:-1] + "9" * 5000,
        AFTER_E4.replace(" 0 1", " 5 1"),
        START + " ",
        START.replace(" ", "  ", 1),
    ]
    for i, f in enumerate(bad_fens):
        rows.append((f"ins:from-parse:{i}", ("insert", ("standard", "e2e4", f, AFTER_E4)), MP))
        rows.append((f"ins:to-parse:{i}", ("insert", ("standard", "e2e4", START, f)), MP))
    for field in FIELDS:
        for value in (None, 1, kq[field].encode(), _S(kq[field])):
            rows.append(
                (
                    f"merge:field-{field}:{type(value).__name__}",
                    ("merge", [{**kq, field: value}]),
                    MER,
                )
            )
    return rows


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
        return list(FIELDS)

    def __iter__(self):
        HOSTILE.append("iter")
        return iter(FIELDS)

    def items(self):
        HOSTILE.append("items")
        return dict.items(self)

    def __len__(self):
        HOSTILE.append("len")
        return 4


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
    kq = _rec("e2e4", POOL[START_KQ], POOL[KINGS])
    ins = ("standard", "e2e4", START_KQ, KINGS)
    rows = []
    for form in KEY_FORMS:
        for i, (name, cls) in enumerate(
            (("variant", UV), ("move", MER), ("from_fen", MP), ("to_fen", MP))
        ):

            def build(form=form, i=i):
                args = list(ins)
                args[i] = _hstr(form, args[i], "standard")
                return tuple(args)

            rows.append((f"ins:{name}:{form}", "insert", build, cls))
        # a hostile FEN with a bad move: the position type check precedes the
        # move grammar, so the class is malformed_position, never the move's
        for i, name in ((2, "from_fen"), (3, "to_fen")):

            def build_bad_move(form=form, i=i):
                args = list(ins)
                args[1] = "e2e2"
                args[i] = _hstr(form, args[i], "standard")
                return tuple(args)

            rows.append((f"ins:{name}+bad-move:{form}", "insert", build_bad_move, MP))
    for where, wrap in (("merge", "merge"), ("validate", "validate")):
        if where == "merge":
            rows.append(("merge:source:list-subclass", "merge", lambda: _LogList([dict(kq)]), MER))
            rows.append(("merge:source:list-subclass-empty", "merge", lambda: _LogList(), MER))

            def box(rec):
                return [rec]
        else:

            def box(rec):
                return rec

        rows.append((f"{where}:record:dict-subclass", wrap, lambda box=box: box(_D(kq)), MER))
        rows.append((f"{where}:record:lying-dict", wrap, lambda box=box: box(_LyingDict(kq)), MER))
        for field in FIELDS:
            for form in KEY_FORMS:
                rows.append(
                    (
                        f"{where}:key-{field}:{form}",
                        wrap,
                        lambda box=box, field=field, form=form: box(
                            {
                                (_hstr(form, k, "variant") if k == field else k): v
                                for k, v in kq.items()
                            }
                        ),
                        MER,
                    )
                )
                rows.append(
                    (
                        f"{where}:value-{field}:{form}",
                        wrap,
                        lambda box=box, field=field, form=form: box(
                            {**kq, field: _hstr(form, kq[field], kq[field])}
                        ),
                        MER,
                    )
                )
    return rows


def _hostile_sweep(mod):
    """Every hostile row: the typed class the contract gives, an empty
    hostile-call log, the argument unchanged and the table unchanged."""
    for label, kind, build, cls in _hostile_rows():
        t = mod.EdgeTable(DOCS)
        t.insert("standard", "e2e4", START, AFTER_E4)
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
    for label, (kind, arg), cls in _sweep_rows():
        for digest in ("real", "const"):
            fn = _identity_digest if digest == "real" else (lambda v, f: _FIXED)
            t = mod.EdgeTable(DOCS, fn)
            t.insert("standard", "e2e4", START, AFTER_E4)
            before = _state(t)
            if kind == "insert":
                out = _outcome(lambda t=t, arg=arg: t.insert(*arg))
            else:
                out = _outcome(lambda t=t, arg=arg: t.merge(_Src(copy.deepcopy(arg))))
            tag = f"sweep:{label}:{digest}"
            if cls is None:
                ok = out[0] == "accept" and _state(t)[2] == before[2]
                yield (f"s {tag} accept", None if ok else f"accept-mismatch:{tag}")
                continue
            yield _reject(out, cls, before, t, tag)
    for label, oracle, holder in _forges():
        for where in ("insert", "merge"):
            t = mod.EdgeTable(DOCS)
            t.insert("standard", "e2e4", START, AFTER_E4)
            t.insert("standard", "e1e2", KINGS, KINGS)
            before = _state(t)
            t.digest_fn = oracle
            if where == "insert":
                out = _outcome(lambda t=t: t.insert("standard", "g1f3", START_KQ, KINGS), holder)
            else:
                out = _outcome(
                    lambda t=t: t.merge(_Src([_rec("g1f3", POOL[START_KQ], POOL[KINGS])])), holder
                )
            t.digest_fn = _identity_digest
            yield _reject(out, MER, before, t, f"forge:{label}:{where}")
            if where == "merge":  # validate_record directly
                out = (
                    _outcome(
                        lambda oracle=oracle: re_.validate_record(
                            *DOCS, _rec("g1f3", POOL[START_KQ], POOL[KINGS]), oracle
                        ),
                        holder,
                    )
                    if mod is re_
                    else None
                )
                if out is not None and out[:2] != ("reject", MER):
                    yield (f"forge:{label}:validate", f"missing-rejection:{MER}:validate:{label}")


D4_SNAP = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1"
DIRECT_MOVES = ("e2e2", "z9e4", "a7a8k", "e2e", "e2e4qq")


def _direct_rows(mod):
    """validate_record called directly, plus the merge one-read TOCTOU row.
    merge re-checks the key set itself and staging re-checks the move, so
    only a direct call pins validate_record's own key-set and move checks."""
    good = _rec("e2e4", POOL[START], POOL[AFTER_E4])
    rows = [
        ("validate:extra-key", {**good, "extra": "x"}),
        ("validate:missing-key", {k: v for k, v in good.items() if k != "move"}),
        ("validate:renamed-key", {("mover" if k == "move" else k): v for k, v in good.items()}),
    ]
    rows += [(f"validate:move:{m!r}", {**good, "move": m}) for m in DIRECT_MOVES]
    for label, rec in rows:
        snapshot = copy.deepcopy(rec)
        out = _outcome(lambda rec=rec: mod.validate_record(*DOCS, rec))
        tag = f"direct:{label}"
        if out[:2] != ("reject", MER):
            kind = "crash" if out[0] == "crash" else "missing-rejection"
            yield f"{tag} {MER} {out[0]}", f"{kind}:{MER}:{tag}:{out[:2]}"
        elif out[2] != re_.FAILURE_MAPPING[MER]:
            yield f"{tag} {MER}", f"wrong-code:{MER}:{tag}"
        elif rec != snapshot:
            yield f"{tag} {MER}", f"input-changed:{tag}"
        else:
            yield f"{tag} reject {MER}", None
    # the oracle rewrites the caller's source record during validation; the
    # staged edge must be the validated one (e2e4 to the e4 snapshot)
    src = _rec("e2e4", POOL[START], POOL[AFTER_E4])
    armed, fired = [], []

    def oracle(variant, fen):
        if armed and not fired:
            fired.append(True)
            src["to_snapshot_fen"] = D4_SNAP
        return _identity_digest(variant, fen)

    t = mod.EdgeTable(DOCS, oracle)
    t.insert("standard", "e1e2", KINGS, KINGS)
    armed.append(True)
    out = _outcome(lambda: t.merge(_Src([src])))
    got = sorted(tuple(r[f] for f in FIELDS) for r in t.records())
    want = sorted(
        [
            ("standard", "e1e2", POOL[KINGS], POOL[KINGS]),
            ("standard", "e2e4", POOL[START], POOL[AFTER_E4]),
        ]
    )
    tag = "direct:merge:source-rewritten-during-validation"
    if out[0] != "accept" or not fired:
        yield f"{tag} {out[0]}", f"toctou:{tag}:{out[:2]}"
    elif got != want:
        yield f"{tag} accept", f"toctou:{tag}:staged-unvalidated"
    else:
        yield f"{tag} accept", None


def _campaign(mod, seed=0, cases=CASES):
    rng = random.Random(seed)
    t, model = mod.EdgeTable(DOCS), {}
    log, problems = [], []
    for line, problem in _sweep(mod):
        log.append(line)
        if problem:
            problems.append((-1, problem))
    for line, problem in _hostile_sweep(mod):
        log.append(line)
        if problem:
            problems.append((-1, problem))
    for line, problem in _direct_rows(mod):
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
        _CLEAN["run"] = _campaign(re_)
    return _CLEAN["run"]


def test_campaign_clean_run():
    log, problems = _clean_campaign()
    assert problems == []
    body = [line.split(" ", 1)[1] for line in log if line.split(" ", 1)[0].isdigit()]
    kinds = {line.split(" ")[0] for line in body}
    for tag in (
        "insert-new",
        "insert-reinsert",
        "insert-conflict",
        "insert-bad",
        "merge-valid",
        "merge-conflict",
        "merge-bad",
        "merge-source",
        "oracle-insert",
        "oracle-merge",
    ):
        assert tag in kinds, tag
    rejected = {line.split(" ")[2] for line in body if " reject " in line}
    assert rejected == CLASSES
    assert int(log[-1].split()[1]) >= 20


def test_every_boundary_class_is_forged():
    labels = {label for label, _o, _h in _forges()}
    assert {f"raise:EdgeError:{c}" for c in re_.FAILURE_MAPPING} <= labels
    assert {f"raise:NodeError:{c}" for c in transposition_node.FAILURE_MAPPING} <= labels
    # every class named in route_edge's own except clauses (tuples flattened)
    caught = set()
    for node in ast.walk(ast.parse(Path(re_.__file__).read_text())):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            elts = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
            caught.update(ast.unparse(e).split(".")[-1] for e in elts)
    assert caught == {"NodeError", "FenError", "ValueError"}
    assert {f"raise:{c}" for c in caught - {"NodeError"}} <= labels


def test_determinism_pinned_log():
    a, _ = _clean_campaign()
    b, _ = _campaign(re_)
    assert a == b
    assert hashlib.sha256("\n".join(a).encode()).hexdigest() == PINNED_LOG_SHA256


PINNED_LOG_SHA256 = "17d961373cd2e97aa3e53d9ed844b99cc279be7032a2cac23cdea95e7a1c502c"


# -- fault injection: one-edit production mutants ---------------------------------------

MUTANTS = [
    (
        "exact-dict-isinstance",
        "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "    return isinstance(obj, dict) and all(type(k) is str for k in dict.keys(obj))",
    ),
    (
        "merge-key-set-before-exact-check",
        "            if not _exact_dict(rec) or set(dict.keys(rec)) != set(\n"
        '                    self.ec["record"]["fields"]):',
        "            if set(dict.keys(rec)) != set(\n"
        '                    self.ec["record"]["fields"]) or not _exact_dict(rec):',
    ),
    (
        "validate-key-set-before-exact-check",
        "    if not _exact_dict(record) or set(dict.keys(record)) != set(\n"
        '            ec["record"]["fields"]):',
        "    if set(dict.keys(record)) != set(\n"
        '            ec["record"]["fields"]) or not _exact_dict(record):',
    ),
    (
        "variant-membership-without-type-check",
        "    if type(variant) is not str or variant not in [",
        "    if variant not in [",
    ),
    (
        "exact-dict-drops-key-check",
        "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "    return type(obj) is dict",
    ),
    (
        "promotion-letter-unchecked",
        '        move[4] in shape["types"]["promotion"]["enum"])',
        "        True)",
    ),
    (
        "move-distinct-skipped",
        '    if shape["from_to_distinct"] and squares[0] == squares[1]:',
        "    if False:",
    ),
    (
        "move-length-unchecked",
        "    if len(move) not in (squares_len, squares_len + 1):\n        return False\n",
        "",
    ),
    (
        "variant-subclass-accepted",
        "    if type(variant) is not str or variant not in [",
        "    if variant not in [",
    ),
    (
        "move-type-before-variant",
        "    if type(variant) is not str or variant not in [\n"
        '            e["id"] for e in vc["variants"]["entries"]]:\n'
        '        _fail(ec, "unknown_variant")\n'
        "    if type(move) is not str:\n"
        '        _fail(ec, "malformed_edge_record")\n',
        "    if type(move) is not str:\n"
        '        _fail(ec, "malformed_edge_record")\n'
        "    if type(variant) is not str or variant not in [\n"
        '            e["id"] for e in vc["variants"]["entries"]]:\n'
        '        _fail(ec, "unknown_variant")\n',
    ),
    (
        "grammar-before-position-type",
        "    if type(from_fen) is not str or type(to_fen) is not str:\n"
        '        _fail(ec, "malformed_position")\n'
        "    if not _move_ok(lc, move):\n"
        '        _fail(ec, "malformed_edge_record")\n',
        "    if not _move_ok(lc, move):\n"
        '        _fail(ec, "malformed_edge_record")\n'
        "    if type(from_fen) is not str or type(to_fen) is not str:\n"
        '        _fail(ec, "malformed_position")\n',
    ),
    (
        "position-str-subclass-accepted",
        "    if type(from_fen) is not str or type(to_fen) is not str:",
        "    if not isinstance(from_fen, str) or not isinstance(to_fen, str):",
    ),
    (
        "oracle-node-error-escapes",
        "    except _node.NodeError:\n        failed = True\n    if failed:\n"
        '        _fail(ec, "malformed_edge_record")\n    return out',
        "    except _node.NodeError:\n        raise\n    return out",
    ),
    (
        "insert-bucket-skips-oracle",
        "        bucket_key = (_oracle(self.ec, self.nc, self.dc, self.digest_fn,\n"
        '                              variant_id, rec["from_snapshot_fen"]), move)',
        '        bucket_key = (rec["from_snapshot_fen"], move)',
    ),
    (
        "conflict-ignored",
        '                _fail(self.ec, "conflicting_edge")',
        "                pass",
    ),
    (
        "reinsert-matches-bucket-only",
        "            if existing_identity == new_identity:",
        "            if True:",
    ),
    (
        "conflict-ignores-from-identity",
        "            if existing_identity[:3] == new_identity[:3]:",
        "            if existing_identity[:2] == new_identity[:2]:",
    ),
    (
        "record-field-types-unchecked",
        "    if not all(type(record[f]) is str for f in _FIELDS):\n"
        '        _fail(ec, "malformed_edge_record")\n',
        "",
    ),
    (
        "record-snapshot-node-error-escapes",
        "        except _node.NodeError:\n            failed = True\n        if failed:\n"
        '            _fail(ec, "malformed_edge_record")\n    return record',
        "        except _node.NodeError:\n            raise\n    return record",
    ),
    (
        "merge-source-list-subclass",
        "        if type(source) is not list:",
        "        if not isinstance(source, (list, tuple)):",
    ),
    (
        "merge-shape-by-length",
        "            if not _exact_dict(rec) or set(dict.keys(rec)) != set(\n"
        '                    self.ec["record"]["fields"]):',
        '            if not _exact_dict(rec) or len(rec) != len(self.ec["record"]["fields"]):',
    ),
    (
        "validate-key-set-superset",
        "    if not _exact_dict(record) or set(dict.keys(record)) != set(\n"
        '            ec["record"]["fields"]):',
        "    if not _exact_dict(record) or not set(dict.keys(record)) >= set(\n"
        '            ec["record"]["fields"]):',
    ),
    (
        "validate-move-unchecked",
        '    if not _move_ok(lc, record["move"]):',
        "    if False:",
    ),
    (
        "merge-one-read-copy-removed",
        "            rec = {f: dict.__getitem__(rec, f) for f in _FIELDS}\n",
        "",
    ),
    (
        "merge-skips-validation",
        "            validate_record(*self._docs(), rec, self.digest_fn)\n",
        "",
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
    "exact-dict-isinstance": f"missing-rejection:{MER}:hostile:merge:record:dict-subclass",
    "merge-key-set-before-exact-check": "hostile-call:hostile:merge:key-",
    "validate-key-set-before-exact-check": "hostile-call:hostile:validate:key-",
    "variant-membership-without-type-check": f"crash:{UV}:hostile:ins:variant:eq-raises",
    "exact-dict-drops-key-check": f"missing-rejection:{MER}:sweep:merge:subclass-key",
    "promotion-letter-unchecked": f"missing-rejection:{MER}:sweep:ins:move:'e7e8k'",
    "move-distinct-skipped": f"missing-rejection:{MER}:sweep:ins:move:'e7e7'",
    "move-length-unchecked": f"crash:{MER}:sweep:ins:move:'e7e'",
    "variant-subclass-accepted": f"missing-rejection:{UV}:sweep:ins:variant:'standard'",
    "move-type-before-variant": f"wrong-class:{UV}:sweep:ins:variant+move-type",
    "grammar-before-position-type": f"wrong-class:{MP}:sweep:ins:pos-type+move-grammar",
    "position-str-subclass-accepted": f"wrong-class:{MP}:hostile:ins:from_fen+bad-move:plain",
    "oracle-node-error-escapes": f"crash:{MER}:forge:",
    "insert-bucket-skips-oracle": f"missing-rejection:{MER}:forge:",
    "conflict-ignored": f"missing-rejection:{CE}:sweep:ins:conflict",
    "reinsert-matches-bucket-only": f"missing-rejection:{CE}:sweep:ins:conflict",
    "conflict-ignores-from-identity": f"wrong-class:{MER}:sweep:merge:new-then-bad:const",
    "record-field-types-unchecked": f"wrong-class:{MER}:sweep:merge:field-variant:NoneType",
    "record-snapshot-node-error-escapes": f"crash:{MER}:sweep:merge:non-canonical-snapshot",
    "merge-source-list-subclass": f"missing-rejection:{MER}:sweep:merge:list-subclass",
    "merge-shape-by-length": f"crash:{MER}:sweep:merge:renamed-field",
    "validate-key-set-superset": f"missing-rejection:{MER}:direct:validate:extra-key",
    "validate-move-unchecked": f"missing-rejection:{MER}:direct:validate:move:",
    "merge-one-read-copy-removed": "toctou:direct:merge:source-rewritten",
    "merge-skips-validation": f"missing-rejection:{MER}:sweep:merge:non-canonical-snapshot",
    "merge-commits-in-place": "rollback:sweep:merge:",
    "merge-shares-bucket-lists": "rollback:sweep:merge:",
}


def _mutant(old, new):
    src = Path(re_.__file__).read_text()
    assert src.count(old) == 1, old
    mod = types.ModuleType("route_edge_mutant")
    mod.__file__ = re_.__file__
    exec(compile(src.replace(old, new), re_.__file__, "exec"), mod.__dict__)  # noqa: S102
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


# Edits that no longer change behavior: the linked parse_fen is total since the
# T0089 'graph.fen: total parse_fen' commit (a non-str or str-subclass payload
# and an over-limit counter both fail as FenError malformed_fen). Precedence
# check: the try block holds only the two parse_fen calls, so no other ValueError
# can reach the handler, and FenError still maps to malformed_position. The
# guard stays as defense in depth. position-str-subclass-accepted is NOT here:
# _move_ok runs between the position type check and parse_fen, so a str-subclass
# FEN with a bad move would change class (pinned by the +bad-move hostile rows).
EQUIVALENT_EDITS = {
    "parse-catches-fen-error-only": (
        "    except (FenError, ValueError):  # ValueError: clock over int-str limit",
        "    except FenError:",
    ),
}


@pytest.mark.parametrize("name", list(EQUIVALENT_EDITS))
def test_equivalent_edit_stays_green(name):
    old, new = EQUIVALENT_EDITS[name]
    log, problems = _campaign(_mutant(old, new))
    assert problems == [], (name, problems[:3])
    assert log == _clean_campaign()[0], name
