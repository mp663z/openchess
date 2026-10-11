"""T0395 production redaction runtime proof.

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

from desktop.privacy import redaction as production
from tests import test_t0394_redaction_red as red

ROOT = Path(__file__).resolve().parents[1]


def test_live_binding_and_green_corpus():
    assert red.BINDING[1] is production.RedactionError
    assert production.RedactionError is not red.ref.RedactionError
    assert red.evidence(*red.BINDING) == ([], [])


def test_independent_production_has_no_test_imports_or_dynamic_execution():
    source = (ROOT / "desktop/privacy/redaction.py").read_text()
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
        with pytest.raises(production.RedactionError) as caught:
            red.execute(context, step["op"], rq)
        errors.append(caught.value)
    assert errors[0] is not errors[1]
    assert rq == snap


def test_production_retains_no_request_or_secret_in_mutable_globals_or_closures():
    # Implementation-specific structural proof, not a claim about arbitrary red bindings.
    assert production.redact.__closure__ is None and production.emit_text.__closure__ is None
    mutable = {
        k: v
        for k, v in vars(production).items()
        if type(v) in (dict, list, set) and k != "__builtins__"
    }
    snapshot = copy.deepcopy(mutable)
    rq = {"text": "sensitive fixture value", "secrets": ["fixture"]}
    assert production.redact(rq) == "redacted"
    assert production.emit_text(rq) == "redacted"
    assert mutable == snapshot
    for value in mutable.values():
        assert not (red.containers(value) & red.containers(rq))
    for fn in (production.redact, production.emit_text):
        assert vars(fn) == {} and fn.__defaults__ is None
    # Helpers contain no global/nonlocal assignment, attribute write or cache append.
    tree = ast.parse((ROOT / "desktop/privacy/redaction.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                assert not isinstance(child, (ast.Global, ast.Nonlocal))
                if isinstance(child, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    assert all(isinstance(target, (ast.Name, ast.Tuple)) for target in targets)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    assert child.func.attr not in (
                        "append",
                        "extend",
                        "update",
                        "add",
                        "setdefault",
                    )
