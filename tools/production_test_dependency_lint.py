"""Fail-closed dependency and capability lint for protected property modules."""

from __future__ import annotations

import ast
from pathlib import Path

PROTECTED_PATHS = (Path("tests/test_t0180_diff_properties.py"),)
_ALLOWED_DIRECT_IMPORTS = {
    "copy": {"deepcopy"},
    "graph.node": {"make_record"},
    "pytest": {"mark", "raises"},
    "random": {"Random"},
}
_ALLOWED_FROM_IMPORTS = {
    "__future__": {"annotations"},
    "graph": {"diff"},
    "graph.node": {"make_record", "record_identity"},
    "pathlib": {"Path"},
    "tools.production_test_dependency_lint": {"findings", "lint"},
    "tools.variant_runtime": {"VariantError"},
}
_FORBIDDEN_NAMES = {
    "__builtins__",
    "__import__",
    "attrgetter",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "import_module",
    "locals",
    "vars",
}
_FORBIDDEN_ATTRIBUTES = {
    "attrgetter",
    "importorskip",
    "main",
    "compile",
    "eval",
    "exec",
    "import_module",
    "vars",
}


class DependencyError(Exception):
    pass


def _is_dunder(value):
    return isinstance(value, str) and value.startswith("__") and value.endswith("__")


def findings(source: str):
    tree = ast.parse(source)
    found = []
    direct_aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                allowed_attributes = _ALLOWED_DIRECT_IMPORTS.get(alias.name)
                if allowed_attributes is None:
                    found.append(node)
                else:
                    bound_name = alias.asname or alias.name.split(".", 1)[0]
                    direct_aliases[bound_name] = allowed_attributes
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            allowed_members = _ALLOWED_FROM_IMPORTS.get(module, set())
            if any(
                alias.name == "*"
                or _is_dunder(alias.name)
                or alias.name not in allowed_members
                for alias in node.names
            ):
                found.append(node)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in direct_aliases
            and node.attr not in direct_aliases[node.value.id]
        ):
            found.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == "pytest_plugins"
                for target in targets
            ):
                found.append(node)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            is_dotted_resolution = node.func.attr in {"setattr", "delattr"}
            target = node.args[0] if node.args else None
            targets_tests = (
                isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and (target.value == "tests" or target.value.startswith("tests."))
            )
            if is_dotted_resolution and targets_tests:
                found.append(node)
        else:
            forbidden_name = isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES
            forbidden_attribute = isinstance(node, ast.Attribute) and (
                node.attr in _FORBIDDEN_ATTRIBUTES or _is_dunder(node.attr)
            )
            forbidden_key = isinstance(node, ast.Constant) and _is_dunder(node.value)
            if forbidden_name or forbidden_attribute or forbidden_key:
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
