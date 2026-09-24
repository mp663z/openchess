"""T0152 follow-up regression: untrusted-input boundaries of
graph.provenance reject typed and atomically.

- source_id must be an exact str before registry membership (T0149
  reference: "exact-str type BEFORE membership"), so an unhashable or
  str-subclass id is malformed_provenance_record, never a raw TypeError
  and never a hash/== call.
- merge(): a records() that raises, a missing/non-callable records, or a
  batch that is not an exact list (T0149 reference: type(source) is list)
  is malformed_provenance_record with a fresh, unchained error and zero
  mutation.
"""

from __future__ import annotations

import copy
import types
from pathlib import Path

import pytest

from graph import provenance as pv

SOURCE = Path(pv.__file__).read_text()
CALLS = []


def _rec(**src_edit):
    src = {"source_id": "pgn-file", "game_id": "g", "first_observed_at": "2026-09-01T00:00:00Z"}
    src.update(src_edit)
    return {
        "target_kind": "opening_context",
        "target": {"variant": "standard", "path_moves": ["e2e4"]},
        "sources": [src],
    }


class _HS(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("hostile eq")

    def __hash__(self):
        CALLS.append("hash")
        raise RuntimeError("hostile hash")


class _S(str):
    pass


class _Src:
    def __init__(self, fn):
        self.records = fn


def _boom():
    raise ZeroDivisionError("records failed")


def _kbd():
    raise KeyboardInterrupt


def _outcome(call, mod):
    try:
        call()
    except mod.ProvenanceError as exc:
        chained = exc.__cause__ is not None or exc.__context__ is not None
        return exc.failure_class, chained
    except BaseException as exc:  # noqa: BLE001 - raw escape is an outcome
        return "RAW " + type(exc).__name__, None
    return None, None


SOURCE_ID_ROWS = {
    "unhashable-list": ["pgn-file"],
    "unhashable-dict": {"a": 1},
    "hostile-subclass": _HS("pgn-file"),
    "plain-subclass": _S("pgn-file"),
    "none": None,
    "int": 7,
}


def _rows(mod):
    out = []
    for name, sid in SOURCE_ID_ROWS.items():
        t = mod.ProvenanceTable()
        t.insert(_rec(game_id="base"))
        before = copy.deepcopy(t._records)
        CALLS.clear()
        for call in (
            lambda sid=sid: mod.validate_record(_rec(source_id=sid)),
            lambda t=t, sid=sid: t.insert(_rec(source_id=sid)),
        ):
            out.append((name, _outcome(call, mod), list(CALLS), t._records == before))
    for name, src in {
        "records-raises": _Src(_boom),
        "records-keyboard-interrupt": _Src(_kbd),
        "records-non-iterable": _Src(lambda: 5),
        "records-generator": _Src(lambda: iter([_rec()])),
        "records-tuple": _Src(lambda: (_rec(),)),
        "records-not-callable": _Src(5),
        "no-records": object(),
        "none": None,
    }.items():
        t = mod.ProvenanceTable()
        t.insert(_rec(game_id="base"))
        before = copy.deepcopy(t._records)
        out.append((name, _outcome(lambda t=t, s=src: t.merge(s), mod), [], t._records == before))
    return out


def _all_typed(rows):
    return all(
        o == ("malformed_provenance_record", False) and not c and same for _, o, c, same in rows
    )


def test_untrusted_inputs_reject_typed_fresh_atomic():
    rows = _rows(pv)
    assert len(rows) == 20
    assert _all_typed(rows), rows


def test_valid_list_merge_still_works():
    t = pv.ProvenanceTable()
    src = pv.ProvenanceTable()
    src.insert(_rec())
    assert t.merge(src).records() == [_rec()]


def _mutant(old, new):
    assert SOURCE.count(old) == 1, old
    mod = types.ModuleType("provenance_mutant")
    mod.__file__ = pv.__file__
    exec(compile(SOURCE.replace(old, new), pv.__file__, "exec"), mod.__dict__)  # noqa: S102
    return mod


MUTANTS = {
    "source-id-exact-str-off": (
        '        if type(entry["source_id"]) is not str:\n',
        "        if False:\n",
    ),
    "records-raw-leak": (
        "        except BaseException:\n            failed = True\n",
        "        except ZeroDivisionError:\n            raise\n",
    ),
    "records-fail-inside-except-chained": (
        "        except BaseException:\n            failed = True\n",
        '        except BaseException:\n            _fail("malformed_provenance_record")\n',
    ),
    "records-list-check-off": (
        "        if type(incoming) is not list:\n",
        "        if incoming is None:\n",
    ),
}


@pytest.mark.parametrize("name", list(MUTANTS))
def test_source_mutants_are_red(name):
    assert not _all_typed(_rows(_mutant(*MUTANTS[name])))


# -- verifier-1: hostile str-subclass dict KEYS at record/source/target level --

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
TARGETS = {
    "opening_context": {"variant": "standard", "path_moves": ["e2e4"]},
    "transposition_node": {"variant": "standard", "snapshot_fen": START},
    "route_edge": {
        "variant": "standard",
        "move": "e2e4",
        "from_snapshot_fen": START,
        "to_snapshot_fen": AFTER_E4,
    },
}


class _RaisingKey(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("hostile key eq")

    def __ne__(self, other):
        CALLS.append("ne")
        raise RuntimeError("hostile key ne")


class _LyingKey(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        CALLS.append("eq")
        return True

    def __ne__(self, other):
        CALLS.append("ne")
        return False


def _rekey(d, name, cls):
    return {(cls(k) if k == name else k): v for k, v in d.items()}


def _key_cases():
    """(level, kind, key class, record, expected class)."""
    out = []
    for kind, target in TARGETS.items():
        base = {"target_kind": kind, "target": dict(target), "sources": _rec()["sources"]}
        for cls in (_RaisingKey, _LyingKey):
            out.append(
                (
                    "record",
                    kind,
                    cls.__name__,
                    _rekey(base, "target_kind", cls),
                    "malformed_provenance_record",
                )
            )
            out.append(
                (
                    "source",
                    kind,
                    cls.__name__,
                    {**base, "sources": [_rekey(base["sources"][0], "source_id", cls)]},
                    "malformed_provenance_record",
                )
            )
            out.append(
                (
                    "target",
                    kind,
                    cls.__name__,
                    {**base, "target": _rekey(target, "variant", cls)},
                    "malformed_target_identity",
                )
            )
    return out


def _key_rows(mod):
    rows = []
    for level, kind, cls, rec, want in _key_cases():
        for path in ("validate", "insert", "merge"):
            t = mod.ProvenanceTable()
            t.insert(_rec(game_id="base"))
            before = copy.deepcopy(t._records)
            CALLS.clear()
            if path == "validate":
                call = lambda r=rec: mod.validate_record(r)  # noqa: E731
            elif path == "insert":
                call = lambda r=rec, t=t: t.insert(r)  # noqa: E731
            else:
                call = lambda r=rec, t=t: t.merge(_Src(lambda r=r: [r]))  # noqa: E731
            got = _outcome(call, mod)
            ok = got == (want, False) and not CALLS and t._records == before
            rows.append(((level, kind, cls, path), ok, got, list(CALLS)))
    return rows


def test_hostile_dict_keys_reject_typed_at_every_level():
    rows = _key_rows(pv)
    assert len(rows) == 54
    assert all(ok for _, ok, _, _ in rows), [r for r in rows if not r[1]]


KEY_MUTANTS = {
    "record-key-type-off": (
        "    if not _exact_dict(record) or set(record) != _RECORD_FIELDS:",
        "    if type(record) is not dict or set(record) != _RECORD_FIELDS:",
    ),
    "source-key-type-off": (
        "        if not _exact_dict(entry) or set(entry) != _SOURCE_FIELDS:",
        "        if type(entry) is not dict or set(entry) != _SOURCE_FIELDS:",
    ),
    "node-target-key-type-off": (
        '    if not _exact_dict(target) or set(target) != {"variant", "snapshot_fen"}:',
        '    if type(target) is not dict or set(target) != {"variant", "snapshot_fen"}:',
    ),
    "edge-target-key-type-off": (
        "    if not _exact_dict(target) or set(target) != fields:",
        "    if type(target) is not dict or set(target) != fields:",
    ),
    "context-target-key-type-off": (
        '    if not _exact_dict(target) or set(target) != {"variant", "path_moves"}:',
        '    if type(target) is not dict or set(target) != {"variant", "path_moves"}:',
    ),
}


@pytest.mark.parametrize("name", list(KEY_MUTANTS))
def test_key_type_mutants_are_red(name):
    assert not all(ok for _, ok, _, _ in _key_rows(_mutant(*KEY_MUTANTS[name])))


# -- verifier-1: linked node-derivation failures map to malformed_target_identity

CASTLING_BAD = "rnbqkrnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"  # DigestError
EP_BAD = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq e3 0 1"  # VariantError


def _derivation_cases():
    out = []
    for label, fen in (("castling", CASTLING_BAD), ("en-passant", EP_BAD)):
        out.append(
            (f"node-{label}", "transposition_node", {"variant": "standard", "snapshot_fen": fen})
        )
        edge = dict(TARGETS["route_edge"])
        out.append((f"edge-from-{label}", "route_edge", {**edge, "from_snapshot_fen": fen}))
        out.append((f"edge-to-{label}", "route_edge", {**edge, "to_snapshot_fen": fen}))
    return out


def _derivation_rows(mod):
    rows = []
    for name, kind, target in _derivation_cases():
        rec = {"target_kind": kind, "target": target, "sources": _rec()["sources"]}
        for path in ("validate", "insert", "merge"):
            t = mod.ProvenanceTable()
            t.insert(_rec(game_id="base"))
            before = copy.deepcopy(t._records)
            if path == "validate":
                call = lambda r=rec: mod.validate_record(r)  # noqa: E731
            elif path == "insert":
                call = lambda r=rec, t=t: t.insert(r)  # noqa: E731
            else:
                call = lambda r=rec, t=t: t.merge(_Src(lambda r=r: [r]))  # noqa: E731
            got = _outcome(call, mod)
            ok = got == ("malformed_target_identity", False) and t._records == before
            rows.append(((name, path), ok, got))
    return rows


def test_linked_node_derivation_failures_are_typed_fresh_atomic():
    rows = _derivation_rows(pv)
    assert len(rows) == 18
    assert all(ok for _, ok, _ in rows), [r for r in rows if not r[1]]


DERIVATION_MUTANTS = {
    "node-digest-error-uncaught": (
        '        return "transposition_node", target["variant"], identity\n'
        "    except (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):\n",
        '        return "transposition_node", target["variant"], identity\n'
        "    except (VariantError, TypeError, ValueError, KeyError, IndexError):\n",
    ),
    "edge-digest-error-uncaught": (
        "        )\n    except (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):\n",  # noqa: E501
        "        )\n    except (VariantError, TypeError, ValueError, KeyError, IndexError):\n",
    ),
    "node-fail-inside-except-chained": (
        '        return "transposition_node", target["variant"], identity\n'
        "    except (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):\n"
        "        failed = True\n",
        '        return "transposition_node", target["variant"], identity\n'
        "    except (VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):\n"
        '        _fail("malformed_target_identity")\n',
    ),
}


@pytest.mark.parametrize("name", list(DERIVATION_MUTANTS))
def test_derivation_mutants_are_red(name):
    assert not all(ok for _, ok, _ in _derivation_rows(_mutant(*DERIVATION_MUTANTS[name])))


# -- verifier-1 Q19: exact-list batch vs list subclasses; exact-dict records --


class _CountingList(list):
    def __iter__(self):
        CALLS.append("iter")
        return list.__iter__(self)

    def __len__(self):
        CALLS.append("len")
        return list.__len__(self)


class _RaisingList(list):
    def __iter__(self):
        CALLS.append("iter")
        raise RuntimeError("hostile list iter")

    def __len__(self):
        CALLS.append("len")
        raise RuntimeError("hostile list len")


class _CountingDict(dict):
    def __getitem__(self, key):
        CALLS.append("getitem")
        return dict.__getitem__(self, key)

    def __iter__(self):
        CALLS.append("iter")
        return dict.__iter__(self)

    def keys(self):
        CALLS.append("keys")
        return dict.keys(self)


def _subclass_rows(mod):
    rows = []
    batches = {
        "list-subclass-valid": lambda: _CountingList([_rec()]),
        "list-subclass-raising": lambda: _RaisingList([_rec()]),
        "list-subclass-empty": lambda: _CountingList(),
    }
    for name, make in batches.items():
        t = mod.ProvenanceTable()
        t.insert(_rec(game_id="base"))
        before = copy.deepcopy(t._records)
        CALLS.clear()
        got = _outcome(lambda t=t, m=make: t.merge(_Src(m)), mod)
        rows.append(
            (
                name,
                got == ("malformed_provenance_record", False)
                and not CALLS
                and t._records == before,
                got,
                list(CALLS),
            )
        )  # noqa: E501
    for path in ("validate", "insert", "merge"):
        t = mod.ProvenanceTable()
        t.insert(_rec(game_id="base"))
        before = copy.deepcopy(t._records)
        rec = _CountingDict(_rec())
        CALLS.clear()
        if path == "validate":
            call = lambda r=rec: mod.validate_record(r)  # noqa: E731
        elif path == "insert":
            call = lambda r=rec, t=t: t.insert(r)  # noqa: E731
        else:
            call = lambda r=rec, t=t: t.merge(_Src(lambda r=r: [r]))  # noqa: E731
        got = _outcome(call, mod)
        ok = got == ("malformed_provenance_record", False) and not CALLS and t._records == before
        rows.append((f"dict-subclass-record-{path}", ok, got, list(CALLS)))
    return rows


def test_subclass_batches_and_records_reject_without_subclass_calls():
    rows = _subclass_rows(pv)
    assert len(rows) == 6
    assert all(ok for _, ok, _, _ in rows), [r for r in rows if not r[1]]


SUBCLASS_MUTANTS = {
    "batch-list-isinstance": (
        "        if type(incoming) is not list:\n",
        "        if not isinstance(incoming, list):\n",
    ),
    "exact-dict-isinstance": (
        "    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))\n",
        "    return isinstance(obj, dict) and all(type(k) is str for k in dict.keys(obj))\n",
    ),
}


@pytest.mark.parametrize("name", list(SUBCLASS_MUTANTS))
def test_subclass_mutants_are_red(name):
    assert not all(ok for _, ok, _, _ in _subclass_rows(_mutant(*SUBCLASS_MUTANTS[name])))


# -- verifier-2: per-module forges for every class the two except tuples name --

FORGE_CLASSES = ("VariantError", "DigestError", "TypeError", "ValueError", "KeyError", "IndexError")


def _forge_factory(mod, name):
    """A zero-arg factory building a REAL instance of the named class (the
    constructor must succeed, or the row would forge TypeError instead)."""
    if name == "VariantError":
        factory = lambda: mod.VariantError("illegal_position", "forged")  # noqa: E731
    elif name == "DigestError":
        factory = lambda: mod.DigestError("malformed_position", "malformed_request")  # noqa: E731
    else:
        cls = {
            "TypeError": TypeError,
            "ValueError": ValueError,
            "KeyError": KeyError,
            "IndexError": IndexError,
        }[name]
        factory = lambda: cls("forged")  # noqa: E731
    assert type(factory()).__name__ == name
    return factory


def _forge_targets():
    edge = TARGETS["route_edge"]
    return {
        "node": ("transposition_node", dict(TARGETS["transposition_node"]), START),
        "edge-from": ("route_edge", dict(edge), START),
        "edge-to": ("route_edge", dict(edge), AFTER_E4),
    }


def _run_forged(mod, hook, exc_factory, kind, target, fen, path):
    """Patch mod.<hook> to raise exc_factory() when it touches `fen`."""
    real = getattr(mod, hook)

    def forged(*args):
        touched = args[1] if hook == "make_node_record" else args[0]["snapshot_fen"]
        if touched == fen:
            raise exc_factory()
        return real(*args)

    rec = {"target_kind": kind, "target": target, "sources": _rec()["sources"]}
    t = mod.ProvenanceTable()
    t.insert(_rec(game_id="base"))
    before = copy.deepcopy(t._records)
    setattr(mod, hook, forged)
    try:
        if path == "validate":
            got = _outcome(lambda: mod.validate_record(rec), mod)
        elif path == "insert":
            got = _outcome(lambda: t.insert(rec), mod)
        else:
            got = _outcome(lambda: t.merge(_Src(lambda: [rec])), mod)
    finally:
        setattr(mod, hook, real)
    return got, t._records == before


def _forge_rows(mod):
    rows = []
    for hook in ("make_node_record", "node_identity"):
        for cls_name in FORGE_CLASSES:
            factory = _forge_factory(mod, cls_name)
            for tname, (kind, target, fen) in _forge_targets().items():
                for path in ("validate", "insert", "merge"):
                    got, same = _run_forged(mod, hook, factory, kind, target, fen, path)
                    ok = got == ("malformed_target_identity", False) and same
                    rows.append(((hook, cls_name, tname, path), ok, got))
    return rows


def test_every_named_except_class_is_forged_and_maps_fresh():
    rows = _forge_rows(pv)
    assert len(rows) == 2 * 6 * 3 * 3
    assert all(ok for _, ok, _ in rows), [r for r in rows if not r[1]]


def test_forged_provenance_error_passes_through_as_its_own_class():
    """The tuple is intentionally narrow: a ProvenanceError from the linked
    derivation keeps its own class (not remapped, not wrapped)."""
    for tname, (kind, target, fen) in _forge_targets().items():
        for path in ("validate", "insert", "merge"):
            got, same = _run_forged(
                pv,
                "make_node_record",
                lambda: pv.ProvenanceError("unknown_source"),
                kind,
                target,
                fen,
                path,
            )
            assert got == ("unknown_source", False) and same, (tname, path, got)


def _tuple_mutants():
    src = SOURCE
    out = {}
    sites = {
        "node": '        return "transposition_node", target["variant"], identity\n    except (',
        "edge": "        )\n    except (",
    }
    line = "VariantError, DigestError, TypeError, ValueError, KeyError, IndexError):\n"
    for site, prefix in sites.items():
        assert src.count(prefix + line) == 1, site
        for cls_name in FORGE_CLASSES:
            kept = ", ".join(c for c in line[:-3].split(", ") if c != cls_name) + "):\n"
            out[f"{site}-drops-{cls_name}"] = (prefix + line, prefix + kept)
    return out


TUPLE_MUTANTS = _tuple_mutants()


@pytest.mark.parametrize("name", list(TUPLE_MUTANTS))
def test_except_class_removal_mutants_are_red(name):
    assert not all(ok for _, ok, _ in _forge_rows(_mutant(*TUPLE_MUTANTS[name])))
