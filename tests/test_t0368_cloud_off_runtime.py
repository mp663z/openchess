"""0368 production privacy runtime proof.

Only BINDING changes in the landed permanent red battery. Its full pinned
and hostile corpus now drives production. The reference remains available
only for synthetic mutant controls; production never imports test code.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from desktop.privacy import cloud_off as production
from tests import test_t0367_cloud_off_red as red

ROOT = Path(__file__).resolve().parents[1]
ORACLE = "BINDING = (fx.world, fx.CloudError)"
REBOUND = 'BINDING = (\n    lambda: __import__("desktop.privacy.cloud_off", fromlist=["CloudSwitch"]).CloudSwitch(\n        __import__("desktop.privacy.sensitive_flag", fromlist=["FlagStore"]).FlagStore()\n    ),\n    __import__("desktop.privacy.cloud_off", fromlist=["CloudError"]).CloudError,\n)'  # noqa: E501 - exact landed binding
LANDED_SHA256 = "a372ee4839628223bf9a6be4ee8e3405f084e5d6543da60762270806624156dd"


def test_rebinding_is_the_only_red_battery_change():
    text = (ROOT / "tests" / "test_t0367_cloud_off_red.py").read_text()
    assert text.count(REBOUND) == 1
    assert hashlib.sha256(text.replace(REBOUND, ORACLE).encode()).hexdigest() == LANDED_SHA256


def test_production_is_independent_and_binding_is_live():
    assert red.BINDING[1] is production.CloudError
    assert red.BINDING[1] is not red.fx.CloudError
    store = red.BINDING[0]()
    assert type(store) is production.CloudSwitch
    source = (ROOT / "desktop/privacy/cloud_off.py").read_text()
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
