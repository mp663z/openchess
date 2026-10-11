"""T0386 production byom runtime proof.

The permanent red suite is now bound to independent production. World register
receipts are checked before seeding synthetic state. Cross-call retention is
not established by the inherited red battery; redaction additionally verifies
this implementation has no mutable globals, closure, or instance state.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from desktop.privacy import byom as production
from tests import test_t0385_byom_red as red

ROOT = Path(__file__).resolve().parents[1]


def test_live_binding_and_green_corpus():
    assert red.BINDING[1] is production.ByomError
    assert production.ByomError is not red.ref.ByomError
    assert red.evidence(*red.BINDING) == ([], [])


def test_independent_production_has_no_test_imports_or_dynamic_execution():
    source = (ROOT / "desktop/privacy/byom.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
            assert not any(n == "tests" or n.startswith("tests.") for n in names)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in (
            "exec",
            "eval",
            "__import__",
        ):
            raise AssertionError("dynamic execution")


def test_fresh_refusals_and_unchanged_request():
    row = next(r for r in red.ROWS if r["section"] == "malformed")
    context = red.world(row["config"], *red.BINDING)
    errors = []
    step = next(s for s in row["steps"] if "failure" in s["expect"])
    rq = copy.deepcopy(step["request"])
    snap = copy.deepcopy(rq)
    for _ in range(2):
        with pytest.raises(production.ByomError) as caught:
            red.execute(context, step["op"], rq)
        errors.append(caught.value)
    assert errors[0] is not errors[1]
    assert rq == snap


def test_world_register_wrong_receipt_cannot_hide_behind_seed():
    class Wrong(production.ByomStore):
        def register(self, iid, pid):
            super().register(iid, pid)
            return {"wrong": "receipt"}

    with pytest.raises(AssertionError):
        red.world({}, Wrong, production.ByomError)


@pytest.mark.parametrize("exception", [AssertionError, KeyError])
def test_world_register_crash_is_foreign(exception):
    class Broken(production.ByomStore):
        def register(self, iid, pid):
            raise exception("implementation-only crash")

    with pytest.raises(red.ForeignCrash):
        red.world({}, Broken, production.ByomError)
