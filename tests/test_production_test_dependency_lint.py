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
