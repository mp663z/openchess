"""T0218 integration/restart battery for the production WAL (store.wal).

The durable form is a JSON-lines log file, one entry per line. A restart
is a fresh interpreter (subprocess) that loads those bytes, builds a new
WalEngine and replays or continues the log. Every scenario checks the
fresh process against the uninterrupted in-process run, an independent
model of data/contracts/wal.yaml (fold, entry-id derivation, graph.diff
state id) and the contract reference engine (tests.test_t0212) as the
differential oracle. Rejections must be typed WalErrors from the closed
set, leave the in-memory log intact and leave the durable bytes
unchanged.

The payload canonicalizer is the module's only except-BaseException
oracle boundary. Every WalError class is forged from it (on replay and on
append, first and last call) and must fail closed as a FRESH
divergent_canonicalization; pass-through mutants per class are pinned in
MUTANT_TARGETS.
"""

from __future__ import annotations

import builtins
import copy
import functools
import hashlib
import json
import os
import random
import subprocess
import sys
import types
from pathlib import Path

import pytest

from graph import diff
from graph.node import make_record, record_identity
from store import wal

ROOT = Path(__file__).resolve().parents[1]
WAL_SOURCE = (ROOT / "store" / "wal.py").read_text()

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
GENESIS = "wal0:" + "0" * 64
CLASSES = tuple(sorted(wal.FAILURE_MAPPING))

# a six-entry history with an upsert, a delete of a present identity, a
# delete of an absent identity and a re-put
BASE_OPS = (("put", 0), ("put", 1), ("delete", 0), ("put", 2),
            ("delete", 6), ("put", 0))
# an alternative history used for foreign-entry splices
ALT_OPS = (("put", 3), ("put", 4), ("put", 5), ("delete", 3),
           ("put", 6), ("put", 1))


# -- independent model --------------------------------------------------------

def _request(op, index):
    return {"op": op, "payload": {"identity": IDENTITIES[index],
                                  "record": dict(RECORDS[index])}}


def _entry_id(sequence, op, identity, record, prior):
    """Independent entry-id derivation from data/contracts/wal.yaml."""
    canonical = (f"{identity}\n{record['variant']}\n{record['digest']}\n"
                 f"{record['snapshot_fen']}")
    return "wal1:" + hashlib.sha256(
        f"{sequence}\n{op}\n{canonical}\n{prior}".encode()).hexdigest()


def _model_log(ops):
    log, prior = [], GENESIS
    for sequence, (op, index) in enumerate(ops, 1):
        entry_id = _entry_id(sequence, op, IDENTITIES[index],
                             RECORDS[index], prior)
        log.append({"entry_id": entry_id, "sequence": sequence, "op": op,
                    "payload": {"identity": IDENTITIES[index],
                                "record": dict(RECORDS[index])},
                    "prior_entry_id": prior})
        prior = entry_id
    return log


def _model_replay(ops):
    state = {}
    for op, index in ops:
        if op == "put":
            state[IDENTITIES[index]] = dict(RECORDS[index])
        else:
            state.pop(IDENTITIES[index], None)
    log = _model_log(ops)
    return {"state": state, "state_id": diff.state_id(state),
            "head": log[-1]["entry_id"] if log else GENESIS,
            "applied": len(ops)}


def _rechain(log, start):
    """Re-derive ids and prior links from START on (a self-consistent
    forgery of everything after an edit)."""
    prior = log[start - 1]["entry_id"] if start else GENESIS
    for entry in log[start:]:
        entry["prior_entry_id"] = prior
        entry["entry_id"] = _entry_id(entry["sequence"], entry["op"],
                                      entry["payload"]["identity"],
                                      entry["payload"]["record"], prior)
        prior = entry["entry_id"]
    return log


# -- durable form -------------------------------------------------------------

def _dumps(log):
    return "".join(json.dumps(entry, ensure_ascii=False) + "\n"
                   for entry in log)


def _loads(text):
    lines = text.split("\n")
    assert lines[-1] == ""
    return [json.loads(line) for line in lines[:-1]]


# -- step runner (shared by the child and the in-process oracle) ---------------

class _Str(str):
    pass


def _canonicalizer(module, spec, forged):
    """Honest canonicalizer, or a fault at call SPEC['at'] of one step."""
    calls = [0]

    def canon(identity, record):
        honest = module.canonical_payload(identity, record)
        index = calls[0]
        calls[0] += 1
        if spec is None or index != spec["at"]:
            return honest
        mode = spec["mode"]
        if mode == "forge":
            forged[0] = module.WalError(spec["cls"],
                                        wal.FAILURE_MAPPING[spec["cls"]])
            raise forged[0]
        if mode == "raise":
            raise getattr(builtins, spec["exc"])("hostile canonicalizer")
        if mode == "scribble":
            record["snapshot_fen"] = FENS[3]
            record["variant"] = "chess960"
            record.pop("digest", None)
            return honest
        return {"none": None, "bytes": honest.encode(),
                "strsub": _Str(honest), "surrogate": honest + "\ud800",
                "divergent": honest + "x", "empty": ""}[mode]
    return canon


def _run(module, text, steps):
    """Load TEXT, run STEPS; a step is replay, append (optionally
    persisted on success) or restart (reload the persisted text)."""
    log = _loads(text)
    out = []
    for step in steps:
        if step["do"] == "restart":
            log = _loads(text)
            continue
        forged = [None]
        engine = module.WalEngine(_canonicalizer(module, step.get("canon"),
                                                 forged))
        before, ids = _dumps(log), [id(entry) for entry in log]
        try:
            if step["do"] == "replay":
                result = engine.replay(log)
            else:
                result = engine.append(log, copy.deepcopy(step["request"]))
        except module.WalError as error:
            out.append({"fail": error.failure_class, "code": error.code,
                        "exact": type(error) is module.WalError,
                        "fresh": forged[0] is None or error is not forged[0],
                        "intact": _dumps(log) == before and
                        [id(entry) for entry in log] == ids})
            continue
        except BaseException as error:  # noqa: BLE001 - raw escape recorded
            out.append({"crash": type(error).__name__})
            continue
        out.append({"ok": result})
        if step.get("persist"):
            text = _dumps(log)
    return {"steps": out, "text": text}


# -- mutants ------------------------------------------------------------------

_BOUNDARY = ("        except BaseException:\n"
             "            _fail(\"divergent_canonicalization\")\n"
             "        if type(out) is not str:\n")


def _passthrough(cls):
    guard = "" if cls is None else f"            if error.failure_class != {cls!r}:\n" \
        "                _fail(\"divergent_canonicalization\")\n"
    return [(_BOUNDARY,
             "        except WalError as error:\n" + guard +
             "            raise\n" + _BOUNDARY)]


MUTANTS = {
    "identity": [],
    "except-exception": [(_BOUNDARY, _BOUNDARY.replace("BaseException",
                                                       "Exception"))],
    "forge-passthrough-all": _passthrough(None),
    **{f"forge-passthrough-{cls}": _passthrough(cls) for cls in CLASSES},
    "canon-shared-arg": [("out = self.canonicalizer(identity, dict(record))",
                          "out = self.canonicalizer(identity, record)")],
    "output-isinstance": [("        if type(out) is not str:\n",
                           "        if not isinstance(out, str):\n")],
    "no-utf8-check": [('out.encode("utf-8")',
                       'out.encode("utf-8", "surrogatepass")')],
    "head-is-first-entry": [("        return tip\n\n    def append",
                             "        return frozen[0][\"entry_id\"] if frozen"
                             " else tip\n\n    def append")],
    "append-prior-genesis": [('                "prior_entry_id": tip,\n',
                              '                "prior_entry_id": GENESIS,\n')],
    "replay-delete-noop": [("                    state.pop(identity, None)\n",
                            "                    pass\n")],
    "replay-applied-off": [('"applied": len(frozen)}',
                            '"applied": max(len(frozen) - 1, 0)}')],
    "no-prior-link-check": [('    if entry["prior_entry_id"] != prior_tip:\n',
                             "    if False:\n")],
    "no-sequence-check": [("    if sequence != position:\n",
                           "    if False:\n")],
    "no-rederive-check": [('                _fail("corrupt_chain")\n'
                           '            tip = entry["entry_id"]\n',
                           "                pass\n"
                           '            tip = entry["entry_id"]\n')],
    "id-grammar-search": [("grammar.fullmatch(entry[field])",
                           "grammar.search(entry[field])")],
    "entry-key-len": [("set(dict.keys(entry)) != set(_FIELDS)",
                       "len(entry) != len(_FIELDS)")],
    "no-op-type-check": [("    op = entry[\"op\"]\n    if type(op) is not str:\n"
                          "        _fail(\"malformed_wal_entry\")\n",
                          "    op = entry[\"op\"]\n")],
}

# the killed list: every non-identity mutant and the probe it must fail
MUTANT_TARGETS = {
    "except-exception": "canon-KeyboardInterrupt-replay",
    "forge-passthrough-all": f"forge-{CLASSES[0]}-replay",
    **{f"forge-passthrough-{cls}": f"forge-{cls}-replay" for cls in CLASSES},
    "canon-shared-arg": "canon-scribble-replay",
    "output-isinstance": "canon-strsub-replay",
    "no-utf8-check": "canon-surrogate-append",
    "head-is-first-entry": "restart-continue",
    "append-prior-genesis": "restart-continue",
    "replay-delete-noop": "restart-continue",
    "replay-applied-off": "restart-empty",
    "no-prior-link-check": "tamper-splice-foreign-last",
    "no-sequence-check": "tamper-reforged-sequence-middle",
    "no-rederive-check": "tamper-swap-payload-last",
    "id-grammar-search": "tamper-entry-id-trailing-newline-middle",
    "entry-key-len": "tamper-rename-sequence-middle",
    "no-op-type-check": "tamper-op-int-middle",
}


def _mutant_module(name):
    """store/wal.py with MUTANTS[NAME] applied (each edit matches exactly
    once) as a fresh module; WalError and _fail are rebound to the
    production class so typed outcomes stay comparable."""
    source = WAL_SOURCE
    for old, new in MUTANTS[name]:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"store._t0218_mutant_{name}")
    module.__file__ = str(ROOT / "store" / "wal.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)
    module.WalError = wal.WalError

    def _fail(failure_class):
        raise wal.WalError(failure_class, wal.FAILURE_MAPPING[failure_class])

    module._fail = _fail
    return module


# -- fresh-process restart ----------------------------------------------------

def _child_main():
    """Child entry point: {"mutant": name|null, "jobs": [...]} on stdin."""
    request = json.load(sys.stdin)
    name = request.get("mutant")
    module = wal if name is None else _mutant_module(name)
    results = [_run(module, job["text"], job["steps"])
               for job in request["jobs"]]
    sys.stdout.write(json.dumps(results, ensure_ascii=False, sort_keys=True))


_CHILD = ("import sys; sys.path.insert(0, sys.argv[1]); "
          "from tests.test_t0218_wal_restart import _child_main; _child_main()")


def _restart(jobs, mutant=None, hash_seed="0", raw=False):
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    run = subprocess.run([sys.executable, "-c", _CHILD, str(ROOT)],
                         input=json.dumps({"mutant": mutant, "jobs": jobs}),
                         text=True, capture_output=True, cwd=ROOT, env=env,
                         timeout=100)
    assert run.returncode == 0, run.stderr
    return run.stdout if raw else json.loads(run.stdout)


def _oracle(text, steps):
    """The contract reference engine on the same bytes and steps,
    normalized through a JSON round trip like the child's output."""
    from tests import test_t0212_wal_contract as contract
    return json.loads(json.dumps(_run(contract, text, steps), sort_keys=True))


def _fail(cls):
    return {"fail": cls, "code": wal.FAILURE_MAPPING[cls], "exact": True,
            "fresh": True, "intact": True}


BASE_TEXT = _dumps(_model_log(BASE_OPS))


# -- tampers (applied to the durable form) --------------------------------------

def _t_entry(pos, key, value):
    def edit(log):
        log[pos][key] = value
        return log
    return edit


def _tampers():
    """name -> (edit(log, pos) -> log, pinned failure class)."""
    t = {}

    def add(name, edit, cls):
        t[name] = (edit, cls)

    def flip(value):
        return value[:-1] + ("0" if value[-1] != "0" else "1")

    add("flip-entry-id", lambda log, p: _t_entry(p, "entry_id",
                                                 flip(log[p]["entry_id"]))(log),
        "corrupt_chain")
    add("sequence-plus-one", lambda log, p: _t_entry(p, "sequence", p + 2)(log),
        "sequence_conflict")
    add("reforged-sequence",
        lambda log, p: _rechain(_t_entry(p, "sequence", p + 2)(log), p),
        "sequence_conflict")
    add("sequence-true", lambda log, p: _t_entry(p, "sequence", True)(log),
        "malformed_wal_entry")
    add("sequence-float", lambda log, p: _t_entry(p, "sequence", p + 1.0)(log),
        "malformed_wal_entry")
    add("sequence-str", lambda log, p: _t_entry(p, "sequence", str(p + 1))(log),
        "malformed_wal_entry")
    add("unknown-op", lambda log, p: _t_entry(p, "op", "upsert")(log),
        "unknown_operation")
    add("op-upper", lambda log, p: _t_entry(p, "op", log[p]["op"].upper())(log),
        "unknown_operation")
    add("drop-prior", lambda log, p: (log[p].pop("prior_entry_id"), log)[1],
        "malformed_wal_entry")
    add("extra-key", lambda log, p: _t_entry(p, "force", True)(log),
        "malformed_wal_entry")
    # a non-str op in the durable form: type-checked before the op registry
    add("op-int", lambda log, p: _t_entry(p, "op", 1)(log), "malformed_wal_entry")
    add("op-null", lambda log, p: _t_entry(p, "op", None)(log), "malformed_wal_entry")
    add("op-list", lambda log, p: _t_entry(p, "op", ["put"])(log), "malformed_wal_entry")

    def rename(log, p, where, old, new):
        container = {"entry": log[p], "payload": log[p]["payload"],
                     "record": log[p]["payload"]["record"]}[where]
        items = [(new if k == old else k, v) for k, v in container.items()]
        container.clear()
        container.update(items)
        return log

    add("rename-sequence", lambda log, p: rename(log, p, "entry", "sequence",
                                                 "sequencx"),
        "malformed_wal_entry")
    add("rename-identity", lambda log, p: rename(log, p, "payload", "identity",
                                                 "identitx"),
        "malformed_wal_entry")
    add("rename-digest", lambda log, p: rename(log, p, "record", "digest",
                                               "digesx"),
        "malformed_wal_entry")
    add("record-extra-field",
        lambda log, p: (log[p]["payload"]["record"].update(zz="1"), log)[1],
        "malformed_wal_entry")
    add("entry-id-trailing-newline",
        lambda log, p: _t_entry(p, "entry_id", log[p]["entry_id"] + "\n")(log),
        "malformed_wal_entry")
    add("entry-id-leading-space",
        lambda log, p: _t_entry(p, "entry_id", " " + log[p]["entry_id"])(log),
        "malformed_wal_entry")
    add("entry-id-upper",
        lambda log, p: _t_entry(p, "entry_id", "wal1:" +
                                log[p]["entry_id"][5:].upper())(log),
        "malformed_wal_entry")
    add("prior-trailing-newline",
        lambda log, p: _t_entry(p, "prior_entry_id",
                                log[p]["prior_entry_id"] + "\n")(log),
        "malformed_wal_entry")

    def swap_payload(log, p):
        index = 3 if log[p]["payload"]["identity"] != IDENTITIES[3] else 4
        log[p]["payload"] = {"identity": IDENTITIES[index],
                             "record": dict(RECORDS[index])}
        return log

    add("swap-payload", swap_payload, "corrupt_chain")
    add("identity-of-other-record",
        lambda log, p: (log[p]["payload"].update(
            identity=IDENTITIES[5] if log[p]["payload"]["identity"] !=
            IDENTITIES[5] else IDENTITIES[4]), log)[1],
        "malformed_wal_entry")
    add("record-wrong-digest",
        lambda log, p: (log[p]["payload"]["record"].update(
            digest="pdv1:" + "1" * 64), log)[1],
        "malformed_wal_entry")
    add("record-non-canonical-fen",
        lambda log, p: (log[p]["payload"]["record"].update(
            snapshot_fen=log[p]["payload"]["record"]["snapshot_fen"]
            .replace(" 0 1", " 00 1")), log)[1],
        "malformed_wal_entry")
    add("payload-null", lambda log, p: _t_entry(p, "payload", None)(log),
        "malformed_wal_entry")
    add("entry-list", lambda log, p: (log.__setitem__(p, list(log[p])), log)[1],
        "malformed_wal_entry")
    add("entry-null", lambda log, p: (log.__setitem__(p, None), log)[1],
        "malformed_wal_entry")
    add("duplicate-entry",
        lambda log, p: (log.insert(p + 1, copy.deepcopy(log[p])), log)[1],
        "sequence_conflict")

    def swap_adjacent(log, p):
        q = p + 1 if p + 1 < len(log) else p - 1
        log[p], log[q] = log[q], log[p]
        return log

    add("swap-adjacent", swap_adjacent, "sequence_conflict")

    def splice(log, p):
        log[p] = copy.deepcopy(_model_log(ALT_OPS)[p])
        return log

    add("splice-foreign", splice, "corrupt_chain")
    return t


TAMPERS = _tampers()
POSITIONS = {"first": 0, "middle": 2, "last": len(BASE_OPS) - 1}
# dropping an entry is a sequence break except at the tail (torn tail)
DROP = {"first": 0, "middle": 2}


def _tampered(name, pos):
    log = _model_log(BASE_OPS)
    edit, _cls = TAMPERS[name]
    return _dumps(edit(log, POSITIONS[pos]))


# -- probes (the mutation slice) ----------------------------------------------

def _probe_jobs():
    """name -> job; expected outcomes come from the contract reference."""
    p = {}
    full = _model_log(BASE_OPS)
    p["restart-continue"] = {"text": _dumps(full[:3]), "steps": [
        {"do": "replay"},
        *({"do": "append", "request": _request(*op), "persist": True}
          for op in BASE_OPS[3:]),
        {"do": "restart"}, {"do": "replay"}]}
    p["restart-empty"] = {"text": "", "steps": [
        {"do": "replay"},
        {"do": "append", "request": _request(*BASE_OPS[0]), "persist": True},
        {"do": "restart"}, {"do": "replay"}]}
    p["restart-replay-full"] = {"text": BASE_TEXT, "steps": [{"do": "replay"}]}
    for name, pos in (("splice-foreign", "last"), ("reforged-sequence", "middle"),
                      ("swap-payload", "last"),
                      ("entry-id-trailing-newline", "middle"),
                      ("rename-sequence", "middle"), ("flip-entry-id", "last"),
                      ("op-int", "middle"), ("op-list", "first")):
        p[f"tamper-{name}-{pos}"] = {"text": _tampered(name, pos),
                                     "steps": [{"do": "replay"}]}
    n = len(BASE_OPS)
    for cls in CLASSES:
        p[f"forge-{cls}-replay"] = {"text": BASE_TEXT, "steps": [
            {"do": "replay", "canon": {"mode": "forge", "cls": cls, "at": n - 1}},
            {"do": "replay"}]}
        p[f"forge-{cls}-append"] = {"text": BASE_TEXT, "steps": [
            {"do": "append", "request": _request("put", 5), "persist": True,
             "canon": {"mode": "forge", "cls": cls, "at": n}},
            {"do": "restart"}, {"do": "replay"}]}
    p["canon-KeyboardInterrupt-replay"] = {"text": BASE_TEXT, "steps": [
        {"do": "replay", "canon": {"mode": "raise", "exc": "KeyboardInterrupt",
                                   "at": 0}}]}
    p["canon-strsub-replay"] = {"text": BASE_TEXT, "steps": [
        {"do": "replay", "canon": {"mode": "strsub", "at": 1}}]}
    p["canon-surrogate-append"] = {"text": BASE_TEXT, "steps": [
        {"do": "append", "request": _request("put", 5), "persist": True,
         "canon": {"mode": "surrogate", "at": n}}]}
    p["canon-scribble-replay"] = {"text": BASE_TEXT, "steps": [
        {"do": "replay", "canon": {"mode": "scribble", "at": n - 1}}]}
    return p


@functools.lru_cache(maxsize=1)
def _probes():
    jobs = _probe_jobs()
    return jobs, {name: _oracle(job["text"], job["steps"])
                  for name, job in jobs.items()}


def _failing(results):
    jobs, expected = _probes()
    return [name for name, got in zip(jobs, results, strict=True)
            if got != expected[name]]


# -- tests: restart continuation -------------------------------------------------

def test_restart_at_every_prefix_continues_bit_identical():
    full = _model_log(BASE_OPS)
    jobs = [{"text": _dumps(full[:k]), "steps": [
        {"do": "replay"},
        *({"do": "append", "request": _request(*op), "persist": True}
          for op in BASE_OPS[k:]),
        {"do": "replay"}]} for k in range(len(BASE_OPS) + 1)]
    for k, got in enumerate(_restart(jobs)):
        assert got["steps"][0] == {"ok": _model_replay(BASE_OPS[:k])}, k
        receipts = [step["ok"] for step in got["steps"][1:-1]]
        assert receipts == full[k:], k
        assert got["steps"][-1] == {"ok": _model_replay(BASE_OPS)}, k
        assert got["text"] == BASE_TEXT, k


def test_process_chain_restart_after_every_append():
    ops = BASE_OPS + ALT_OPS
    text = ""
    for k, op in enumerate(ops):
        [got] = _restart([{"text": text, "steps": [
            {"do": "replay"},
            {"do": "append", "request": _request(*op), "persist": True}]}])
        assert got["steps"][0] == {"ok": _model_replay(ops[:k])}, k
        assert got["steps"][1] == {"ok": _model_log(ops)[k]}, k
        text = got["text"]
        assert text == _dumps(_model_log(ops[:k + 1])), k
    [got] = _restart([{"text": text, "steps": [{"do": "replay"}]}])
    assert got["steps"] == [{"ok": _model_replay(ops)}]


def test_replay_never_changes_durable_bytes_and_is_repeatable():
    [got] = _restart([{"text": BASE_TEXT, "steps": [
        {"do": "replay"}, {"do": "replay"}, {"do": "restart"},
        {"do": "replay"}]}])
    assert got["steps"] == [{"ok": _model_replay(BASE_OPS)}] * 3
    assert got["text"] == BASE_TEXT


def test_fresh_process_output_is_deterministic_across_hash_seeds():
    jobs = [{"text": BASE_TEXT, "steps": [
        {"do": "replay"},
        {"do": "append", "request": _request("put", 5), "persist": True},
        {"do": "append", "request": _request("delete", 1), "persist": True},
        {"do": "restart"}, {"do": "replay"}]}]
    outputs = {_restart(jobs, hash_seed=seed, raw=True)
               for seed in ("0", "1", "4242", "random")}
    assert len(outputs) == 1


# -- tests: boundaries ------------------------------------------------------------

def test_empty_log_restart_boundary():
    [got] = _restart([{"text": "", "steps": [
        {"do": "replay"},
        {"do": "append", "request": _request("delete", 2), "persist": True},
        {"do": "replay"}]}])
    assert got["steps"][0] == {"ok": {"state": {}, "state_id": diff.state_id({}),
                                      "head": GENESIS, "applied": 0}}
    first = got["steps"][1]["ok"]
    assert first["sequence"] == 1 and first["prior_entry_id"] == GENESIS
    assert got["steps"][2] == {"ok": _model_replay((("delete", 2),))}


def test_long_log_restart_boundary():
    rng = random.Random(218)
    ops = tuple((rng.choice(("put", "put", "delete")), rng.randrange(len(FENS)))
                for _ in range(64))
    [got] = _restart([{"text": _dumps(_model_log(ops)), "steps": [
        {"do": "replay"},
        {"do": "append", "request": _request("put", 0), "persist": True},
        {"do": "restart"}, {"do": "replay"}]}])
    assert got["steps"][0] == {"ok": _model_replay(ops)}
    assert got["steps"][1]["ok"]["sequence"] == 65
    assert got["steps"][2] == {"ok": _model_replay(ops + (("put", 0),))}


@pytest.mark.parametrize("lost", [1, 2, len(BASE_OPS)])
def test_torn_tail_replays_to_prefix_and_reappend_reproduces_bytes(lost):
    keep = len(BASE_OPS) - lost
    [got] = _restart([{"text": _dumps(_model_log(BASE_OPS[:keep])), "steps": [
        {"do": "replay"},
        *({"do": "append", "request": _request(*op), "persist": True}
          for op in BASE_OPS[keep:])]}])
    assert got["steps"][0] == {"ok": _model_replay(BASE_OPS[:keep])}
    assert got["text"] == BASE_TEXT


# -- tests: malformed durable logs ---------------------------------------------------

TAMPER_CASES = [(name, pos) for name in TAMPERS for pos in POSITIONS]


def test_tampered_durable_logs_are_rejected_typed_and_untouched():
    jobs = [{"text": _tampered(name, pos), "steps": [
        {"do": "replay"},
        {"do": "append", "request": _request("put", 5), "persist": True}]}
        for name, pos in TAMPER_CASES]
    jobs += [{"text": _dumps(_model_log(BASE_OPS)[:DROP[pos]] +
                             _model_log(BASE_OPS)[DROP[pos] + 1:]),
              "steps": [{"do": "replay"},
                        {"do": "append", "request": _request("put", 5),
                         "persist": True}]} for pos in DROP]
    cases = TAMPER_CASES + [("drop-entry", pos) for pos in DROP]
    classes = {name: cls for name, (_edit, cls) in TAMPERS.items()}
    classes["drop-entry"] = "sequence_conflict"
    for (name, pos), job, got in zip(cases, jobs, _restart(jobs), strict=True):
        assert got["steps"] == [_fail(classes[name])] * 2, (name, pos, got)
        assert got["text"] == job["text"], (name, pos)
        assert got == _oracle(job["text"], job["steps"]), (name, pos)


# -- tests: rollback -------------------------------------------------------------------

def test_rejected_appends_after_restart_roll_back_then_continue():
    bad_identity = _request("put", 5)
    bad_identity["payload"]["identity"] = IDENTITIES[4]
    extra = _request("put", 5)
    extra["force"] = True
    n = len(BASE_OPS)
    job = {"text": BASE_TEXT, "steps": [
        {"do": "append", "request": {"op": "upsert",
                                     "payload": _request("put", 5)["payload"]},
         "persist": True},
        {"do": "append", "request": bad_identity, "persist": True},
        {"do": "append", "request": extra, "persist": True},
        {"do": "append", "request": _request("put", 5), "persist": True,
         "canon": {"mode": "raise", "exc": "SystemExit", "at": n}},
        {"do": "append", "request": _request("put", 5), "persist": True,
         "canon": {"mode": "divergent", "at": 0}},
        {"do": "replay"},
        {"do": "append", "request": _request("put", 5), "persist": True}]}
    [got] = _restart([job])
    assert got["steps"][:5] == [_fail("unknown_operation"),
                                _fail("malformed_wal_entry"),
                                _fail("malformed_wal_entry"),
                                _fail("divergent_canonicalization"),
                                _fail("corrupt_chain")]
    assert got["steps"][5] == {"ok": _model_replay(BASE_OPS)}
    ops = BASE_OPS + (("put", 5),)
    assert got["steps"][6] == {"ok": _model_log(ops)[-1]}
    assert got["text"] == _dumps(_model_log(ops))
    assert got == _oracle(job["text"], job["steps"])
    [again] = _restart([{"text": got["text"], "steps": [{"do": "replay"}]}])
    assert again["steps"] == [{"ok": _model_replay(ops)}]


def test_divergent_new_entry_is_caught_after_restart():
    n = len(BASE_OPS)
    [got] = _restart([{"text": BASE_TEXT, "steps": [
        {"do": "append", "request": _request("put", 5), "persist": True,
         "canon": {"mode": "divergent", "at": n}}]}])
    assert got["steps"][0]["ok"]["entry_id"] != \
        _model_log(BASE_OPS + (("put", 5),))[-1]["entry_id"]
    [after] = _restart([{"text": got["text"], "steps": [
        {"do": "replay"},
        {"do": "append", "request": _request("put", 6), "persist": True}]}])
    assert after["steps"] == [_fail("corrupt_chain")] * 2
    assert after["text"] == got["text"]


# -- tests: the oracle boundary ------------------------------------------------------

BOUNDARY_FAULTS = (
    [{"mode": "forge", "cls": cls} for cls in CLASSES]
    + [{"mode": "raise", "exc": exc} for exc in
       ("KeyboardInterrupt", "SystemExit", "GeneratorExit", "MemoryError",
        "RecursionError", "RuntimeError")]
    + [{"mode": mode} for mode in ("none", "bytes", "strsub", "surrogate")])


def test_boundary_faults_fail_closed_fresh_after_restart():
    n = len(BASE_OPS)
    jobs, labels = [], []
    for fault in BOUNDARY_FAULTS:
        for where, at in (("replay-first", 0), ("replay-last", n - 1),
                          ("append-first", 0), ("append-new", n)):
            canon = dict(fault, at=at)
            action = ({"do": "replay", "canon": canon} if where.startswith("replay")
                      else {"do": "append", "request": _request("put", 5),
                            "persist": True, "canon": canon})
            jobs.append({"text": BASE_TEXT, "steps": [
                action, {"do": "restart"}, {"do": "replay"}]})
            labels.append((json.dumps(fault), where))
    for label, job, got in zip(labels, jobs, _restart(jobs), strict=True):
        assert got["steps"] == [_fail("divergent_canonicalization"),
                                {"ok": _model_replay(BASE_OPS)}], label
        assert got["text"] == BASE_TEXT, label
        assert got == _oracle(job["text"], job["steps"]), label


def test_every_wal_error_class_is_forged():
    forged = {fault["cls"] for fault in BOUNDARY_FAULTS if fault["mode"] == "forge"}
    assert forged == set(wal.FAILURE_MAPPING)
    assert {f"forge-passthrough-{cls}" for cls in CLASSES} <= set(MUTANT_TARGETS)


def test_scribbling_canonicalizer_is_inert_after_restart():
    n = len(BASE_OPS)
    [got] = _restart([{"text": BASE_TEXT, "steps": [
        {"do": "replay", "canon": {"mode": "scribble", "at": n - 1}},
        {"do": "append", "request": _request("put", 5), "persist": True,
         "canon": {"mode": "scribble", "at": n}}]}])
    ops = BASE_OPS + (("put", 5),)
    assert got["steps"] == [{"ok": _model_replay(BASE_OPS)},
                            {"ok": _model_log(ops)[-1]}]
    assert got["text"] == _dumps(_model_log(ops))


# -- tests: integration with the linked runtimes --------------------------------------

def test_restart_state_is_bound_to_linked_runtimes():
    [got] = _restart([{"text": BASE_TEXT, "steps": [{"do": "replay"}]}])
    result = got["steps"][0]["ok"]
    for identity, record in result["state"].items():
        rebuilt = make_record(record["variant"], record["snapshot_fen"])
        assert rebuilt == record
        assert record_identity(rebuilt) == identity
    assert result["state_id"] == diff.state_id(result["state"])
    assert result["head"] == _model_log(BASE_OPS)[-1]["entry_id"]


def test_production_matches_contract_reference_in_process():
    for name, job in _probe_jobs().items():
        got = json.loads(json.dumps(_run(wal, job["text"], job["steps"]),
                                    sort_keys=True))
        assert got == _probes()[1][name], name


# -- tests: mutation check -------------------------------------------------------------

def test_probes_green_in_a_fresh_process():
    jobs, _expected = _probes()
    assert _failing(_restart(list(jobs.values()))) == []
    assert _failing(_restart(list(jobs.values()), mutant="identity")) == []


def test_killed_list_covers_every_mutant():
    assert set(MUTANT_TARGETS) == set(MUTANTS) - {"identity"}
    assert set(MUTANT_TARGETS.values()) <= set(_probe_jobs())


@pytest.mark.parametrize("name", sorted(MUTANT_TARGETS))
def test_mutant_is_red_on_its_target(name):
    jobs, _expected = _probes()
    failing = _failing(_restart(list(jobs.values()), mutant=name))
    assert MUTANT_TARGETS[name] in failing, (name, failing)
