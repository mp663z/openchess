"""T0448: errors classification acceptance battery, ready for the T0449 classifier.

The single binding below is deliberately the T0446 *test reference* while
there is no shipped classifier. T0449 must replace this binding with its
independently implemented production classifier and error class. The closed
T0447 fixture supplies every corpus expectation; the oracles here (typed
error shape, purity, determinism, precedence, atomicity, hostile refusal
without user code, no echo of payload content) are restated from
data/contracts/control_plane_errors.yaml, never delegated to whichever
implementation is bound. One shared runner (_acceptance) is both the green
gate on the bound classifier and the red gate on every faulty binding.

Boundary under test: classify(source, status, payload, operation=None) ->
exact verdict dict {"kind": "success"} or {"kind": "error", "code",
"message", "retryable"}, raising the bound error class with failure_class
"malformed_error_result", code "malformed_request", retryable False, fresh
and without leaked context, and never echoing payload content. Validation
runs source, then status, then operation, then payload; the first failure
wins. A refused classification returns nothing and leaves the source and
the payload bit-identical; the classifier is pure, so there is no state to
roll back. The one context-carrying refusal path is the operation-parse
guard, pinned by the closed corpus.
"""

from __future__ import annotations

import copy
import json
import time
from functools import partial
from pathlib import Path

import pytest

from server import control_plane_errors as production
from tests import test_t0446_control_plane_errors_contract as _reference
from tests import test_t0447_control_plane_errors_fixture as _fixture

PRODUCTION_BINDING = (production.classify, production.ErrorsError)

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/control-plane-errors/cases.json").read_text())
VERDICT_SECTIONS = ("happy", "boundary")
FAILURE = "malformed_error_result"
CODE = "malformed_request"
NO_ECHO = ("s3cr3t-fixture", "hunter2-fixture")
HOSTILE_SOURCE = [{"set": {"path": ["schema_version"], "value": True}}]


def _row_by_name(section, name):
    return next(row for row in CASES[section] if row["name"] == name)


# -- executed calls against the bound classifier ---------------------------------


def _typed_refusal(binding, call, label, *, context):
    _, error_type = binding
    with pytest.raises(error_type) as caught:
        call()
    error = caught.value
    assert type(error) is error_type, label
    assert getattr(error, "failure_class", None) == FAILURE, label
    assert getattr(error, "code", None) == CODE, label
    assert getattr(error, "retryable", None) is False, label
    assert error.__cause__ is None, label
    if context:
        assert error.__context__ is not None, label
    else:
        assert error.__context__ is None, label
    for marker in NO_ECHO:
        assert marker not in str(error) and marker not in repr(error), label
    return error


def _run_verdict_call(binding, status, operation, payload, source_mutations, expect, where):
    classify, _ = binding
    source = _fixture._materialize_source(source_mutations, where)
    source_snap = _fixture._snap(source)
    payload_snap = _fixture._snap(payload)
    first = classify(source, status, payload, operation)
    second = classify(source, status, payload, operation)
    assert first == expect, where
    assert second == expect, where
    assert first is not second, where  # a fresh dict per classification
    if first["kind"] == "error":
        assert first is not payload["error"], where
        original = payload["error"]["message"]
        payload["error"]["message"] = "mutated-after-classification"
        assert first["message"] == expect["message"], where  # detached result
        payload["error"]["message"] = original
    assert _fixture._snap(payload) == payload_snap, where
    assert _fixture._snap(source) == source_snap, where


def _run_refusal_call(binding, status, operation, payload, source_mutations, name, where):
    classify, _ = binding
    source = _fixture._materialize_source(source_mutations, where)
    source_snap = _fixture._snap(source)
    payload_snap = _fixture._snap(payload)
    errors = [
        _typed_refusal(
            binding,
            lambda: classify(source, status, payload, operation),
            where,
            context=name in _fixture.CONTEXT_CARRYING,
        )
        for _ in range(2)
    ]
    assert errors[0] is not errors[1], where  # a fresh typed error per refusal
    assert _fixture._snap(payload) == payload_snap, where
    assert _fixture._snap(source) == source_snap, where
    return errors


def _run_verdict_row(binding, row, where):
    # The payload is deep-copied per execution: a faulty binding under test
    # may mutate its inputs, and the closed corpus must survive the battery.
    _run_verdict_call(
        binding,
        row["status"],
        row["operation"],
        copy.deepcopy(row["payload"]),
        row.get("source", []),
        row["expect"],
        where,
    )


def _run_refusal_row(binding, row, where):
    return _run_refusal_call(
        binding,
        row["status"],
        row["operation"],
        copy.deepcopy(row["payload"]),
        row.get("source", []),
        row["name"],
        where,
    )


def _run_rollback_row(binding, row):
    where = f"rollback:{row['name']}"
    all_errors = []
    for index, call in enumerate(row["calls"]):
        call_where = f"{where}:call{index}"
        if "expect_failure" in call:
            all_errors += _run_refusal_call(
                binding,
                call["status"],
                call["operation"],
                copy.deepcopy(call["payload"]),
                call["source"],
                row["name"],
                call_where,
            )
        else:
            _run_verdict_call(
                binding,
                call["status"],
                call["operation"],
                copy.deepcopy(call["payload"]),
                call["source"],
                call["expect"],
                call_where,
            )
    ids = {id(error) for error in all_errors}
    assert len(ids) == len(all_errors), where  # no typed error is ever reused


def _run_derived_probe(binding, probe):
    label, status, operation, payload = probe
    payload_snap = _fixture._snap(payload)
    errors = [
        _typed_refusal(
            binding,
            lambda: _binding_call(binding, status, operation, payload),
            label,
            context=False,
        )
        for _ in range(2)
    ]
    assert errors[0] is not errors[1], label
    assert _fixture._snap(payload) == payload_snap, label


def _binding_call(binding, status, operation, payload):
    classify, _ = binding
    return classify(copy.deepcopy(_fixture.SOURCE_DOC), status, payload, operation)


def _timing(binding):
    """Executed bound: a linear FEN scan classifies a 16KiB clean string in
    milliseconds; a quadratic scan needs seconds."""
    classify, _ = binding
    payload = {"extra": "p" * _fixture.TIMING_PROBE_CHARS}
    started = time.perf_counter()
    assert classify(copy.deepcopy(_fixture.SOURCE_DOC), 200, payload) == {"kind": "success"}
    assert time.perf_counter() - started < _fixture.TIMING_BOUND_S


def _matrix(binding):
    """Every closed corpus row plus the derived hostile probes and the
    timing probe, executed against the binding under test."""
    for section in VERDICT_SECTIONS:
        for row in CASES[section]:
            _run_verdict_row(binding, row, f"{section}:{row['name']}")
    for row in CASES["malformed"]:
        _run_refusal_row(binding, row, f"malformed:{row['name']}")
    for row in CASES["rollback"]:
        _run_rollback_row(binding, row)
    # Probes are rebuilt, not reused: they carry fresh payloads per matrix
    # run, so a faulty binding cannot pollute another binding's probes.
    for probe in _fixture._derived_probes():
        _run_derived_probe(binding, probe)
    _timing(binding)


def _acceptance(binding):
    """The one full acceptance battery: the closed corpus matrix, the
    no-effects, precedence and hostile oracles. The green gate on the bound
    classifier and every red gate on a faulty binding run exactly this, so
    no fault class can pass a red gate that the green gate does not run."""
    _matrix(binding)
    _no_effects(binding)
    _precedence(binding)
    _hostile(binding)


# -- contract-restated oracles ----------------------------------------------------


def _no_effects(binding):
    """A refused classification returns nothing and leaves no residue: after
    typed refusals the valid rows still classify exactly."""
    classify, _ = binding
    _typed_refusal(
        binding,
        lambda: classify(copy.deepcopy(_fixture.SOURCE_DOC), 300, _fixture._base_envelope(), None),
        "no-effects:status-300",
        context=False,
    )
    _run_verdict_row(binding, _row_by_name("happy", "enum-not-found"), "no-effects:happy")
    _typed_refusal(
        binding,
        lambda: classify(
            _fixture._materialize_source(HOSTILE_SOURCE, "no-effects"),
            503,
            _fixture._base_envelope(),
            None,
        ),
        "no-effects:hostile-source",
        context=False,
    )
    _run_verdict_row(binding, _row_by_name("boundary", "status-200-edge"), "no-effects:boundary")
    _run_verdict_row(binding, _row_by_name("happy", "enum-not-found"), "no-effects:happy-again")


def _precedence(binding):
    """Validation order is source, then status, then operation, then
    payload; the first failure wins. The operation-parse guard is the one
    refusal path the closed corpus pins as context-carrying, so a beaten
    operation parse is observable as a refusal without context."""
    classify, _ = binding
    # a hostile source beats an unparsable operation: no operation context
    _typed_refusal(
        binding,
        lambda: classify(
            _fixture._materialize_source(HOSTILE_SOURCE, "precedence"),
            400,
            _fixture._base_envelope(),
            "identity.login.x",
        ),
        "precedence:source-beats-operation",
        context=False,
    )
    # an unsupported status beats an unparsable operation: no context
    _typed_refusal(
        binding,
        lambda: classify(
            copy.deepcopy(_fixture.SOURCE_DOC), 300, _fixture._base_envelope(), "identity.login.x"
        ),
        "precedence:status-beats-operation",
        context=False,
    )
    # an unparsable operation beats a malformed payload: context-carrying
    _typed_refusal(
        binding,
        lambda: classify(
            copy.deepcopy(_fixture.SOURCE_DOC), 400, {"error": []}, "identity.login.x"
        ),
        "precedence:operation-beats-payload",
        context=True,
    )


# -- hostile boundary probes: typed refusal without running user code -------------


class _Armed:
    active = False
    calls = []


def _trap(name):
    if _Armed.active:
        _Armed.calls.append(name)
        raise AssertionError(f"user code executed: {name}")


class EvilStr(str):
    # __hash__ is deliberately delegated untrapped: the bound contract's
    # declared-field membership probe hashes keys before their exact-type
    # check, and hashing is side-effect-free. Comparisons, repr and content
    # methods stay trapped. It must exist: defining __eq__ alone would
    # default __hash__ to None and make the key unhashable.
    def __hash__(self):
        return str.__hash__(self)

    def __eq__(self, other):
        _trap("str eq")
        return str.__eq__(self, other)

    def __repr__(self):
        _trap("str repr")
        return str.__repr__(self)

    def lower(self):
        _trap("str lower")
        return str.lower(self)

    def split(self, *args):
        _trap("str split")
        return str.split(self, *args)


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


class EvilInt(int):
    def __eq__(self, other):
        _trap("int eq")
        return int.__eq__(self, other)

    def __hash__(self):
        _trap("int hash")
        return int.__hash__(self)

    def __le__(self, other):
        _trap("int le")
        return int.__le__(self, other)

    def __ge__(self, other):
        _trap("int ge")
        return int.__ge__(self, other)


def _hostile_probes():
    """(label, status, operation, payload, source) refusal probes carrying
    armed Python-only shapes the closed JSON corpus cannot represent."""
    probes = []

    def add(label, payload, status=400, operation=None, source=None):
        probes.append((label, status, operation, payload, source))

    payload = _fixture._base_envelope()
    add("evil-payload-dict", EvilDict(payload))
    payload = _fixture._base_envelope()
    payload["error"] = EvilDict(payload["error"])
    add("evil-error-dict", payload)
    payload = _fixture._base_envelope()
    payload["error"]["code"] = EvilStr("not_found")
    add("evil-error-code-str", payload)
    payload = _fixture._base_envelope()
    payload["error"]["message"] = EvilStr("fixture detail")
    add("evil-error-message-str", payload)
    payload = {EvilStr("extra"): "v", **_fixture._base_envelope()}
    add("evil-top-key-str", payload)
    payload = _fixture._base_envelope()
    payload["extra"] = EvilList([1, 2])
    add("evil-extra-list", payload)
    add("evil-operation-str", _fixture._base_envelope(), operation=EvilStr("identity.login"))
    add("evil-status-int", _fixture._base_envelope(), status=EvilInt(400))
    add(
        "evil-source-dict",
        _fixture._base_envelope(),
        source=EvilDict(copy.deepcopy(_fixture.SOURCE_DOC)),
    )
    cycle = []
    cycle.append(cycle)
    payload = _fixture._base_envelope()
    payload["extra"] = cycle
    add("cyclic-extra", payload)
    deep = "leaf"
    for _ in range(100):
        deep = [deep]
    payload = _fixture._base_envelope()
    payload["extra"] = deep
    add("deep-extra", payload)
    return probes


def _hostile(binding):
    classify, _ = binding
    for label, status, operation, payload, source in _hostile_probes():
        if source is None:
            source = copy.deepcopy(_fixture.SOURCE_DOC)
        payload_snap = _fixture._snap(payload)
        source_snap = _fixture._snap(source)
        _Armed.calls = []
        _Armed.active = True
        try:
            call = partial(classify, source, status, payload, operation)
            _typed_refusal(binding, call, label, context=False)
        finally:
            _Armed.active = False
        assert _Armed.calls == [], f"{label}: {_Armed.calls}"
        assert _fixture._snap(payload) == payload_snap, label
        assert _fixture._snap(source) == source_snap, label


def _key_renaming_classify(source, status, payload, operation=None):
    """A faulty binding: classifies exactly like the reference, then renames
    the error message key in the caller's payload while preserving every
    value and the insertion order."""
    try:
        return _reference.classify(source, status, payload, operation)
    finally:
        if type(payload) is dict and type(payload.get("error")) is dict:
            error = payload["error"]
            payload["error"] = {
                ("note" if type(key) is str and key == "message" else key): value
                for key, value in dict.items(error)
            }


def test_key_renaming_binding_turns_the_same_hostile_battery_red():
    with pytest.raises(AssertionError):
        _hostile((_key_renaming_classify, _reference.ErrorsError))


# -- acceptance on the current binding --------------------------------------------


def test_closed_corpus_is_manifest_bound():
    _fixture._check(CASES)


def test_acceptance_battery_green_on_current_binding():
    _acceptance(PRODUCTION_BINDING)


# -- black-box behavioral mutants ---------------------------------------------------
# The SAME battery executes against each faulty binding; a kill is any red
# executed check, never a source-text or fixture-shape assertion.


def _always_success(source, status, payload, operation=None):
    return {"kind": "success"}


def _always_refuse(source, status, payload, operation=None):
    raise _reference.ErrorsError("refused")


def _convolved_success(source, status, payload, operation=None):
    try:
        return _reference.classify(source, status, payload, operation)
    except _reference.ErrorsError:
        return {"kind": "success"}


def _convolved_error(source, status, payload, operation=None):
    try:
        return _reference.classify(source, status, payload, operation)
    except _reference.ErrorsError:
        return {"kind": "error", "code": "internal", "message": "convolved", "retryable": False}


def _leaked_context(source, status, payload, operation=None):
    try:
        return _reference.classify(source, status, payload, operation)
    except _reference.ErrorsError as error:
        raise _reference.ErrorsError(str(error)) from error


class _WrongClass(Exception):
    def __init__(self, message):
        super().__init__(message)
        self.failure_class = "error_mismatch"
        self.code = "malformed_request"
        self.retryable = False


def _wrong_failure_class(source, status, payload, operation=None):
    try:
        return _reference.classify(source, status, payload, operation)
    except _reference.ErrorsError as error:
        raise _WrongClass(str(error)) from None


def _plain_runtime_error(source, status, payload, operation=None):
    try:
        return _reference.classify(source, status, payload, operation)
    except _reference.ErrorsError as error:
        raise RuntimeError(str(error)) from None


def _impure_payload(source, status, payload, operation=None):
    result = _reference.classify(source, status, payload, operation)
    if type(payload) is dict:
        payload.pop("error", None)
    return result


def _impure_source(source, status, payload, operation=None):
    result = _reference.classify(source, status, payload, operation)
    if type(source) is dict:
        source.pop("areas", None)
    return result


def _make_nondeterministic():
    state = {"flip": False}

    def classify(source, status, payload, operation=None):
        result = _reference.classify(source, status, payload, operation)
        state["flip"] = not state["flip"]
        if state["flip"] and type(result) is dict and result["kind"] == "error":
            return {"kind": "success"}
        return result

    return classify


_FROZEN_ERROR = _reference.ErrorsError("reused")


def _reused_error(source, status, payload, operation=None):
    try:
        return _reference.classify(source, status, payload, operation)
    except _reference.ErrorsError:
        raise _FROZEN_ERROR from None


def _make_cached_result():
    cache = {}

    def classify(source, status, payload, operation=None):
        key = (id(source), status, operation, id(payload))
        if key not in cache:
            cache[key] = _reference.classify(source, status, payload, operation)
        return cache[key]

    return classify


def _echoed_secret(source, status, payload, operation=None):
    try:
        return _reference.classify(source, status, payload, operation)
    except _reference.ErrorsError:
        raise _reference.ErrorsError(f"refused payload: {payload!r}") from None


def _clamped_status(source, status, payload, operation=None):
    if type(status) is not int:
        status = int(status)
    return _reference.classify(source, status, payload, operation)


def _user_code_iterating(source, status, payload, operation=None):
    """A faulty binding: discriminates with isinstance instead of an exact
    type check, so a dict subclass enters and its user-defined __iter__
    runs, then delegates to the reference. Clean exact-dict corpus rows
    pass it; only the hostile gate catches the executed user code."""
    if isinstance(payload, dict):
        for _ignored in payload:
            pass
    return _reference.classify(source, status, payload, operation)


MUTANTS = {
    "permissive-classifier": (_always_success, _reference.ErrorsError),
    "pessimistic-classifier": (_always_refuse, _reference.ErrorsError),
    "refusals-convolved-to-success": (_convolved_success, _reference.ErrorsError),
    "refusals-convolved-to-error-verdict": (_convolved_error, _reference.ErrorsError),
    "leaked-error-context": (_leaked_context, _reference.ErrorsError),
    "wrong-failure-class": (_wrong_failure_class, _WrongClass),
    "untyped-plain-error": (_plain_runtime_error, RuntimeError),
    "impure-payload": (_impure_payload, _reference.ErrorsError),
    "impure-source": (_impure_source, _reference.ErrorsError),
    "nondeterministic-classifier": (_make_nondeterministic(), _reference.ErrorsError),
    "reused-error-instance": (_reused_error, _reference.ErrorsError),
    "cached-result-object": (_make_cached_result(), _reference.ErrorsError),
    "echoed-payload-content": (_echoed_secret, _reference.ErrorsError),
    "status-type-clamped": (_clamped_status, _reference.ErrorsError),
    "user-code-dict-iter": (_user_code_iterating, _reference.ErrorsError),
}


def _battery_red(binding):
    """True when any executed check of the full acceptance battery disagrees
    with the binding under test - the same _acceptance the green gate runs,
    so a fault caught only by an oracle (purity, precedence, executed user
    code) is just as red as a corpus mismatch. A binding that raises its own
    error type on a verdict row, or one that crashes, is red too: every kill
    still comes from an executed battery check, never a source-text
    assertion."""
    try:
        _acceptance(binding)
    except BaseException:  # noqa: BLE001
        return True
    return False


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_behavioral_mutants_turn_the_battery_red(name):
    assert _battery_red(MUTANTS[name]), name


def test_missing_production_binding_is_demonstrably_red():
    def not_implemented(_source, _status, _payload, _operation=None):
        raise NotImplementedError("T0449 has not supplied a classifier")

    with pytest.raises(NotImplementedError):
        _acceptance((not_implemented, _reference.ErrorsError))


# Reference source-edit mutants: the same executed battery kills each faulty
# classifier; the strictly equivalent edits keep it green.


@pytest.mark.parametrize("name", sorted(_reference.REFERENCE_MUTANTS))
def test_reference_source_mutants_turn_the_battery_red(name):
    assert _battery_red((_reference._mutant(name), _reference.ErrorsError)), name


@pytest.mark.parametrize("name", sorted(_fixture.EQUIVALENT_EDITS))
def test_reference_equivalent_edits_keep_the_battery_green(name):
    _acceptance((_fixture._equivalent_classify(name), _reference.ErrorsError))
