"""T0143 follow-up regression: same-key context mismatch on merge is
conflicting_context in both merge orders, before registry validation.

Contract (data/contracts/opening_context.yaml, merge.rule): "For an
existing key the exact incoming record is compared with the stored record
and any context mismatch is a conflicting_context rejection before any
mutation; an incoming code unknown under the receiver's registry is
unknown_opening_code, a failure separate from same-key conflict."
Precedence follows the merged T0140 reference merge
(tests/test_t0140_opening_context_contract.py lines 222-241): type checks
first (malformed_context_record), then the same-key code/name comparison
(conflicting_context), and registry validation only for new keys - so a
same-key record with bad code grammar or broken sentinel pairing whose
code/name differs is conflicting_context. A new key with an unknown code
stays unknown_opening_code. The receiver's own stored records are still
self-validated first (T0143 corruption pins), which is where production
differs from the reference: see the poisoned-receiver rows below.
"""

from __future__ import annotations

import copy
import types
from pathlib import Path

import pytest

from graph import opening_context as oc

PETROFF = ["e2e4", "e7e5", "g1f3", "g8f6"]
QG = ["d2d4", "d7d5", "c2c4"]
SOURCE = Path(oc.__file__).read_text()
OLD_ORDER = (
    "for key, record in self._iter_pairs(other._records, self._typed):",
    "for key, record in self._iter_pairs(other._records):",
)


def _registry(mutate):
    reg = copy.deepcopy(oc.ContextTable().registry)
    mutate(reg)
    return reg


def _rename_code(reg):
    for entry in reg["entries"]:
        if entry["code"] == "C20":
            entry["code"], entry["name"] = "C21", "Drifted Name"


def _rename_name(reg):
    for entry in reg["entries"]:
        if entry["code"] == "C20":
            entry["name"] = "King Pawn"


def _add_b99(reg):
    reg["entries"].append({"code": "B99", "name": "Mystery", "moves": ["a2a3"]})


def _class(call, mod):
    try:
        call()
    except mod.ContextError as exc:
        return exc.failure_class
    return None


def _drift_rows(mod):
    """Both merge orders over two registry snapshots; returns the classes
    and whether every receiver stayed bit-identical."""
    out = []
    for mutate in (_rename_code, _rename_name):
        a = mod.ContextTable()
        a.insert("standard", PETROFF)
        a.insert("standard", QG)
        b = mod.ContextTable(registry=_registry(mutate))
        b.insert("standard", PETROFF)
        for x, y in ((a, b), (b, a)):
            before = copy.deepcopy(x._records)
            cls = _class(lambda x=x, y=y: x.merge(y), mod)
            out.append((mutate.__name__, cls, x._records == before))
    return out


def test_same_key_mismatch_is_conflicting_both_orders_zero_mutation():
    rows = _drift_rows(oc)
    assert rows == [
        ("_rename_code", "conflicting_context", True),
        ("_rename_code", "conflicting_context", True),
        ("_rename_name", "conflicting_context", True),
        ("_rename_name", "conflicting_context", True),
    ]
    err = oc.ContextError("conflicting_context")
    assert err.code == oc.FAILURE_MAPPING["conflicting_context"] == "conflicting_context"


def test_new_key_unknown_code_keeps_unknown_opening_code():
    src = oc.ContextTable(registry=_registry(_add_b99))
    src.insert("standard", ["e2e4", "c7c5"])
    src.insert("standard", ["a2a3"])
    dst = oc.ContextTable()
    dst.insert("standard", QG)
    before = copy.deepcopy(dst._records)
    assert _class(lambda: dst.merge(src), oc) == "unknown_opening_code"
    assert dst._records == before


def _same_key_incoming(**edit):
    dst = oc.ContextTable()
    dst.insert("standard", PETROFF)
    src = oc.ContextTable()
    src.insert("standard", PETROFF)
    key = next(iter(src._records))
    src._records[key].update(edit)
    return dst, src


@pytest.mark.parametrize(
    "edit, cls",
    [
        ({"opening_code": "c20"}, "conflicting_context"),  # grammar, code differs
        ({"opening_code": "C2O"}, "conflicting_context"),  # grammar, code differs
        ({"opening_code": "-"}, "conflicting_context"),  # sentinel pairing broken
        ({"opening_name": "-"}, "conflicting_context"),  # sentinel pairing broken
        ({"opening_name": 7}, "malformed_context_record"),  # type
        ({"opening_code": None}, "malformed_context_record"),  # type
        ({"path_moves": ("e2e4", "e7e5", "g1f3", "g8f6")}, "malformed_context_record"),
        ({"extra": 1}, "malformed_context_record"),  # field set
        ({"variant": "nope"}, "malformed_context_record"),  # key/record mismatch
    ],
    ids=[
        "grammar-lower",
        "grammar-letter-o",
        "sentinel-code",
        "sentinel-name",
        "type-name",
        "type-code",
        "type-path-tuple",
        "field-set",
        "key-mismatch",
    ],
)
def test_same_key_incoming_precedence(edit, cls):
    dst, src = _same_key_incoming(**edit)
    before = copy.deepcopy(dst._records)
    assert _class(lambda: dst.merge(src), oc) == cls
    assert dst._records == before


def _poisoned(**edit):
    """A table whose stored PETROFF record was edited in place (the
    receiver-corruption case the T0143 runtime tests pin)."""
    t = oc.ContextTable()
    t.insert("standard", PETROFF)
    next(iter(t._records.values())).update(edit)
    return t


@pytest.mark.parametrize(
    "edit", [{"opening_code": "c20"}, {"opening_code": "-"}], ids=["grammar", "sentinel"]
)
def test_grammar_bad_same_key_mismatch_both_orders(edit):
    """PINNED-CURRENT, OWNER DECISION PENDING for the reverse order
    (coordinator: T0140 reference vs merged T0143 corruption pin, no
    precedence rule; parked for the owner batch).
    Forward: valid receiver, bad incoming -> conflicting_context.
    Reverse: the bad record sits in the RECEIVER, whose own stored records
    are self-validated first -> malformed_context_record (T0143 pin; the
    reference does not revalidate stored records and would say
    conflicting_context). Zero mutation in both."""
    good = oc.ContextTable()
    good.insert("standard", PETROFF)
    bad = _poisoned(**edit)
    for x, y, cls in (
        (good, bad, "conflicting_context"),
        (bad, good, "malformed_context_record"),
    ):
        before = copy.deepcopy(x._records)
        assert _class(lambda x=x, y=y: x.merge(y), oc) == cls
        assert x._records == before


def test_grammar_bad_same_key_identical_record():
    """PINNED-CURRENT, OWNER DECISION PENDING (reference no-op vs merged
    T0143 receiver self-check; parked for the owner batch).
    An identical grammar-bad incoming record needs a grammar-bad
    receiver entry (a valid receiver never stores one). Production rejects
    on the receiver self-check: malformed_context_record, zero mutation.
    The reference skips the identical record (no-op). An identical VALID
    record is a no-op in both (next test)."""
    dst = _poisoned(opening_code="c20")
    src = _poisoned(opening_code="c20")
    before = copy.deepcopy(dst._records)
    assert _class(lambda: dst.merge(src), oc) == "malformed_context_record"
    assert dst._records == before


def test_same_key_identical_record_is_a_no_op():
    dst, src = _same_key_incoming()
    before = copy.deepcopy(dst._records)
    dst.merge(src)
    assert dst._records == before


def _mutant(old, new):
    assert SOURCE.count(old) == 1
    mod = types.ModuleType("opening_context_mutant")
    mod.__file__ = oc.__file__
    exec(compile(SOURCE.replace(old, new), oc.__file__, "exec"), mod.__dict__)
    return mod


def test_mutant_registry_first_order_turns_pinned_rows_red():
    rows = _drift_rows(_mutant(*OLD_ORDER))
    assert [cls for _, cls, _ in rows] == [
        "unknown_opening_code",
        "unknown_opening_code",
        "malformed_context_record",
        "malformed_context_record",
    ]
    assert rows != _drift_rows(oc)


def test_mutant_new_keys_skip_registry_validation_turns_row_red():
    mod = _mutant(
        "staged._records[key] = copy.deepcopy(self._validate(record))",
        "staged._records[key] = copy.deepcopy(record)",
    )
    src = mod.ContextTable(registry=_registry(_add_b99))
    src.insert("standard", ["a2a3"])
    assert _class(lambda: mod.ContextTable().merge(src), mod) is None


# -- verifier-2 gaps: _typed preflight and untrusted key tuples ----------------

CALLS = []


class _HD(dict):
    def __getitem__(self, key):
        CALLS.append("getitem")
        return dict.__getitem__(self, key)

    def __iter__(self):
        CALLS.append("iter")
        return dict.__iter__(self)

    def keys(self):
        CALLS.append("keys")
        return dict.keys(self)


class _HL(list):
    def __iter__(self):
        CALLS.append("iter")
        raise RuntimeError("hostile list iter")


class _HS(str):
    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("hostile str eq")

    def __ne__(self, other):
        CALLS.append("ne")
        raise RuntimeError("hostile str ne")

    def __hash__(self):
        CALLS.append("hash")
        raise RuntimeError("hostile str hash")


class _HHash(str):
    def __hash__(self):
        CALLS.append("hash")
        raise RuntimeError("hostile str hash")


def _plain(path):
    return {
        "variant": "standard",
        "path_moves": list(path),
        "opening_code": oc.ContextTable().insert("standard", path)["opening_code"],
        "opening_name": oc.ContextTable().insert("standard", path)["opening_name"],
    }


def _typed_cases(path):
    good = _plain(path)
    return {
        "dict-subclass": _HD(good),
        "four-wrong-keys": {"a": 1, "b": 2, "c": 3, "d": 4},
        "list-subclass-path": {**good, "path_moves": _HL(path)},
        "str-subclass-move": {**good, "path_moves": [_HS(m) for m in path]},
        "str-subclass-variant": {**good, "variant": _HS("standard")},
        "str-subclass-code": {**good, "opening_code": _HS(good["opening_code"])},
        "str-subclass-name": {**good, "opening_name": _HS(good["opening_name"])},
    }


def _typed_rows(mod):
    """(case, key-kind, class, hostile calls, unchanged) per row."""
    out = []
    for where, path in (("existing", PETROFF), ("new", QG)):
        for name, bad in _typed_cases(path).items():
            dst = mod.ContextTable()
            dst.insert("standard", PETROFF)
            src = mod.ContextTable()
            src._records = {("standard", tuple(path)): bad}
            before = copy.deepcopy(dst._records)
            CALLS.clear()
            try:
                cls = _class(lambda d=dst, s=src: d.merge(s), mod)
            except BaseException as exc:  # noqa: BLE001 - raw escape is a row
                cls = "RAW " + type(exc).__name__
            out.append((name, where, cls, list(CALLS), dst._records == before))
    return out


def _all_mcr(rows):
    return all(
        c == "malformed_context_record" and not calls and same for _, _, c, calls, same in rows
    )


def test_typed_preflight_rejects_hostile_records_at_new_and_existing_keys():
    rows = _typed_rows(oc)
    assert len(rows) == 14
    assert _all_mcr(rows), rows


ARMED = []


class _ArmedEq(str):
    """Hashes normally; its compare raises once armed."""

    __hash__ = str.__hash__

    def __eq__(self, other):
        if ARMED:
            CALLS.append("eq")
            raise RuntimeError("hostile key eq")
        return str.__eq__(self, other)

    def __ne__(self, other):
        if ARMED:
            CALLS.append("ne")
            raise RuntimeError("hostile key ne")
        return str.__ne__(self, other)


class _ArmedHash(str):
    """Compares normally; its hash raises once armed."""

    def __eq__(self, other):
        return str.__eq__(self, other)

    def __hash__(self):
        if ARMED:
            CALLS.append("hash")
            raise RuntimeError("hostile key hash")
        return str.__hash__(self)


class _ArmedTuple(tuple):
    __hash__ = tuple.__hash__

    def __eq__(self, other):
        if ARMED:
            CALLS.append("eq")
            raise RuntimeError("hostile tuple eq")
        return tuple.__eq__(self, other)

    def __ne__(self, other):
        if ARMED:
            CALLS.append("ne")
            raise RuntimeError("hostile tuple ne")
        return tuple.__ne__(self, other)


class _ArmedList(list):
    def __hash__(self):
        return hash(tuple(self))

    def __eq__(self, other):
        if ARMED:
            CALLS.append("eq")
            raise RuntimeError("hostile list eq")
        return list.__eq__(self, other)

    def __ne__(self, other):
        if ARMED:
            CALLS.append("ne")
            raise RuntimeError("hostile list ne")
        return list.__ne__(self, other)


def _key_rows(mod):
    good = _plain(QG)
    keys = {
        "key-str-eq": (_ArmedEq("standard"), tuple(QG)),
        "key-str-hash": (_ArmedHash("standard"), tuple(QG)),
        "key-move-eq": ("standard", tuple(_ArmedEq(m) for m in QG)),
        "key-not-tuple": "standard e2e4",
        "key-tuple-subclass": _ArmedTuple(("standard", tuple(QG))),
        "key-len-1": ("standard",),
        "key-empty": (),
        "key-path-tuple-subclass": ("standard", _ArmedTuple(QG)),
        "key-path-list-subclass": ("standard", _ArmedList(QG)),
    }
    out = []
    for name, key in keys.items():
        for side in ("incoming", "receiver"):
            ARMED.clear()
            dst = mod.ContextTable()
            dst.insert("standard", PETROFF)
            src = mod.ContextTable()
            box = src if side == "incoming" else dst
            box._records[key] = dict(good)
            before = dict(dst._records)
            ARMED.append(True)
            CALLS.clear()
            try:
                cls = _class(lambda d=dst, s=src: d.merge(s), mod)
            except BaseException as exc:  # noqa: BLE001 - raw escape is a row
                cls = "RAW " + type(exc).__name__
            calls = list(CALLS)
            same = dst._records is not None and len(dst._records) == len(before)
            ARMED.clear()
            same = same and list(dst._records.items()) == list(before.items())
            out.append((name, side, cls, calls, same))
    return out


def test_untrusted_key_tuples_reject_before_any_compare_or_hash():
    rows = _key_rows(oc)
    assert len(rows) == 18
    assert _all_mcr(rows), rows


TYPED_MUTANTS = {
    "typed-dict-isinstance": (
        '        if not _exact_dict(record) or set(record) != _FIELDS:\n            _fail("malformed_context_record")\n        if type(record["path_moves"])',  # noqa: E501
        '        if not isinstance(record, dict) or set(record) != _FIELDS:\n            _fail("malformed_context_record")\n        if type(record["path_moves"])',  # noqa: E501
    ),
    "typed-field-count": (
        '        if not _exact_dict(record) or set(record) != _FIELDS:\n            _fail("malformed_context_record")\n        if type(record["path_moves"])',  # noqa: E501
        '        if not _exact_dict(record) or len(record) != len(_FIELDS):\n            _fail("malformed_context_record")\n        if type(record["path_moves"])',  # noqa: E501
    ),
    "typed-path-isinstance": (
        'if type(record["path_moves"]) is not list or not all(',
        'if not isinstance(record["path_moves"], list) or not all(',
    ),
    "typed-move-isinstance": (
        '            type(move) is str for move in record["path_moves"]\n        ):\n            _fail("malformed_context_record")\n        if not all(type(record[f])',  # noqa: E501
        '            isinstance(move, str) for move in record["path_moves"]\n        ):\n            _fail("malformed_context_record")\n        if not all(type(record[f])',  # noqa: E501
    ),
    "typed-fields-isinstance": (
        'if not all(type(record[f]) is str for f in ("variant", "opening_code", "opening_name")):',
        'if not all(isinstance(record[f], str) for f in ("variant", "opening_code", "opening_name")):',  # noqa: E501
    ),
}
KEY_MUTANT = ("            if not _exact_key(raw_key):\n", "            if False:\n")


@pytest.mark.parametrize("name", list(TYPED_MUTANTS))
def test_typed_mutants_are_red(name):
    assert not _all_mcr(_typed_rows(_mutant(*TYPED_MUTANTS[name])))


def test_key_exact_type_mutant_is_red():
    assert not _all_mcr(_key_rows(_mutant(*KEY_MUTANT)))


def _batch_order_rows(mod):
    conflict = dict(_plain(PETROFF), opening_code="C21", opening_name="Drifted")
    type_bad = {"a": 1, "b": 2, "c": 3, "d": 4}
    out = []
    for order in (("conflict", "type-bad"), ("type-bad", "conflict")):
        dst = mod.ContextTable()
        dst.insert("standard", PETROFF)
        src = mod.ContextTable()
        rows = {
            "conflict": (("standard", tuple(PETROFF)), conflict),
            "type-bad": (("standard", tuple(QG)), type_bad),
        }
        src._records = dict(rows[name] for name in order)
        before = copy.deepcopy(dst._records)
        try:
            cls = _class(lambda d=dst, s=src: d.merge(s), mod)
        except BaseException as exc:  # noqa: BLE001
            cls = "RAW " + type(exc).__name__
        out.append((order, cls, dst._records == before))
    return out


BATCH_ORDER_PIN = [
    (("conflict", "type-bad"), "conflicting_context", True),
    (("type-bad", "conflict"), "malformed_context_record", True),
]
WHOLE_BATCH_PREPASS = (
    "for key, record in self._iter_pairs(other._records, self._typed):",
    "for key, record in list(self._iter_pairs(other._records, self._typed)):",
)


def test_batch_order_first_failing_record_decides():
    assert _batch_order_rows(oc) == BATCH_ORDER_PIN


def test_mutant_whole_batch_typed_prepass_is_red():
    assert _batch_order_rows(_mutant(*WHOLE_BATCH_PREPASS)) != BATCH_ORDER_PIN


EXACT_KEY_MUTANTS = {
    "key-tuple-isinstance": ("        type(key) is tuple\n", "        isinstance(key, tuple)\n"),
    "key-len-deleted": ("        and len(key) == 2\n", ""),
    "key-path-isinstance": (
        "        and type(key[1]) is tuple\n",
        "        and isinstance(key[1], tuple)\n",
    ),
    "key-path-type-deleted": ("        and type(key[1]) is tuple\n", ""),
}


@pytest.mark.parametrize("name", list(EXACT_KEY_MUTANTS))
def test_exact_key_mutants_are_red(name):
    assert not _all_mcr(_key_rows(_mutant(*EXACT_KEY_MUTANTS[name])))


# -- verifier-1: hostile str-subclass record KEYS (_exact_dict) ---------------


class _RaisingFieldKey(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        CALLS.append("eq")
        raise RuntimeError("hostile key eq")

    def __ne__(self, other):
        CALLS.append("ne")
        raise RuntimeError("hostile key ne")


class _LyingFieldKey(str):
    __hash__ = str.__hash__

    def __eq__(self, other):
        CALLS.append("eq")
        return True

    def __ne__(self, other):
        CALLS.append("ne")
        return False


def _rekeyed(path, cls):
    return {(cls(k) if k == "variant" else k): v for k, v in _plain(path).items()}


def _field_key_rows(mod):
    out = []
    for cls in (_RaisingFieldKey, _LyingFieldKey):
        for side, path in (("incoming-new", QG), ("incoming-same", PETROFF), ("receiver", QG)):
            dst = mod.ContextTable()
            dst.insert("standard", PETROFF)
            src = mod.ContextTable()
            src.insert("standard", ["d2d4"])
            box = dst if side == "receiver" else src
            box._records[("standard", tuple(path))] = _rekeyed(path, cls)
            before = list(dst._records.items())
            CALLS.clear()
            try:
                got = _class(lambda d=dst, s=src: d.merge(s), mod)
            except BaseException as exc:  # noqa: BLE001 - raw escape is a row
                got = "RAW " + type(exc).__name__
            calls = list(CALLS)
            same = len(dst._records) == len(before) and all(
                a is b or (a[0] == b[0] and a[1] is b[1])
                for a, b in zip(dst._records.items(), before, strict=False)
            )
            out.append((cls.__name__, side, got, calls, same))
    return out


def test_hostile_record_keys_reject_before_any_set_build():
    rows = _field_key_rows(oc)
    assert len(rows) == 6
    assert _all_mcr(rows), rows


FIELD_KEY_MUTANTS = {
    "typed-exact-dict-off": (
        '        exact field set, list path of str moves, str fields."""\n'
        "        if not _exact_dict(record) or set(record) != _FIELDS:\n",
        '        exact field set, list path of str moves, str fields."""\n'
        "        if type(record) is not dict or set(record) != _FIELDS:\n",
    ),
    "validate-exact-dict-off": (
        "    def _validate(self, record):\n        if not _exact_dict(record) or set(record) != _FIELDS:\n",  # noqa: E501
        "    def _validate(self, record):\n        if type(record) is not dict or set(record) != _FIELDS:\n",  # noqa: E501
    ),
}


@pytest.mark.parametrize("name", list(FIELD_KEY_MUTANTS))
def test_field_key_mutants_are_red(name):
    assert not _all_mcr(_field_key_rows(_mutant(*FIELD_KEY_MUTANTS[name])))
