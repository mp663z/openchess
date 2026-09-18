"""T3656: every inventory component records linkage, deployment and distribution."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs/component-inventory.json"
LINKAGE = ROOT / "data/linkage-classification.yaml"

LINKAGE_CLASSES = {"subprocess", "dynamic_link", "static_link", "source_inclusion", "data"}
DEPLOYMENT_CLASSES = {"ci_only", "server_only", "cli_tooling", "browser_bundle"}
DISTRIBUTION_CLASSES = {"not_distributed", "distributed_with_app"}

FIELDS = ("linkage", "deployment", "distribution")


def _components(inv):
    for lib in inv["libraries"]:
        yield ("library", lib["name"], lib)
    for e in inv["engines"]:
        yield ("engine", e["name"], e)
    for ds in inv["datasets"]:
        yield ("dataset", ds["name"], ds)
    for w in inv["model_weights"]:
        yield ("model_weight", w["name"], w)
    for a in inv["bundled_assets"]:
        yield ("bundled_asset", a["name"], a)


def test_every_component_classified_with_allowed_values():
    inv = json.loads(MANIFEST.read_text())
    seen = list(_components(inv))
    assert seen, "empty inventory"
    for kind, name, comp in seen:
        for field in FIELDS:
            assert comp.get(field), (kind, name, field)
        assert comp["linkage"] in LINKAGE_CLASSES, (name, comp["linkage"])
        assert comp["deployment"] in DEPLOYMENT_CLASSES, (name, comp["deployment"])
        assert comp["distribution"] in DISTRIBUTION_CLASSES, (name, comp["distribution"])


def test_classification_file_covers_exactly_the_inventory_set():
    inv = json.loads(MANIFEST.read_text())
    classified = set(yaml.safe_load(LINKAGE.read_text())["components"])
    inventoried = {name for _, name, _ in _components(inv)}
    missing = inventoried - classified
    extra = classified - inventoried
    assert not missing, f"components without classification: {missing}"
    assert not extra, f"classifications for unknown components: {extra}"


def test_stockfish_is_external_subprocess_not_distributed():
    inv = json.loads(MANIFEST.read_text())
    sf = next(e for e in inv["engines"] if e["name"] == "stockfish")
    assert sf["linkage"] == "subprocess"
    assert sf["distribution"] == "not_distributed"
    assert sf["bundled"] is False


def test_no_component_is_distributed_today():
    """Nothing ships yet; any future distributed_with_app is a deliberate diff."""
    inv = json.loads(MANIFEST.read_text())
    distributed = [
        name for _, name, comp in _components(inv)
        if comp["distribution"] == "distributed_with_app"
    ]
    assert distributed == [], f"unexpectedly distributed: {distributed}"


def test_no_browser_bundle_today():
    inv = json.loads(MANIFEST.read_text())
    bundled = [
        name for _, name, comp in _components(inv)
        if comp["deployment"] == "browser_bundle"
    ]
    assert bundled == [], f"unexpected browser-bundle components: {bundled}"


def test_generation_fails_closed_without_classification(tmp_path, monkeypatch):
    """A component missing from the classification file must fail generation."""
    import tools.component_inventory as ci

    classes = ci._classifications()
    assert "pytest" in classes
    real_open = Path.read_text

    def broken(self, *args, **kwargs):
        text = real_open(self, *args, **kwargs)
        if self.name == "linkage-classification.yaml":
            text = text.replace("  pytest:", "  pytest_REMOVED:")
        return text

    monkeypatch.setattr(Path, "read_text", broken)
    try:
        import pytest as _pytest

        with _pytest.raises(RuntimeError, match="no linkage classification"):
            ci.generate()
    finally:
        monkeypatch.undo()
