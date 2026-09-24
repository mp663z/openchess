"""T0183 Graph/diff/instrument: behavior-neutral diagnostics for the
production graph-state diff (graph.diff) via graph.diff_instrument.

DiffTracer wraps state_id, compute, validate_diff and apply. This file
proves:
- neutrality: over a seeded trajectory and every failure class, a traced
  call returns what the bare call returns (the same object from the
  wrapped function) or raises the same class - the same object when the
  wrapped function raises it - and never changes an argument;
- diagnostics: one record per call with operation, argument snapshot,
  outcome (accept / reject with failure class and mapped code / crash
  with the error type), a result summary and arguments_unchanged, in
  seq order; append-only, isolated views, deterministic JSONL;
- totality: hostile error attributes, a faulting trace container and
  pathological depth never change the wrapped result;
- hostile types at every input boundary of every operation (each state
  mapping, its keys in 3 forms, each record mapping, record keys in 3
  forms and each record string value; each diff mapping, its keys in 3
  forms, each section mapping, each id value, each changed witness):
  the traced and bare outcomes are the same typed class, no user dunder
  runs in either (empty hostile-call log), the arguments are unchanged
  and the snapshot records an opaque marker instead of dispatching;
- metaclass dispatch: an argument or result whose metaclass defines
  __eq__ and __name__ runs no caller code; an error class whose metaclass
  __name__ raises is re-raised as the same object;
- arguments_unchanged is False when the wrapped function mutates an
  argument and then rejects or crashes; a tuple argument snapshots as a
  list; an injected BaseException fault inside the snapshot never escapes;
- one-edit mutants of the instrument are red on their own check.
"""

from __future__ import annotations

import copy
import json
import random
import types
from contextlib import suppress
from pathlib import Path

import pytest

from graph import diff
from graph import diff_instrument as di
from graph.node import make_record, record_identity

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "graph" / "diff_instrument.py").read_text()
MDR, CB, UI, DT = (
    "malformed_diff_record",
    "conflicting_base",
    "unknown_identity",
    "divergent_target",
)
FENS = (
    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
    "4k3/8/8/8/8/8/8/4K3 w - - 0 1",
    "4k3/8/8/8/8/8/8/4K3 b - - 0 1",
    "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1",
    "8/8/8/8/8/8/8/K6k w - - 0 1",
)
RECS = tuple(make_record("standard", f) for f in FENS)
KEYS = tuple(record_identity(r) for r in RECS)


def _state(idx):
    return {KEYS[i]: dict(RECS[i]) for i in idx}


def _bare(fn, *args):
    try:
        return ("ok", fn(*args))
    except diff.DiffError as error:
        return ("reject", error.failure_class, error.code)
    except BaseException as error:  # noqa: BLE001
        return ("crash", type(error).__name__)


def _traced(tracer, name, *args):
    return _bare(getattr(tracer, name), *args)


def _pairs(seed=0, n=40):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        a = sorted(rng.sample(range(len(KEYS)), rng.randint(0, len(KEYS))))
        b = sorted(rng.sample(range(len(KEYS)), rng.randint(0, len(KEYS))))
        out.append((_state(a), _state(b)))
    return out


# -- hostile types --------------------------------------------------------------------

HOSTILE = []  # user dunder calls observed during the production call


class _S(str):
    pass


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


class _LogList(list):
    def __iter__(self):
        HOSTILE.append("iter")
        return list.__iter__(self)

    def __len__(self):
        HOSTILE.append("len")
        return list.__len__(self)

    def __getitem__(self, i):
        HOSTILE.append("getitem")
        return list.__getitem__(self, i)


class _LogTuple(tuple):
    def __iter__(self):
        HOSTILE.append("iter")
        return tuple.__iter__(self)

    def __eq__(self, other):
        HOSTILE.append("eq")
        return tuple.__eq__(self, other)

    def __ne__(self, other):
        HOSTILE.append("ne")
        return tuple.__ne__(self, other)

    def __hash__(self):
        HOSTILE.append("hash")
        return tuple.__hash__(self)


class _D(dict):
    pass


class _LyingDict(dict):
    def __getitem__(self, key):
        HOSTILE.append(f"getitem:{key}")
        return dict.__getitem__(self, key)

    def keys(self):
        HOSTILE.append("keys")
        return []

    def __iter__(self):
        HOSTILE.append("iter")
        return iter([])

    def items(self):
        HOSTILE.append("items")
        return []

    def __len__(self):
        HOSTILE.append("len")
        return 0


def _inert(value):
    if isinstance(value, dict):
        pairs = sorted((_inert(k), _inert(v)) for k, v in dict.items(value))
        return f"{type(value).__name__}{{{pairs}}}"
    if isinstance(value, list):
        return f"{type(value).__name__}[" + ",".join(_inert(v) for v in list.__iter__(value)) + "]"
    return f"{type(value).__name__}:{json.dumps(value)}"


def _hostile_state(spec):
    s = _state([0, 1])
    key = KEYS[0]
    kind, _, form = spec.partition(":")
    field, _, f = form.partition("@")
    if kind == "state-dict-subclass":
        return _D(s)
    if kind == "state-lying-dict":
        return _LyingDict(s)
    if kind == "state-key":
        return {(_hstr(form, k, KEYS[1]) if k == key else k): v for k, v in s.items()}
    if kind == "record-dict-subclass":
        return {**s, key: _D(s[key])}
    if kind == "record-lying-dict":
        return {**s, key: _LyingDict(s[key])}
    if kind == "record-key":
        return {
            **s,
            key: {(_hstr(f, k, "digest") if k == field else k): v for k, v in s[key].items()},
        }
    if kind == "record-value":
        return {**s, key: {**s[key], field: _hstr(f, s[key][field], s[key][field])}}
    raise AssertionError(spec)


def _state_specs():
    specs = ["state-dict-subclass", "state-lying-dict", "record-dict-subclass", "record-lying-dict"]
    for form in KEY_FORMS:
        specs.append(f"state-key:{form}")
        for field in ("variant", "digest", "snapshot_fen"):
            specs.append(f"record-key:{field}@{form}")
            specs.append(f"record-value:{field}@{form}")
    return specs


def _hostile_diff(spec):
    d = diff.compute(_state([0, 1]), _state([1, 2]))
    kind, _, form = spec.partition(":")
    if kind == "diff-dict-subclass":
        return _D(d)
    if kind == "diff-lying-dict":
        return _LyingDict(d)
    if kind == "diff-key":
        field, _, f = form.partition("@")
        return {(_hstr(f, k, "added") if k == field else k): v for k, v in d.items()}
    if kind == "diff-id-value":
        field, _, f = form.partition("@")
        return {**d, field: _hstr(f, d[field], d[field])}
    if kind == "section-dict-subclass":
        return {**d, form: _D(d[form])}
    if kind == "section-lying-dict":
        return {**d, form: _LyingDict(d[form])}
    if kind == "section-state":  # a hostile state inside the added section
        return {**d, "added": _hostile_state(form)}
    if kind == "changed-witness":
        k = KEYS[1]
        w = {"base": dict(RECS[1]), "target": dict(RECS[1])}
        wit = {"dict-subclass": _D(w), "lying-dict": _LyingDict(w)}.get(form)
        if wit is None:
            f = form.partition("@")[2] or form
            wit = {(_hstr(f, x, "target") if x == "base" else x): v for x, v in w.items()}
        return {**d, "changed": {k: wit}}
    raise AssertionError(spec)


def _diff_specs():
    specs = ["diff-dict-subclass", "diff-lying-dict"]
    for form in KEY_FORMS:
        for field in ("base_id", "target_id", "added", "removed", "changed"):
            specs.append(f"diff-key:{field}@{form}")
        for field in ("base_id", "target_id"):
            specs.append(f"diff-id-value:{field}@{form}")
        specs.append(f"changed-witness:key@{form}")
    for section in ("added", "removed", "changed"):
        specs.append(f"section-dict-subclass:{section}")
        specs.append(f"section-lying-dict:{section}")
    specs += ["changed-witness:dict-subclass", "changed-witness:lying-dict"]
    specs += [f"section-state:{s}" for s in _state_specs() if not s.startswith("state-lying")]
    return specs


def _hostile_calls():
    """(label, operation, args builder) for every hostile row."""
    rows = []
    for spec in _state_specs():
        rows.append((f"state_id:{spec}", "state_id", lambda spec=spec: (_hostile_state(spec),)))
        rows.append(
            (
                f"compute-base:{spec}",
                "compute",
                lambda spec=spec: (_hostile_state(spec), _state([2])),
            )
        )
        rows.append(
            (
                f"compute-target:{spec}",
                "compute",
                lambda spec=spec: (_state([2]), _hostile_state(spec)),
            )
        )
        rows.append(
            (
                f"apply-base:{spec}",
                "apply",
                lambda spec=spec: (
                    diff.compute(_state([0, 1]), _state([1, 2])),
                    _hostile_state(spec),
                ),
            )
        )
    # a list subclass where a mapping is expected
    rows.append(("state_id:list-subclass", "state_id", lambda: (_LogList([KEYS[0]]),)))
    rows.append(("validate:list-subclass", "validate_diff", lambda: (_LogList(["base_id"]),)))
    for spec in _diff_specs():
        rows.append((f"validate:{spec}", "validate_diff", lambda spec=spec: (_hostile_diff(spec),)))
        rows.append(
            (f"apply-diff:{spec}", "apply", lambda spec=spec: (_hostile_diff(spec), _state([0, 1])))
        )
    return rows


def _hostile_problems(module=di):
    bad = []
    for label, op, build in _hostile_calls():
        bare_args, traced_args = build(), build()
        before = [_inert(a) for a in traced_args]
        HOSTILE.clear()
        want = _bare(getattr(diff, op), *bare_args)
        bare_calls = list(HOSTILE)
        tracer = module.DiffTracer()
        HOSTILE.clear()
        got = _traced(tracer, op, *traced_args)
        calls = list(HOSTILE)
        rec = tracer.records[-1] if tracer.records else {}
        if want != ("reject", MDR, diff.FAILURE_MAPPING[MDR]):
            bad.append((label, "bare", want))
        if got != want:
            bad.append((label, "outcome", got))
        if bare_calls or calls:
            bad.append((label, "hostile-call", bare_calls, calls))
        if [_inert(a) for a in traced_args] != before:
            bad.append((label, "input-changed"))
        if rec.get("outcome") != "reject" or rec.get("failure_class") != MDR:
            bad.append((label, "trace", rec.get("outcome")))
        if rec.get("arguments_unchanged") is not True:
            bad.append((label, "trace-unchanged"))
        if "__opaque__" not in json.dumps(rec.get("arguments")):
            bad.append((label, "snapshot-not-opaque"))
    return bad


# -- tests: neutrality -----------------------------------------------------------------


def test_traced_calls_match_bare_calls_over_a_seeded_trajectory():
    tracer = di.DiffTracer()
    expected = []
    for base, target in _pairs():
        assert _traced(tracer, "state_id", base) == _bare(diff.state_id, base)
        d = diff.compute(base, target)
        assert _traced(tracer, "compute", base, target) == ("ok", d)
        assert _traced(tracer, "validate_diff", d) == ("ok", None)
        assert _traced(tracer, "apply", d, base) == ("ok", target)
        expected += [
            ("state_id", diff.state_id(base)),
            (
                "compute",
                {
                    "base_id": d["base_id"],
                    "target_id": d["target_id"],
                    "added": len(d["added"]),
                    "removed": len(d["removed"]),
                    "changed": len(d["changed"]),
                },
            ),
            ("validate_diff", None),
            ("apply", {"identities": len(target)}),
        ]
    recs = tracer.records
    assert [(r["operation"], r["result"]) for r in recs] == expected
    assert [r["seq"] for r in recs] == list(range(len(recs)))
    assert all(r["outcome"] == "accept" and r["arguments_unchanged"] is True for r in recs)


def _reject_rows():
    a, b = _state([0, 1]), _state([1, 2])
    d = diff.compute(a, b)
    other = diff.compute(_state([3]), _state([3, 4]))
    return [
        ("state_id", ([1],), MDR),
        ("compute", (a, {KEYS[0]: {**RECS[0], "digest": "x"}}), MDR),
        ("validate_diff", ({**d, "base_id": "gs1:" + "0" * 63},), MDR),
        ("validate_diff", ({**d, "added": {}, "removed": {}, "changed": {}},), DT),
        ("apply", (other, a), CB),
        (
            "apply",
            (
                {
                    **diff.compute(_state([5, 0]), _state([0])),
                    "base_id": diff.state_id(_state([0, 1])),
                },
                a,
            ),
            UI,
        ),
        ("apply", ({**d, "target_id": diff.state_id(_state([4]))}, a), DT),
    ]


def test_every_failure_class_is_traced_like_the_bare_call():
    classes = set()
    for op, args, cls in _reject_rows():
        tracer = di.DiffTracer()
        frozen = copy.deepcopy(args)
        want = _bare(getattr(diff, op), *copy.deepcopy(args))
        assert want == ("reject", cls, diff.FAILURE_MAPPING[cls]), (op, cls, want)
        assert _traced(tracer, op, *args) == want
        assert args == frozen
        rec = tracer.records[-1]
        assert (rec["outcome"], rec["failure_class"], rec["code"]) == ("reject", cls, want[2])
        assert rec["arguments_unchanged"] is True
        assert rec["arguments"] == json.loads(json.dumps(list(frozen)))
        classes.add(cls)
    assert classes == set(diff.FAILURE_MAPPING)


@pytest.mark.parametrize("op", ["state_id", "compute", "validate_diff", "apply"])
def test_wrapped_result_and_error_objects_pass_through_identically(op):
    sentinel = object()
    nargs = 2 if op in ("compute", "apply") else 1
    kw = {
        "state_id": "state_id_fn",
        "compute": "compute_fn",
        "validate_diff": "validate_fn",
        "apply": "apply_fn",
    }[op]
    tracer = di.DiffTracer(**{kw: lambda *a: sentinel})
    assert getattr(tracer, op)(*([{}] * nargs)) is sentinel
    err = diff.DiffError(MDR)

    def reject(*a):
        raise err

    tracer = di.DiffTracer(**{kw: reject})
    with pytest.raises(diff.DiffError) as caught:
        getattr(tracer, op)(*([{}] * nargs))
    assert caught.value is err and tracer.records[-1]["outcome"] == "reject"
    stop = KeyboardInterrupt("stop")

    def crash(*a):
        raise stop

    tracer = di.DiffTracer(**{kw: crash})
    with pytest.raises(KeyboardInterrupt) as caught:
        getattr(tracer, op)(*([{}] * nargs))
    assert caught.value is stop
    assert tracer.records[-1]["outcome"] == "crash"
    assert tracer.records[-1]["error_type"] == "KeyboardInterrupt"


def test_argument_mutation_by_the_wrapped_function_is_reported():
    def scribble(state):
        state["x"] = 1
        return "gs1:" + "0" * 64

    tracer = di.DiffTracer(state_id_fn=scribble)
    tracer.state_id({})
    assert tracer.records[-1]["arguments_unchanged"] is False
    assert tracer.records[-1]["arguments"] == [{}]


# -- tests: diagnostics and totality ----------------------------------------------------


def test_records_are_append_only_isolated_and_jsonl_deterministic():
    def session():
        t = di.DiffTracer()
        a, b = _state([0]), _state([0, 1])
        t.compute(a, b)
        with pytest.raises(diff.DiffError):
            t.state_id([])
        return t

    first, second = session(), session()
    assert first.to_jsonl() == second.to_jsonl() and first.to_jsonl()
    view = first.records
    view[0]["outcome"] = "tampered"
    assert first.records[0]["outcome"] == "accept"
    assert [r["seq"] for r in first.records] == [0, 1]


def test_hostile_error_attributes_never_replace_the_original_error():
    class Evil(diff.DiffError):
        def __getattribute__(self, name):
            if name in {"failure_class", "code"}:
                raise KeyboardInterrupt(name)
            return super().__getattribute__(name)

    err = Evil(MDR)

    def fail(state):
        raise err

    tracer = di.DiffTracer(state_id_fn=fail)
    with pytest.raises(Evil) as caught:
        tracer.state_id({})
    assert caught.value is err
    assert tracer.records[-1]["failure_class"] == {"__opaque__": "attribute-KeyboardInterrupt"}


def test_faulting_trace_container_is_recovered_without_changing_the_result():
    class BadList(list):
        def append(self, value):
            raise SystemExit("append")

    tracer = di.DiffTracer()
    tracer._trace = BadList()
    s = _state([0])
    assert tracer.state_id(s) == diff.state_id(s)
    assert tracer.records[-1]["outcome"] == "accept"
    tracer._trace = "not a list"
    assert tracer.state_id(s) == diff.state_id(s)
    assert len(tracer.records) == 1


def test_pathological_depth_is_an_opaque_marker():
    deep = {}
    node = deep
    for _ in range(200):
        node["k"] = {}
        node = node["k"]
    tracer = di.DiffTracer()
    with pytest.raises(diff.DiffError):
        tracer.state_id(deep)
    assert "max-depth-exceeded" in json.dumps(tracer.records[-1]["arguments"])


def test_argument_snapshot_never_dispatches_copy_hooks():
    class Bomb(dict):
        def __deepcopy__(self, memo):
            raise AssertionError("copy hook")

        def __iter__(self):
            raise AssertionError("iter")

    tracer = di.DiffTracer()
    with pytest.raises(diff.DiffError):
        tracer.validate_diff(Bomb())
    assert tracer.records[-1]["arguments"] == [{"__opaque__": "Bomb"}]


# -- tests: hostile types ----------------------------------------------------------------


def test_hostile_types_at_every_input_boundary_are_typed_inert_and_opaque():
    assert _hostile_problems() == []
    assert len(_hostile_calls()) == 4 * len(_state_specs()) + 2 * len(_diff_specs()) + 2


# -- tests: metaclass dispatch, reject/crash change reports, tuples, internal faults ---


META_LOG = []


class _MetaEq(type):
    def __eq__(cls, other):
        META_LOG.append("meta-eq")
        return False

    def __hash__(cls):
        return id(cls)

    @property
    def __name__(cls):
        META_LOG.append("meta-name")
        return "Lying"


class _Weird(metaclass=_MetaEq):
    pass


class _MetaNameRaises(type):
    @property
    def __name__(cls):
        META_LOG.append("meta-name-raise")
        raise ValueError("name")


class _NamelessError(Exception, metaclass=_MetaNameRaises):
    pass


class _Halt(BaseException):
    pass


def _last(tracer):
    records = tracer.records
    return records[-1] if records else {}


def _edge_problems(module=di):
    """Rows for metaclass dispatch, change reports on reject and crash,
    tuple snapshots and an internal BaseException fault; [] when all hold."""
    bad = []
    # a metaclass __eq__/__name__ argument: no caller code, traced == bare
    META_LOG.clear()
    want = _bare(diff.state_id, _Weird())
    META_LOG.clear()
    tracer = module.DiffTracer()
    arg = _Weird()
    if _traced(tracer, "state_id", arg) != want:
        bad.append(("meta-eq", "outcome"))
    if META_LOG:
        bad.append(("meta-eq", "caller-code", tuple(META_LOG)))
    if _last(tracer).get("arguments") != [{"__opaque__": "_Weird"}]:
        bad.append(("meta-eq", "snapshot", _last(tracer).get("arguments")))
    # an accepted result whose type has a metaclass __name__
    META_LOG.clear()
    tracer = module.DiffTracer(compute_fn=lambda a, b: _Weird())
    tracer.compute({}, {})
    if META_LOG or _last(tracer)["result"] != {"__opaque__": "_Weird"}:
        bad.append(("meta-result", tuple(META_LOG)))
    # an error class whose metaclass __name__ raises: the same object re-raised
    META_LOG.clear()
    err = _NamelessError("x")

    def crash(state):
        raise err

    tracer = module.DiffTracer(state_id_fn=crash)
    try:
        tracer.state_id({})
        bad.append(("meta-name-error", "no-raise"))
    except BaseException as caught:  # noqa: BLE001 - identity is the check
        if caught is not err:
            bad.append(("meta-name-error", "replaced", type(caught).__mro__[0]))
    if META_LOG:
        bad.append(("meta-name-error", "caller-code", tuple(META_LOG)))
    if _last(tracer).get("error_type") != "_NamelessError":
        bad.append(("meta-name-error", "error-type"))
    # the wrapped function mutates its argument, then rejects or crashes
    for label, error in (
        ("reject", diff.DiffError(MDR)),
        ("crash", RuntimeError("x")),
        ("crash", ValueError("x")),
    ):

        def scribble_then_fail(state, error=error):
            state["k"] = 1
            raise error

        tracer = module.DiffTracer(state_id_fn=scribble_then_fail)
        try:
            tracer.state_id({})
            bad.append(("mutate-then-" + label, "no-raise"))
        except BaseException as caught:  # noqa: BLE001 - identity is the check
            if caught is not error:
                bad.append(("mutate-then-" + label, "error-replaced"))
        rec = _last(tracer)
        if rec.get("outcome") != label or rec.get("arguments_unchanged") is not False:
            bad.append(("mutate-then-" + label, rec.get("arguments_unchanged")))
    # a tuple argument snapshots as a list of its snapshotted items
    tracer = module.DiffTracer()
    arg = (1, "a", [None, True], {"k": 2.5})
    if _traced(tracer, "state_id", arg) != _bare(diff.state_id, arg):
        bad.append(("tuple", "outcome"))
    elif _last(tracer)["arguments"] != [[1, "a", [None, True], {"k": 2.5}]]:
        bad.append(("tuple", "snapshot", _last(tracer)["arguments"]))
    # an internal fault raising a BaseException subclass inside the snapshot
    # (injected at the depth marker: every natural snapshot path is total)
    deep = {}
    node = deep
    for _ in range(80):
        node["k"] = {}
        node = node["k"]
    real = module._opaque

    def faulty(kind):
        if kind == "max-depth-exceeded":
            raise _Halt(kind)
        return real(kind)

    module._opaque = faulty
    try:
        tracer = module.DiffTracer()
        out = _traced(tracer, "state_id", deep)
        if out != _bare(diff.state_id, deep):
            bad.append(("internal-fault", "outcome", out))
        elif "snapshot-_Halt" not in json.dumps(_last(tracer)["arguments"]):
            bad.append(("internal-fault", "marker"))
    except _Halt:
        bad.append(("internal-fault", "escaped"))
    finally:
        module._opaque = real
    # hostile results through the summary: never dispatched, opaque markers
    HOSTILE.clear()
    lying = _LenLies({"a": 1})
    tracer = module.DiffTracer(
        compute_fn=lambda a, b: {
            "base_id": "x",
            "target_id": "y",
            "added": [1, 2],
            "removed": lying,
        }
    )
    tracer.compute({}, {})
    summary = _last(tracer).get("result", {})
    if (
        HOSTILE
        or summary.get("added") != {"__opaque__": "section"}
        or summary.get("removed") != {"__opaque__": "section"}
    ):
        bad.append(("summary-sections", tuple(HOSTILE), summary))
    for op, kw in (("compute", "compute_fn"), ("apply", "apply_fn")):
        tracer = module.DiffTracer(**{kw: lambda *a: _LenLies({"a": 1})})
        getattr(tracer, op)({}, {})
        if HOSTILE or _last(tracer).get("result") != {"__opaque__": "_LenLies"}:
            bad.append(("summary-non-dict", op, tuple(HOSTILE)))
    # a tampered trace container is never iterated: records () and empty jsonl
    tracer = module.DiffTracer()
    tracer._trace = _IterLogs([{"seq": 0}])
    if tracer.records != () or tracer.to_jsonl() != "" or HOSTILE:
        bad.append(("tampered-trace-read", tuple(HOSTILE)))
    # an int beyond the int-string limit: an opaque marker, other records intact
    tracer = module.DiffTracer()
    tracer.state_id(_state([0]))
    with suppress(diff.DiffError):
        tracer.state_id({"a": {"x": 10**5000}})
    lines = tracer.to_jsonl().split("\n")
    if len(lines) != 2 or json.loads(lines[0]) != tracer.records[0]:
        bad.append(("big-int", "records-lost"))
    elif "int-too-large" not in lines[1]:
        bad.append(("big-int", "marker"))
    return bad


class _LenLies(dict):
    def __len__(self):
        HOSTILE.append("len")
        return 99

    def get(self, *a):
        HOSTILE.append("get")
        return None


class _IterLogs(list):
    def __iter__(self):
        HOSTILE.append("iter")
        return list.__iter__(self)

    def __len__(self):
        HOSTILE.append("len")
        return 1


def test_metaclass_dispatch_change_reports_tuples_and_internal_faults():
    assert _edge_problems() == []


# -- mutants of the instrument --------------------------------------------------------


def _mutant(name, edits):
    source = SOURCE
    for old, new in edits:
        assert source.count(old) == 1, (name, old)
        source = source.replace(old, new)
    module = types.ModuleType(f"graph._t0183_mutant_{name}")
    module.__file__ = str(ROOT / "graph" / "diff_instrument.py")
    exec(compile(source, f"<mutant {name}>", "exec"), module.__dict__)  # noqa: S102
    return module


def _neutral_problems(module):
    bad = []
    for op, args, cls in _reject_rows():
        want = _bare(getattr(diff, op), *copy.deepcopy(args))
        tracer = module.DiffTracer()
        if _traced(tracer, op, *args) != want:
            bad.append(("outcome", op, cls))
        elif tracer.records[-1].get("failure_class") != cls:
            bad.append(("trace", op, cls))
    for base, target in _pairs(n=10):
        tracer = module.DiffTracer()
        if _traced(tracer, "apply", diff.compute(base, target), base) != ("ok", target):
            bad.append(("apply",))
    return bad


_REJECT_TAIL = "                }\n            )\n            "
_UNCHANGED_LINE = (
    '                    "arguments_unchanged": [_snapshot(a) for a in args] == before,'
)
_FORCED_TRUE = '                    "arguments_unchanged": True,'

MUTANTS = {
    "snapshot-dispatches-subclass-iteration": (
        [
            ("        if t is list or t is tuple:", "        if isinstance(value, (list, tuple)):"),
            (
                "            items = list.__iter__(value) if t is list else tuple.__iter__(value)",
                "            items = iter(value)",
            ),
        ],
        _hostile_problems,
    ),
    "snapshot-dispatches-dict-subclass": (
        [("        if t is dict:", "        if isinstance(value, dict):")],
        _hostile_problems,
    ),
    "snapshot-keeps-str-subclass-keys": (
        [
            (
                "                if type(key) is not str:\n",
                "                if not isinstance(key, str):\n",
            )
        ],
        _hostile_problems,
    ),
    "reject-swallowed": (
        [
            (
                _REJECT_TAIL + "raise\n        except BaseException",
                _REJECT_TAIL + "return None\n        except BaseException",
            )
        ],
        _neutral_problems,
    ),
    "type-check-by-equality": (
        [
            (
                "        if t is type(None) or t is bool or t is float or t is str:",
                "        if t in (type(None), bool, float, str):",
            )
        ],
        _edge_problems,
    ),
    "type-name-dispatches": (
        [("        name = _TYPE_NAME.__get__(t)", "        name = t.__name__")],
        _edge_problems,
    ),
    "crash-name-unguarded": (
        [
            (
                '"error_type": _type_name(type(error)),',
                '"error_type": type(error).__name__,',
            )
        ],
        _edge_problems,
    ),
    "unchanged-forced-true-on-reject": (
        [
            (
                '"code": _attribute(error, "code"),\n' + _UNCHANGED_LINE,
                '"code": _attribute(error, "code"),\n' + _FORCED_TRUE,
            )
        ],
        _edge_problems,
    ),
    "unchanged-forced-true-on-crash": (
        [
            (
                '"error_type": _type_name(type(error)),\n' + _UNCHANGED_LINE,
                '"error_type": _type_name(type(error)),\n' + _FORCED_TRUE,
            )
        ],
        _edge_problems,
    ),
    "summary-len-dispatch": (
        [
            (
                'dict.__len__(section) if type(section) is dict else _opaque("section")',
                "len(section)",
            )
        ],
        _edge_problems,
    ),
    "records-iterates-tampered-trace": (
        [
            (
                "return tuple(_snapshot(trace)) if type(trace) is list else ()",
                "return tuple(trace)",
            )
        ],
        _edge_problems,
    ),
    "int-limit-off": (
        [
            (
                "                int.__repr__(value)  "
                "# beyond the int-string limit: not serializable\n",
                "                pass\n",
            )
        ],
        _edge_problems,
    ),
    "tuple-opaque": (
        [("        if t is list or t is tuple:", "        if t is list:")],
        _edge_problems,
    ),
    "snapshot-narrow-except": (
        [
            (
                "        return _opaque(_type_name(t))\n    except BaseException as error:",
                "        return _opaque(_type_name(t))\n    except Exception as error:",
            )
        ],
        _edge_problems,
    ),
    "reject-traced-as-crash": (
        [("        except _diff.DiffError as error:", "        except KeyError as error:")],
        _neutral_problems,
    ),
}


def test_identity_mutant_is_green():
    module = _mutant("identity", [])
    assert _hostile_problems(module) == [] and _neutral_problems(module) == []
    assert _edge_problems(module) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_is_red_on_its_own_check(name):
    edits, check = MUTANTS[name]
    assert check(_mutant(name, edits)), name
