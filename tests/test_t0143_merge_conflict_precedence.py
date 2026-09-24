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
    "incoming = self._validated_pairs(other._records, self._typed)",
    "incoming = self._validated_pairs(other._records)",
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
    """Forward: valid receiver, bad incoming -> conflicting_context.
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
    """An identical grammar-bad incoming record needs a grammar-bad
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
