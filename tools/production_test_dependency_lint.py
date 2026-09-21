"""Reject test-package dependencies in production/property modules."""

from __future__ import annotations

import ast
from pathlib import Path


class DependencyError(Exception):
    pass


def _literal_tests_module(node):
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value == "tests" or node.value.startswith("tests."))
    )


def findings(source: str):
    tree = ast.parse(source)
    aliases = set()
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tests" or alias.name.startswith("tests."):
                    found.append(node)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "tests" or module.startswith("tests."):
                found.append(node)
            if module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Call):
            fn = node.func
            dynamic = (isinstance(fn, ast.Name) and fn.id in {"__import__", *aliases}) or (
                isinstance(fn, ast.Attribute)
                and fn.attr == "import_module"
                and isinstance(fn.value, ast.Name)
            )
            if dynamic:
                if not node.args or not _literal_tests_module(node.args[0]):
                    # Nonliteral dynamic imports cannot prove they avoid tests.*.
                    found.append(node)
                elif _literal_tests_module(node.args[0]):
                    found.append(node)
    return found


def lint(path: Path):
    hits = findings(path.read_text())
    if hits:
        raise DependencyError(f"{path}: forbidden tests-package dependency")
