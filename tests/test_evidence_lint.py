"""T0007 v2: strict evidence-contract lint - adversarial tests for every bypass."""

import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools.evidence_lint as el  # noqa: E402

SHA = "a" * 40
OTHER = "b" * 40

TASKS = {
    "T9999": {
        "id": "T9999",
        "status": "done",
        "verification": "independent review",
        "done_sha": SHA,
    },
    "T9998": {"id": "T9998", "status": "in_progress", "verification": "auto"},
    "T9997": {"id": "T9997", "status": "done", "verification": "auto", "done_sha": SHA},
    "T9996": {
        "id": "T9996",
        "status": "done",
        "verification": "human checkpoint",
        "checkpoint": "C-X",
        "done_sha": SHA,
    },
    "T9995": {
        "id": "T9995",
        "status": "done",
        "verification": "independent verifier",
        "done_sha": SHA,
    },
    "T9001": {
        "id": "T9001",
        "status": "todo",
        "verification": "human checkpoint",
        "tags": ["deferred:marketing-wedge", "reverify:T9996"],
    },
    "T9002": {
        "id": "T9002",
        "status": "todo",
        "verification": "independent verifier",
        "tags": ["reverify:T9996"],
    },
}

WAMID = "wamid.HBgMOTE4MTIxNzk4Mjg1FQIAEhgUM0E2NDE0QzQ5MzI5NjgyNUIzREQA"

RECORD = f"""# Substitution: T9996 (example gate)

- Task: T9996 - example human gate.
- What the human would have done: decide.
- Why no human pass happened: no human available; owner delegated.
- Owner wamid: {WAMID} (2026-09-19).
- Provisional substitute decision: GO.
- Re-verify hook: re-run T9996 as a human gate once evidence exists.
"""

GRANTS = {
    "T9996": {
        "wamid": WAMID,
        "date": "2026-09-19",
        "record": "evidence/substitutions/T9996.md",
        "hooks": ["T9001", "T9002"],
    }
}


def subst_evidence(wamid=WAMID, record="evidence/substitutions/T9996.md", hooks="T9001, T9002"):
    verification = (
        "human checkpoint - judgment-substituted per owner "
        f"{wamid} (2026-09-19); provisional decision: {record}; "
        f"re-verify hooks: {hooks}"
    )
    return f"""# T9996 Example gate - evidence

Status: done
Verification: {verification}
Commands: `python tools/dag.py verify`. Environment: python 3.12.
Recorded merge SHA on main: {SHA}
"""


def with_record(
    tmp_path, monkeypatch, record_text=RECORD, record_rel="evidence/substitutions/T9996.md"
):
    monkeypatch.setattr(el, "ROOT", tmp_path)
    rp = tmp_path / record_rel
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(record_text)
    return rp


FULL = f"""# T9999 Example - evidence

Acceptance restated. Artifact at {SHA}.

Status: done
Verification: independent review - PASS at {SHA}
Commands: `pytest tests/test_example.py -q`. Environment: python 3.12.
Recorded merge SHA on main: {SHA}
"""

AUTO = f"""# T9997 Auto task - evidence

Status: done
Verification: auto - green CI at {SHA}
Commands: `python tools/x.py`. Environment: python 3.12.
Recorded merge SHA on main: {SHA}
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_full_contract_file_passes(tmp_path):
    assert el.lint_file(write(tmp_path, "T9999.md", FULL), TASKS, {}) == []
    assert el.lint_file(write(tmp_path, "T9997.md", AUTO), TASKS, {}) == []


def test_prose_marker_mention_does_not_grandfather(tmp_path):
    text = FULL + "\nThis file mentions `pre-contract: true` inline as prose.\n"
    assert el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {}) == []


def test_marker_line_on_non_allowlisted_file_fails(tmp_path):
    text = "# T9999 fake\n\npre-contract: true\n"
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("non-allowlisted" in e for e in errors)


def test_allowlisted_file_passes_but_edit_invalidates(tmp_path):
    original = "# T9999 old\n\npre-contract: true - indexed.\n"
    p = write(tmp_path, "T9999.md", original)
    allow = {"evidence/T9999.md": hashlib.sha256(original.encode()).hexdigest()}
    assert el.lint_file(p, TASKS, allow, rel="evidence/T9999.md") == []
    # edit above the marker (marker still closes the file): digest mismatch
    p.write_text("# T9999 old - edited\n\npre-contract: true - indexed.\n")
    errors = el.lint_file(p, TASKS, allow, rel="evidence/T9999.md")
    assert any("edited since grandfathering" in e for e in errors)


def test_pending_word_in_prose_does_not_satisfy_status(tmp_path):
    text = "# T9999 pending work\n\nEverything is pending, honestly.\n"
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("Status:" in e for e in errors)


def test_done_without_board_done_fails(tmp_path):
    text = FULL.replace("# T9999", "# T9998")
    errors = el.lint_file(write(tmp_path, "T9998.md", text), TASKS, {})
    assert any("board status is not done" in e for e in errors)


def test_done_with_wrong_sha_fails(tmp_path):
    text = FULL.replace(
        f"Recorded merge SHA on main: {SHA}", f"Recorded merge SHA on main: {OTHER}"
    )
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("!= board done_sha" in e for e in errors)


def test_short_sha_fails(tmp_path):
    text = FULL.replace(f"Recorded merge SHA on main: {SHA}", "Recorded merge SHA on main: aaaa")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("40-hex" in e for e in errors)


def test_verification_banana_fails(tmp_path):
    text = FULL.replace("Verification: independent review", "Verification: banana")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("mode" in e for e in errors)


def test_independent_done_needs_pass_at_matching_sha(tmp_path):
    text = FULL.replace(
        f"Verification: independent review - PASS at {SHA}",
        "Verification: independent review - routed for review",
    )
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("PASS at" in e for e in errors)
    text2 = FULL.replace(f"PASS at {SHA}", f"PASS at {OTHER}")
    errors2 = el.lint_file(write(tmp_path, "T9999.md", text2), TASKS, {})
    assert any("PASS SHA" in e for e in errors2)


def test_unverified_verdict_fails(tmp_path):
    text = FULL.replace(f"PASS at {SHA}", "UNVERIFIED")
    errors = el.lint_file(write(tmp_path, "T9999.md", text), TASKS, {})
    assert any("UNVERIFIED" in e for e in errors)


def test_commands_need_command_and_environment(tmp_path):
    no_env = FULL.replace(". Environment: python 3.12.", ".")
    errors = el.lint_file(write(tmp_path, "T9999.md", no_env), TASKS, {})
    assert any("Environment:" in e for e in errors)
    no_cmd = FULL.replace("`pytest tests/test_example.py -q`. ", "")
    errors = el.lint_file(write(tmp_path, "T9999.md", no_cmd), TASKS, {})
    assert any("backticked" in e for e in errors)


def test_repo_tree_is_lint_clean():
    out = subprocess.run(
        [sys.executable, "tools/evidence_lint.py"], cwd=ROOT, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stdout


def test_allowlist_is_frozen_yaml_with_sha256():
    data = yaml.safe_load((ROOT / "data/evidence-pre-contract.yaml").read_text())
    assert len(data["files"]) == 23
    for path, digest in data["files"].items():
        assert len(digest) == 64, path
        assert (ROOT / path).exists(), path


"""v3 adversarial tests: frozen allowlist, EOF marker, DAG membership, strict fields."""


EXPECTED_ALLOWLIST = {
    "evidence/T2229.md": "d7e091ad0e0ecdb40c21b4b1d3f5a1fa842fe9690a7b30fba31117f6b1300daa",
    "evidence/T2230.md": "01a13dcda7d4b0457ea7f83106976eac1bbc0fb6940b4efc1dabd10b35743102",
    "evidence/T2231.md": "f635ec9e7d124bb2ade2f6b5b854464e288f4639ee9b51bb44379f106e1efe41",
    "evidence/T2232.md": "955b2a47fca203742a28a6a64a736f08cf3d5878a89ae907aa37c824906f94bb",
    "evidence/T2233.md": "8a71c57991f3bb8d716044a34ca3a26831e777127ad8b5ed3b370e7a4bc644d3",
    "evidence/T2234.md": "e52b64687cb45028b90b5bd1517c5915da90764862670673d052a80d0f6de296",
    "evidence/T2235.md": "af861ad43de15357e03ca57f1472f560bf5a1d1474815638723fb4fff2041915",
    "evidence/T2236.md": "44d1be44756cf16b13d015648eeac7d1bb5191cf3e9da6510fac8bf2276bfa7c",
    "evidence/T2237.md": "a878c8b3c00d83f96a29b835871c98b2efae9fc37b9e2c33a982ab371b3c764d",
    "evidence/T2710.md": "1ecc596f34297b51c9fa06dbfc0503460a8792fffd6ecf516915dba6d4a33e68",
    "evidence/T2711.md": "670f6326236e1e9ef347e0a6ae26e8263ced7297137730e450b3c7cfe978493c",
    "evidence/T2712.md": "d8b3412efbc5d3bf310a1307478557e3d82d914468e1ad91193b502fb02646db",
    "evidence/T2713.md": "0714cb90ef0e24d0b9ad9e8805a50a0d147a1e6b5855f8f3052753fa8cad21bf",
    "evidence/T2714.md": "84112a011a89a09e35715ee5e69b199689703bd96b6b616de61b92ee21999605",
    "evidence/T2715.md": "9c418cc637b9dc8a2e717a2056684d96e63f14570cdd1aeb589229110feb5dfd",
    "evidence/T2716.md": "0ba61bac6db05d6e4ef0057d3abb263f01c6147d5419f57bc9605ab7764782e9",
    "evidence/T2717.md": "e1d898f3957df9ea6d719fc259747b6fcb43bf33f70c58b97f0ccf97103b6e24",
    "evidence/T2718.md": "9ba2cd041194fd049eac399216f752450122a05994de738bfbefbb1de0af655c",
    "evidence/T2719.md": "6279c717bc6cd1f1e51b91c08ecc726afdfdd5939462fafa82bbbca7121a77de",
    "evidence/T2720.md": "1123d79a606a72678c302067b09be393ea2542feea5fa4944c7e035c5611d8a7",
    "evidence/T2721.md": "968e2a965aa894aa638125d850cf848aa3e631e0421003bba1cb74122a047b36",
    "evidence/T2722.md": "8c884034c50e7fff13149dbc76679bd319077dd535ed3d07ae34fc25c16c30ba",
    "evidence/T2723.md": "62d9e4066e493ef39cf4bd228147c9a46e07673ea0fca1115d90bdc1784fe950",
}


def test_allowlist_matches_exact_pinned_mapping():
    allow, errors = el.load_allowlist(ROOT / "data" / "evidence-pre-contract.yaml")
    assert errors == []
    assert allow == EXPECTED_ALLOWLIST
    assert len(allow) == 23


def test_allowlist_replacement_preserving_count_fails_closed(tmp_path):
    # verifier attack: swap a real entry for a fake done file, keep 23 entries
    fake = tmp_path / "T9999.md"
    fake.write_text("# T9999 fake done\n\nStatus: done\npre-contract: true\n")
    tampered = dict(EXPECTED_ALLOWLIST)
    tampered.pop(next(iter(tampered)))
    tampered["evidence/T9999.md"] = hashlib.sha256(fake.read_bytes()).hexdigest()
    assert len(tampered) == 23
    p = tmp_path / "evidence-pre-contract.yaml"
    p.write_text(yaml.safe_dump({"files": tampered}))
    allow, errors = el.load_allowlist(p)
    assert allow == {}
    assert any("tampered" in e for e in errors)
    # fail closed: the fake marker file is now non-allowlisted
    errs = el.lint_file(fake, TASKS, allow, rel="evidence/T9999.md")
    assert any("non-allowlisted" in e for e in errs)


def test_allowlist_digest_constant_covers_current_file():
    raw = (ROOT / "data" / "evidence-pre-contract.yaml").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == el.ALLOWLIST_SHA256


def test_marker_before_trailing_content_does_not_grandfather(tmp_path):
    original = "# T9999 old\n\npre-contract: true - indexed.\n"
    allow = {"evidence/T9999.md": hashlib.sha256(original.encode()).hexdigest()}
    p = write(tmp_path, "T9999.md", original + "trailing content after the marker\n")
    errors = el.lint_file(p, TASKS, allow, rel="evidence/T9999.md")
    # marker no longer closes the file -> full contract applies -> strict errors
    assert any("Status:" in e for e in errors)
    assert not any("edited since grandfathering" in e for e in errors)


def test_marker_with_trailing_whitespace_only_still_grandfathers(tmp_path):
    original = "# T9999 old\n\npre-contract: true - indexed.\n"
    allow = {"evidence/T9999.md": hashlib.sha256(original.encode()).hexdigest()}
    p = write(tmp_path, "T9999.md", original)
    assert el.lint_file(p, TASKS, allow, rel="evidence/T9999.md") == []


def test_unknown_task_missing_from_dag_fails(tmp_path):
    text = (
        "# T0000 ghost\n\nStatus: in-progress\n"
        "Verification: banana - nothing\n"
        "Commands: `true`. Environment: python 3.12.\n"
    )
    errors = el.lint_file(write(tmp_path, "T0000.md", text), TASKS, {})
    assert any("missing from tasks/dag.json" in e for e in errors)


def test_empty_and_lone_backticks_fail(tmp_path):
    for bad in (
        "Commands: ``. Environment: python 3.12.",
        "Commands: `. Environment: python 3.12.",
        "Commands: `   `. Environment: python 3.12.",
    ):
        text = f"# T9998 wip\n\nStatus: in-progress\nVerification: auto - building\n{bad}\n"
        errors = el.lint_file(write(tmp_path, "T9998.md", text), TASKS, {})
        assert any("nonempty backticked command" in e for e in errors), bad


def test_empty_environment_value_fails(tmp_path):
    text = (
        "# T9998 wip\n\nStatus: in-progress\nVerification: auto - building\n"
        "Commands: `make test`. Environment:\n"
    )
    errors = el.lint_file(write(tmp_path, "T9998.md", text), TASKS, {})
    assert any("nonempty environment" in e for e in errors)


def subst_lint(
    tmp_path,
    monkeypatch,
    text,
    tasks=TASKS,
    grants=GRANTS,
    record_text=RECORD,
    record_rel="evidence/substitutions/T9996.md",
    name="T9996.md",
):
    if record_text is not None:
        with_record(tmp_path, monkeypatch, record_text=record_text, record_rel=record_rel)
    else:
        monkeypatch.setattr(el, "ROOT", tmp_path)
    return el.lint_file(write(tmp_path, name, text), tasks, {}, grants)


def test_judgment_substitution_passes(tmp_path, monkeypatch):
    assert subst_lint(tmp_path, monkeypatch, subst_evidence()) == []


def test_substitution_requires_pinned_grant(tmp_path, monkeypatch):
    problems = subst_lint(tmp_path, monkeypatch, subst_evidence(), grants={})
    assert any("no pinned judgment grant" in p for p in problems)


def test_substitution_mode_matrix_rejected(tmp_path, monkeypatch):
    """Only exactly 'human checkpoint' is substitutable."""
    for mode in (
        "auto + human",
        "human/external",
        "human/legal audit",
        "independent verifier",
        "human",
    ):
        tasks = {**TASKS, "T9996": {**TASKS["T9996"], "verification": mode}}
        text = subst_evidence().replace("Verification: human checkpoint", f"Verification: {mode}")
        problems = subst_lint(tmp_path, monkeypatch, text, tasks=tasks)
        assert any("not substitutable" in p for p in problems), mode


def test_substitution_verdict_grants_nothing_on_auto_mode(tmp_path, monkeypatch):
    """A substitution verdict on an auto-mode task is inert: no human
    gate exists to substitute, so the task stands on auto evidence."""
    tasks = {**TASKS, "T9996": {**TASKS["T9996"], "verification": "auto"}}
    text = subst_evidence().replace("Verification: human checkpoint", "Verification: auto")
    problems = subst_lint(tmp_path, monkeypatch, text, tasks=tasks)
    assert not any("substitut" in p for p in problems)


def test_substitution_requires_nonempty_checkpoint(tmp_path, monkeypatch):
    for empty in ("", None):
        tasks = {**TASKS, "T9996": {**TASKS["T9996"], "checkpoint": empty}}
        problems = subst_lint(tmp_path, monkeypatch, subst_evidence(), tasks=tasks)
        assert any("nonempty board checkpoint" in p for p in problems), empty


def test_substitution_verdict_must_match_grant(tmp_path, monkeypatch):
    cases = [
        ({"wamid": "wamid.OTHER"}, subst_evidence(), "verdict wamid != pinned grant wamid"),
        ({"date": "2026-09-20"}, subst_evidence(), "verdict date != pinned grant date"),
        (
            {"record": "evidence/substitutions/T9997.md"},
            subst_evidence(),
            "verdict record != pinned grant record",
        ),
        ({"hooks": ["T9002", "T9001"]}, subst_evidence(), "verdict hooks != pinned grant hooks"),
    ]
    for patch, text, needle in cases:
        grants = {"T9996": {**GRANTS["T9996"], **patch}}
        problems = subst_lint(tmp_path, monkeypatch, text, grants=grants)
        assert any(needle in p for p in problems), needle


def test_substitution_tampered_verdict_fails(tmp_path, monkeypatch):
    text = subst_evidence(
        wamid="wamid.HBgMOTE4MTIxNzk4Mjg1FQIAEhgUM0I4RkZBRkZBQUFBQUFBQUFBQUFBQUFBQUFBQUUA"
    )
    problems = subst_lint(tmp_path, monkeypatch, text)
    assert any("wamid" in p for p in problems)


def test_substitution_missing_record_fails(tmp_path, monkeypatch):
    problems = subst_lint(tmp_path, monkeypatch, subst_evidence(), record_text=None)
    assert any("missing" in p for p in problems)


def test_substitution_record_for_other_task_fails(tmp_path, monkeypatch):
    problems = subst_lint(
        tmp_path,
        monkeypatch,
        subst_evidence(record="evidence/substitutions/T9997.md"),
        record_rel="evidence/substitutions/T9997.md",
    )
    assert any("not T9996" in p for p in problems)


def test_substitution_record_title_must_name_task(tmp_path, monkeypatch):
    problems = subst_lint(
        tmp_path,
        monkeypatch,
        subst_evidence(),
        record_text=RECORD.replace("# Substitution: T9996", "# Substitution: T9997"),
    )
    assert any("title must name" in p for p in problems)


def test_substitution_record_must_carry_grant_wamid(tmp_path, monkeypatch):
    problems = subst_lint(
        tmp_path, monkeypatch, subst_evidence(), record_text=RECORD.replace(WAMID, "wamid.OTHER")
    )
    assert any("does not carry" in p for p in problems)


def test_substitution_record_required_fields(tmp_path, monkeypatch):
    for field in (
        "What the human would have done:",
        "Why no human pass happened:",
        "Provisional substitute decision:",
        "Re-verify hook:",
    ):
        text = RECORD.replace(f"- {field}", f"- Omitted{field[-1]}")
        problems = subst_lint(tmp_path, monkeypatch, subst_evidence(), record_text=text)
        assert any(field.strip(":") in p for p in problems), field


def test_substitution_hook_rules(tmp_path, monkeypatch):
    # hook cannot be the task itself
    grants = {"T9996": {**GRANTS["T9996"], "hooks": ["T9996"]}}
    text = subst_evidence(hooks="T9996")
    problems = subst_lint(tmp_path, monkeypatch, text, grants=grants)
    assert any("cannot be T9996 itself" in p for p in problems)
    # unknown hook task
    grants = {"T9996": {**GRANTS["T9996"], "hooks": ["T4321"]}}
    text = subst_evidence(hooks="T4321")
    problems = subst_lint(tmp_path, monkeypatch, text, grants=grants)
    assert any("T4321 is not a dag task" in p for p in problems)
    # done hook supersedes the substitution
    tasks = {**TASKS, "T9001": {**TASKS["T9001"], "status": "done", "done_sha": SHA}}
    problems = subst_lint(tmp_path, monkeypatch, subst_evidence(), tasks=tasks)
    assert any("superseded" in p for p in problems)
    # hook without the reverify link-back tag
    tasks = {**TASKS, "T9001": {**TASKS["T9001"], "tags": []}}
    problems = subst_lint(tmp_path, monkeypatch, subst_evidence(), tasks=tasks)
    assert any("missing reverify:T9996 tag" in p for p in problems)
    # hook without a human/independent re-verification shape
    tasks = {**TASKS, "T9001": {**TASKS["T9001"], "verification": "auto"}}
    problems = subst_lint(tmp_path, monkeypatch, subst_evidence(), tasks=tasks)
    assert any("re-verification shape" in p for p in problems)


def test_grant_registry_tampering_fails_closed(tmp_path):
    p = tmp_path / "judgment-grants.yaml"
    p.write_text("grants: {}\n")
    mapping, errors = el.load_grants(p)
    assert mapping == {}
    assert any("tampered" in e for e in errors)


def test_substitution_never_for_independent_verifier(tmp_path, monkeypatch):
    tasks = {**TASKS, "T9995": {**TASKS["T9995"], "checkpoint": "C-IV"}}
    text = (
        subst_evidence()
        .replace("Verification: human checkpoint", "Verification: independent verifier")
        .replace("# T9996 Example gate", "# T9995 Example gate")
    )
    problems = subst_lint(tmp_path, monkeypatch, text, tasks=tasks, name="T9995.md")
    assert any("not substitutable" in p for p in problems)


def test_substitution_malformed_verdict_fails(tmp_path, monkeypatch):
    text = subst_evidence().replace("; provisional decision:", "; decision:")
    problems = subst_lint(tmp_path, monkeypatch, text)
    assert any("PASS at" in p or "human pass required" in p.lower() for p in problems)
