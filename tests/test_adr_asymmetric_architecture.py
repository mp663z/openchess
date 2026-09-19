"""T2792: ADR-0004 structural battery v3 - the asymmetric-split policy is
STRUCTURED front matter evaluated as a truth table over scenarios:
an operation/owner matrix enforced as a COMPLETE assignment (missing,
extra or deviating owners all fail; heavy compute is desktop-exact),
an offline-required set, a data-flow matrix, a phone-training
declaration, and external-provider exceptions validated against
structured per-invocation facts (fail closed on missing facts).
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
WEB_OFFLINE_CAPABLE = ["queue", "approval", "training"]
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
DENIED = [
    "retain plaintext",
    "hosted engines whenever",
    "review and training require the network",
    "training require the network",
    "may run Stockfish",
    "primary phone story",
]
INVOCATION_FACTS = ["opt_in", "payload_class", "sends", "persistence",
                    "relay_plaintext", "critical_path"]
GOOD_INVOCATION = {
    "provider": "hosted-byom", "opt_in": True,
    "payload_class": "inference-request-only",
    "sends": ["inference-payload"], "persistence": "none",
    "relay_plaintext": False, "critical_path": False,
}


def evaluate(policy: dict, scenario: dict) -> list[str]:
    """Truth-table evaluator. A scenario describes a COMPLETE owner
    assignment, network requirements, data grants, the phone training
    path and any hosted-inference invocations with structured facts.
    Returns the policy violations; empty means permitted. Anything
    missing, extra or deviating fails closed."""
    violations: list[str] = []
    declared = policy["operations"]
    # complete owner assignment, exactly per the declared matrix
    owners = scenario.get("owners")
    if not owners:
        violations.append("missing or empty owners assignment")
        owners = {}
    for op in owners:
        if op not in declared:
            violations.append(f"unknown operation {op}")
    for op, want in declared.items():
        got = owners.get(op)
        if got is None:
            violations.append(f"missing owner for {op}")
        elif got != want:
            violations.append(f"{op} owner {got} != {want}")
    for op in HEAVY:
        if owners.get(op) != "desktop":
            violations.append(f"heavy compute {op} not on desktop: "
                              f"{owners.get(op)}")
    # offline-required operations never need a network
    networked = set(scenario.get("requires_network", []))
    for op in sorted(set(policy["offline_required"]) & networked):
        violations.append(f"offline-required operation {op} requires network")
    # the server never receives plaintext content or keys
    grants = scenario.get("data_grants", {})
    for cls in ("content", "keys"):
        if grants.get("server", {}).get(cls) not in {None, "never"}:
            violations.append(f"server granted {cls}")
    if scenario.get("phone_training_requires_export"):
        violations.append("export required for phone training")
    # hosted inference: validate structured invocation facts, fail closed
    providers = policy.get("external_providers", {})
    for inv in scenario.get("hosted_inference", []):
        name = inv.get("provider")
        prov = providers.get(name)
        if prov is None or not prov.get("allowed"):
            violations.append(f"undeclared hosted-compute path {name}")
            continue
        missing = [f for f in INVOCATION_FACTS if f not in inv]
        if missing:
            violations.append(
                f"provider {name} invocation missing facts: {missing}")
            continue
        if inv["opt_in"] is not True:
            violations.append(f"provider {name} used without opt-in")
        if inv["payload_class"] != "inference-request-only":
            violations.append(
                f"provider {name} payload class {inv['payload_class']}")
        leaked = set(prov.get("never_receives", [])) & set(inv["sends"])
        for item in sorted(leaked):
            violations.append(f"provider {name} receives {item}")
        if inv["persistence"] != "none":
            violations.append(
                f"provider {name} granted persistence "
                f"{inv['persistence']}")
        if inv["relay_plaintext"] is not False:
            violations.append(f"provider {name} relays plaintext")
        if inv["critical_path"] is not False:
            violations.append(f"provider {name} on the critical path")
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


def _policy():
    return yaml.safe_load(ADR.split("---\n", 2)[1])


def _check(adr: str) -> None:
    fm, body, secs = _parse(adr)
    assert fm["adr"] == "ADR-0004"
    assert fm["status"] == "proposed"
    # exact structured pins
    assert fm["operations"] == OPERATIONS
    assert set(fm["offline_required"]) == OFFLINE_REQUIRED
    assert fm["web_offline_capable"] == WEB_OFFLINE_CAPABLE
    assert fm["data_flow"] == DATA_FLOW
    assert fm["phone_training"] == PHONE_TRAINING
    assert fm["external_providers"]["hosted-byom"] == HOSTED_BYOM
    assert fm["external_providers"]["local-large-llm"] == LOCAL_LLM
    assert fm["invariants"] == INVARIANTS
    # the declared policy itself must evaluate clean
    assert evaluate(fm, {"owners": dict(fm["operations"]),
                         "hosted_inference": [GOOD_INVOCATION]}) == []
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
    assert "only on desktop" in cons
    assert "no network" in cons
    assert "never receives or stores plaintext content or keys" in cons
    assert "export is never required" in cons
    assert "opt-in, default off" in cons
    assert "suspends" in cons and "p95" in cons
    # the web-owned habit operations are offline-capable, not desktop-owned
    assert "offline-capable in the PWA" in cons
    assert "desktop-owned operations" in cons


def _bad(mutated: str) -> None:
    try:
        _check(mutated)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def _violating(scenario: dict) -> None:
    v = evaluate(_policy(), scenario)
    assert v, f"scenario passed: {scenario}"


def test_real_adr_passes():
    _check(ADR)


def test_evaluator_positive_scenarios():
    assert evaluate(_policy(), {"owners": dict(OPERATIONS)}) == []
    assert evaluate(_policy(), {
        "owners": dict(OPERATIONS),
        "requires_network": ["sync-encrypt", "sync-decrypt"]}) == []
    assert evaluate(_policy(), {
        "owners": dict(OPERATIONS),
        "hosted_inference": [GOOD_INVOCATION]}) == []


def test_evaluator_owner_assignment_complete_and_exact():
    _violating({"owners": {}})                       # empty owners map
    _violating({})                                   # missing owners
    short = {k: v for k, v in OPERATIONS.items() if k != "stockfish"}
    _violating({"owners": short})                    # missing stockfish
    _violating({"owners": {**OPERATIONS, "hints": "web"}})   # extra op
    for op in sorted(HEAVY):
        for actor in ("web", "server", "provider", "phone"):
            _violating({"owners": {**OPERATIONS, op: actor}})
    for op in ("queue", "approval", "training"):
        for actor in ("desktop", "server"):
            _violating({"owners": {**OPERATIONS, op: actor}})


def test_evaluator_rejects_networked_full_loop_op():
    for op in sorted(OFFLINE_REQUIRED):
        _violating({"owners": dict(OPERATIONS),
                    "requires_network": [op]})


def test_evaluator_rejects_server_content_or_keys():
    _violating({"owners": dict(OPERATIONS),
                "data_grants": {"server": {"content": "store"}}})
    _violating({"owners": dict(OPERATIONS),
                "data_grants": {"server": {"keys": "store"}}})


def test_evaluator_rejects_export_required_phone_training():
    _violating({"owners": dict(OPERATIONS),
                "phone_training_requires_export": True})


def test_evaluator_hosted_byom_invocation_facts():
    base = {"owners": dict(OPERATIONS)}
    for send in ("full-corpus", "sync-keys", "account-keys"):
        _violating({**base, "hosted_inference": [
            {**GOOD_INVOCATION, "sends": ["inference-payload", send]}]})
    _violating({**base, "hosted_inference": [
        {**GOOD_INVOCATION, "persistence": "store-plaintext"}]})
    _violating({**base, "hosted_inference": [
        {**GOOD_INVOCATION, "persistence": "store-ciphertext"}]})
    _violating({**base, "hosted_inference": [
        {**GOOD_INVOCATION, "opt_in": False}]})
    _violating({**base, "hosted_inference": [
        {**GOOD_INVOCATION, "critical_path": True}]})
    _violating({**base, "hosted_inference": [
        {**GOOD_INVOCATION, "relay_plaintext": True}]})
    _violating({**base, "hosted_inference": [
        {**GOOD_INVOCATION, "payload_class": "full-game-history"}]})
    for fact in INVOCATION_FACTS:
        inv = {k: v for k, v in GOOD_INVOCATION.items() if k != fact}
        _violating({**base, "hosted_inference": [inv]})
    _violating({**base, "hosted_inference": [{"provider": "acme-cloud",
                                              **{f: None for f in
                                                 INVOCATION_FACTS}}]})


def test_mutation_prose_contradictions_fail_simultaneously():
    mutated = (ADR.replace(
        "the server is a zero-knowledge ciphertext relay holding\n"
        "  only blobs and account entitlements",
        "the server is a zero-knowledge ciphertext relay; the server "
        "may also retain plaintext and run hosted engines whenever "
        "convenient")
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
    _bad(ADR.replace("run on desktop\n  with no network",
                     "require the network,\n  as does sync transport"))
    _bad(ADR.replace("the web/mobile\nsurface carries no heavy compute",
                     "the web/mobile\nsurface may run Stockfish and models"))
    _bad(ADR.replace("a feature, not\n  as the training story",
                     "the primary phone story"))


def test_mutation_operations_fails():
    _bad(ADR.replace("  stockfish: desktop", "  stockfish: web"))
    _bad(ADR.replace("  queue: web", "  queue: desktop"))


def test_mutation_offline_required_fails():
    _bad(ADR.replace("  - training\n  - export", "  - export"))


def test_mutation_web_offline_capable_fails():
    _bad(ADR.replace("  - approval\n", ""))


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
