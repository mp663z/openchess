"""Fail-closed dependency and capability lint for protected property modules."""

from __future__ import annotations

import ast
from pathlib import Path

PROTECTED_PATHS = (
    Path("tests/test_t0152_provenance_properties.py"),
    Path("tests/test_t0180_diff_properties.py"),
)
_ALLOWED_DIRECT_IMPORTS = {
    "copy": {"deepcopy"},
    "graph.node": {"make_record"},
    "graph.provenance": {
        "FAILURE_MAPPING",
        "ProvenanceError",
        "ProvenanceTable",
        "validate_record",
    },
    "pytest": {"mark", "raises"},
    "random": {"Random"},
}
_ALLOWED_FROM_IMPORTS = {
    "__future__": {"annotations"},
    "graph": {"diff"},
    "graph.provenance": {
        "FAILURE_MAPPING",
        "ProvenanceError",
        "ProvenanceTable",
        "validate_record",
    },
    "graph.node": {"make_record", "record_identity"},
    "tools.production_test_dependency_lint": {
        "findings",
        "lint",
        "t0151_switch_findings",
    },
    "tools.variant_runtime": {"VariantError"},
}
_FROM_IMPORT_ATTRIBUTE_SURFACES = {
    ("graph", "diff"): {"DiffError", "apply", "compute", "state_id"},
}
_MONKEYPATCH_TARGETS = {
    "graph.node": {"parse_position"},
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
_ALLOWED_TEST_PARAMETERS = {
    "failure",
    "fen",
    "hostile",
    "monkeypatch",
    "record",
    "section",
    "seed",
    "variant",
}
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


def _binds_name(target, name):
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_binds_name(element, name) for element in target.elts)
    if isinstance(target, ast.Starred):
        return _binds_name(target.value, name)
    return False


def _pattern_binds(pattern, name):
    if isinstance(pattern, ast.MatchAs):
        return pattern.name == name or (
            pattern.pattern is not None and _pattern_binds(pattern.pattern, name)
        )
    if isinstance(pattern, ast.MatchStar):
        return pattern.name == name
    if isinstance(pattern, ast.MatchMapping):
        return pattern.rest == name or any(
            _pattern_binds(child, name) for child in pattern.patterns
        )
    if isinstance(pattern, ast.MatchSequence):
        return any(_pattern_binds(child, name) for child in pattern.patterns)
    if isinstance(pattern, ast.MatchClass):
        return any(
            _pattern_binds(child, name)
            for child in (*pattern.patterns, *pattern.kwd_patterns)
        )
    if isinstance(pattern, ast.MatchOr):
        return any(_pattern_binds(child, name) for child in pattern.patterns)
    return False


def _nested_binding(node, name):
    """Reject lexical shadowing that could impersonate a fixture binding."""
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, ast.ClassDef) and child.name == name:
            return True
        if isinstance(child, (ast.Global, ast.Nonlocal)) and name in child.names:
            return True
        if isinstance(child, ast.match_case) and _pattern_binds(child.pattern, name):
            return True
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = child.args
            parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
            if arguments.vararg:
                parameters.append(arguments.vararg)
            if arguments.kwarg:
                parameters.append(arguments.kwarg)
            if any(parameter.arg == name for parameter in parameters):
                return True
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name:
                return True
        elif (
            (isinstance(child, ast.comprehension) and _binds_name(child.target, name))
            or (isinstance(child, ast.ExceptHandler) and child.name == name)
            or (
                isinstance(child, ast.withitem)
                and child.optional_vars is not None
                and _binds_name(child.optional_vars, name)
            )
        ):
            return True
        elif isinstance(child, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = child.targets if isinstance(child, ast.Assign) else [child.target]
            if any(_binds_name(target, name) for target in targets):
                return True
        elif isinstance(child, ast.Delete) and any(
            _binds_name(target, name) for target in child.targets
        ):
            return True
        elif isinstance(child, (ast.Import, ast.ImportFrom)):
            for alias in child.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if bound == name and alias.name not in _MONKEYPATCH_TARGETS:
                    return True
    return False


def _binding_nodes(tree, name):
    """All AST nodes that bind or delete name, including pattern targets."""
    bound = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                bound.append(node)
            arguments = node.args if not isinstance(node, ast.ClassDef) else None
            if arguments is not None:
                parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
                if arguments.vararg:
                    parameters.append(arguments.vararg)
                if arguments.kwarg:
                    parameters.append(arguments.kwarg)
                if any(parameter.arg == name for parameter in parameters):
                    bound.append(node)
        elif isinstance(node, ast.Lambda):
            arguments = node.args
            parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
            if arguments.vararg:
                parameters.append(arguments.vararg)
            if arguments.kwarg:
                parameters.append(arguments.kwarg)
            if any(parameter.arg == name for parameter in parameters):
                bound.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(_binds_name(target, name) for target in targets):
                bound.append(node)
        elif (
            (isinstance(node, (ast.For, ast.AsyncFor)) and _binds_name(node.target, name))
            or (isinstance(node, ast.comprehension) and _binds_name(node.target, name))
            or (
                isinstance(node, ast.withitem)
                and node.optional_vars is not None
                and _binds_name(node.optional_vars, name)
            )
            or (isinstance(node, ast.ExceptHandler) and node.name == name)
            or (isinstance(node, ast.match_case) and _pattern_binds(node.pattern, name))
            or (
                isinstance(node, ast.Delete)
                and any(_binds_name(target, name) for target in node.targets)
            )
            or (isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names)
        ):
            bound.append(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name.split(".", 1)[0]) == name:
                    bound.append(alias)
    return bound


def findings(source: str):
    tree = ast.parse(source)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    found = []
    attribute_roots = {}
    monkeypatch_roots = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                allowed = _ALLOWED_DIRECT_IMPORTS.get(alias.name)
                if allowed is not None:
                    attribute_roots[bound] = allowed
                patchable = _MONKEYPATCH_TARGETS.get(alias.name)
                if patchable is not None:
                    monkeypatch_roots[bound] = patchable
        elif isinstance(statement, ast.ImportFrom):
            module = statement.module or ""
            for alias in statement.names:
                surface = _FROM_IMPORT_ATTRIBUTE_SURFACES.get((module, alias.name))
                if surface is not None:
                    attribute_roots[alias.asname or alias.name] = surface
    authorized_imports = {}
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for alias in statement.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                if alias.name in _MONKEYPATCH_TARGETS:
                    authorized_imports.setdefault(bound, []).append((statement, alias))
    for root, imports in authorized_imports.items():
        bindings = _binding_nodes(tree, root)
        sole_approved = (
            len(imports) == 1
            and len(bindings) == 1
            and bindings[0] is imports[0][1]
        )
        if not sole_approved:
            found.append(imports[0][0])
            monkeypatch_roots.pop(root, None)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if node.args.vararg:
                parameters.append(node.args.vararg)
            if node.args.kwarg:
                parameters.append(node.args.kwarg)
            parameter_names = {parameter.arg for parameter in parameters}
            bound_names = set()
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                function = decorator.func
                is_parametrize = (
                    isinstance(function, ast.Attribute)
                    and function.attr == "parametrize"
                    and isinstance(function.value, ast.Attribute)
                    and function.value.attr == "mark"
                    and isinstance(function.value.value, ast.Name)
                    and function.value.value.id in attribute_roots
                    and "mark" in attribute_roots[function.value.value.id]
                )
                indirect = next(
                    (keyword.value for keyword in decorator.keywords
                     if keyword.arg == "indirect"),
                    None,
                )
                non_indirect = indirect is None or (
                    isinstance(indirect, ast.Constant) and indirect.value is False
                )
                names = decorator.args[0] if decorator.args else None
                if (is_parametrize and non_indirect
                        and isinstance(names, ast.Constant)
                        and isinstance(names.value, str)):
                    bound_names.update(name.strip() for name in names.value.split(","))
            monkeypatch_ok = False
            if parameter_names == {"monkeypatch"} and not _nested_binding(
                node, "monkeypatch"
            ):
                local_targets = dict(monkeypatch_roots)
                target_bindings_closed = all(
                    not _nested_binding(node, root) for root in local_targets
                )
                patch_calls = [
                    call for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "monkeypatch"
                ]
                monkeypatch_ok = target_bindings_closed and bool(patch_calls) and all(
                    call.func.attr == "setattr"
                    and len(call.args) == 3
                    and not call.keywords
                    and isinstance(call.args[0], ast.Name)
                    and call.args[0].id in local_targets
                    and isinstance(call.args[1], ast.Constant)
                    and isinstance(call.args[1].value, str)
                    and call.args[1].value in local_targets[call.args[0].id]
                    for call in patch_calls
                )
            invalid_name = (
                not parameter_names <= _ALLOWED_TEST_PARAMETERS
                or parameter_names - {"monkeypatch"} != bound_names
                or ("monkeypatch" in parameter_names and not monkeypatch_ok)
            )
            has_annotation = any(parameter.annotation is not None for parameter in parameters)
            has_defaults = bool(node.args.defaults) or any(
                default is not None for default in node.args.kw_defaults
            )
            if invalid_name or has_annotation or has_defaults:
                found.append(node)
        elif isinstance(node, ast.Import):
            if node not in tree.body:
                found.append(node)
                continue
            for alias in node.names:
                allowed_attributes = _ALLOWED_DIRECT_IMPORTS.get(alias.name)
                if allowed_attributes is None:
                    found.append(node)
                else:
                    bound_name = alias.asname or alias.name.split(".", 1)[0]
                    attribute_roots[bound_name] = allowed_attributes
        elif isinstance(node, ast.ImportFrom):
            if node not in tree.body:
                found.append(node)
                continue
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
            and node.attr in attribute_roots[node.value.id]
        ):
            parent = parents.get(node)
            laundering_contexts = (
                ast.Assign,
                ast.AnnAssign,
                ast.NamedExpr,
                ast.Return,
                ast.List,
                ast.Tuple,
                ast.Set,
                ast.Dict,
            )
            if isinstance(parent, laundering_contexts):
                found.append(node)
        elif isinstance(node, ast.Name) and node.id == "monkeypatch":
            parent = parents.get(node)
            grandparent = parents.get(parent)
            approved_receiver = (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr == "setattr"
                and isinstance(grandparent, ast.Call)
                and grandparent.func is parent
                and len(grandparent.args) == 3
                and not grandparent.keywords
            )
            if not approved_receiver:
                found.append(node)
        elif isinstance(node, ast.Name) and node.id in attribute_roots:
            parent = parents.get(node)
            is_allowed_attribute_root = (
                isinstance(parent, ast.Attribute)
                and parent.value is node
                and parent.attr in attribute_roots[node.id]
            )
            is_monkeypatch_object = (
                node.id == "node"
                and isinstance(parent, ast.Call)
                and isinstance(parent.func, ast.Attribute)
                and parent.func.attr == "setattr"
                and bool(parent.args)
                and parent.args[0] is node
                and len(parent.args) >= 3
            )
            is_allowed_root = is_allowed_attribute_root or is_monkeypatch_object
            if not is_allowed_root:
                found.append(node)
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
            is_monkeypatch = (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "monkeypatch"
            )
            if is_monkeypatch:
                parent_function = parents.get(node)
                while parent_function is not None and not isinstance(
                    parent_function, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    parent_function = parents.get(parent_function)
                local_targets = dict(monkeypatch_roots)
                if parent_function is not None:
                    for statement in ast.walk(parent_function):
                        if isinstance(statement, ast.Import):
                            for alias in statement.names:
                                allowed = _MONKEYPATCH_TARGETS.get(alias.name)
                                if allowed is not None:
                                    local_targets[
                                        alias.asname or alias.name.split(".", 1)[0]
                                    ] = allowed
                valid_patch = (
                    method == "setattr"
                    and len(node.args) == 3
                    and not node.keywords
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in local_targets
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and node.args[1].value in local_targets[node.args[0].id]
                )
                if not valid_patch:
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


def t0151_switch_findings(root: Path = Path(".")):
    """Prove T0151's production review changes only its three bindings."""
    path = root / "tests/test_t0151_provenance_red.py"
    source = path.read_text()
    oracle = """from tests import test_t0149_provenance_contract as oracle

FAILURE_MAPPING = oracle.FAILURE_MAPPING
ProvenanceError = oracle.ProvenanceError
ProvenanceTable = oracle.ProvenanceTable


def validate_record(record):
    return oracle.validate_record(*oracle._docs(), record)
"""
    production = """from graph.provenance import ProvenanceError, ProvenanceTable, validate_record
from tests.test_t0149_provenance_contract import FAILURE_MAPPING
"""
    if source.count(oracle) != 1:
        return ["T0151 oracle binding block drifted"]
    switched = source.replace(oracle, production)
    before = ast.parse(source)
    after = ast.parse(switched)
    before_tests = [
        node for node in before.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    after_tests = [
        node for node in after.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    before_dump = [ast.dump(node, include_attributes=False) for node in before_tests]
    after_dump = [ast.dump(node, include_attributes=False) for node in after_tests]
    if before_dump != after_dump:
        return ["T0151 scenarios/assertions changed"]
    if sum(node.name.startswith("test_") for node in before_tests) != 6:
        return ["T0151 test-function set drifted"]
    return []


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
