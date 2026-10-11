"""0377 production privacy runtime proof.

Only BINDING changes in the landed permanent red battery. Its full pinned
and hostile corpus now drives production. The reference remains available
only for synthetic mutant controls; production never imports test code.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from desktop.privacy import local_model as production
from tests import test_t0376_local_model_red as red

ROOT = Path(__file__).resolve().parents[1]
ORACLE = "BINDING = (fx.world, fx.ModelError)"
REBOUND = 'BINDING = (\n    __import__("desktop.privacy.local_model", fromlist=["LocalModels"]).LocalModels,\n    __import__("desktop.privacy.local_model", fromlist=["ModelError"]).ModelError,\n)'  # noqa: E501 - exact landed binding
LANDED_SHA256 = "92e1c7688ae82b75bbc01a744510cdb2431b2c32fe47fbd600d2596959c291e8"


def test_rebinding_is_the_only_red_battery_change():
    text = (ROOT / "tests" / "test_t0376_local_model_red.py").read_text()
    assert text.count(REBOUND) == 1
    assert hashlib.sha256(text.replace(REBOUND, ORACLE).encode()).hexdigest() == LANDED_SHA256


def test_production_is_independent_and_binding_is_live():
    assert red.BINDING[1] is production.ModelError
    assert red.BINDING[1] is not red.fx.ModelError
    store = red.BINDING[0]()
    assert type(store) is production.LocalModels
    source = (ROOT / "desktop/privacy/local_model.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
            )
            assert not any(n == "tests" or n.startswith("tests.") for n in names)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in (
            "exec",
            "eval",
            "__import__",
        ):
            raise AssertionError("dynamic execution in production")
    assert red.evidence(*red.BINDING) == ([], [])


def test_refusal_and_receipt_instances_are_detached():
    factory, error = red.BINDING
    row = next(r for s, r in red.ROWS if s == "rollback")
    store = factory()
    red.setup(store, row["setup"], error)
    errors = []
    for _ in range(2):
        try:
            getattr(store, row["rejected"]["op"])(row["rejected"]["request"])
        except error as exc:
            errors.append(exc)
    assert len(errors) == 2 and errors[0] is not errors[1]
    red.invoke(store, row["follow_up"], error, row["follow_up"]["expect"])
