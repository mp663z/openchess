from pathlib import Path

import pytest

from tools.production_test_dependency_lint import (
    PROTECTED_PATHS,
    DependencyError,
    lint_protected_paths,
)


def test_repository_guard_lints_protected_property_files_independently():
    assert (Path("tests/test_t0180_diff_properties.py"),) == PROTECTED_PATHS
    lint_protected_paths()


def test_removing_in_file_self_check_does_not_disable_external_enforcement(tmp_path):
    protected_path = PROTECTED_PATHS[0]
    source = protected_path.read_text()
    start = source.index("def test_property_file_has_no_tests_package_imports():")
    end = source.index("\ndef test_repository_dependency_lint", start)
    without_self_check = source[:start] + source[end + 1 :]
    mutant_path = tmp_path / protected_path
    mutant_path.parent.mkdir(parents=True)
    mutant_path.write_text("import tests.forbidden_helper\n" + without_self_check)

    with pytest.raises(DependencyError, match="forbidden test or dynamic dependency"):
        lint_protected_paths(tmp_path)


def test_ordinary_pytest_and_object_monkeypatch_usage_remain_allowed():
    from tools.production_test_dependency_lint import findings

    allowed = [
        "import pytest\npytest.mark.parametrize('x', [1])",
        "import pytest\nwith pytest.raises(ValueError):\n    raise ValueError",
        "def f(monkeypatch, node):\n    monkeypatch.setattr(node, 'value', 1)",
    ]
    for source in allowed:
        assert findings(source) == [], source


def test_direct_module_attribute_allowlist_tracks_aliases():
    from tools.production_test_dependency_lint import findings

    allowed = [
        "import copy as c\nc.deepcopy({})",
        "import random as rng\nrng.Random(1)",
        "import pytest as pt\npt.mark.parametrize('x', [1])\npt.raises(ValueError)",
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
