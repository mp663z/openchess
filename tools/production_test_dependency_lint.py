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
    "tools.production_test_dependency_lint": {"findings", "lint"},
    "tools.variant_runtime": {"VariantError"},
}
_FROM_IMPORT_ATTRIBUTE_SURFACES = {
    ("graph", "diff"): {"DiffError", "apply", "compute", "state_id"},
}
_FORBIDDEN_NAMES = {
    "__builtins__",
    "__import__",
    "attrgetter",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "import_module",
    "input",
    "locals",
    "open",
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
_ALLOWED_TEST_PARAMETERS = {"fen", "hostile", "monkeypatch", "section", "seed", "variant"}
_FORBIDDEN_ATTRIBUTE_PREFIXES = (
    "ag_",
    "co_",
    "cr_",
    "f_",
    "gi_",
    "tb_",
)


class DependencyError(Exception):
    pass


def _is_dunder(value):
    return isinstance(value, str) and value.startswith("__") and value.endswith("__")


def findings(source: str):
    tree = ast.parse(source)
    found = []
    attribute_roots = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg:
                parameters.append(node.args.vararg)
            if node.args.kwarg:
                parameters.append(node.args.kwarg)
            invalid_name = any(
                parameter.arg not in _ALLOWED_TEST_PARAMETERS for parameter in parameters
            )
            has_annotation = any(parameter.annotation is not None for parameter in parameters)
            has_defaults = bool(node.args.defaults) or any(
                default is not None for default in node.args.kw_defaults
            )
            if invalid_name or has_annotation or has_defaults:
                found.append(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                allowed_attributes = _ALLOWED_DIRECT_IMPORTS.get(alias.name)
                if allowed_attributes is None:
                    found.append(node)
                else:
                    bound_name = alias.asname or alias.name.split(".", 1)[0]
                    attribute_roots[bound_name] = allowed_attributes
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            allowed_members = _ALLOWED_FROM_IMPORTS.get(module, set())
            for alias in node.names:
                if (
                    alias.name == "*"
                    or _is_dunder(alias.name)
                    or alias.name not in allowed_members
                ):
                    found.append(node)
                    continue
                surface = _FROM_IMPORT_ATTRIBUTE_SURFACES.get((module, alias.name))
                if surface is not None:
                    attribute_roots[alias.asname or alias.name] = surface
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in attribute_roots
            and node.attr not in attribute_roots[node.value.id]
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
            method = node.func.attr
            is_pytest_mark = (
                isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id in attribute_roots
                and node.func.value.attr == "mark"
            )
            indirect = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "indirect"),
                None,
            )
            param_names = node.args[0] if node.args else None
            valid_param_names = (
                isinstance(param_names, ast.Constant)
                and isinstance(param_names.value, str)
                and all(
                    name.strip() in _ALLOWED_TEST_PARAMETERS
                    for name in param_names.value.split(",")
                )
            )
            if is_pytest_mark and (
                method != "parametrize" or indirect is not None or not valid_param_names
            ):
                found.append(node)
                continue
            target = node.args[0] if node.args else None
            # pytest MonkeyPatch object forms carry a separate attribute name:
            # setattr(object, name, value), delattr(object, name). Their dotted
            # string forms do not. Unresolved dotted targets fail closed.
            is_string_form = (method == "setattr" and len(node.args) < 3) or (
                method == "delattr" and len(node.args) < 2
            )
            literal_target = target.value if isinstance(target, ast.Constant) else None
            targets_tests = isinstance(literal_target, str) and (
                literal_target == "tests" or literal_target.startswith("tests.")
            )
            unresolved_string_target = is_string_form and not isinstance(
                literal_target, str
            )
            if method in {"setattr", "delattr"} and (
                targets_tests or unresolved_string_target
            ):
                found.append(node)
        else:
            forbidden_name = isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES
            forbidden_attribute = isinstance(node, ast.Attribute) and (
                node.attr in _FORBIDDEN_ATTRIBUTES
                or _is_dunder(node.attr)
                or node.attr == "tb"
                or node.attr.startswith(_FORBIDDEN_ATTRIBUTE_PREFIXES)
            )
            forbidden_key = isinstance(node, ast.Constant) and _is_dunder(node.value)
            if forbidden_name or forbidden_attribute or forbidden_key:
                found.append(node)
    return found


def lint(path: str | Path):
    checked_path = Path(path)
    hits = findings(checked_path.read_text())
    if hits:
        raise DependencyError(f"{checked_path}: forbidden test or dynamic dependency")


def lint_protected_paths(root: Path = Path(".")):
    for relative_path in PROTECTED_PATHS:
        lint(root / relative_path)


if __name__ == "__main__":
    lint_protected_paths()
