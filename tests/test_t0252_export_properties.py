"""T0252 deterministic unit/property battery for the production export.

Seeded properties over store.export (the shipped T0251 runtime) only: no
tests.* helpers. Source logs are built with the shipped store.wal from
records made by the shipped graph.node runtime; receipts are checked
against the WAL replay, graph.diff, an independent put/delete model, an
independent canonical rendering and an independent export-id derivation.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import random
from pathlib import Path

import pytest

from graph import diff
from graph.node import make_record, record_identity
from store import export, wal

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
FIELDS = ("export_id", "head", "state_id", "record_count", "format", "document")
FMT = "jsonl-v1"
GENESIS = "wal0:" + "0" * 64
SEEDS = range(40)


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


def _armed(call, *args):
    _Colliding.armed = True
    try:
        return call(*args)
    finally:
        _Colliding.armed = False


def _engine(exporter=export.render_document):
    return export.ExportEngine(exporter)


def _ops(seed, length=None):
    rng = random.Random(seed)
    n = rng.randrange(0, 11) if length is None else length
    return [(rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
            for _ in range(n)]


def _build(ops):
    log, engine = [], wal.WalEngine(wal.canonical_payload)
    for op, index in ops:
        engine.append(log, {"op": op, "payload": {
            "identity": IDENTITIES[index], "record": dict(RECORDS[index])}})
    return log


def _fold(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    return state


def _render(state):
    """Independent canonical rendering from data/contracts/export.yaml."""
    lines = []
    for key in sorted(state):
        body = ",".join(f"{json.dumps(f, ensure_ascii=False)}:"
                        f"{json.dumps(state[key][f], ensure_ascii=False)}"
                        for f in sorted(state[key]))
        lines.append('{"identity":' + json.dumps(key, ensure_ascii=False)
                     + ',"record":{' + body + "}}\n")
    return "".join(lines)


def _export_id(head, sid, count, fmt, document):
    return "exp1:" + hashlib.sha256(
        f"{head}\n{sid}\n{count}\n{fmt}\n{document}".encode()).hexdigest()


def _shape(value):
    if type(value) is dict:
        return [(key, _shape(item)) for key, item in value.items()]
    if type(value) is list:
        return [_shape(item) for item in value]
    return value


def _deep_ids(value):
    if isinstance(value, dict):
        return [id(value)] + [x for k, v in value.items()
                              for x in (id(k), *_deep_ids(v))]
    if isinstance(value, list):
        return [id(value)] + [x for v in value for x in _deep_ids(v)]
    return [id(value)]


def _snap(value):
    return _shape(value), _deep_ids(value)


def _fails(failure, call, *args):
    with pytest.raises(export.ExportError) as exc:
        try:
            _armed(call, *args)
        except export.ExportError:
            raise
        else:
            raise AssertionError("accepted")
    assert exc.value.failure_class == failure
    assert exc.value.code == export.FAILURE_MAPPING[failure]


def _check(out, ops, log_copy):
    model = _fold(ops)
    replay = wal.WalEngine(wal.canonical_payload).replay(copy.deepcopy(log_copy))
    assert list(out) == list(FIELDS)
    assert out["head"] == replay["head"] == (log_copy[-1]["entry_id"] if log_copy else GENESIS)
    assert out["state_id"] == replay["state_id"] == diff.state_id(copy.deepcopy(model))
    assert type(out["record_count"]) is int and out["record_count"] == len(model)
    assert out["format"] == FMT
    assert type(out["document"]) is str
    assert out["document"] == _render(model) == export.render_document(copy.deepcopy(model), FMT)
    assert out["export_id"] == _export_id(out["head"], out["state_id"],
                                          out["record_count"], FMT, out["document"])
    assert out["export_id"] == export.derive_export_id(
        out["head"], out["state_id"], out["record_count"], FMT, out["document"])


# -- R1: honest exports ----------------------------------------------------------------

@pytest.mark.parametrize("seed", SEEDS)
def test_r1_export_matches_model_and_derivations(seed):
    ops = _ops(seed)
    log = _build(ops)
    request = {"format": FMT}
    before, req_before, calls = _snap(log), _snap(request), []

    def exporter(state, fmt):
        calls.append((copy.deepcopy(state), fmt))
        return export.render_document(state, fmt)

    out = _engine(exporter).export(log, request)
    _check(out, ops, log)
    assert calls == [(_fold(ops), FMT)]
    state_arg = calls[0][0]
    assert type(state_arg) is dict and all(
        type(k) is str and type(r) is dict for k, r in state_arg.items())
    assert _snap(log) == before and _snap(request) == req_before
    assert _engine().export(copy.deepcopy(log), {"format": FMT}) == out


@pytest.mark.parametrize("seed", SEEDS)
def test_r2_every_prefix_exports(seed):
    ops = _ops(seed)
    log = _build(ops)
    ids = set()
    for n in range(len(log) + 1):
        prefix = copy.deepcopy(log[:n])
        out = _engine().export(prefix, {"format": FMT})
        _check(out, ops[:n], prefix)
        ids.add(out["export_id"])
    # the head is bound, so distinct prefixes give distinct ids
    assert len(ids) == len(log) + 1


def test_r2b_empty_log_and_empty_state():
    out = _engine().export([], {"format": FMT})
    assert out["head"] == GENESIS and out["state_id"] == diff.state_id({})
    assert out["record_count"] == 0 and out["document"] == ""
    # put then delete: empty state under a non-genesis head
    log = _build([("put", 0), ("delete", 0)])
    out2 = _engine().export(log, {"format": FMT})
    assert out2["document"] == "" and out2["record_count"] == 0
    assert out2["head"] == log[-1]["entry_id"] != GENESIS
    assert out2["export_id"] != out["export_id"]


def test_r3_render_document_is_the_independent_rendering():
    for seed in SEEDS:
        model = _fold(_ops(seed))
        before = _snap(model)
        assert export.render_document(model, FMT) == _render(model)
        assert _snap(model) == before
        for line in export.render_document(model, FMT).splitlines():
            parsed = json.loads(line)
            assert parsed["record"] == model[parsed["identity"]]
    odd = {"\u00e9-key": {"b": "\u00fc\"q\\", "a": "\n"}, "a-key": {"z": "1"}}
    assert export.render_document(odd, FMT) == _render(odd)
    for fmt in ("jsonl", "JSONL-V1", "", "jsonl-v2"):
        with pytest.raises(ValueError):
            export.render_document({}, fmt)
    assert export.FORMATS == (FMT,)


def test_r3b_derive_export_id_vectors():
    """Direct vectors on the public derivation, including a non-ASCII
    document: the id hashes the UTF-8 bytes of every field."""
    head, sid = "wal1:" + "a" * 64, "gs1:" + "b" * 64
    for count, doc in ((0, ""), (2, _render({"\u00e9": {"k": "\u00fc\u2603"}})),
                       (1, "\u00e9\n"), (3, '{"identity":"x"}\n')):
        assert export.derive_export_id(head, sid, count, FMT, doc) == \
            _export_id(head, sid, count, FMT, doc)
    assert export.derive_export_id(head, sid, 1, FMT, "\u00e9\n") != \
        export.derive_export_id(head, sid, 1, FMT, "\n")
    assert export.derive_export_id(head, sid, 1, FMT, "\u00e9") == "exp1:" + \
        hashlib.sha256(f"{head}\n{sid}\n1\n{FMT}\n\u00e9".encode()).hexdigest()


@pytest.mark.parametrize("seed", range(12))
def test_r4_exporter_argument_is_detached(seed):
    ops = _ops(seed, length=2 + seed % 6)
    log = _build(ops)
    before = _snap(log)

    def exporter(state, fmt):
        doc = export.render_document(state, fmt)
        for record in state.values():
            record["digest"] = "x"
            record["zz"] = "1"
        state["zz"] = {}
        return doc

    out = _engine(exporter).export(log, {"format": FMT})
    _check(out, ops, log)
    assert _snap(log) == before


# -- R5: exporter boundary -----------------------------------------------------------

def _raiser(kind):
    def exporter(state, fmt):
        raise kind()
    return exporter


def _forged(error):
    """An exporter raising a pre-built (forged) typed error."""
    def exporter(state, fmt):
        raise error
    return exporter


FORGED_ERRORS = {
    **{f"forged-export-error-{c}": export.ExportError(c, export.FAILURE_MAPPING[c])
       for c in sorted(export.FAILURE_MAPPING) if c != "divergent_export"},
    **{f"forged-wal-error-{c}": wal.WalError(c, wal.FAILURE_MAPPING[c])
       for c in sorted(wal.FAILURE_MAPPING)},
}


HOSTILE_EXPORTERS = {
    **{name: _forged(error) for name, error in FORGED_ERRORS.items()},
    "value-error": _raiser(ValueError),
    "keyboard-interrupt": _raiser(KeyboardInterrupt),
    "system-exit": _raiser(SystemExit),
    "generator-exit": _raiser(GeneratorExit),
    "none": lambda state, fmt: None,
    "bytes": lambda state, fmt: export.render_document(state, fmt).encode(),
    "int": lambda state, fmt: 0,
    "list": lambda state, fmt: export.render_document(state, fmt).splitlines(),
    "str-subclass": lambda state, fmt: _Str(export.render_document(state, fmt)),
    "surrogate": lambda state, fmt: export.render_document(state, fmt) + "\ud800",
    "trailing-space": lambda state, fmt: export.render_document(state, fmt) + " ",
    "spaced-separator": lambda state, fmt: export.render_document(state, fmt).replace(
        ":", ": ", 1) if state else "x",
    "reversed-lines": lambda state, fmt: "".join(reversed(
        export.render_document(state, fmt).splitlines(True))) + ("" if len(state) > 1 else "x"),
    "other-state": lambda state, fmt: export.render_document(
        {IDENTITIES[0]: dict(RECORDS[0])} if IDENTITIES[0] not in state else {}, fmt),
}


@pytest.mark.parametrize("name", sorted(HOSTILE_EXPORTERS))
@pytest.mark.parametrize("seed", range(5))
def test_r5_hostile_exporter_fails_closed(name, seed):
    log = _build(_ops(seed, length=seed % 5))
    request = {"format": FMT}
    before, req_before = _snap(log), _snap(request)
    _fails("divergent_export", _engine(HOSTILE_EXPORTERS[name]).export, log, request)
    assert _snap(log) == before and _snap(request) == req_before


@pytest.mark.parametrize("seed", range(10))
@pytest.mark.parametrize("fail", [False, True])
def test_r6_live_input_mutation_is_undone(seed, fail):
    """An exporter closing over the caller's LIVE log and request adds keys
    at every level, changes and removes values and inserts into both
    containers; everything is put back (values, key order, identity) on
    both exits, and the receipt is the unmutated export."""
    ops = _ops(seed, length=1 + seed % 5)
    log = _build(ops)
    request = {"format": FMT}
    original = copy.deepcopy(log)
    before, req_before = _snap(log), _snap(request)

    def exporter(state, fmt):
        doc = export.render_document(state, fmt)
        for entry in list(log):
            entry["zz"] = 1
            entry["sequence"] = 99
            entry["payload"]["zz"] = 1
            entry["payload"]["record"]["zz"] = 1
            entry["payload"]["record"].pop("digest")
        log.insert(0, {"foreign": True})
        del log[-1]
        del request["format"]
        request["zz"] = 1
        request[_Str("format")] = FMT
        request["format"] = "other"
        return None if fail else doc

    if fail:
        _fails("divergent_export", _engine(exporter).export, log, request)
    else:
        out = _engine(exporter).export(log, request)
        _check(out, ops, original)
    assert _snap(log) == before and _snap(request) == req_before


# -- R7: malformed requests and logs -------------------------------------------------

REQUEST_TAMPERS = [
    ("renamed-key", {"formatx": FMT}),
    ("missing-key", {}),
    ("extra-key", {"format": FMT, "zz": 1}),
    ("str-subclass-key", {_Str("format"): FMT}),
    ("colliding-key", {_Colliding("format"): FMT}),
    ("dict-subclass", _Dict({"format": FMT})),
    ("list", [("format", FMT)]),
    ("none", None),
    ("format-str-subclass", {"format": _Str(FMT)}),
    ("format-bytes", {"format": FMT.encode()}),
    ("format-none", {"format": None}),
    ("format-int", {"format": 1}),
    ("format-list", {"format": [FMT]}),
]


@pytest.mark.parametrize("name,request_", REQUEST_TAMPERS, ids=[n for n, _ in REQUEST_TAMPERS])
def test_r7_malformed_request_is_rejected_first(name, request_):
    for seed in range(6):
        for corrupt in (False, True):
            log = _build(_ops(seed, length=2 + seed % 4))
            if corrupt:
                log[0]["sequence"] += 1
            before, req_before, calls = _snap(log), _snap(request_), []
            _fails("malformed_export_request",
                   _engine(lambda s, f, calls=calls: calls.append(s)).export, log, request_)
            assert calls == [] and _snap(log) == before
            assert _snap(request_) == req_before


@pytest.mark.parametrize("bad", [None, (), {}, "log", 0, _Dict(), _List()])
def test_r7b_non_list_log_is_malformed(bad):
    _fails("malformed_export_request", _engine().export, bad, {"format": FMT})


def test_r7c_list_subclass_log_is_malformed():
    log = _List(_build(_ops(3, length=2)))
    _fails("malformed_export_request", _engine().export, log, {"format": FMT})


@pytest.mark.parametrize("fmt", ["jsonl", "JSONL-V1", "", "jsonl-v1 ", " jsonl-v1",
                                 "jsonl-v1\n", "jsonl-v2", "csv"])
def test_r8_unknown_format_is_unsupported_before_validation(fmt):
    for corrupt in (False, True):
        log = _build(_ops(2, length=3))
        if corrupt:
            log[1]["entry_id"] = log[1]["entry_id"][:-1] + "x"
        request = {"format": fmt}
        before, req_before, calls = _snap(log), _snap(request), []
        _fails("unsupported_format",
               _engine(lambda s, f, calls=calls: calls.append(s)).export, log, request)
        assert calls == [] and _snap(log) == before and _snap(request) == req_before


# -- R9: corrupt source ----------------------------------------------------------------

def _flip(text):
    return text[:-1] + ("0" if text[-1] != "0" else "1")


SOURCE_TAMPERS = {
    "sequence-shift": lambda e: e.__setitem__("sequence", e["sequence"] + 1),
    "sequence-bool": lambda e: e.__setitem__("sequence", True),
    "unknown-op": lambda e: e.__setitem__("op", "move"),
    "op-str-subclass": lambda e: e.__setitem__("op", _Str(e["op"])),
    "entry-id-flip": lambda e: e.__setitem__("entry_id", _flip(e["entry_id"])),
    "prior-flip": lambda e: e.__setitem__("prior_entry_id", _flip(e["prior_entry_id"])),
    "extra-key": lambda e: e.__setitem__("zz", 1),
    "str-subclass-key": lambda e: e.__setitem__(_Str("op"), e.pop("op")),
    "colliding-key": lambda e: e.__setitem__(_Colliding("op"), 1),
    "digest-swap": lambda e: e["payload"]["record"].__setitem__(
        "digest", RECORDS[(IDENTITIES.index(e["payload"]["identity"]) + 1)
                          % len(RECORDS)]["digest"]),
    "identity-str-subclass": lambda e: e["payload"].__setitem__(
        "identity", _Str(e["payload"]["identity"])),
    "record-dict-subclass": lambda e: e["payload"].__setitem__(
        "record", _Dict(e["payload"]["record"])),
    "entry-dict-subclass": lambda e: _Dict(e),
    "entry-not-dict": lambda e: [e],
}


@pytest.mark.parametrize("name", sorted(SOURCE_TAMPERS))
def test_r9_corrupt_source_fails_before_the_exporter(name):
    for seed in range(8):
        log = _build(_ops(seed, length=1 + seed % 6))
        position = random.Random(seed + 500).randrange(len(log))
        replacement = SOURCE_TAMPERS[name](log[position])
        if replacement is not None:
            log[position] = replacement
        request = {"format": FMT}
        before, calls = _snap(log), []
        _fails("corrupt_source",
               _engine(lambda s, f, calls=calls: calls.append(s)).export, log, request)
        assert _snap(log) == before and calls == []


def test_r10_property_file_uses_no_test_helpers():
    tree = ast.parse(Path(__file__).read_text())
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            modules.add(node.module)
    assert not any(m == "tests" or m.startswith("tests.") for m in modules)
    assert modules <= {"__future__", "ast", "copy", "hashlib", "json", "random",
                       "pathlib", "pytest", "graph", "graph.node", "store"}
