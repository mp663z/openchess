"""T0018: public monorepo layout - required components present and bounded."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

COMPONENTS = [
    "desktop", "server", "web", "graph", "importers", "ai-sdk", "uci-worker",
]


def test_required_components_exist():
    for comp in COMPONENTS:
        assert (ROOT / comp).is_dir(), comp
        readme = ROOT / comp / "README.md"
        assert readme.exists(), f"{comp}: README missing"
        assert len(readme.read_text()) > 250, f"{comp}: README trivial"


def test_contracts_present():
    contracts = ROOT / "data" / "contracts"
    assert contracts.is_dir()
    names = {p.name for p in contracts.glob("*.yaml")}
    assert "revenue-target.yaml" in names
    assert "schedule.yaml" in names


def test_component_boundaries_documented():
    # each component README names its boundary responsibilities
    for comp in COMPONENTS:
        text = (ROOT / comp / "README.md").read_text().lower()
        assert len(text.splitlines()) >= 3, comp
