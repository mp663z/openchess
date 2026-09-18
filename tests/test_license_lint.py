"""T0008: LICENSE is verbatim AGPL-3.0; declarations are AGPL-3.0-or-later."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.license_lint as ll  # noqa: E402


def test_license_is_verbatim_agpl3():
    assert (ROOT / "LICENSE").exists()
    assert ll._normalized_sha256(ROOT / "LICENSE") == ll.AGPL3_NORMALIZED_SHA256


def test_all_declarations_are_or_later():
    for rel in ll.DECLARATIONS:
        text = (ROOT / rel).read_text()
        assert "AGPL-3.0-or-later" in text, rel
        assert "AGPL-3.0-only" not in text, rel


def test_repo_tree_lint_clean():
    out = subprocess.run(
        [sys.executable, "tools/license_lint.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout


def test_only_variant_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "ROOT", tmp_path)
    (tmp_path / "LICENSE").write_text((ROOT / "LICENSE").read_text())
    (tmp_path / "docs").mkdir()
    for rel in ll.DECLARATIONS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("License: AGPL-3.0-only\n")
    errors = ll.lint()
    assert len([e for e in errors if "AGPL-3.0-only" in e]) == len(ll.DECLARATIONS)
    assert any("does not declare" in e for e in errors)


def test_tampered_license_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "ROOT", tmp_path)
    (tmp_path / "LICENSE").write_text("GNU AFFERO GENERAL PUBLIC LICENSE (edited)\n")
    (tmp_path / "docs").mkdir()
    for rel in ll.DECLARATIONS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("License: AGPL-3.0-or-later\n")
    errors = ll.lint()
    assert any("verbatim" in e for e in errors)


def test_missing_license_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(ll, "ROOT", tmp_path)
    for rel in ll.DECLARATIONS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("License: AGPL-3.0-or-later\n")
    assert any("LICENSE file missing" in e for e in ll.lint())


def test_canonical_hash_is_documented_and_pinned():
    """The pinned hash is the whitespace-normalized canonical FSF text."""
    assert ll.AGPL3_NORMALIZED_SHA256 == (
        "2cd0fb7883a3b3553dedbb0bad171646d46f51e2642951aae7c59cb5cec86c46"
    )
    assert "gnu.org/licenses/agpl-3.0.txt" in (ROOT / "tools/license_lint.py").read_text()
