"""T0178 permanent red battery for graph-diff implementations."""

from __future__ import annotations

import copy

from tests.test_t0176_diff_contract import (
    AFTER_E4,
    K1,
    STARTPOS,
    DiffEngine,
    DiffError,
    _node,
    _state,
    state_id,
)


def _states():
    a = _node(STARTPOS)
    b = _node(AFTER_E4)
    twin = _node(STARTPOS, K1)
    return _state(a), _state(a, b), _state(twin)


def _probe(cls):
    failures = []
    for name, check in {
        "mixed-roundtrip": _mixed_roundtrip,
        "whole-base": _whole_base,
        "target-id": _target_id,
        "added-conflict": _added_conflict,
        "changed-witness": _changed_witness,
        "rollback": _rollback,
    }.items():
        try:
            if not check(cls):
                failures.append(name)
        except BaseException as error:
            failures.append(f"{name}:{type(error).__name__}")
    return failures


def _mixed_roundtrip(cls):
    base, target, _ = _states()
    engine = cls()
    diff = engine.compute(base, target)
    return engine.apply(diff, base) == target and base != target


def _whole_base(cls):
    base, target, _ = _states()
    engine = cls()
    diff = engine.compute(base, target)
    tampered = copy.deepcopy(base)
    key = next(iter(tampered))
    tampered[key]["digest"] = K1
    try:
        engine.apply(diff, tampered)
    except DiffError as error:
        return error.failure_class == "conflicting_base"
    return False


def _target_id(cls):
    base, target, _ = _states()
    engine = cls()
    diff = engine.compute(base, target)
    diff["target_id"] = "gs1:" + "f" * 64
    try:
        engine.apply(diff, base)
    except DiffError as error:
        return error.failure_class == "divergent_target"
    return False


def _added_conflict(cls):
    base, target, _ = _states()
    engine = cls()
    diff = engine.compute(base, target)
    polluted = copy.deepcopy(base)
    polluted.update(copy.deepcopy(diff["added"]))
    diff["base_id"] = state_id(polluted)
    diff["target_id"] = "gs1:" + "d" * 64
    try:
        engine.apply(diff, polluted)
    except DiffError as error:
        return error.failure_class == "conflicting_base"
    return False


def _changed_witness(cls):
    base, _, twin = _states()
    engine = cls()
    diff = engine.compute(base, twin)
    return (
        bool(diff["changed"])
        and not diff["added"]
        and not diff["removed"]
        and engine.apply(diff, base) == twin
    )


def _rollback(cls):
    base, target, _ = _states()
    engine = cls()
    diff = engine.compute(base, target)
    diff["target_id"] = "gs1:" + "e" * 64
    before = copy.deepcopy(base)
    try:
        engine.apply(diff, base)
    except DiffError:
        return base == before
    return False


class OmitsAdded(DiffEngine):
    def compute(self, base, target):
        out = super().compute(base, target)
        out["added"] = {}
        return out


class TrustsCallerBaseId(DiffEngine):
    def apply(self, diff, base):
        patched = copy.deepcopy(diff)
        patched["base_id"] = state_id(base)
        return super().apply(patched, base)


class IgnoresTargetId(DiffEngine):
    def apply(self, diff, base):
        out = copy.deepcopy(diff)
        staged = copy.deepcopy(base)
        for key in out["removed"]:
            staged.pop(key, None)
        for key, witness in out["changed"].items():
            staged[key] = copy.deepcopy(witness["target"])
        staged.update(copy.deepcopy(out["added"]))
        out["target_id"] = state_id(staged)
        return super().apply(out, base)


class OverwritesAdded(DiffEngine):
    def apply(self, diff, base):
        if set(diff["added"]) & set(base):
            clean = copy.deepcopy(base)
            for key in diff["added"]:
                clean.pop(key, None)
            patched = copy.deepcopy(diff)
            patched["base_id"] = state_id(clean)
            staged = copy.deepcopy(clean)
            staged.update(copy.deepcopy(patched["added"]))
            patched["target_id"] = state_id(staged)
            return super().apply(patched, clean)
        return super().apply(diff, base)


class TreatsChangedAsAdded(DiffEngine):
    def compute(self, base, target):
        out = super().compute(base, target)
        for key, witness in list(out["changed"].items()):
            out["added"][key] = witness["target"]
        out["changed"] = {}
        return out


class MutatesBeforeValidation(DiffEngine):
    def apply(self, diff, base):
        if base:
            base.pop(next(iter(base)))
        return super().apply(diff, base)


MUTANTS = {
    "omits-added": OmitsAdded,
    "trusts-caller-base-id": TrustsCallerBaseId,
    "ignores-target-id": IgnoresTargetId,
    "overwrites-added": OverwritesAdded,
    "changed-as-added": TreatsChangedAsAdded,
    "mutates-before-validation": MutatesBeforeValidation,
}


def test_honest_engine_passes_probe():
    assert _probe(DiffEngine) == []


def test_every_mutant_is_red():
    for name, mutant in MUTANTS.items():
        assert _probe(mutant), f"{name}: mutant passed all probes"
