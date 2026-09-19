"""T0043 red suite: every T0042 fixture case executed against the
variant runtime (tools/variant_runtime - the T0044 deliverable).

NEVER collected by the default gate: this filename intentionally does
not match the python_files pattern (test_*.py), so directory discovery
skips it; tests/test_t0043_variant_red.py runs it by explicit path and
asserts the red signature. When tools/variant_runtime lands, these
tests must turn green and the T0043 harness flips in the same PR.

Runtime API under test (T0044 target, derived from the T0041 contract):
- parse_position(variant: str, fen: str) -> position record
- identity(record) -> exact canonical five-field dict
- VariantError(Exception) with .code (closed error enum) and
  .failure_class (FEN failure class) attributes
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "variant" / "cases.json").read_text())
HAPPY = CASES["happy"]
BOUNDARY = CASES["boundary"]
MALFORMED = CASES["malformed"]
ROLLBACK = CASES["rollback"]

DEFERRED = [c for c in BOUNDARY if "deferred" in c]
ACTIVE_BOUNDARY = [c for c in BOUNDARY if "deferred" not in c]
UNKNOWN_VARIANT = [c for c in ROLLBACK if c["kind"] == "unknown-variant"]
ADDITIVE = [c for c in ROLLBACK if c["kind"] == "additive-fields"]


def _rt():
    return importlib.import_module("tools.variant_runtime")


@pytest.mark.parametrize("case", HAPPY, ids=[c["name"] for c in HAPPY])
def test_happy(case):
    rt = _rt()
    record = rt.parse_position(case["variant"], case["fen"])
    assert rt.identity(record) == case["expect_identity"]


@pytest.mark.parametrize("case", ACTIVE_BOUNDARY, ids=[c["name"] for c in ACTIVE_BOUNDARY])
def test_boundary(case):
    rt = _rt()
    ident_a, ident_b = (
        rt.identity(rt.parse_position(r["variant"], r["fen"])) for r in case["pair"]
    )
    if case["expect"] == "identical":
        assert ident_a == ident_b
    else:
        diffs = {f for f in ident_a if ident_a[f] != ident_b[f]}
        assert diffs == set(case["changed_fields"])


@pytest.mark.parametrize("case", DEFERRED, ids=[c["name"] for c in DEFERRED])
def test_boundary_deferred(case):
    pytest.skip(f"deferred ({case['deferred']['hook']}): {case['deferred']['reason']}")


@pytest.mark.parametrize("case", MALFORMED, ids=[c["name"] for c in MALFORMED])
def test_malformed(case):
    rt = _rt()
    with pytest.raises(rt.VariantError) as excinfo:
        rt.parse_position("standard", case["fen"])
    assert excinfo.value.failure_class == case["expect_failure"]


@pytest.mark.parametrize("case", UNKNOWN_VARIANT, ids=[c["name"] for c in UNKNOWN_VARIANT])
def test_rollback_unknown_variant(case):
    rt = _rt()
    with pytest.raises(rt.VariantError) as excinfo:
        rt.parse_position(case["input"]["variant"], case["input"]["fen"])
    assert excinfo.value.code == case["expect_error_code"]


@pytest.mark.parametrize("case", ADDITIVE, ids=[c["name"] for c in ADDITIVE])
def test_rollback_additive_fields(case):
    rt = _rt()
    base = next(h for h in HAPPY if h["name"] == case["base_case"])
    augmented = dict(base["expect_identity"])
    augmented.update(case["extra_fields"])
    assert rt.identity(augmented) == base["expect_identity"]
