"""Reject test-package and dynamic-execution dependencies in property modules."""

from __future__ import annotations

import ast
from pathlib import Path

PROTECTED_PATHS = (Path("tests/test_t0180_diff_properties.py"),)
_FORBIDDEN_API_NAMES = {"eval", "exec", "__import__", "import_module"}
_DYNAMIC_MODULES = {"builtins", "importlib"}


class DependencyError(Exception):
    pass


def _is_forbidden_module_lookup(node, module_aliases):
    """Return whether an expression references a forbidden dynamic API."""
    if isinstance(node, ast.Attribute):
        if node.attr in _FORBIDDEN_API_NAMES:
            return True
        return (
            node.attr == "__dict__"
            and isinstance(node.value, ast.Name)
            and node.value.id in module_aliases
        )
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in module_aliases
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _FORBIDDEN_API_NAMES
        )
    if isinstance(node, ast.Subscript):
        key = node.slice
        return (
            _is_forbidden_module_lookup(node.value, module_aliases)
            and isinstance(key, ast.Constant)
            and key.value in _FORBIDDEN_API_NAMES
        )
    return False


def findings(source: str):
    tree = ast.parse(source)
    module_aliases = set(_DYNAMIC_MODULES)
    api_aliases = set(_FORBIDDEN_API_NAMES)
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tests" or alias.name.startswith("tests."):
                    found.append(node)
                if alias.name in _DYNAMIC_MODULES:
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "tests" or module.startswith("tests."):
                found.append(node)
            if module in _DYNAMIC_MODULES:
                for alias in node.names:
                    if alias.name in _FORBIDDEN_API_NAMES:
                        api_aliases.add(alias.asname or alias.name)
                        found.append(node)

    # Reject references, not just calls. Once a forbidden API cannot be named or
    # retrieved, lambda, partial, attribute, container, and callable indirection
    # cannot launder it past this static boundary.
    for node in ast.walk(tree):
        named_api = isinstance(node, ast.Name) and node.id in api_aliases
        if named_api or _is_forbidden_module_lookup(node, module_aliases):
            found.append(node)
    return found


def lint(path: Path):
    hits = findings(path.read_text())
    if hits:
        raise DependencyError(f"{path}: forbidden test or dynamic dependency")


def lint_protected_paths(root: Path = Path(".")):
    for relative_path in PROTECTED_PATHS:
        lint(root / relative_path)


if __name__ == "__main__":
    lint_protected_paths()
