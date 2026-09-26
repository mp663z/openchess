"""T0439: versioning compare acceptance battery, ready for the T0440 compare.

The single binding below is deliberately the T0437 *test reference* while
there is no shipped compare. T0440 must replace this binding with its
independently implemented production compare and error class. The closed
T0438 fixture supplies every expectation; the oracles here (typed error
shape, purity, determinism, precedence, hostile refusal without user code)
are restated from the versioning contract, never delegated to whichever
implementation is bound.

Boundary under test: compare(old, new, old_minor, new_minor) -> exact bool,
raising the bound error class with failure_class "malformed_version_request",
code "malformed_request", retryable False, fresh and without leaked context.
Both snapshots and minors are validated before any comparison; a rejected
comparison has no effects on either snapshot.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
from pathlib import Path

import pytest

from tests import test_t0437_control_plane_versioning_contract as _reference

PRODUCTION_BINDING = (_reference.compare, _reference.VersionError)

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/control-plane-versioning/cases.json").read_text())
VERDICT_SECTIONS = ("happy", "boundary", "incompatible")
FAILURE = "malformed_version_request"
CODE = "malformed_request"
CEILING = 2147483647
MARKER = "sk-live-9f8e7d6c5b4a-fixture-marker"
ACTIONS = {
    "set": {"path", "value"},
    "del": {"path"},
    "append": {"path", "value"},
    "remove": {"path", "value"},
    "extend": {"path", "values"},
    "reverse": {"path"},
}


class FixtureError(Exception):
    """The closed corpus itself is unmaterializable; never a binding verdict."""


def _walk(doc, path, where):
    node = doc
    for key in path[:-1]:
        if type(node) is not dict or type(key) is not str or key not in node:
            raise FixtureError(f"{where}: unresolvable path {path}")
        node = node[key]
    if type(node) is not dict or type(path[-1]) is not str:
        raise FixtureError(f"{where}: unresolvable path {path}")
    return node, path[-1]


def _apply(doc, mutations, where):
    for mutation in mutations:
        if type(mutation) is not dict or len(mutation) != 1:
            raise FixtureError(f"{where}: malformed mutation {mutation}")
        (action,) = mutation
        spec = mutation[action]
        if action not in ACTIONS or type(spec) is not dict or set(spec) != ACTIONS[action]:
            raise FixtureError(f"{where}: malformed mutation {mutation}")
        path = spec["path"]
        if type(path) is not list or not path or not all(type(key) is str for key in path):
            raise FixtureError(f"{where}: malformed path {path}")
        node, last = _walk(doc, path, where)
        if action == "set":
            node[last] = copy.deepcopy(spec["value"])
        elif action == "del":
            if last not in node:
                raise FixtureError(f"{where}: del of missing key {path}")
            del node[last]
        else:
            target = node.get(last)
            if type(target) is not list:
                raise FixtureError(f"{where}: list action on non-list {path}")
            if action == "append":
                target.append(copy.deepcopy(spec["value"]))
            elif action == "remove":
                if spec["value"] not in target:
                    raise FixtureError(f"{where}: remove of absent value {path}")
                target.remove(spec["value"])
            elif action == "extend":
                if type(spec["values"]) is not list:
                    raise FixtureError(f"{where}: extend of non-list {path}")
                target.extend(copy.deepcopy(spec["values"]))
            else:
                target.reverse()


def _snapshots(row):
    where = f"row:{row['name']}"
    old = copy.deepcopy(CASES["base_snapshot"])
    _apply(old, row["old"], f"{where}:old")
    new = copy.deepcopy(CASES["base_snapshot"])
    _apply(new, row["new"], f"{where}:new")
    return old, new


def _snapshot(value, seen=None):
    """Inspect built-in containers without invoking user-defined operators."""
    seen = {} if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return ("alias", seen[id(value)])
        seen[id(value)] = len(seen)
        if isinstance(value, dict):
            items = [
                (
                    type(key).__name__,
                    str.__str__(key) if isinstance(key, str) else "non-str",
                    _snapshot(item, seen),
                )
                for key, item in dict.items(value)
            ]
        else:
            items = [_snapshot(item, seen) for item in list.__iter__(value)]
        return (type(value).__name__, items)
    if isinstance(value, str):
        return (type(value).__name__, str.__str__(value))
    if type(value) in (int, float, bool, type(None)):
        return (type(value).__name__, value)
    return (type(value).__name__, id(value))


def _typed_refusal(binding, call, label):
    _, error_type = binding
    with pytest.raises(error_type) as caught:
        call()
    error = caught.value
    assert type(error) is error_type, label
    assert getattr(error, "failure_class", None) == FAILURE, label
    assert getattr(error, "code", None) == CODE, label
    assert getattr(error, "retryable", None) is False, label
    assert error.__cause__ is None and error.__context__ is None, label
    assert MARKER not in str(error) and MARKER not in repr(error), label
    return error


def _run_verdict_row(binding, row):
    compare, _ = binding
    old, new = _snapshots(row)
    before = (_snapshot(old), _snapshot(new))
    first = compare(old, new, row["old_minor"], row["new_minor"])
    assert (_snapshot(old), _snapshot(new)) == before, row["name"]
    second = compare(old, new, row["old_minor"], row["new_minor"])
    assert first is row["expect"], row["name"]
    assert second is row["expect"], row["name"]
    assert (_snapshot(old), _snapshot(new)) == before, row["name"]


def _run_malformed_row(binding, row):
    compare, _ = binding
    old, new = _snapshots(row)
    before = (_snapshot(old), _snapshot(new))
    errors = [
        _typed_refusal(
            binding,
            lambda: compare(old, new, row["old_minor"], row["new_minor"]),
            row["name"],
        )
        for _ in range(2)
    ]
    assert errors[0] is not errors[1], row["name"]
    assert (_snapshot(old), _snapshot(new)) == before, row["name"]


def _matrix(binding):
    for section in VERDICT_SECTIONS:
        for row in CASES[section]:
            _run_verdict_row(binding, row)
    for row in CASES["malformed"]:
        _run_malformed_row(binding, row)


def _no_effects(binding):
    """A rejected comparison changes nothing: after typed refusals the valid
    rows still verdict exactly, and refusal leaves no residue behind."""
    compare, _ = binding
    happy = next(row for row in CASES["happy"] if row["name"] == "optional-request-field-added")
    incompatible = next(row for row in CASES["incompatible"] if row["name"] == "operation-removed")
    _typed_refusal(binding, lambda: compare(None, None, 0, 1), "none-snapshots")
    _run_verdict_row(binding, happy)
    _typed_refusal(binding, lambda: compare({}, {}, -1, -2), "bad-minors")
    _run_verdict_row(binding, incompatible)
    _run_verdict_row(binding, happy)


def _precedence(binding):
    """Validation precedes comparison: a request that is both invalid and
    incompatible refuses typed, never returns the compatibility verdict."""
    compare, _ = binding
    precedence_row = next(
        row
        for row in CASES["malformed"]
        if row["name"] == "precedence-minor-bound-beats-comparison"
    )
    old, new = _snapshots(precedence_row)
    # the same breaking change at a valid minor is a False verdict, not an error
    assert compare(old, new, 1, 2) is False
    # at a downgraded minor the invalid request refuses typed instead
    _typed_refusal(binding, lambda: compare(old, new, 2, 1), "precedence")
    equal_row = next(row for row in CASES["boundary"] if row["name"] == "equal-snapshots-zero-zero")
    marker_old, marker_new = _snapshots(equal_row)
    marker_new["areas"]["identity"]["ops"]["refresh"]["errors"].append(MARKER)
    _typed_refusal(binding, lambda: compare(marker_old, marker_new, 0, 1), "marker")


# -- hostile boundary probes ------------------------------------------------------


class _Armed:
    active = False
    calls = []


def _trap(name):
    if _Armed.active:
        _Armed.calls.append(name)
        raise AssertionError(f"user code executed: {name}")


class EvilStr(str):
    def __eq__(self, other):
        _trap("str eq")
        return str.__eq__(self, other)

    def __hash__(self):
        _trap("str hash")
        return str.__hash__(self)

    def __repr__(self):
        _trap("str repr")
        return str.__repr__(self)


class EvilDict(dict):
    def __iter__(self):
        _trap("dict iter")
        return dict.__iter__(self)

    def __getitem__(self, key):
        _trap("dict getitem")
        return dict.__getitem__(self, key)

    def __eq__(self, other):
        _trap("dict eq")
        return dict.__eq__(self, other)


class EvilList(list):
    def __iter__(self):
        _trap("list iter")
        return list.__iter__(self)

    def __eq__(self, other):
        _trap("list eq")
        return list.__eq__(self, other)


class IntSub(int):
    pass


def _replace(doc, path, value):
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return doc


def _evil_key(mapping, key):
    out = dict(mapping)
    out[EvilStr(key)] = out.pop(key)
    return out


def _int_key(mapping, key):
    out = dict(mapping)
    out[7] = out[key]
    return out


def _hostile_probes():
    """Python-only hostile shapes at the boundary; the closed JSON corpus
    cannot represent subclasses or non-string keys."""
    probes = []

    def probe(label, mutate, minors=(0, 1)):
        probes.append((label, mutate, minors))

    probe("evil-dict-top-old", lambda old, new: (EvilDict(old), new))
    probe("evil-dict-top-new", lambda old, new: (old, EvilDict(new)))
    probe("none-old", lambda old, new: (None, new))
    probe("list-new", lambda old, new: (old, [new]))
    probe("str-old", lambda old, new: (EvilStr("doc"), new))
    probe(
        "evil-dict-areas",
        lambda old, new: (old, _replace(new, ["areas"], EvilDict(new["areas"]))),
    )
    probe(
        "evil-dict-op-spec",
        lambda old, new: (
            old,
            _replace(
                new,
                ["areas", "identity", "ops", "refresh"],
                EvilDict(new["areas"]["identity"]["ops"]["refresh"]),
            ),
        ),
    )
    probe(
        "evil-dict-fields",
        lambda old, new: (
            old,
            _replace(
                new,
                ["areas", "identity", "ops", "register", "request", "fields"],
                EvilDict(new["areas"]["identity"]["ops"]["register"]["request"]["fields"]),
            ),
        ),
    )
    probe(
        "evil-list-allowlist",
        lambda old, new: (
            old,
            _replace(
                new,
                ["contract", "transport", "read_only_operations"],
                EvilList(new["contract"]["transport"]["read_only_operations"]),
            ),
        ),
    )
    probe(
        "evil-list-errors",
        lambda old, new: (
            old,
            _replace(
                new,
                ["areas", "identity", "ops", "refresh", "errors"],
                EvilList(new["areas"]["identity"]["ops"]["refresh"]["errors"]),
            ),
        ),
    )
    probe(
        "evil-str-base-path",
        lambda old, new: (
            old,
            _replace(new, ["contract", "versioning", "base_path"], EvilStr("/cp/v1")),
        ),
    )
    probe(
        "evil-str-rule-valid-content",
        lambda old, new: (
            old,
            _replace(
                new,
                ["contract", "versioning", "rule"],
                EvilStr(new["contract"]["versioning"]["rule"]),
            ),
        ),
    )
    probe(
        "evil-str-op-key",
        lambda old, new: (
            old,
            _replace(
                new,
                ["areas", "identity", "ops"],
                _evil_key(new["areas"]["identity"]["ops"], "refresh"),
            ),
        ),
    )
    probe(
        "int-key-in-ops",
        lambda old, new: (
            old,
            _replace(
                new,
                ["areas", "identity", "ops"],
                _int_key(new["areas"]["identity"]["ops"], "refresh"),
            ),
        ),
    )
    probe(
        "int-key-in-fields",
        lambda old, new: (
            old,
            _replace(
                new,
                ["areas", "identity", "ops", "register", "request", "fields"],
                _int_key(new["areas"]["identity"]["ops"]["register"]["request"]["fields"], "email"),
            ),
        ),
    )
    probe("intsub-minor", lambda old, new: (old, new), minors=(IntSub(0), 1))
    probe("intsub-minor-new", lambda old, new: (old, new), minors=(0, IntSub(1)))
    probe("evil-str-minor", lambda old, new: (old, new), minors=(EvilStr("0"), 1))
    probe("none-minor", lambda old, new: (old, new), minors=(0, None))
    return probes


def _hostile(binding):
    compare, _ = binding
    for label, mutate, minors in _hostile_probes():
        old, new = _snapshots(CASES["boundary"][0])
        old, new = mutate(old, new)
        before = (_snapshot(old), _snapshot(new))
        _Armed.calls = []
        _Armed.active = True
        try:
            call = lambda old=old, new=new, minors=minors: compare(old, new, *minors)  # noqa: E731
            _typed_refusal(binding, call, label)
        finally:
            _Armed.active = False
        assert _Armed.calls == [], f"{label}: {_Armed.calls}"
        assert (_snapshot(old), _snapshot(new)) == before, label


# -- acceptance on the current binding --------------------------------------------


def test_acceptance_battery_green_on_current_binding():
    _matrix(PRODUCTION_BINDING)
    _no_effects(PRODUCTION_BINDING)
    _precedence(PRODUCTION_BINDING)
    _hostile(PRODUCTION_BINDING)


# -- black-box behavioral mutants ---------------------------------------------------
# The SAME battery executes against each faulty binding; a kill is any red
# executed check, never a source-text or fixture-shape assertion.


def _always_true(old, new, old_minor, new_minor):
    return True


def _always_false(old, new, old_minor, new_minor):
    return False


def _convolved_false(old, new, old_minor, new_minor):
    try:
        return _reference.compare(old, new, old_minor, new_minor)
    except _reference.VersionError:
        return False


def _leaked_context(old, new, old_minor, new_minor):
    try:
        return _reference.compare(old, new, old_minor, new_minor)
    except _reference.VersionError as error:
        raise _reference.VersionError(str(error)) from error


class _WrongClass(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.failure_class = "version_mismatch"
        self.code = "malformed_request"
        self.retryable = False


def _wrong_failure_class(old, new, old_minor, new_minor):
    try:
        return _reference.compare(old, new, old_minor, new_minor)
    except _reference.VersionError as error:
        raise _WrongClass(str(error)) from None


def _plain_runtime_error(old, new, old_minor, new_minor):
    try:
        return _reference.compare(old, new, old_minor, new_minor)
    except _reference.VersionError as error:
        raise RuntimeError(str(error)) from None


def _impure(old, new, old_minor, new_minor):
    result = _reference.compare(old, new, old_minor, new_minor)
    if type(new) is dict:
        new.pop("areas", None)
    return result


def _make_nondeterministic():
    state = {"flip": False}

    def compare(old, new, old_minor, new_minor):
        result = _reference.compare(old, new, old_minor, new_minor)
        state["flip"] = not state["flip"]
        if state["flip"] and type(result) is bool:
            return not result
        return result

    return compare


def _minor_clamped(old, new, old_minor, new_minor):
    def clamp(value):
        if type(value) is not int:
            return 0
        return max(0, min(value, CEILING))

    old_minor, new_minor = clamp(old_minor), clamp(new_minor)
    return _reference.compare(old, new, min(old_minor, new_minor), max(old_minor, new_minor))


MUTANTS = {
    "permissive-compare": (_always_true, _reference.VersionError),
    "pessimistic-compare": (_always_false, _reference.VersionError),
    "malformed-convolved-to-false": (_convolved_false, _reference.VersionError),
    "leaked-error-context": (_leaked_context, _reference.VersionError),
    "wrong-failure-class": (_wrong_failure_class, _WrongClass),
    "untyped-plain-error": (_plain_runtime_error, RuntimeError),
    "impure-compare": (_impure, _reference.VersionError),
    "nondeterministic-compare": (_make_nondeterministic(), _reference.VersionError),
    "minor-validation-clamped": (_minor_clamped, _reference.VersionError),
}


def _battery_red(binding):
    """True when any executed battery check disagrees with the binding under
    test. The probe runner records the failure class broadly because a faulty
    binding's red signal is not always an AssertionError: a compare that
    raises its own error type on a verdict row, or a mutant that crashes, is
    just as red. Every kill still comes from an executed battery row."""
    try:
        _matrix(binding)
    except BaseException:  # noqa: BLE001
        return True
    return False


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_behavioral_mutants_turn_the_battery_red(name):
    assert _battery_red(MUTANTS[name]), name


def test_missing_production_binding_is_demonstrably_red():
    def not_implemented(_old, _new, _old_minor, _new_minor):
        raise NotImplementedError("T0440 has not supplied a compare")

    with pytest.raises(NotImplementedError):
        _matrix((not_implemented, _reference.VersionError))


# Reference source-edit mutants: the same fixture battery kills each faulty
# compare; equivalents preempted by strict T0419 validation stay green.


def _equivalent_compare(name):
    before, replacement, _probe = _reference.EQUIVALENT[name]
    source = (
        inspect.getsource(_reference._fields)
        + "\n"
        + inspect.getsource(_reference._minor)
        + "\n"
        + inspect.getsource(_reference.compare)
    )
    assert source.count(before) == 1, name
    scope = dict(vars(_reference))
    exec(  # noqa: S102
        compile(ast.parse(source.replace(before, replacement, 1)), f"<equivalent:{name}>", "exec"),
        scope,
    )
    return scope["compare"]


@pytest.mark.parametrize("name", sorted(_reference.REFERENCE_MUTANTS))
def test_reference_source_mutants_turn_the_battery_red(name):
    binding = (_reference._mutant_compare(name), _reference.VersionError)
    assert _battery_red(binding), name


@pytest.mark.parametrize("name", sorted(_reference.EQUIVALENT))
def test_reference_equivalent_edits_keep_the_battery_green(name):
    _matrix((_equivalent_compare(name), _reference.VersionError))
