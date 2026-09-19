"""T2792: ADR-0004 structural battery v2 - the asymmetric-split policy is
STRUCTURED front matter evaluated as a truth table over scenarios:
an operation/owner matrix, an offline-required set, a data-flow matrix,
a phone-training declaration and declared external-provider exceptions.
Prose consequences are derived from the policy and contradictory
permission clauses are rejected. Mutations weakening any of these fail.
"""

from pathlib import Path

import yaml

ADR = (Path(__file__).resolve().parent.parent / "docs/adr"
       / "ADR-0004-asymmetric-architecture.md").read_text()

HEAVY = {"import", "index", "stockfish", "model-inference", "delta"}
OPERATIONS = {
    "import": "desktop", "index": "desktop", "stockfish": "desktop",
    "model-inference": "desktop", "delta": "desktop",
    "queue": "web", "approval": "web", "training": "web",
    "sync-encrypt": "desktop", "sync-decrypt": "web", "export": "desktop",
}
OFFLINE_REQUIRED = {
    "import", "index", "stockfish", "model-inference", "delta",
    "queue", "approval", "training", "export",
}
DATA_FLOW = {
    "desktop": {"content": "store-process", "keys": "store",
                "ciphertext": "store", "account-metadata": "store"},
    "web": {"content": "process-local-only", "keys": "store-local-only",
            "ciphertext": "receive-decrypt", "account-metadata": "store"},
    "server": {"content": "never", "keys": "never",
               "ciphertext": "store-relay-only",
               "account-metadata": "store-entitlements"},
}
PHONE_TRAINING = {"surface": "web-pwa", "export_required": False}
HOSTED_BYOM = {
    "allowed": True, "opt_in": True, "default": False,
    "payload": "inference-request-only",
    "never_receives": ["account-keys", "full-corpus", "sync-keys"],
    "relay_plaintext": False, "local_completeness": True,
    "critical_path": False, "suspends_reference_claims": False,
}
LOCAL_LLM = {
    "allowed": True, "opt_in": True, "default": False,
    "payload": "none-local-only", "suspends_reference_claims": True,
}
INVARIANTS = [
    "heavy-compute-only-on-desktop",
    "full-loop-offline-on-desktop",
    "server-never-content-or-keys",
    "export-never-required-for-phone-training",
    "hosted-byom-opt-in-only",
]
# Contradictory permission clauses that must never survive anywhere.
DENIED = [
    "retain plaintext",
    "hosted engines whenever",
    "review and training require the network",
    "training require the network",
    "may run Stockfish",
    "primary phone story",
]


def evaluate(policy: dict, scenario: dict) -> list[str]:
    """Truth-table evaluator: a scenario describes an assignment of
    operation owners, network requirements, data grants, the phone
    training path and any hosted inference providers. Returns the list
    of policy violations; empty means the scenario is permitted."""
    violations: list[str] = []
    owners = scenario.get("owners", {})
    for op in HEAVY:
        if owners.get(op) in {"web", "server"}:
            violations.append(f"heavy compute {op} assigned to {owners[op]}")
    networked = set(scenario.get("requires_network", []))
    for op in sorted(set(policy["offline_required"]) & networked):
        violations.append(f"offline-required operation {op} requires network")
    grants = scenario.get("data_grants", {})
    for cls in ("content", "keys"):
        if grants.get("server", {}).get(cls) not in {None, "never"}:
            violations.append(f"server granted {cls}")
    if scenario.get("phone_training_requires_export"):
        violations.append("export required for phone training")
    providers = policy.get("external_providers", {})
    for name in scenario.get("hosted_inference", []):
        prov = providers.get(name)
        if prov is None or not prov.get("allowed"):
            violations.append(f"undeclared hosted-compute path {name}")
        elif not (prov.get("opt_in") and prov.get("default") is False
                  and prov.get("relay_plaintext") is False
                  and prov.get("local_completeness")
                  and prov.get("critical_path") is False):
            violations.append(f"provider {name} violates exception scope")
    return violations


def _parse(adr: str):
    assert adr.startswith("---\n"), "YAML front matter required"
    fm = yaml.safe_load(adr.split("---\n", 2)[1])
    body = adr.split("---\n", 2)[2]
    secs: dict[str, list[str]] = {"__header__": []}
    current = "__header__"
    for line in body.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            secs[current] = []
        else:
            secs.setdefault(current, []).append(line)
    return fm, body, {k: "\n".join(v) for k, v in secs.items()}


def _check(adr: str) -> None:
    fm, body, secs = _parse(adr)
    assert fm["adr"] == "ADR-0004"
    assert fm["status"] == "proposed"
    # exact structured pins
    assert fm["operations"] == OPERATIONS
    assert set(fm["offline_required"]) == OFFLINE_REQUIRED
    assert fm["data_flow"] == DATA_FLOW
    assert fm["phone_training"] == PHONE_TRAINING
    assert fm["external_providers"]["hosted-byom"] == HOSTED_BYOM
    assert fm["external_providers"]["local-large-llm"] == LOCAL_LLM
    assert fm["invariants"] == INVARIANTS
    # the declared policy itself must evaluate clean
    assert evaluate(fm, {"owners": fm["operations"],
                         "hosted_inference": ["hosted-byom"]}) == []
    # contradictory permission clauses rejected
    for clause in DENIED:
        assert clause not in body, f"contradiction clause present: {clause}"
    for line in body.splitlines():
        if "plaintext" in line and "server" in line:
            assert "never" in line or "ciphertext" in line, \
                f"unguarded server/plaintext line: {line}"
    # options matrix still complete
    assert "Status: proposed" in secs.get("__header__", "")
    a, rest = body.split("### Option B")
    b, c = rest.split("### Option C")
    for axis in ("Authority", "Cost", "Privacy", "Habit", "Interoperability"):
        assert f"{axis}:" in a and f"{axis}:" in b and f"{axis}:" in c
    decision = secs.get("Decision drivers and proposed choice", "")
    assert "Proposed: Option A" in decision
    assert "front matter are normative" in decision
    # consequences derived from the policy, exact phrases
    cons = secs.get("Consequences", "")
    heavy_sorted = ", ".join(sorted(HEAVY - {"model-inference"}))
    assert "only on desktop" in cons
    assert "no network" in cons
    assert "never receives or stores plaintext content or keys" in cons
    assert "export is never required" in cons
    assert "opt-in, default off" in cons
    assert "suspends" in cons and "p95" in cons
    assert heavy_sorted.split(", ")[0] in cons


def _bad(mutated: str) -> None:
    try:
        _check(mutated)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR)


def test_evaluator_positive_scenario():
    policy = yaml.safe_load(ADR.split("---\n", 2)[1])
    assert evaluate(policy, {"owners": OPERATIONS}) == []
    assert evaluate(policy, {"owners": OPERATIONS,
                             "requires_network": ["sync-encrypt",
                                                  "sync-decrypt"]}) == []


def test_evaluator_rejects_heavy_compute_on_web_or_server():
    policy = yaml.safe_load(ADR.split("---\n", 2)[1])
    for op in sorted(HEAVY):
        for actor in ("web", "server"):
            assert evaluate(policy, {"owners": {**OPERATIONS, op: actor}})


def test_evaluator_rejects_networked_full_loop_op():
    policy = yaml.safe_load(ADR.split("---\n", 2)[1])
    for op in sorted(OFFLINE_REQUIRED):
        assert evaluate(policy, {"requires_network": [op]})


def test_evaluator_rejects_server_content_or_keys():
    policy = yaml.safe_load(ADR.split("---\n", 2)[1])
    assert evaluate(policy, {"data_grants": {"server": {"content": "store"}}})
    assert evaluate(policy, {"data_grants": {"server": {"keys": "store"}}})


def test_evaluator_rejects_export_required_phone_training():
    policy = yaml.safe_load(ADR.split("---\n", 2)[1])
    assert evaluate(policy, {"phone_training_requires_export": True})


def test_evaluator_rejects_undeclared_hosted_path():
    policy = yaml.safe_load(ADR.split("---\n", 2)[1])
    assert evaluate(policy, {"hosted_inference": ["acme-cloud"]})


def test_mutation_prose_contradictions_fail_simultaneously():
    mutated = (ADR.replace(
        "the server is a zero-knowledge ciphertext relay holding\n"
        "  only blobs and account entitlements",
        "the server is a zero-knowledge ciphertext relay; the server "
        "may also retain plaintext and run hosted engines whenever "
        "convenient")
        .replace("The review/training loop", "PLACEHOLDER")
        .replace("export to Anki/Chessable ships as a feature, not\n"
                 "  as the training story",
                 "export to Anki/Chessable ships as the primary phone "
                 "story"))
    mutated += ("\nExcept review and training require the network, and "
                "web surfaces may run Stockfish and models.\n")
    _bad(mutated)


def test_mutation_each_prose_contradiction_fails_alone():
    _bad(ADR.replace("zero-knowledge ciphertext relay",
                     "relay that may retain plaintext"))
    _bad(ADR.replace("complete with no network; only sync transport",
                     "require the network, as does sync transport"))
    _bad(ADR.replace("the web/mobile\nsurface carries no heavy compute",
                     "the web/mobile\nsurface may run Stockfish and models"))
    _bad(ADR.replace("a feature, not\n  as the training story",
                     "the primary phone story"))


def test_mutation_operations_fails():
    _bad(ADR.replace("  stockfish: desktop", "  stockfish: web"))


def test_mutation_offline_required_fails():
    _bad(ADR.replace("  - training\n  - export", "  - export"))


def test_mutation_data_flow_fails():
    _bad(ADR.replace("    content: never\n    keys: never",
                     "    content: store\n    keys: never"))


def test_mutation_phone_training_fails():
    _bad(ADR.replace("  export_required: false", "  export_required: true"))


def test_mutation_provider_scope_fails():
    _bad(ADR.replace("    critical_path: false", "    critical_path: true"))
    _bad(ADR.replace("    default: off", "    default: on", 1))
    _bad(ADR.replace("    relay_plaintext: false",
                     "    relay_plaintext: true"))


def test_mutation_invariant_fails():
    _bad(ADR.replace("server-never-content-or-keys",
                     "server-stores-no-plaintext"))
