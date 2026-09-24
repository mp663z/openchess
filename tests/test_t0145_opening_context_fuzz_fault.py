"""T0145 opening-context fuzz/fault campaign against the production
runtime (graph.opening_context, T0143).

Where T0144 pins properties over seeded paths, this task runs a larger
classified campaign with four duties:

1. CLASSIFICATION: every campaign case (insert, or merge of a source
   table) ends exactly one of accept - the table equals an independent
   longest-prefix model built from data/openings/registry.yaml - or
   reject - ContextError with the failure class the case's single
   corruption predicts and its mapped code. Any other exception is a
   CRASH and fails the suite with the case recorded.
2. ROLLBACK: every reject leaves the destination bit-identical.
3. FAULT INJECTION: one-edit production mutants are run through the
   same campaign detectors; each must be caught by its own check.
4. DETERMINISM: the campaign is pure in its seed; its classified log's
   SHA-256 is pinned, so a generator edit cannot silently change
   coverage.
5. DETACHMENT: after every accept, scribbling on the returned record and
   on every records/map item leaves the table unchanged.

A deterministic sweep runs before the seeded cases: record-semantics
rows (sentinel pairing, registry name, renamed field) in the stored and
incoming positions, incoming tuple path, incoming non-str fields on an
existing key, well-formed drifted keys, and merge sources that are a
ContextTable subclass or hold a dict-subclass store.

Every corruption generator GUARANTEES invalidity: an independent
predicate re-checks the produced input against the model and the
campaign fails if a "corruption" is actually valid.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import random
import types
from pathlib import Path

import pytest
import yaml

from graph import opening_context as oc

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = yaml.safe_load((ROOT / "data" / "openings" / "registry.yaml").read_text())["registry"]
SENTINEL = REGISTRY["none_sentinel"]
ENTRIES = REGISTRY["entries"]
FILES, RANKS, PROMOS = "abcdefgh", "12345678", "qrbn"
CASES = 800
MCR, MP, UV, UOC, CC = (
    "malformed_context_record",
    "malformed_path",
    "unknown_variant",
    "unknown_opening_code",
    "conflicting_context",
)
CLASSES = {MCR, MP, UV, UOC, CC}


class _S(str):
    pass


class _L(list):
    pass


class _D(dict):
    pass


# -- independent model --------------------------------------------------------------


def _model(path):
    best = None
    for entry in ENTRIES:
        n = len(entry["moves"])
        if path[:n] == entry["moves"] and (best is None or n > len(best["moves"])):
            best = entry
    return (SENTINEL, SENTINEL) if best is None else (best["code"], best["name"])


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


def _valid_path(p):
    return type(p) is list and all(_valid_move(m) for m in p)


def _record(path, variant="standard"):
    code, name = _model(path)
    return {
        "variant": variant,
        "path_moves": list(path),
        "opening_code": code,
        "opening_name": name,
    }


def _move(rng):
    while True:
        a = rng.choice(FILES) + rng.choice(RANKS)
        b = rng.choice(FILES) + rng.choice(RANKS)
        if a != b:
            return a + b + rng.choice(["", "", "", *PROMOS])


def _path(rng):
    base = list(rng.choice(ENTRIES)["moves"]) if rng.random() < 0.8 else []
    base = base[: rng.randint(0, len(base))] if rng.random() < 0.4 else base
    return base + [_move(rng) for _ in range(rng.randint(0, 3))]


# -- corruption generators (each guarantees invalidity) -------------------------------


def _bad_move(rng):
    good = _move(rng)
    kind = rng.randrange(9)
    if kind == 0:
        return "z" + good[1:]
    if kind == 1:
        return good[0] + rng.choice("09") + good[2:]
    if kind == 2:
        return good[:2] + "i" + good[3:]
    if kind == 3:
        return good[:3] + "9" + good[4:]  # the off-by-one destination rank
    if kind == 4:
        return good[:2] + good[:2]
    if kind == 5:
        return good[:4] + rng.choice("kKQx1")
    if kind == 6:
        return good[: rng.choice([0, 1, 2, 3])]
    if kind == 7:
        return good[:4] + "qq"
    return rng.choice([good.upper(), _S(good), None, 5, b"e2e4", "\uff45" + good[1:]])


def _corrupt_insert(rng):
    """(variant, path, expected class) with exactly one corruption."""
    path = _path(rng)
    kind = rng.randrange(5)
    if kind == 4:  # an otherwise valid move with a trailing newline
        i = rng.randint(0, len(path))
        return "standard", path[:i] + [_move(rng) + "\n"] + path[i:], MP
    if kind == 3:  # a str-subclass move that would otherwise be valid
        i = rng.randint(0, len(path))
        return "standard", path[:i] + [_S(_move(rng))] + path[i:], MP
    if kind == 0:
        variant = rng.choice(["chess960", "Standard", "", _S("standard"), None, 1])
        return variant, path, UV
    if kind == 1:
        bad = rng.choice([tuple(path), " ".join(path), None, _L(path), {"m": path}])
        return "standard", bad, MP
    i = rng.randint(0, len(path))
    return "standard", path[:i] + [_bad_move(rng)] + path[i:], MP


def _corrupt_record(rng, path):
    """(record, expected class) - one corruption of a stored record whose
    key is NEW to the destination."""
    rec = _record(path)
    kind = rng.randrange(11)
    if kind == 9:  # a str-subclass field name
        field = rng.choice(sorted(rec))
        return {(_S(k) if k == field else k): v for k, v in rec.items()}, MCR
    if kind == 10:  # an exact-str move with a trailing newline
        return {**rec, "path_moves": path + [_move(rng) + "\n"]}, MP
    if kind == 0:
        return _D(rec), MCR
    if kind == 1:
        rec.pop(rng.choice(sorted(rec)))
        return rec, MCR
    if kind == 2:
        return {**rec, "extra": 1}, MCR
    if kind == 3:
        return {**rec, "opening_name": _S(rec["opening_name"])}, MCR
    if kind == 4:  # a real registry pair that is not this path's resolution
        other = next(e for e in ENTRIES if (e["code"], e["name"]) != _model(path))
        return {**rec, "opening_code": other["code"], "opening_name": other["name"]}, MCR
    if kind == 5:  # well-formed code absent from the registry
        return {**rec, "opening_code": "E99", "opening_name": "Nothing"}, UOC
    if kind == 6:  # code and name disagree on the sentinel
        return {**rec, "opening_code": SENTINEL, "opening_name": "Nothing"}, MCR
    if kind == 7:
        return {**rec, "variant": "chess960"}, UV
    bad = path + [_bad_move(rng)]
    # merge preflight is type-only (reference order): a non-exact-str move
    # is a malformed record there; an exact str failing the grammar is a
    # malformed path at validation
    return {**rec, "path_moves": bad}, MP if all(type(m) is str for m in bad) else MCR


def _is_valid_record(rec, variants=frozenset({"standard"})):
    return (
        type(rec) is dict
        and all(type(k) is str for k in dict.keys(rec))
        and set(rec) == {"variant", "path_moves", "opening_code", "opening_name"}
        and type(rec["variant"]) is str
        and rec["variant"] in variants
        and _valid_path(rec["path_moves"])
        and all(type(rec[f]) is str for f in ("opening_code", "opening_name"))
        and (rec["opening_code"], rec["opening_name"]) == _model(rec["path_moves"])
    )


# -- the campaign ---------------------------------------------------------------------


def _outcome(mod, call):
    try:
        return ("accept", call())
    except Exception as exc:
        if type(exc).__name__ == "ContextError" and type(exc).__module__ in (
            oc.__name__,
            "opening_context_mutant",
        ):
            return ("reject", exc.failure_class, exc.code)
        return ("crash", type(exc).__name__)


def _state(t):
    return copy.deepcopy(t._records), t.serialize()


def _case(mod, rng, t, model):
    """One classified case. Returns (log line, problem or None); `model`
    maps (variant, path tuple) -> record for the destination."""
    op = rng.randrange(5)
    before = _state(t)
    if op == 0:  # valid insert (new or reinsert)
        path = _path(rng) if rng.random() < 0.7 or not model else list(rng.choice(sorted(model))[1])
        out = _outcome(mod, lambda: t.insert("standard", list(path)))
        want = _record(path)
        model.setdefault(("standard", tuple(path)), want)
        if out != ("accept", want):
            return f"insert-valid {out[0]}", "accept-mismatch"
        if not _detached(t, [out[1]]):
            return "insert-valid", "aliased"
    elif op == 1:  # corrupted insert
        variant, path, cls = _corrupt_insert(rng)
        if (
            type(path) is list
            and _valid_path(path)
            and variant == "standard"
            and type(variant) is str
        ):
            return "generator", "corruption-was-valid"
        out = _outcome(mod, lambda: t.insert(variant, path))
        if out != ("reject", cls, oc.FAILURE_MAPPING[cls]):
            return f"insert-bad {cls} {out[0]}", f"missing-rejection:{cls}:{out[:2]}"
        if _state(t) != before:
            return f"insert-bad {cls}", "rollback"
    elif op == 2:  # merge of a valid source table
        src = mod.ContextTable()
        add = [_path(rng) for _ in range(rng.randint(0, 3))]
        for p in add:
            src.insert("standard", list(p))
        out = _outcome(mod, lambda: t.merge(src))
        for p in add:
            model.setdefault(("standard", tuple(p)), _record(p))
        if out[0] != "accept":
            return f"merge-valid {out[0]}", f"accept-mismatch:{out}"
        if not _detached(t, []):
            return "merge-valid", "aliased"
    elif op == 3 and rng.random() < 0.2:
        # a STORED destination record whose opening_code is not an exact str
        # (None or int) beside a real, non-sentinel opening name: the next
        # merge revalidates the destination and must reject it typed. Runs on
        # a scratch table so the campaign table and model stay untouched.
        dst = mod.ContextTable()
        dpath = _path(rng)
        dst.insert("standard", list(dpath))
        dkey = next(iter(dst._records))
        code = rng.choice([None, 7])
        dst._records[dkey] = {
            **dst._records[dkey],
            "opening_code": code,
            "opening_name": rng.choice(ENTRIES)["name"],
        }
        dbefore = _state(dst)
        out = _outcome(mod, lambda: dst.merge(mod.ContextTable()))
        if out != ("reject", MCR, oc.FAILURE_MAPPING[MCR]):
            return f"merge-dst-bad {out[0]}", f"missing-rejection:{MCR}:dst:{out[:2]}"
        if _state(dst) != dbefore:
            return "merge-dst-bad", "rollback"
        return f"merge-dst-bad {out[1]}", None
    elif op == 3:  # merge of a source carrying one corrupted NEW record
        path = _path(rng) + [_move(rng), _move(rng)]
        while ("standard", tuple(path)) in model:
            path.append(_move(rng))
        rec, cls = _corrupt_record(rng, path)
        if _is_valid_record(rec):
            return "generator", "corruption-was-valid"
        src = mod.ContextTable()
        good = _path(rng)
        src.insert("standard", list(good))
        # the key the record claims (so key drift is never the failure)
        moves = dict.get(rec, "path_moves", path)
        key = (dict.get(rec, "variant", "standard"), tuple(moves))
        if rng.random() < 0.3:  # instead: a valid record under a malformed key
            rec, cls = _record(path), MCR
            key = rng.choice(
                [
                    # wrong arity weighted up: the tuple-length guard
                    ("standard", tuple(path), "x"),
                    ("standard", tuple(path), "x"),
                    ("standard",),
                    ("standard",),
                    ("standard", " ".join(path)),
                    (_S("standard"), tuple(path)),
                    ("standard", tuple(_S(m) for m in path)),
                ]
            )
        src._records[key] = rec
        out = _outcome(mod, lambda: t.merge(src))
        if out[0] != "reject" or out[1] not in CLASSES:
            return f"merge-bad {cls} {out[0]}", f"missing-rejection:{cls}:{out[:2]}"
        if out[1:] != (cls, oc.FAILURE_MAPPING[cls]):
            return f"merge-bad {cls} {out[1]}", f"wrong-class:{cls}:{out[1]}"
        if _state(t) != before:
            return f"merge-bad {cls}", "rollback"
        return f"merge-bad {out[1]}", None
    else:  # merge with a same-key record whose code/name differ
        if not model:
            return "skip", None
        key = rng.choice(sorted(model))
        other = next(e for e in ENTRIES if (e["code"], e["name"]) != _model(list(key[1])))
        src = mod.ContextTable()
        src._records[key] = {
            **model[key],
            "opening_code": other["code"],
            "opening_name": other["name"],
        }
        out = _outcome(mod, lambda: t.merge(src))
        if out != ("reject", CC, oc.FAILURE_MAPPING[CC]):
            return f"merge-conflict {out[0]}", f"missing-rejection:{CC}:{out[:2]}"
        if _state(t) != before:
            return "merge-conflict", "rollback"
    want_rows = sorted(
        (r["variant"], " ".join(r["path_moves"]), r["opening_code"], r["opening_name"])
        for r in model.values()
    )
    if t.serialize() != want_rows:
        return f"op{op}", "table-model-mismatch"
    return f"op{op} {out[0]}" + (f" {out[1]}" if out[0] == "reject" else ""), None


def _scribble(rec):
    rec["path_moves"].append("a1a2")
    rec["opening_name"] += "~"
    rec["variant"] = "zz"


def _detached(t, held):
    """Scribbling on every record the caller holds (returned values, each
    records item, each map value) never reaches the table."""
    after = _state(t)
    for rec in [*held, *t.records, *t.map.values()]:
        _scribble(rec)
    return _state(t) == after


P_BASE = ["e2e4", "e7e5"]  # C20, inserted into every sweep destination
Q_PATH = ["d2d4", "d7d5", "c2c4"]  # D20, never in a sweep destination


def _q(**over):
    return {**_record(Q_PATH), **over}


def _rename(rec, field, new):
    return {(new if k == field else k): v for k, v in rec.items()}


def _sweep_rows():
    """(label, position, key, record): position "stored" plants into the
    destination and merges an empty source; "incoming" plants into the
    source. Every row expects malformed_context_record."""
    q_key = ("standard", tuple(Q_PATH))
    p_key = ("standard", tuple(P_BASE))
    other_name = next(e["name"] for e in ENTRIES if e["code"] != _model(Q_PATH)[0])
    semantic = [
        ("sentinel-code-real-name", _q(opening_code=SENTINEL)),
        ("real-code-sentinel-name", _q(opening_name=SENTINEL)),
        ("real-code-other-entry-name", _q(opening_name=other_name)),
        ("renamed-field", _rename(_q(), "opening_name", "opening")),
    ]
    rows = []
    for label, rec in semantic:
        for position in ("stored", "incoming"):
            rows.append((f"{label}:{position}", position, q_key, rec))
    rows.append(("tuple-path:incoming", "incoming", q_key, _q(path_moves=tuple(Q_PATH))))
    for field, value in (("opening_code", None), ("opening_name", 7), ("variant", None)):
        rec = {**_record(P_BASE), field: value}
        rows.append(
            (f"existing-key-{field}-{type(value).__name__}:incoming", "incoming", p_key, rec)
        )
    drift = ("standard", ("e2e4",))
    for position in ("stored", "incoming"):
        rows.append((f"well-formed-key-drift:{position}", position, drift, _record(["d2d4"])))
    return rows


def _sweep(mod):
    class _CT(mod.ContextTable):
        pass

    def fresh():
        dst = mod.ContextTable()
        dst.insert("standard", list(P_BASE))
        return dst

    cases = []
    for label, position, key, rec in _sweep_rows():
        dst, src = fresh(), mod.ContextTable()
        (dst if position == "stored" else src)._records[key] = copy.deepcopy(rec)
        cases.append((label, dst, src))
    dst = fresh()
    cases.append(("source-is-subclass", dst, _CT()))
    src = mod.ContextTable()
    src._records = _D({("standard", tuple(Q_PATH)): _record(Q_PATH)})
    cases.append(("source-store-dict-subclass", fresh(), src))
    for label, dst, src in cases:
        # raw store only: serialize() is not defined over a corrupted store
        before = copy.deepcopy(dst._records)
        out = _outcome(mod, lambda dst=dst, src=src: dst.merge(src))
        if out != ("reject", MCR, oc.FAILURE_MAPPING[MCR]):
            yield f"sweep {label}", f"missing-rejection:{MCR}:sweep:{label}:{out[:2]}"
        elif dst._records != before:
            yield f"sweep {label}", "rollback"
        else:
            yield f"sweep {label} reject {MCR}", None


def _campaign(mod, seed=0, cases=CASES):
    rng = random.Random(seed)
    t, model = mod.ContextTable(), {}
    log, problems = [], []
    for line, problem in _sweep(mod):
        log.append(f"s {line}")
        if problem:
            problems.append((-1, problem))
    for i in range(cases):
        line, problem = _case(mod, rng, t, model)
        log.append(f"{i} {line}")
        if problem:
            problems.append((i, problem))
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
        _CLEAN["run"] = _campaign(oc)
    return _CLEAN["run"]


def test_campaign_clean_run():
    log, problems = _clean_campaign()
    assert problems == []
    kinds = {line.split(" ", 2)[1] for line in log}
    assert {"op0", "op1", "op2", "merge-bad", "op4"} <= kinds
    sweep = [line for line in log if line.startswith("s ")]
    assert len(sweep) == len(_sweep_rows()) + 2
    assert all(line.endswith(f" reject {MCR}") for line in sweep)
    rejected = {line.rsplit(" ", 1)[-1] for line in log if "reject" in line or "merge-bad" in line}
    assert {MP, UV, CC, MCR, UOC} <= rejected


def test_determinism_pinned_log():
    a, _ = _clean_campaign()
    b, _ = _campaign(oc)
    assert a == b
    digest = hashlib.sha256("\n".join(a).encode()).hexdigest()
    assert digest == PINNED_LOG_SHA256


PINNED_LOG_SHA256 = "271834d680cdd292971301e81f7753d3cc69b22f171eaea2b48589014f0641ec"


# -- fault injection: one-edit production mutants ---------------------------------------

MUTANTS = [
    (
        "move-regex-match-not-fullmatch",
        "_MOVE.fullmatch(move) is None",
        "_MOVE.match(move) is None",
    ),
    (
        "exact-dict-drops-key-check",
        "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))",
        "    return type(obj) is dict",
    ),
    ("exact-key-drops-arity", "        and len(key) == 2\n", ""),
    (
        "validate-code-name-type-and",
        "        if type(code) is not str or type(name) is not str:",
        "        if type(code) is not str and type(name) is not str:",
    ),
    (
        "validate-shape-by-length",
        "        if not _exact_dict(record) or set(record) != _FIELDS:\n"
        '            _fail("malformed_context_record")\n'
        '        if type(record["variant"]) is not str',
        "        if not _exact_dict(record) or len(record) != len(_FIELDS):\n"
        '            _fail("malformed_context_record")\n'
        '        if type(record["variant"]) is not str',
    ),
    (
        "typed-drops-path-list-check",
        '        if type(record["path_moves"]) is not list or not all(',
        "        if not all(",
    ),
    (
        "typed-fields-all-to-any",
        "        if not all(type(record[f]) is str for f in",
        "        if not any(type(record[f]) is str for f in",
    ),
    (
        "merge-accepts-subclass",
        "        if type(other) is not ContextTable:",
        "        if not isinstance(other, ContextTable):",
    ),
    (
        "iter-pairs-accepts-dict-subclass",
        "        if type(records) is not dict:",
        "        if not isinstance(records, dict):",
    ),
    (
        "key-drift-unchecked",
        "            if raw_key != expected_key:\n"
        '                _fail("malformed_context_record")\n',
        "",
    ),
    (
        "records-returns-live",
        "        return copy.deepcopy(list(self._records.values()))",
        "        return list(self._records.values())",
    ),
    (
        "insert-returns-stored-existing",
        "            return copy.deepcopy(existing)",
        "            return existing",
    ),
    (
        "path-move-isinstance",
        "            if type(move) is not str or not move.isascii()",
        "            if not isinstance(move, str) or not move.isascii()",
    ),
    ("move-regex-drops-rank", "[a-h][1-8][a-h][1-8][qrbn]?$|", "[a-h][1-8][a-h][1-9][qrbn]?$|"),
    ("move-distinct-skipped", "            if move[:2] == move[2:4]:", "            if False:"),
    (
        "path-accepts-list-subclass",
        "    if type(value) is not list:",
        "    if not isinstance(value, list):",
    ),
    (
        "resolve-shortest-prefix",
        'len(prefix) > len(best["moves"])',
        'len(prefix) < len(best["moves"])',
    ),
    (
        "variant-subclass-accepted",
        "        if type(variant) is not str or variant not in self.variants:",
        "        if variant not in self.variants:",
    ),
    (
        "merge-conflict-ignored",
        '                    _fail("conflicting_context")',
        "                    pass",
    ),
    (
        "merge-new-key-unvalidated",
        "            staged._records[key] = copy.deepcopy(self._validate(record))",
        "            staged._records[key] = copy.deepcopy(record)",
    ),
    (
        "merge-commits-in-place",
        "        staged._records = {key: copy.deepcopy(record) for key, record in current}",
        "        staged._records = self._records",
    ),
    (
        "validate-resolution-skipped",
        "        if (code, name) != resolve(self.registry, moves):",
        "        if False:",
    ),
]


def _mutant(old, new):
    src = Path(oc.__file__).read_text()
    assert src.count(old) == 1, old
    mod = types.ModuleType("opening_context_mutant")
    mod.__file__ = oc.__file__
    exec(compile(src.replace(old, new), oc.__file__, "exec"), mod.__dict__)  # noqa: S102
    return mod


OWN_CHECK = {
    "move-regex-match-not-fullmatch": "missing-rejection:malformed_path",
    "exact-dict-drops-key-check": "missing-rejection:malformed_context_record",
    "exact-key-drops-arity": "missing-rejection:malformed_context_record",
    "validate-code-name-type-and": "missing-rejection:malformed_context_record:dst",
    "validate-shape-by-length": f"missing-rejection:{MCR}:sweep:renamed-field:stored",
    "typed-drops-path-list-check": f"missing-rejection:{MCR}:sweep:tuple-path:incoming",
    "typed-fields-all-to-any": f"missing-rejection:{MCR}:sweep:existing-key-opening_code",
    "merge-accepts-subclass": f"missing-rejection:{MCR}:sweep:source-is-subclass",
    "iter-pairs-accepts-dict-subclass": f"missing-rejection:{MCR}:sweep:source-store-dict",
    "key-drift-unchecked": f"missing-rejection:{MCR}:sweep:well-formed-key-drift",
    "records-returns-live": "aliased",
    "insert-returns-stored-existing": "aliased",
    "path-move-isinstance": "missing-rejection:malformed_path",
    "move-regex-drops-rank": "missing-rejection:malformed_path",
    "move-distinct-skipped": "missing-rejection:malformed_path",
    "path-accepts-list-subclass": "missing-rejection:malformed_path",
    "resolve-shortest-prefix": "table-model-mismatch",
    "variant-subclass-accepted": "missing-rejection:unknown_variant",
    "merge-conflict-ignored": "missing-rejection:conflicting_context",
    "merge-new-key-unvalidated": "missing-rejection:",
    "merge-commits-in-place": "rollback",
    "validate-resolution-skipped": "missing-rejection:malformed_context_record",
}


@pytest.mark.parametrize("name,old,new", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_injected_fault_detected_by_its_own_check(name, old, new):
    _, problems = _campaign(_mutant(old, new), cases=300)
    assert problems, name
    assert any(p.startswith(OWN_CHECK[name]) for _i, p in problems), (name, problems[:3])
