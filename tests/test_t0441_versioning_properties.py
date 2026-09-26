"""Seeded contract-derived properties for the shipped version comparator.

Expectations are specified by the versioning contract, not by calling the
T0437 comparator or using production results as an oracle. T0419's strict
source derivation is used only to attest generated source validity.
"""

from __future__ import annotations

import copy
import random
from pathlib import Path

import pytest
import yaml

from server.control_plane_versioning import VersionError, compare
from tests.test_t0419_openapi_contract import derive as strict_source_derivation

SOURCE = Path(__file__).resolve().parents[1] / "data/contracts/control-plane.yaml"
SEEDS = tuple(range(32))
CEILING = 2147483647


def source():
    return yaml.safe_load(SOURCE.read_text())


def valid(doc):
    assert strict_source_derivation(doc)["openapi"] == "3.1.0"


def verdict(old, new, older, newer, expected):
    before = copy.deepcopy((old, new))
    valid(old)
    valid(new)
    for _ in range(2):
        result = compare(old, new, older, newer)
        assert type(result) is bool and result is expected
        assert (old, new) == before


def refused(old, new, older, newer):
    before = copy.deepcopy((old, new))
    errors = []
    for _ in range(2):
        with pytest.raises(VersionError) as caught:
            compare(old, new, older, newer)
        error = caught.value
        assert type(error) is VersionError
        assert (error.failure_class, error.code, error.retryable) == (
            "malformed_version_request",
            "malformed_request",
            False,
        )
        assert error.__cause__ is None and error.__context__ is None
        errors.append(error)
    assert errors[0] is not errors[1]
    assert (old, new) == before


def target_fields(doc, randomizer, side):
    choices = [
        op[side]["fields"]
        for area in doc["areas"].values()
        for op in area["ops"].values()
        if op["method"] != "GET" or side == "response"
    ]
    fields = randomizer.choice(choices)
    objects = [spec["fields"] for spec in fields.values() if spec["type"] == "object"]
    if objects and randomizer.randrange(2):
        fields = randomizer.choice(objects)
    return fields


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_optional_additions_and_requiredness(seed):
    randomizer = random.Random(seed)
    old, new = source(), source()
    side = randomizer.choice(("request", "response"))
    fields = target_fields(new, randomizer, side)
    name = f"extension_{seed}"
    fields[name] = {"type": randomizer.choice(("string", "boolean", "integer")), "required": False}
    minor = randomizer.choice((0, 1, 17, CEILING - 1))
    verdict(old, new, minor, CEILING, True)
    # Adding a required field is a valid source but not an additive MINOR.
    fields[name]["required"] = True
    verdict(old, new, minor, CEILING, False)
    new["contract"]["versioning"]["base_path"] = "/cp/v2"
    verdict(old, new, CEILING, 0, True)


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_operation_areas_and_set_like_allowlists(seed):
    randomizer = random.Random(seed + 77000)
    old, new = source(), source()
    suffix = chr(ord("a") + seed // 26) + chr(ord("a") + seed % 26)
    area = f"extension_{suffix}"
    method = randomizer.choice(("GET", "POST"))
    auth = randomizer.choice(("public", "required"))
    mutating = method == "POST" and bool(randomizer.randrange(2))
    name = f"{area}.read"
    new["areas"][area] = {
        "ops": {
            "read": {
                "method": method,
                "path": f"/extension-{seed}",
                "auth": auth,
                "mutating": mutating,
                "request": {"fields": {}},
                "response": {"fields": {}},
                "errors": ["internal"],
            }
        }
    }
    transport = new["contract"]["transport"]
    if auth == "public":
        transport["auth"]["public_operations"].insert(0, name)
    if not mutating:
        transport["read_only_operations"].insert(0, name)
    randomizer.shuffle(transport["auth"]["public_operations"])
    randomizer.shuffle(transport["read_only_operations"])
    verdict(old, new, 0, randomizer.randrange(CEILING + 1), True)
    # Existing operation removal is not a MINOR; source is still valid.
    verdict(new, old, 0, 1, False)


@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_breaking_existing_declarations(seed):
    randomizer = random.Random(seed + 55000)
    old, new = source(), source()
    choice = randomizer.randrange(4)
    if choice == 0:
        new["contract"]["privacy"]["logs"] = f"changed-{seed}"
    elif choice == 1:
        new["areas"]["identity"]["ops"]["register"]["path"] = f"/register-{seed}"
    elif choice == 2:
        new["areas"]["identity"]["ops"]["register"]["request"]["fields"]["email"]["type"] = (
            "integer"
        )
        new["areas"]["identity"]["ops"]["register"]["request"]["fields"]["email"].pop("example")
    else:
        fields = new["areas"]["identity"]["ops"]["register"]["response"]["fields"]
        fields.pop(randomizer.choice(("display_name", "account_id")))
    verdict(old, new, 0, 1, False)
    new["contract"]["versioning"]["base_path"] = "/cp/v2"
    verdict(old, new, CEILING, 0, True)


@pytest.mark.parametrize("old_minor,new_minor", [(0, 0), (0, 1), (1, CEILING), (CEILING, CEILING)])
def test_identical_bounded_minor_upgrade(old_minor, new_minor):
    doc = source()
    verdict(doc, copy.deepcopy(doc), old_minor, new_minor, True)


@pytest.mark.parametrize(
    "old_minor,new_minor",
    [
        (-1, 0),
        (0, -1),
        (0, CEILING + 1),
        (CEILING + 1, CEILING + 1),
        (1, 0),
        (True, 1),
        (1, False),
        (0.0, 1),
        (0, "1"),
    ],
)
def test_minor_bounds_fail_before_comparison(old_minor, new_minor):
    old, new = source(), source()
    new["contract"]["privacy"]["logs"] = "breaking declaration"
    refused(old, new, old_minor, new_minor)


def test_invalid_other_source_precedes_major_compatibility():
    old, new = source(), source()
    new["contract"]["versioning"]["base_path"] = "/cp/v2"
    old["areas"]["identity"]["ops"]["refresh"]["errors"].append("unknown_code")
    refused(old, new, 0, 0)
    old, new = source(), source()
    new["contract"]["versioning"]["base_path"] = "/cp/v2"
    new["contract"]["transport"]["read_only_operations"].append("identity.login")
    refused(old, new, 0, 0)


def test_alias_cycle_and_deep_metadata_not_conflated():
    old, new = source(), source()
    shared = {"type": "boolean", "required": False}
    for side in ("request", "response"):
        new["areas"]["identity"]["ops"]["register"][side]["fields"]["extra_flag"] = shared
    verdict(old, new, 0, 1, True)
    old, new = source(), source()
    deep = "opaque"
    for _ in range(2200):
        deep = [deep]
    new["contract"]["privacy"]["logs"] = deep
    valid(new)
    assert compare(old, new, 0, 1) is False
    cycle = []
    cycle.append(cycle)
    new["contract"]["privacy"]["logs"] = cycle
    # Do not deepcopy cyclic values via verdict's source-validity assertion.
    with pytest.raises(VersionError):
        compare(old, new, 0, 1)


def test_nan_identity_and_opaque_scalar_type():
    old = source()
    old["contract"]["privacy"]["logs"] = float("nan")
    valid(old)
    assert compare(old, old, 0, 0) is True
    assert compare(old, copy.deepcopy(old), 0, 0) is True
    old, new = source(), source()
    old["contract"]["privacy"]["logs"] = 1
    new["contract"]["privacy"]["logs"] = 1.0
    verdict(old, new, 0, 1, False)


class _Alarm:
    calls = []
    armed = False

    @classmethod
    def trip(cls, name):
        cls.calls.append(name)
        raise AssertionError("user operator called")


class _HostileStr(str):
    def __eq__(self, other):
        _Alarm.trip("str.eq")

    def __hash__(self):
        if _Alarm.armed:
            _Alarm.trip("str.hash")
        return str.__hash__(self)

    def __str__(self):
        _Alarm.trip("str.str")


class _HostileDict(dict):
    def __iter__(self):
        _Alarm.trip("dict.iter")

    def __getitem__(self, item):
        _Alarm.trip("dict.get")


class _HostileList(list):
    def __iter__(self):
        _Alarm.trip("list.iter")

    def __eq__(self, other):
        _Alarm.trip("list.eq")


class _HostileInt(int):
    def __le__(self, other):
        _Alarm.trip("int.le")

    def __eq__(self, other):
        _Alarm.trip("int.eq")


@pytest.mark.parametrize("side", ["old", "new"])
@pytest.mark.parametrize("kind", ["dict", "list", "str", "key", "int", "minor"])
def test_hostile_subclasses_refuse_before_user_operators(side, kind):
    old, new = source(), source()
    doc = old if side == "old" else new
    older, newer = 0, 1
    if kind == "dict":
        doc["contract"]["privacy"]["logs"] = _HostileDict({"plain": 1})
    elif kind == "list":
        doc["contract"]["privacy"]["logs"] = _HostileList([1])
    elif kind == "str":
        doc["contract"]["privacy"]["logs"] = _HostileStr("opaque")
    elif kind == "key":
        hostile_key = _HostileStr("x")
        mapping = {}
        dict.__setitem__(mapping, hostile_key, 1)
        doc["contract"]["privacy"]["logs"] = mapping
    elif kind == "int":
        doc["contract"]["privacy"]["logs"] = _HostileInt(1)
    else:
        older, newer = (_HostileInt(0), 1) if side == "old" else (0, _HostileInt(1))
    _Alarm.calls = []
    _Alarm.armed = True
    try:
        for _ in range(2):
            with pytest.raises(VersionError) as caught:
                compare(old, new, older, newer)
            assert caught.value.failure_class == "malformed_version_request"
            assert caught.value.code == "malformed_request"
            assert caught.value.__context__ is None
        assert _Alarm.calls == []
    finally:
        _Alarm.armed = False


def test_valid_request_survives_malformed_predecessors_without_residue():
    valid_old, valid_new = source(), source()
    valid_new["areas"]["identity"]["ops"]["register"]["response"]["fields"]["new_opt"] = {
        "type": "boolean",
        "required": False,
    }
    for bad_minor in (-1, True, CEILING + 1):
        refused(valid_old, valid_new, bad_minor, 1)
        verdict(valid_old, valid_new, 0, 1, True)


def test_property_suite_catches_own_behavioral_fault_variants(monkeypatch):
    """These are local perturbations of the shipped production seam, not
    T0439 mutants or source-text substitutions. A kill is an executed
    assertion from this suite turning red against the faulty binding.
    """
    import tests.test_t0441_versioning_properties as module

    real_compare = compare
    old, new = source(), source()
    fields = new["areas"]["identity"]["ops"]["register"]["response"]["fields"]
    fields["new_opt"] = {"type": "boolean", "required": False}

    def always_false(*_args):
        return False

    def always_true(*_args):
        return True

    def impure(prior, current, older, newer):
        result = real_compare(prior, current, older, newer)
        current["contract"]["privacy"]["logs"] = "tampered"
        return result

    def skip_minor_validation(prior, current, _older, _newer):
        return real_compare(prior, current, 0, 1)

    variants = (
        (always_false, lambda: verdict(old, new, 0, 1, True)),
        (always_true, lambda: verdict(new, old, 0, 1, False)),
        (impure, lambda: verdict(old, new, 0, 1, True)),
        (skip_minor_validation, lambda: refused(old, new, -1, 1)),
    )
    for faulty, probe in variants:
        with monkeypatch.context() as patch:
            patch.setattr(module, "compare", faulty)
            with pytest.raises(BaseException) as caught:
                probe()
            assert type(caught.value) in (AssertionError, pytest.fail.Exception)
