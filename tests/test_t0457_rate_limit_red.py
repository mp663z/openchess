"""T0457: executable rate-limit acceptance and red-test battery.

One binding to the independent T0458 production decision. The T0455
test-only synthetic decision remains the mutant baseline, not the shipped
implementation. The T0456 closed corpus owns hand-authored verdicts and row
manifests. Both green and red gates use the same corpus and hostile probes.
This binds production admission logic, but does not claim live middleware,
normalization policy or an atomic persistence/identity-effect integration.
"""

from __future__ import annotations

import copy

import pytest

from server import identity_admission as production
from tests import test_t0455_rate_limit_contract as reference
from tests import test_t0456_rate_limit_fixture as fixture

# Single rebinding point for T0458. The error type is part of this binding.
BASE_DECISION = reference.decision
PRODUCTION_BINDING = (production.decide, production.AdmissionRefusal)


def _run(binding):
    """Executed acceptance, including every row/probe, on a clean corpus.

    The fixture has a late-bound decision hook. Deep-copying the corpus and
    rebuilding probes isolates later faulty bindings that mutate their input;
    no mutant may change the expectations of the next binding.
    """
    decision, error_type = binding
    original = fixture.CASES
    original_fields = fixture._FIELD_PROBES
    original_states = fixture._STATE_PROBES
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(fixture, "CASES", copy.deepcopy(original))
        patch.setattr(fixture, "_FIELD_PROBES", fixture._field_probes())
        patch.setattr(fixture, "_STATE_PROBES", fixture._state_probes())
        patch.setattr(fixture._REF, "decision", decision)
        patch.setattr(fixture._REF, "Refusal", error_type)
        red = fixture._fixture_red()
    assert fixture.CASES is original
    assert fixture._FIELD_PROBES is original_fields
    assert fixture._STATE_PROBES is original_states
    return red


def _acceptance(binding):
    assert _run(binding) == []


def _row(section, name):
    return next(row for row in fixture.CASES[section] if row["name"] == name)


def test_closed_corpus_and_coverage_pins():
    fixture._check(fixture.CASES)
    assert {section: len(fixture.CASES[section]) for section in fixture.SECTIONS} == {
        "happy": 12,
        "boundary": 16,
        "malformed": 50,
        "rollback": 10,
    }
    # Keep named witnesses tied to the closed manifest, not a bare row count.
    for section, name in (
        ("happy", "admit-first-empty-login"),
        ("happy", "replay-cached-identical"),
        ("boundary", "deny-second-bucket-leaves-first-uncharged"),
        ("boundary", "admit-at-exact-window-start"),
        ("malformed", "body-same-nonbool-int-cached"),
        ("malformed", "conflict-with-malformed-operation"),
        ("malformed", "store-unavailable-at-full-bucket"),
        ("rollback", "effect-failure-then-success-chain"),
        ("rollback", "replay-never-double-charges-chain"),
    ):
        row = _row(section, name)
        assert (
            fixture.CASES["section_manifests"][section][name]
            == fixture.hashlib.sha256(fixture._canon(row)).hexdigest()
        )


def test_full_acceptance_green_on_bound_decision():
    _acceptance(PRODUCTION_BINDING)


def test_declared_reference_gaps_are_executed():
    """Reference behavior to preserve, not a decision about live normalizers."""
    assert "probe:opaque-key-lone-surrogate" not in _run(PRODUCTION_BINDING)
    assert "probe:replay-skips-validation" not in _run(PRODUCTION_BINDING)
    assert "malformed:body-same-nonbool-int-cached" not in _run(PRODUCTION_BINDING)


def _always_admit(**kwargs):
    return "admit", copy.deepcopy(kwargs["state"])


def _always_refuse(**_kwargs):
    raise reference.Refusal("rate_limited")


def _convert_refusal(code):
    def faulty(**kwargs):
        try:
            return BASE_DECISION(**kwargs)
        except reference.Refusal:
            raise reference.Refusal(code) from None

    return faulty


def _shallow_admit(**kwargs):
    result, state = BASE_DECISION(**kwargs)
    if result == "admit":
        return result, kwargs["state"]
    return result, state


def _shallow_replay(**kwargs):
    result, state = BASE_DECISION(**kwargs)
    if result == "replay":
        return result, kwargs["state"]
    return result, state


def _charge_on_denial(**kwargs):
    try:
        return BASE_DECISION(**kwargs)
    except reference.Refusal as exc:
        if exc.code == "rate_limited":
            kwargs["state"][("identity.login", "account", "opaque:account")] = (0, 999)
        raise


def _poison_on_failure(**kwargs):
    try:
        return BASE_DECISION(**kwargs)
    except reference.Refusal as exc:
        if exc.code == "internal" and type(kwargs["state"]) is dict:
            kwargs["state"].clear()
        raise


def _ignore_store(**kwargs):
    return BASE_DECISION(**{**kwargs, "store_available": True})


def _ignore_effect(**kwargs):
    return BASE_DECISION(**{**kwargs, "effect_ok": True})


def _clamp_clock(**kwargs):
    now = kwargs["now"]
    if type(now) is int and now < 0:
        kwargs = {**kwargs, "now": 0}
    return BASE_DECISION(**kwargs)


def _coerce_cached_body(**kwargs):
    if kwargs["replay"] == "cached":
        kwargs = {**kwargs, "body_same": bool(kwargs["body_same"])}
    return BASE_DECISION(**kwargs)


def _validate_before_replay(**kwargs):
    if (
        kwargs["replay"] == "cached"
        and kwargs["body_same"] is True
        and (type(kwargs["operation"]) is not str or type(kwargs["policy"]) is not dict)
    ):
        raise reference.Refusal("malformed_request")
    return BASE_DECISION(**kwargs)


def _no_surrogate(**kwargs):
    if type(kwargs["account"]) is str and "\ud800" in kwargs["account"]:
        raise reference.Refusal("malformed_request")
    return BASE_DECISION(**kwargs)


def _shared_operation_bucket(**kwargs):
    if kwargs["operation"] == "identity.register":
        # Only internal bucket keys change, leaving the outward operation alone.
        state = kwargs["state"]
        shared = {
            ("identity.register", scope, opaque): value
            for (op, scope, opaque), value in state.items()
            if op == "identity.login"
        }
        if shared:
            return BASE_DECISION(**{**kwargs, "state": shared})
    return BASE_DECISION(**kwargs)


def _source_first_only(**kwargs):
    if kwargs["replay"] == "new" and type(kwargs["state"]) is dict:
        account_key = (kwargs["operation"], "account", kwargs["account"])
        state = {k: v for k, v in kwargs["state"].items() if k != account_key}
        result, new = BASE_DECISION(**{**kwargs, "state": state})
        if account_key in kwargs["state"]:
            new[account_key] = kwargs["state"][account_key]
        return result, new
    return BASE_DECISION(**kwargs)


# Witnesses are executed fixture labels. These deliberately faulty bindings
# must disagree on their named case, not merely crash during setup.
MUTANTS = {
    "always-admit": (_always_admit, "boundary:deny-account-at-capacity"),
    "always-refuse": (_always_refuse, "happy:admit-first-empty-login"),
    "convolve-all-refusals": (_convert_refusal("internal"), "malformed:now-negative"),
    "wrong-error-code": (_convert_refusal("rate_limited"), "malformed:now-negative"),
    "admit-result-alias": (_shallow_admit, "happy:admit-first-empty-login"),
    "replay-result-alias": (_shallow_replay, "happy:replay-cached-identical"),
    "deny-charges-caller": (_charge_on_denial, "boundary:deny-source-at-capacity"),
    "failure-poisons-caller": (_poison_on_failure, "malformed:store-unavailable-at-full-bucket"),
    "store-outage-ignored": (_ignore_store, "malformed:store-unavailable"),
    "effect-failure-ignored": (_ignore_effect, "malformed:effect-failure"),
    "negative-clock-clamped": (_clamp_clock, "malformed:now-negative"),
    "cached-body-coerced": (_coerce_cached_body, "malformed:body-same-nonbool-int-cached"),
    "replay-validates-request": (_validate_before_replay, "probe:replay-skips-validation"),
    "surrogate-rejected": (_no_surrogate, "probe:opaque-key-lone-surrogate"),
    "operation-buckets-shared": (
        _shared_operation_bucket,
        "happy:admit-other-operation-own-buckets",
    ),
    "account-bucket-skipped": (_source_first_only, "boundary:deny-account-at-capacity"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_black_box_mutants_turn_same_battery_red(name):
    faulty, witness = MUTANTS[name]
    red = _run((faulty, reference.Refusal))
    assert witness in red, (name, red)


@pytest.mark.parametrize("name", sorted(fixture.REFERENCE_MUTANTS))
def test_source_faults_turn_same_battery_red(name):
    before, after, _why = fixture.REFERENCE_MUTANTS[name]
    mutant = fixture._rebind(before, after, name)
    assert _run((mutant, reference.Refusal)), name


@pytest.mark.parametrize("name", sorted(fixture.EQUIVALENT_EDITS))
def test_equivalent_source_edits_keep_battery_green(name):
    before, after = fixture.EQUIVALENT_EDITS[name]
    _acceptance((fixture._rebind(before, after, name), reference.Refusal))


def test_unimplemented_binding_is_demonstrably_red():
    def missing(**_kwargs):
        raise NotImplementedError("T0458 not implemented")

    assert "happy:admit-first-empty-login" in _run((missing, reference.Refusal))


class _EqualCached:
    def __eq__(self, other):
        return other == "cached"


class _CachedSubclass(str):
    pass


class _Truthy:
    def __bool__(self):
        return True


class _ExplosiveBool:
    def __bool__(self):
        raise AssertionError("hostile operator executed")


@pytest.mark.parametrize("replay", [_EqualCached(), _CachedSubclass("cached")])
def test_production_replay_token_rejects_hostile_equality(replay):
    decision, refusal = PRODUCTION_BINDING
    args = reference.params(replay=replay, operation=object())
    with pytest.raises(refusal) as caught:
        decision(**args)
    assert type(caught.value) is refusal
    assert caught.value.code == "malformed_request"
    assert args["state"] == {}


@pytest.mark.parametrize("flag", ["store_available", "effect_ok"])
@pytest.mark.parametrize("value", [_Truthy(), _ExplosiveBool(), 1])
def test_production_internal_flags_require_exact_bool(flag, value):
    decision, refusal = PRODUCTION_BINDING
    args = reference.params(**{flag: value})
    with pytest.raises(refusal) as caught:
        decision(**args)
    assert type(caught.value) is refusal
    assert caught.value.code == "internal"
    assert args["state"] == {}
