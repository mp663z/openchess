"""T3656: every inventory component records linkage, deployment and distribution."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs/component-inventory.json"
LINKAGE = ROOT / "data/linkage-classification.yaml"

LINKAGE_CLASSES = {"subprocess", "dynamic_link", "static_link", "source_inclusion", "data"}
DEPLOYMENT_CLASSES = {"ci_only", "cli_tooling", "local_external", "server_only", "browser_bundle"}
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
    raw = yaml.safe_load(LINKAGE.read_text())
    classified = set(raw["components"]) | set(raw.get("files") or {})
    inventoried = set()
    for kind, name, comp in _components(inv):
        if kind in ("model_weight", "bundled_asset"):
            inventoried.add(f"{kind}:{comp['path']}")
        else:
            inventoried.add(name)
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
    import pytest as _pytest

    import tools.component_inventory as ci

    classes, _ = ci._classifications()
    assert "pytest" in classes
    real_open = Path.read_text

    def broken(self, *args, **kwargs):
        text = real_open(self, *args, **kwargs)
        if self.name == "linkage-classification.yaml":
            text = text.replace("  pytest:", "  pytest_REMOVED:")
        return text

    monkeypatch.setattr(Path, "read_text", broken)
    with _pytest.raises(RuntimeError, match="no linkage classification"):
        ci.generate()


import pytest  # noqa: E402


@pytest.mark.parametrize(
    "kind,dirname", [("model_weight", "models"), ("bundled_asset", "assets")]
)
def test_unclassified_discovered_file_fails_closed(kind, dirname, tmp_path, monkeypatch):
    """A discovered weight/asset with no files: classification aborts generation."""
    import tools.component_inventory as ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)  # probes never touch the repo tree
    (tmp_path / dirname).mkdir()
    (tmp_path / dirname / "probe-unclassified.bin").write_bytes(b"probe")
    with pytest.raises(RuntimeError, match=f"no linkage classification for {kind}:"):
        ci.generate()


@pytest.mark.parametrize(
    "kind,dirname", [("model_weight", "models"), ("bundled_asset", "assets")]
)
def test_classified_discovered_file_joins_all_fields(kind, dirname, tmp_path, monkeypatch):
    """A classified discovered file carries linkage + deployment + distribution."""
    import tools.component_inventory as ci

    monkeypatch.setattr(ci, "ROOT", tmp_path)  # probes never touch the repo tree
    (tmp_path / dirname).mkdir()
    (tmp_path / dirname / "probe-classified.bin").write_bytes(b"probe")
    real_text = LINKAGE.read_text()
    patched = real_text.replace(
        "files: {}",
        "files:\n"
        f"  {kind}:{dirname}/probe-classified.bin:\n"
        "    linkage: data\n    deployment: local_external\n"
        "    distribution: not_distributed",
    )
    monkeypatch.setattr(ci, "LINKAGE", _TmpLinkage(patched))
    inv = ci.generate()
    key = "model_weights" if kind == "model_weight" else "bundled_assets"
    entry = next(f for f in inv[key] if f["path"] == f"{dirname}/probe-classified.bin")
    assert entry["linkage"] == "data"
    assert entry["deployment"] == "local_external"
    assert entry["distribution"] == "not_distributed"
    assert len(entry["sha256"]) == 64


class _TmpLinkage:
    name = "linkage-classification.yaml"

    def __init__(self, text):
        self._text = text

    def read_text(self):
        return self._text

    def read_bytes(self):
        return self._text.encode()
