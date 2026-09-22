from pathlib import Path

import pytest

from tools.production_test_dependency_lint import (
    PROTECTED_PATHS,
    DependencyError,
    lint_protected_paths,
)


def test_repository_guard_lints_protected_property_files_independently():
    assert (
        Path("tests/test_t0152_provenance_properties.py"),
        Path("tests/test_t0180_diff_properties.py"),
    ) == PROTECTED_PATHS
    lint_protected_paths()


def test_removing_in_file_self_check_does_not_disable_external_enforcement(tmp_path):
    protected_path = Path("tests/test_t0180_diff_properties.py")
    source = protected_path.read_text()
    start = source.index("def test_property_file_has_no_tests_package_imports():")
    end = source.index("\ndef test_repository_dependency_lint", start)
    without_self_check = source[:start] + source[end + 1 :]
    mutant_path = tmp_path / protected_path
    mutant_path.parent.mkdir(parents=True)
    mutant_path.write_text("import tests.forbidden_helper\n" + without_self_check)
    other_path = tmp_path / PROTECTED_PATHS[0]
    other_path.write_text(PROTECTED_PATHS[0].read_text())

    with pytest.raises(DependencyError, match="forbidden test or dynamic dependency"):
        lint_protected_paths(tmp_path)


def test_ordinary_pytest_and_object_monkeypatch_usage_remain_allowed():
    from tools.production_test_dependency_lint import findings

    allowed = [
        "import pytest\npytest.mark.parametrize('seed', [1])",
        "import pytest\nwith pytest.raises(ValueError):\n    raise ValueError",
        (
            "import graph.node as node\n"
            "def f(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
        ),
    ]
    for source in allowed:
        assert findings(source) == [], source


def test_direct_module_attribute_allowlist_tracks_aliases():
    from tools.production_test_dependency_lint import findings

    allowed = [
        "import copy as c\nc.deepcopy({})",
        "import random as rng\nrng.Random(1)",
        "import pytest as pt\npt.mark.parametrize('seed', [1])\npt.raises(ValueError)",
        "import graph.node as node\nnode.make_record('standard', 'fen')",
    ]
    for source in allowed:
        assert findings(source) == [], source

    forbidden = [
        "import random\nrandom._os.system('python -c pass')",
        "import random as rng\nrng._sys.modules['tests.x']",
        "import pytest\npytest.console_main()",
    ]
    for source in forbidden:
        assert findings(source), source


def test_from_imported_module_surface_tracks_aliases():
    from tools.production_test_dependency_lint import findings

    allowed = [
        "from graph import diff as d\nd.compute({}, {})",
        "from graph import diff as d\nd.apply({}, {})",
        "from graph import diff as d\nd.state_id({})",
        "from graph import diff as d\nraise d.DiffError('x')",
    ]
    for source in allowed:
        assert findings(source) == [], source

    forbidden = [
        (
            "from graph import diff as d\n"
            "d.re.enum.sys.modules['os'].system('python -c pass')"
        ),
        "from graph import diff as d\nd.hashlib.sha256(b'x')",
    ]
    for source in forbidden:
        assert findings(source), source


def test_fixture_parameters_require_literal_local_nonindirect_parametrize():
    from tools.production_test_dependency_lint import findings

    forbidden = [
        "def test_x(record): assert record",
        "def test_x(failure): assert failure",
        "def test_x(monkeypatch): assert monkeypatch",
        "def test_x(unknown): assert unknown",
        (
            "import pytest as pt\n"
            "@pt.mark.parametrize('record', [1], indirect=True)\n"
            "def test_x(record): assert record"
        ),
        (
            "import pytest as pt\n"
            "p = pt.mark.parametrize\n"
            "@p('record', [1])\n"
            "def test_x(record): assert record"
        ),
        (
            "import pytest\n"
            "@pytest.mark.parametrize('record', [1])\n"
            "def test_x(record, failure): assert record and failure"
        ),
    ]
    for source in forbidden:
        assert findings(source), source

    allowed = [
        (
            "import pytest\n"
            "@pytest.mark.parametrize('record,failure', [(1, 2)])\n"
            "def test_x(record, failure): assert record and failure"
        ),
        (
            "import pytest as pt\n"
            "@pt.mark.parametrize('record', [1])\n"
            "@pt.mark.parametrize('failure', [2])\n"
            "def test_x(record, failure): assert record and failure"
        ),
    ]
    for source in allowed:
        assert findings(source) == [], source


def test_monkeypatch_capabilities_are_per_call_exact_and_fail_closed():
    from tools.production_test_dependency_lint import findings

    forbidden = [
        (
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr('os.system', lambda command: 0)"
        ),
        (
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr('builtins.eval', lambda text: None)"
        ),
        (
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr('graph.node.other', lambda: None)"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None)\n"
            "    monkeypatch.setattr('os.system', lambda command: 0)"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.delattr(node, 'parse_position')"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position')"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None, False)"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    alias = node\n"
            "    monkeypatch.setattr(alias, 'parse_position', lambda: None)"
        ),
    ]
    for source in forbidden:
        assert findings(source), source

    allowed = (
        "import graph.node as node\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
    )
    assert findings(allowed) == []


def test_monkeypatch_fixture_cannot_be_laundered_as_a_value():
    from tools.production_test_dependency_lint import findings

    decoy = "monkeypatch.setattr(node, 'parse_position', lambda: None)"
    mutants = [
        f"m = monkeypatch\n    {decoy}\n    m.setattr('os.system', lambda x: 0)",
        f"f = monkeypatch.setattr\n    {decoy}\n    f('os.system', lambda x: 0)",
        f"{decoy}\n    (monkeypatch.setattr,)[0]('os.system', lambda x: 0)",
        f"{decoy}\n    (lambda f: f)(monkeypatch.setattr)('os.system', lambda x: 0)",
        f"{decoy}\n    helper(monkeypatch)",
    ]
    for body in mutants:
        source = "import graph.node as node\ndef test_x(monkeypatch):\n    " + body
        assert findings(source), source

    nested = (
        "import graph.node as node\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(node, 'parse_position', lambda: None)\n"
        "    def inner():\n"
        "        return monkeypatch\n"
        "    inner()"
    )
    assert findings(nested)


def test_monkeypatch_fixture_shadowing_is_rejected_with_executable_witness():
    import graph.node as node
    from tools.production_test_dependency_lint import findings

    mutants = [
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    def inner(monkeypatch):\n"
            "        monkeypatch.setattr(node, 'parse_position', lambda: None)"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    async def inner(monkeypatch):\n"
            "        monkeypatch.setattr(node, 'parse_position', lambda: None)"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    f = lambda monkeypatch: monkeypatch.setattr("
            "node, 'parse_position', lambda: None)"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    [monkeypatch for monkeypatch in []]"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    try: raise RuntimeError()\n"
            "    except RuntimeError as monkeypatch:\n"
            "        pass"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch = object()"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    (monkeypatch := object())"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    import graph.node as monkeypatch"
        ),
    ]
    for source in mutants:
        assert findings(source), source

    class Double:
        def setattr(self, obj, name, value):
            obj.unapproved_capability = value

    def executable_witness(monkeypatch):
        monkeypatch.setattr(node, "parse_position", lambda: None)

    executable_witness(Double())
    try:
        assert node.unapproved_capability is not None
        assert findings(mutants[0])
    finally:
        del node.unapproved_capability


def test_monkeypatch_class_pattern_and_scope_declarations_are_rejected():
    import graph.node as node
    from tools.production_test_dependency_lint import findings

    class_binding = (
        "import graph.node as node\n"
        "def test_x(monkeypatch):\n"
        "    class monkeypatch:\n"
        "        def setattr(obj, name, value):\n"
        "            obj.unapproved_capability = value\n"
        "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
    )
    match_capture = (
        "import graph.node as node\n"
        "def test_x(monkeypatch):\n"
        "    class Double:\n"
        "        def setattr(self, obj, name, value):\n"
        "            obj.unapproved_capability = value\n"
        "    match Double():\n"
        "        case monkeypatch:\n"
        "            monkeypatch.setattr(node, 'parse_position', lambda: None)"
    )
    regressions = [
        class_binding,
        match_capture,
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    def inner():\n"
            "        global monkeypatch"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    def inner():\n"
            "        nonlocal monkeypatch"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    class Deep:\n"
            "        class monkeypatch: pass"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    match {'x': 1}:\n"
            "        case {'x': x, **monkeypatch}: pass"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    match [1]:\n"
            "        case [x, *monkeypatch]: pass"
        ),
        (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    match 1:\n"
            "        case (1 as monkeypatch) | (2 as monkeypatch): pass"
        ),
    ]
    for source in regressions:
        assert findings(source), source

    namespace = {"node": node}
    exec(compile(class_binding, "<class-binding-witness>", "exec"), namespace)
    namespace["test_x"](object())
    try:
        assert node.unapproved_capability is not None
        assert findings(class_binding)
    finally:
        del node.unapproved_capability

    namespace = {"node": node}
    exec(compile(match_capture, "<match-capture-witness>", "exec"), namespace)
    namespace["test_x"](object())
    try:
        assert node.unapproved_capability is not None
        assert findings(match_capture)
    finally:
        del node.unapproved_capability


def test_monkeypatch_target_root_is_lexically_closed_with_witnesses():
    import pytest

    from tools.production_test_dependency_lint import findings

    nested_parameter = (
        "import graph.node as node\n"
        "class Decoy:\n"
        "    @staticmethod\n"
        "    def parse_position(): return 'safe'\n"
        "def test_x(monkeypatch):\n"
        "    def inner(node):\n"
        "        monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')\n"
        "    inner(Decoy)"
    )
    lambda_parameter = (
        "import graph.node as node\n"
        "def test_x(monkeypatch):\n"
        "    (lambda node: monkeypatch.setattr("
        "node, 'parse_position', lambda: 'pwned'))(object())"
    )
    except_alias = (
        "import graph.node as node\n"
        "def test_x(monkeypatch):\n"
        "    try: raise RuntimeError()\n"
        "    except RuntimeError as node:\n"
        "        monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
    )
    additional_bindings = [
        "def inner(node): pass",
        "f = lambda node: node",
        "[node for node in []]",
        "try: raise RuntimeError()\n    except RuntimeError as node: pass",
        "with context() as node: pass",
        "node = object()",
        "(node := object())",
        "import graph.position_digest as node",
        "class node: pass",
        "def node(): pass",
        "global node",
        "nonlocal node",
        "del node",
        "match {}:\n        case {'x': x, **node}: pass",
    ]
    for source in (nested_parameter, lambda_parameter, except_alias):
        assert findings(source), source
    for binding in additional_bindings:
        source = (
            "import graph.node as node\n"
            "def test_x(monkeypatch):\n"
            f"    {binding}\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
        )
        assert findings(source), source

    namespace = {"pytest": pytest}
    exec(compile(nested_parameter, "<target-shadow-witness>", "exec"), namespace)
    original = namespace["Decoy"].parse_position
    namespace["test_x"](pytest.MonkeyPatch())
    try:
        assert original() == "safe"
        assert namespace["Decoy"].parse_position() == "pwned"
        assert findings(nested_parameter)
    finally:
        namespace["Decoy"].parse_position = original


def test_monkeypatch_authority_requires_module_top_level_import():
    import pytest

    from tools.production_test_dependency_lint import findings

    witnesses = [
        (
            "class node:\n"
            "    @staticmethod\n"
            "    def parse_position(): return 'safe'\n"
            "def test_x(monkeypatch):\n"
            "    def unrelated():\n"
            "        import graph.node as node\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
        ),
        (
            "class node:\n"
            "    @staticmethod\n"
            "    def parse_position(): return 'safe'\n"
            "def test_x(monkeypatch):\n"
            "    if False:\n"
            "        import graph.node as node\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
        ),
        (
            "class node:\n"
            "    @staticmethod\n"
            "    def parse_position(): return 'safe'\n"
            "def test_x(monkeypatch):\n"
            "    try:\n"
            "        pass\n"
            "    except ImportError:\n"
            "        import graph.node as node\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
        ),
    ]
    for source in witnesses:
        assert findings(source), source

    namespace = {}
    exec(compile(witnesses[0], "<nested-import-witness>", "exec"), namespace)
    original = namespace["node"].parse_position
    namespace["test_x"](pytest.MonkeyPatch())
    try:
        assert original() == "safe"
        assert namespace["node"].parse_position() == "pwned"
        assert findings(witnesses[0])
    finally:
        namespace["node"].parse_position = original


def test_patch_root_requires_exactly_one_module_binding_with_witnesses():
    import pytest

    from tools.production_test_dependency_lint import findings

    class_overwrite = (
        "import graph.node as node\n"
        "class node:\n"
        "    @staticmethod\n"
        "    def parse_position(): return 'safe'\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
    )
    function_overwrite = (
        "import graph.node as node\n"
        "def node(): pass\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
    )
    match_overwrite = (
        "import graph.node as node\n"
        "class Double:\n"
        "    def parse_position(self): return 'safe'\n"
        "match Double():\n"
        "    case node: pass\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
    )
    except_overwrite = (
        "import graph.node as node\n"
        "try: raise RuntimeError()\n"
        "except RuntimeError as node: pass\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(node, 'parse_position', lambda: 'pwned')"
    )
    ordering = [
        "class node: pass\nimport graph.node as node",
        "import graph.node as node\nclass node: pass",
        "import graph.node as node\nimport graph.node as node",
        "import graph.node as node\nnode = object()\nnode = object()",
    ]
    for source in (class_overwrite, function_overwrite, match_overwrite, except_overwrite):
        assert findings(source), source
    for prefix in ordering:
        source = (
            f"{prefix}\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
        )
        assert findings(source), source

    namespace = {}
    exec(compile(match_overwrite, "<module-match-witness>", "exec"), namespace)
    original = namespace["node"].parse_position
    namespace["test_x"](pytest.MonkeyPatch())
    try:
        assert original() == "safe"
        assert namespace["node"].parse_position() == "pwned"
        assert findings(match_overwrite)
    finally:
        namespace["node"].parse_position = original


def test_patch_root_import_alias_occurrences_are_counted_exactly():
    from tools.production_test_dependency_lint import findings

    sources = [
        (
            "import graph.node as node, copy as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
        ),
        (
            "import copy as node, graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
        ),
        (
            "import graph.node as node, graph.node as node\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(node, 'parse_position', lambda: None)"
        ),
    ]
    for source in sources:
        assert findings(source), source
