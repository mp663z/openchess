"""0359 production privacy runtime proof.

Only BINDING changes in the landed permanent red battery. Its full pinned
and hostile corpus now drives production. The reference remains available
only for synthetic mutant controls; production never imports test code.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from desktop.privacy import sensitive_flag as production
from tests import test_t0358_sensitive_flag_red as red

ROOT = Path(__file__).resolve().parents[1]
ORACLE = "BINDING = (fx.FlagStore, fx.FlagError)"
REBOUND = 'BINDING = (\n    __import__("desktop.privacy.sensitive_flag", fromlist=["FlagStore"]).FlagStore,\n    __import__("desktop.privacy.sensitive_flag", fromlist=["FlagError"]).FlagError,\n)'  # noqa: E501 - exact landed binding
LANDED_SHA256 = "2dce22210f07b90af416f8b844711bcae649ddb00ad8c306e40116f3530495df"


def test_rebinding_is_the_only_red_battery_change():
    text = (ROOT / "tests" / "test_t0358_sensitive_flag_red.py").read_text()
    assert text.count(REBOUND) == 1
    assert hashlib.sha256(text.replace(REBOUND, ORACLE).encode()).hexdigest() == LANDED_SHA256


def test_production_is_independent_and_binding_is_live():
    assert red.BINDING[1] is production.FlagError
    assert red.BINDING[1] is not red.fx.FlagError
    store = red.BINDING[0]()
    assert type(store) is production.FlagStore
    source = (ROOT / "desktop/privacy/sensitive_flag.py").read_text()
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
