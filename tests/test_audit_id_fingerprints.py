"""Audit: id()-based fingerprint fallbacks in earlier batteries.

An id() fallback is sound only when the fingerprinted object is the
caller's own stored object; on a fresh temporary (a slice, tuple(...),
list(...), dict(...), a generator expression) it is vacuous, since the
temporary dies and its id can be reused, so every comparison passes.
Every audited site fingerprints objects reached by walking the caller's
own live inputs (or a class's own namespace), never a temporary:

- test_t0150_provenance_fixture._object_fingerprint: values from
  vars(cls) and inspect.getattr_static(cls, name), stored on the class.
- test_t0199_migration_fuzz_fault._deep and
  test_t0262_idempotency_fuzz_fault._deep: called on the live request,
  source/state, log and ledger; id() only on dicts, lists, tuples, non-str
  keys, str/int subclasses and frozensets found inside them.
- test_t0226_backup_fuzz_fault._shape: called on the live log and receipt;
  id() on their dicts, lists, non-str keys and _Colliding leaves.
- test_t0257_idempotency_contract._snap: the *objs tuple itself is a
  fresh temporary, but tuples are walked by content, never by id; id()
  only on non-str keys and opaque leaves of the caller's inputs.
- test_t0270_corruption_properties._shape: id() is only the cycle guard
  over live containers on the current path; _snap pairs the content
  shape with _deep_ids of the live value.

No site is vacuous. The rows below pin that each fingerprint really
separates: an equal but distinct replacement of an id-fingerprinted
object (the original kept alive, so its id cannot be reused) and a
content edit are both detected, and an untouched input fingerprints the
same twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import test_t0150_provenance_fixture as t0150  # noqa: E402
from tests import test_t0199_migration_fuzz_fault as t0199  # noqa: E402
from tests import test_t0226_backup_fuzz_fault as t0226  # noqa: E402
from tests import test_t0257_idempotency_contract as t0257  # noqa: E402
from tests import test_t0262_idempotency_fuzz_fault as t0262  # noqa: E402
from tests import test_t0270_corruption_properties as t0270  # noqa: E402


class _S(str):
    pass


class _I(int):
    pass


class _Opaque:
    def __init__(self, value):
        self.value = value


def _separates(fingerprint, build, edits):
    """BUILD() -> (input, handle). The untouched input fingerprints the
    same twice; each edit(input, handle, keep) must change it (KEEP holds
    every replaced object alive)."""
    value, _ = build()
    assert fingerprint(value) == fingerprint(value)
    for name, edit in edits.items():
        value, handle = build()
        before = fingerprint(value)
        keep = []
        edit(value, handle, keep)
        assert fingerprint(value) != before, name
        assert keep or name.startswith("content"), name


def _swap(key, fresh):
    def edit(value, handle, keep):
        keep.append(handle[key])
        handle[key] = fresh()

    return edit


def _set(key, new):
    def edit(value, handle, keep):
        handle[key] = new

    return edit


def test_t0150_object_fingerprint_is_stable_and_separates():
    def f():
        return 1

    def g():
        return 2

    stored = [1]
    fp = t0150._object_fingerprint
    assert fp(stored) == fp(stored) and fp(stored)[0] == "identity"
    assert fp(stored)[1].obj is stored  # pinned: the fingerprint holds the object
    assert fp([1]) != fp(stored)  # a distinct list alive beside it never shares the id
    assert fp(f) == fp(f) and fp(f) != fp(g)
    assert fp(f)[0] == "code"

    def build():
        cls = type("Anchor", (), {"tag": [1], "insert": f})
        return cls, cls

    value, _ = build()
    assert t0150._class_fingerprint(value) == t0150._class_fingerprint(value)
    for name, edit in {
        "equal-fresh-attribute": lambda cls, keep: (keep.append(cls.tag), setattr(cls, "tag", [1])),
        "content-method-code": lambda cls, keep: (
            keep.append(cls.insert),
            setattr(cls, "insert", g),
        ),
    }.items():
        cls, _ = build()
        before = t0150._class_fingerprint(cls)
        keep = []
        edit(cls, keep)
        assert t0150._class_fingerprint(cls) != before, name


def _deep_rows():
    def build():
        key = _S("k")
        value = {"s": _S("x"), "f": frozenset({1}), "t": (1, [2]), "l": [3], key: 1}
        return value, value

    return build, {
        "equal-fresh-str-subclass": _swap("s", lambda: _S("x")),
        "equal-fresh-frozenset": _swap("f", lambda: frozenset({1})),
        "equal-fresh-tuple": _swap("t", lambda: (1, [2])),
        "equal-fresh-list": _swap("l", lambda: [3]),
        "content-str-subclass": _set("s", _S("y")),
        "content-list": lambda v, h, keep: h["l"].append(4),
    }


def test_t0199_deep_separates():
    build, edits = _deep_rows()
    _separates(t0199._deep, build, edits)


def test_t0262_deep_separates():
    build, edits = _deep_rows()
    edits = {
        **edits,
        "equal-fresh-int-subclass": _swap("i", lambda: _I(7)),
        "content-int-subclass": _set("i", _I(8)),
    }

    def build_i():
        value, handle = build()
        value["i"] = _I(7)
        return value, handle

    _separates(t0262._deep, build_i, edits)


def test_t0226_shape_separates():
    def build():
        value = {"c": t0226._Colliding("a"), "l": [1], "d": {"x": 1}}
        return value, value

    _separates(
        t0226._shape,
        build,
        {
            "equal-fresh-colliding": _swap("c", lambda: t0226._Colliding("a")),
            "equal-fresh-list": _swap("l", lambda: [1]),
            "equal-fresh-dict": _swap("d", lambda: {"x": 1}),
            "content-dict": lambda v, h, keep: h["d"].update(x=2),
        },
    )


def test_t0257_snap_separates():
    def build():
        value = {"o": _Opaque(1), "l": [1, (2, 3)]}
        return value, value

    _separates(
        lambda v: t0257._snap(v, "extra"),
        build,
        {
            "equal-fresh-opaque": _swap("o", lambda: _Opaque(1)),
            "content-list": lambda v, h, keep: h["l"].append(4),
            "content-tuple": _set("l", [1, (2, 4)]),
        },
    )
    # the *objs tuple is a fresh temporary each call, walked by content
    live = [1]
    assert t0257._snap(live, 2) == t0257._snap(live, 2)
    assert t0257._snap(live, 2) != t0257._snap(live, 3)


def test_t0270_snap_separates():
    def build():
        inner = [1]
        value = {"l": inner, "d": {"x": inner}}
        value["self"] = value
        return value, value

    _separates(
        t0270._snap,
        build,
        {
            "equal-fresh-list": _swap("l", lambda: [1]),
            "content-list": lambda v, h, keep: h["l"].append(2),
            "content-nested": lambda v, h, keep: h["d"].update(x=[1, 2]),
        },
    )
