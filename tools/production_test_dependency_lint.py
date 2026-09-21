"""Reject test-package and dynamic-execution dependencies in property modules."""

from __future__ import annotations

import ast
from pathlib import Path

PROTECTED_PATHS = (Path("tests/test_t0180_diff_properties.py"),)


class DependencyError(Exception):
    pass


def _is_importlib_module(node, importlib_aliases):
    return isinstance(node, ast.Name) and node.id in importlib_aliases


def _is_import_module_api(node, importlib_aliases, import_module_aliases):
    if isinstance(node, ast.Name):
        return node.id in import_module_aliases
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "import_module"
        and _is_importlib_module(node.value, importlib_aliases)
    ) or (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and _is_importlib_module(node.args[0], importlib_aliases)
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "import_module"
    )


def findings(source: str):
    tree = ast.parse(source)
    importlib_aliases = {"importlib"}
    import_module_aliases = set()
    found = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tests" or alias.name.startswith("tests."):
                    found.append(node)
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "tests" or module.startswith("tests."):
                found.append(node)
            if module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        import_module_aliases.add(alias.asname or alias.name)

    # Conservatively identify assignments derived from dynamic-import APIs.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                continue
            value = node.value
            hazardous = _is_import_module_api(
                value, importlib_aliases, import_module_aliases
            ) or (isinstance(value, ast.Name) and value.id in import_module_aliases)
            if not hazardous:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in import_module_aliases:
                    import_module_aliases.add(target.id)
                    found.append(node)
                    changed = True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        is_dynamic_execution = isinstance(fn, ast.Name) and fn.id in {
            "eval",
            "exec",
            "__import__",
        }
        if is_dynamic_execution or _is_import_module_api(
            fn, importlib_aliases, import_module_aliases
        ):
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
