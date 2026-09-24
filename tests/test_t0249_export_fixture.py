"""T0249: export conformance fixture - the fixture must PROVE
happy, boundary, malformed and rollback behavior against the
T0248 store-export contract. The cases execute against the
contract-derived reference in tests.test_t0248_export_contract
(itself derived from data/contracts/export.yaml plus the linked
WAL contract) - nothing is re-implemented here. Pinned export
receipts were computed from that reference at authoring time,
so any contract or derivation drift breaks this battery. Every
malformed case is discriminating: applying ONLY its declared
single-locus repair (log, request or exporter) makes it export
successfully. Rollback cases prove a rejected export leaves the
exact supplied log and request bit-identical before the valid
follow-up export succeeds with the pinned receipt.

DESIGN CAUTION: the reference engine is derived from the same
contract document, so this fixture proves fixture/contract
CONSISTENCY, not production behavior. The later runtime work
must execute these same cases against a separately implemented
runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0212_wal_contract import (  # noqa: E402
    WalEngine,
    WalError,
    canonical_payload,
)
from tests.test_t0248_export_contract import (  # noqa: E402
    ExportEngine,
    ExportError,
    render_document,
)
from tools.export_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
)

FIXTURE_SCHEMA_VERSION = 1
FIXTURE = Path(__file__).parent / "fixtures" / "export" / "cases.json"
CASES = json.loads(FIXTURE.read_text())
_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
FAILURE_CLASSES = set(_CC["failures"]["classes"])
RECEIPT_FIELDS = list(_CC["record"]["fields"])
_EXPORT_RE = re.compile(_CC["identifiers"]["export_id"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["head"]["grammar"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])

SECTIONS = ("happy", "boundary", "malformed", "rollback")
TOP_KEYS = {"schema", "contract", "contract_base_path", "notes", *SECTIONS}
OK_KEYS = {"name", "why", "log", "request", "exporter", "expect"}
BAD_KEYS = {"name", "defect", "expect_failure", "log", "request",
            "exporter", "minimal_repair"}
RB_KEYS = {"name", "why", "log", "request", "rejected_exporter",
           "expect_failure", "expect"}


# -- closed exporter registry (names in the fixture resolve ONLY here) -------


def _raising(state, fmt):
    raise ValueError("untrusted exporter failure")


def _bytes(state, fmt):
    return render_document(state, fmt).encode()


def _reversed(state, fmt):
    lines = render_document(state, fmt).splitlines(keepends=True)
    return "".join(reversed(lines))


def _surrogate(state, fmt):
    return render_document(state, fmt) + "\ud800"


def _spaced(state, fmt):
    return "".join(
        json.dumps({"identity": k, "record": dict(state[k])},
                   sort_keys=True, ensure_ascii=False) + "\n"
        for k in sorted(state))


def _no_final_newline(state, fmt):
    return render_document(state, fmt)[:-1]


EXPORTERS = {
    "canonical": render_document,
    "raising": _raising,
    "bytes": _bytes,
    "reversed": _reversed,
    "surrogate": _surrogate,
    "spaced": _spaced,
    "no_final_newline": _no_final_newline,
}


def _rollback_exporter(name, log, request):
    """Hostile exporters bound to the CALLER's live inputs."""

    def scribble():
        log.append({"forged": True})
        if log and type(log[0]) is dict:
            log[0]["op"] = "delete"
        request["format"] = "csv-v1"
        request["extra"] = 1

    def mutate_inputs_then_raise(state, fmt):
        scribble()
        raise RuntimeError("after scribbling")

    def mutate_inputs_then_diverge(state, fmt):
        scribble()
        return render_document(state, fmt) + "\n"

    def mutate_state_then_diverge(state, fmt):
        state.pop(sorted(state)[0])
        return render_document(state, fmt)

    return {
        "mutate_inputs_then_raise": mutate_inputs_then_raise,
        "mutate_inputs_then_diverge": mutate_inputs_then_diverge,
        "mutate_state_then_diverge": mutate_state_then_diverge,
    }[name]


ROLLBACK_EXPORTERS = ("mutate_inputs_then_raise",
                      "mutate_inputs_then_diverge",
                      "mutate_state_then_diverge")


# -- structure ---------------------------------------------------------------


def _validate_receipt(receipt, label):
    assert type(receipt) is dict, label
    assert set(receipt) == set(RECEIPT_FIELDS), label
    assert _EXPORT_RE.fullmatch(receipt["export_id"]), label
    assert _HEAD_RE.fullmatch(receipt["head"]), label
    assert _STATE_RE.fullmatch(receipt["state_id"]), label
    assert type(receipt["record_count"]) is int, label
    assert receipt["record_count"] >= 0, label
    assert receipt["format"] in _CC["request"]["formats"], label
    assert type(receipt["document"]) is str, label
    assert receipt["document"].count("\n") == receipt["record_count"], label


def _validate_structure(cases):
    assert type(cases) is dict and set(cases) == TOP_KEYS
    assert cases["schema"] == FIXTURE_SCHEMA_VERSION
    assert type(cases["schema"]) is int
    assert cases["contract"] == _CC["id"]
    assert cases["contract_base_path"] == _CC["versioning"]["base_path"]
    assert type(cases["notes"]) is str and cases["notes"]
    names = []
    for section in SECTIONS:
        rows = cases[section]
        assert type(rows) is list and rows, section
        for row in rows:
            assert type(row) is dict, section
            names.append(row.get("name"))
            label = f"{section}:{row.get('name')}"
            assert type(row.get("name")) is str, label
            assert re.fullmatch(r"[a-z0-9_]+", row["name"]), label
            if section in ("happy", "boundary"):
                assert set(row) == OK_KEYS, label
                assert row["exporter"] == "canonical", label
                _validate_receipt(row["expect"], label)
            elif section == "malformed":
                assert set(row) == BAD_KEYS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                assert row["exporter"] in EXPORTERS, label
                assert type(row["defect"]) is str and row["defect"], label
                repair = row["minimal_repair"]
                assert type(repair) is dict and len(repair) == 1, label
                (locus, value), = repair.items()
                assert locus in ("log", "request", "exporter"), label
                assert value != row[locus], label
                if locus == "exporter":
                    assert value == "canonical", label
            else:
                assert set(row) == RB_KEYS, label
                assert row["rejected_exporter"] in ROLLBACK_EXPORTERS, label
                assert row["expect_failure"] in FAILURE_CLASSES, label
                _validate_receipt(row["expect"], label)
    assert len(names) == len(set(names)), "fixture names must be unique"


# -- the closed scenario manifests -------------------------------------------
# Every section's ordered names must equal its manifest EXACTLY (no
# missing, duplicated, renamed, reordered or extra rows) and every
# row's metadata and semantic shape must equal its manifest entry.
G0 = "wal0:" + "0" * 64

# happy/boundary: name -> (op sequence, record_count, head kind)
HAPPY_MANIFEST = {
    "three_live_records": (["put", "put", "put"], 3, "wal1"),
    "single_record": (["put"], 1, "wal1"),
}
BOUNDARY_MANIFEST = {
    "empty_log_genesis": ([], 0, "wal0"),
    "put_then_delete_zero_records": (["put", "delete"], 0, "wal1"),
    "delete_leaves_one_live": (["put", "put", "delete"], 1, "wal1"),
}
# malformed: name -> (failure class, exporter, repair locus,
# pinned defect text); the name is the CLOSED scenario tag
MALFORMED_MANIFEST = {
    "log_not_a_list": ('malformed_export_request', 'canonical', 'log',
        'the source log is a dict, not a list'),
    "request_missing_format": ('malformed_export_request', 'canonical', 'request',
        'request has no format field'),
    "request_extra_field": ('malformed_export_request', 'canonical', 'request',
        'request carries an unknown extra field'),
    "format_not_a_string": ('malformed_export_request', 'canonical', 'request',
        'format is an int, not a string'),
    "format_outside_closed_list": ('unsupported_format', 'canonical', 'request',
        'format csv-v1 is not in the closed list'),
    "format_wrong_case": ('unsupported_format', 'canonical', 'request',
        'format match is exact, JSONL-V1 is not jsonl-v1'),
    "tampered_record_digest": ('corrupt_source', 'canonical', 'log',
        'entry 2 record digest does not match its snapshot'),
    "sequence_gap": ('corrupt_source', 'canonical', 'log',
        'entry 2 removed, breaking sequence and prior link'),
    "forged_entry_id": ('corrupt_source', 'canonical', 'log',
        'entry 3 entry_id does not re-derive'),
    "exporter_raises": ('divergent_export', 'raising', 'exporter',
        'the exporter raises'),
    "exporter_non_string": ('divergent_export', 'bytes', 'exporter',
        'the exporter returns bytes, not str'),
    "exporter_reversed_order": ('divergent_export', 'reversed', 'exporter',
        'the exporter emits lines in reverse identity order'),
    "exporter_surrogate": ('divergent_export', 'surrogate', 'exporter',
        'the exporter output is not UTF-8 encodable'),
    "exporter_pretty_json": ('divergent_export', 'spaced', 'exporter',
        'the exporter uses non-compact separators'),
    "exporter_missing_trailing_newline": ('divergent_export', 'no_final_newline', 'exporter',
        'the exporter drops the final newline'),
}
# rollback: name -> (rejected exporter, failure class, op sequence,
# record_count of the valid follow-up)
ROLLBACK_MANIFEST = {
    "mutate_inputs_then_raise": ("mutate_inputs_then_raise",
                                 "divergent_export",
                                 ["put", "put", "put"], 3),
    "mutate_inputs_then_diverge": ("mutate_inputs_then_diverge",
                                   "divergent_export",
                                   ["put", "put", "put"], 3),
    "mutate_state_then_diverge": ("mutate_state_then_diverge",
                                  "divergent_export",
                                  ["put", "put", "put"], 3),
}
MANIFESTS = {"happy": HAPPY_MANIFEST, "boundary": BOUNDARY_MANIFEST,
             "malformed": MALFORMED_MANIFEST,
             "rollback": ROLLBACK_MANIFEST}

# content binding: canonical sha256 of every row (log, request,
# exporter, repair, defect and pinned receipt). Any edit to a row
# must update this table in the same change.
ROW_DIGESTS = {
    "happy:three_live_records":
        "28ee11e980c7b56c864b1772df3f6ba49e2166849452c29a331a09dae6884a60",
    "happy:single_record":
        "74ac41230bf5b090118a9daadb8f3d69429de9aa40f969521cac0d78e805b2c1",
    "boundary:empty_log_genesis":
        "c26abd85cff85c0a58f5e2f4404940f0eecea358fa222793205e7a101bac36a1",
    "boundary:put_then_delete_zero_records":
        "1fc31564561ec14179cd96e12df32710dbc5f4c6f315f4e78571f7172517eb82",
    "boundary:delete_leaves_one_live":
        "285eb91f17bd1875c19727a03b827ec14be972c6d15cdfc5de402d65d5f94073",
    "malformed:log_not_a_list":
        "8dc53646f3e322cd01a287b152cc92e73bfd9dbc7e4fb6c3219dfe7c496090d8",
    "malformed:request_missing_format":
        "54bf774624682f5d473b20af306af9852f812234b86e2a35c0c1e63ee328d99d",
    "malformed:request_extra_field":
        "9205226cc152dbe9e32a4882911ac8d5b664e02752db3650ab8552673024afd4",
    "malformed:format_not_a_string":
        "b816921283deaf734e8a4f3e278fa994ce31d0d1fc2aa1c09fd22bbf36151dae",
    "malformed:format_outside_closed_list":
        "7174a854fb72c09c9725655ffe4b4cfab5233d64f8eb3c8fbbd57e89a4f61a7a",
    "malformed:format_wrong_case":
        "111582c69eee26088258fa73a729c346469d15b97c0be60358a5eae7a098f2d6",
    "malformed:tampered_record_digest":
        "0237615c94febc0184db861bfc33a34d37cdb54d07b33c1d9e80354707c04cc0",
    "malformed:sequence_gap":
        "243351591a53a08e6b56756b0ba4113ac1c1bd115c62a71da42e7c84d8106548",
    "malformed:forged_entry_id":
        "d625cfd31b4477c02435c1e25434ecf0fd29022e3e6b952f1ad9dcc41847de85",
    "malformed:exporter_raises":
        "1923190d42f7b33eeca937cba2950fbd0842c602a1db3c84ddee03f8012aacaf",
    "malformed:exporter_non_string":
        "470ea2034701ed4b4adece1db3c5d99aec1449a56dabb52cfa6422ab075fc36d",
    "malformed:exporter_reversed_order":
        "7399beb743c0afed96a3e3057e3848e2b15cdb31891e9bd5710504f1decb36ea",
    "malformed:exporter_surrogate":
        "585c92971a1007725299bc22cc2a5ad9ee0f4db892ebc182a726fcd3cd0c1536",
    "malformed:exporter_pretty_json":
        "50e31e01b6f487cec175ddd4fa6655a849340cee7ef6a9012875bc9ce795b048",
    "malformed:exporter_missing_trailing_newline":
        "a71b5958cc62c3bc4ac94184ce890bde61d19c459f44bdd9ce50c925637319d3",
    "rollback:mutate_inputs_then_raise":
        "c803f253af31f07d6ab1379c31e0197b149da8a85d6f51a057872450eba1efbe",
    "rollback:mutate_inputs_then_diverge":
        "3d0bf6e6af6684f1b01471c0f7930cb4f62183e83c665dd23f78d84a49ea670a",
    "rollback:mutate_state_then_diverge":
        "aa45243e35b834f95f944de4744c523502cef03f42c629bd4ec163b90c1357a9",
}


def _row_digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()


def _ops(log):
    return [entry["op"] for entry in log]


def _diff_paths(a, b, path=()):
    """Every leaf path where two JSON values differ."""
    if type(a) is dict and type(b) is dict:
        out = set()
        for key in set(a) | set(b):
            if key not in a or key not in b:
                out.add((*path, key))
            else:
                out |= _diff_paths(a[key], b[key], (*path, key))
        return out
    if type(a) is list and type(b) is list and len(a) == len(b):
        out = set()
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out |= _diff_paths(x, y, (*path, i))
        return out
    return set() if (type(a) is type(b) and a == b) else {path}


def _canonical_ok(log, request):
    try:
        ExportEngine(render_document).export(copy.deepcopy(log),
                                             copy.deepcopy(request))
    except ExportError:
        return False
    return True


FORMATS = list(_CC["request"]["formats"])
VALID_REQ = {"format": FORMATS[0]}
_EXPORTER_TAGS = {"exporter_raises", "exporter_non_string",
                  "exporter_reversed_order", "exporter_surrogate",
                  "exporter_pretty_json",
                  "exporter_missing_trailing_newline"}


def _check_malformed_scenario(row):
    """Each closed tag: the data realizes EXACTLY that defect and the
    repair changes ONLY that locus."""
    name = row["name"]
    log, request = row["log"], row["request"]
    (locus, fix), = row["minimal_repair"].items()
    if name in _EXPORTER_TAGS:
        assert locus == "exporter" and fix == "canonical", name
        assert _canonical_ok(log, request), name
        good = render_document(_state_of(log), request["format"])
        fn = EXPORTERS[row["exporter"]]
        if name == "exporter_raises":
            with pytest.raises(ValueError):
                fn(_state_of(log), request["format"])
            return
        out = fn(_state_of(log), request["format"])
        if name == "exporter_non_string":
            assert type(out) is bytes and out.decode() == good, name
        elif name == "exporter_reversed_order":
            assert type(out) is str and out != good, name
            assert out.splitlines() == good.splitlines()[::-1], name
        elif name == "exporter_surrogate":
            assert type(out) is str and out[:-1] == good, name
            with pytest.raises(UnicodeEncodeError):
                out.encode("utf-8")
        elif name == "exporter_pretty_json":
            assert type(out) is str and out != good, name
            assert [json.loads(x) for x in out.splitlines()] == \
                [json.loads(x) for x in good.splitlines()], name
            assert ", " in out or ": " in out, name
        elif name == "exporter_missing_trailing_newline":
            assert out == good[:-1] and good.endswith("\n"), name
        else:
            raise AssertionError(name)
        return
    assert row["exporter"] == "canonical", name
    if locus == "request":
        assert _canonical_ok(log, fix), name
        assert fix == VALID_REQ, name
        if name == "request_missing_format":
            assert type(request) is dict and "format" not in request
            assert request == {}, name
        elif name == "request_extra_field":
            assert type(request) is dict and len(request) == 2, name
            assert request.get("format") == fix["format"], name
            # the one stray key is pinned (content-bound below too)
            assert {k: v for k, v in request.items()
                    if k != "format"} == {"filter": "all"}, name
        elif name == "format_not_a_string":
            assert set(request) == {"format"}, name
            assert type(request["format"]) is not str, name
        elif name == "format_outside_closed_list":
            fmt = request["format"]
            assert set(request) == {"format"} and type(fmt) is str
            assert fmt not in FORMATS, name
            assert fmt.lower() not in FORMATS, name
        elif name == "format_wrong_case":
            fmt = request["format"]
            assert set(request) == {"format"} and type(fmt) is str
            assert fmt not in FORMATS and fmt.lower() in FORMATS, name
        else:
            raise AssertionError(name)
        return
    assert locus == "log", name
    assert request == VALID_REQ, name
    assert _canonical_ok(fix, request), name
    if name == "log_not_a_list":
        assert type(log) is not list, name
        return
    assert type(log) is list, name
    if name == "tampered_record_digest":
        assert _diff_paths(log, fix) == {
            (1, "payload", "record", "digest")}, name
    elif name == "forged_entry_id":
        assert _diff_paths(log, fix) == {(2, "entry_id")}, name
    elif name == "sequence_gap":
        assert len(fix) == len(log) + 1, name
        assert fix[:1] + fix[2:] == log, name
    else:
        raise AssertionError(name)


def _state_of(log):
    return WalEngine(canonical_payload).replay(
        copy.deepcopy(log))["state"]


def _validate_closure(cases):
    for section, manifest in MANIFESTS.items():
        rows = cases[section]
        assert [r["name"] for r in rows] == list(manifest), section
        for row in rows:
            label = f"{section}:{row['name']}"
            assert ROW_DIGESTS.get(label) == _row_digest(row), label
            meta = manifest[row["name"]]
            if section in ("happy", "boundary"):
                ops, count, head = meta
                assert _ops(row["log"]) == ops, label
                assert row["expect"]["record_count"] == count, label
                assert row["expect"]["head"].split(":")[0] == head
                assert (row["expect"]["head"] == G0) == (ops == [])
            elif section == "malformed":
                assert (row["expect_failure"], row["exporter"],
                        next(iter(row["minimal_repair"])),
                        row["defect"]) == meta, label
                _check_malformed_scenario(row)
            else:
                rej, failure, ops, count = meta
                assert (row["rejected_exporter"],
                        row["expect_failure"]) == (rej, failure), label
                assert _ops(row["log"]) == ops, label
                assert row["expect"]["record_count"] == count, label
    assert set(ROW_DIGESTS) == {
        f"{s}:{n}" for s, m in MANIFESTS.items() for n in m}


def test_fixture_scenario_closure():
    _validate_closure(CASES)


def test_fixture_structure():
    _validate_structure(CASES)
    _validate_closure(CASES)


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(schema=2),
    lambda c: c.update(schema=True),
    lambda c: c.update(contract="store-backup"),
    lambda c: c.update(extra=1),
    lambda c: c.pop("rollback"),
    lambda c: c["happy"].clear(),
    lambda c: c["happy"][0].update(exporter="reversed"),
    lambda c: c["happy"][0]["expect"].pop("document"),
    lambda c: c["happy"][0]["expect"].update(export_id="exp1:zz"),
    lambda c: c["malformed"][0].update(expect_failure="internal"),
    lambda c: c["malformed"][0].update(exporter="eval"),
    lambda c: c["malformed"][0]["minimal_repair"].update(request={}),
    lambda c: c["malformed"][1].update(minimal_repair={"request": {}}),
    lambda c: c["malformed"][9].update(minimal_repair={"exporter": "bytes"}),
    lambda c: c["rollback"][0].update(rejected_exporter="canonical"),
    lambda c: c["boundary"].append(copy.deepcopy(c["happy"][0])),
], ids=lambda f: "mut")
def test_structure_mutations_fail(mutate):
    cases = copy.deepcopy(CASES)
    mutate(cases)
    with pytest.raises(AssertionError):
        _validate_structure(cases)


def test_failure_class_coverage():
    covered = {row["expect_failure"] for row in CASES["malformed"]}
    assert covered == FAILURE_CLASSES
    loci = {next(iter(row["minimal_repair"])) for row in CASES["malformed"]}
    assert loci == {"log", "request", "exporter"}


# -- execution ---------------------------------------------------------------


def _wal_replays(log):
    try:
        WalEngine(canonical_payload).replay(copy.deepcopy(log))
    except WalError:
        return False
    return True


def _export(exporter, log, request):
    return ExportEngine(exporter).export(log, request)


def _names(section):
    return [row["name"] for row in CASES[section]]


def _row(section, name):
    (row,) = [r for r in CASES[section] if r["name"] == name]
    return row


@pytest.mark.parametrize("section,name", [
    (s, n) for s in ("happy", "boundary") for n in _names(s)])
def test_pinned_receipts(section, name):
    row = _row(section, name)
    log, request = copy.deepcopy(row["log"]), copy.deepcopy(row["request"])
    receipt = _export(EXPORTERS[row["exporter"]], log, request)
    assert receipt == row["expect"]
    # read-only: the successful export never touched its inputs
    assert log == row["log"] and request == row["request"]
    # determinism: a second export is identical
    assert _export(render_document, log, request) == row["expect"]


def test_boundary_covers_genesis_and_zero_live_records():
    rows = {r["name"]: r["expect"] for r in CASES["boundary"]}
    heads = {e["head"] for e in rows.values()}
    assert "wal0:" + "0" * 64 in heads
    assert any(e["record_count"] == 0 and e["head"].startswith("wal1:")
               for e in rows.values())
    assert any(e["record_count"] < len(r["log"])
               for r, e in zip(CASES["boundary"], rows.values(), strict=True)
               if e["record_count"] > 0)


def _apply_repair(row):
    (locus, value), = row["minimal_repair"].items()
    parts = {"log": row["log"], "request": row["request"],
             "exporter": row["exporter"]}
    parts[locus] = value
    return parts


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed(name):
    row = _row("malformed", name)
    log, request = copy.deepcopy(row["log"]), copy.deepcopy(row["request"])
    with pytest.raises(ExportError) as err:
        _export(EXPORTERS[row["exporter"]], log, request)
    assert err.value.failure_class == row["expect_failure"]
    assert err.value.code == FAILURE_MAPPING[row["expect_failure"]]
    assert err.value.code in ERROR_ENUM
    assert log == row["log"] and request == row["request"]


@pytest.mark.parametrize("name", _names("malformed"))
def test_malformed_minimal_repair_is_discriminating(name):
    row = _row("malformed", name)
    parts = _apply_repair(row)
    receipt = _export(EXPORTERS[parts["exporter"]],
                      copy.deepcopy(parts["log"]),
                      copy.deepcopy(parts["request"]))
    _validate_receipt(receipt, name)
    assert receipt == _export(render_document,
                              copy.deepcopy(parts["log"]),
                              copy.deepcopy(parts["request"]))


@pytest.mark.parametrize("name", [
    n for n in _names("malformed")
    if _row("malformed", n)["expect_failure"] == "corrupt_source"])
def test_corrupt_source_rows_fail_the_linked_wal(name):
    row = _row("malformed", name)
    assert not _wal_replays(row["log"])
    assert _wal_replays(row["minimal_repair"]["log"])


def test_injected_valid_malformed_row_fails_rejection():
    row = copy.deepcopy(CASES["malformed"][9])
    row["exporter"] = "canonical"
    with pytest.raises(pytest.fail.Exception), pytest.raises(ExportError):
        _export(EXPORTERS[row["exporter"]], copy.deepcopy(row["log"]),
                copy.deepcopy(row["request"]))


def test_tampered_pinned_receipt_is_detected():
    row = copy.deepcopy(CASES["happy"][0])
    row["expect"]["record_count"] += 1
    receipt = _export(render_document, copy.deepcopy(row["log"]),
                      copy.deepcopy(row["request"]))
    assert receipt != row["expect"]


def test_malformed_param_ids_equal_fixture_names():
    assert _names("malformed") == list(MALFORMED_MANIFEST)
    for section, manifest in MANIFESTS.items():
        assert _names(section) == list(manifest), section


# -- rollback ----------------------------------------------------------------


class _Pin:
    """Identity token for id()-only fingerprint fallbacks: it holds a
    strong reference, so the object stays alive (its address cannot be
    freed and reused) for as long as the fingerprint does. It compares by
    identity only, so no user code runs."""

    __slots__ = ("obj",)

    def __init__(self, obj):
        self.obj = obj

    def __eq__(self, other):
        return type(other) is _Pin and other.obj is self.obj

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return id(self.obj)

    def __repr__(self):
        return f"<pin {id(self.obj):#x}>"


def _deep_ids(obj, out):
    out.append((_Pin(obj), type(obj)))
    if type(obj) is dict:
        for value in obj.values():
            _deep_ids(value, out)
    elif type(obj) is list:
        for value in obj:
            _deep_ids(value, out)
    return out


@pytest.mark.parametrize("name", _names("rollback"))
def test_rollback(name):
    row = _row("rollback", name)
    log, request = copy.deepcopy(row["log"]), copy.deepcopy(row["request"])
    before_ids = _deep_ids(log, []) + _deep_ids(request, [])
    hostile = _rollback_exporter(row["rejected_exporter"], log, request)
    with pytest.raises(ExportError) as err:
        _export(hostile, log, request)
    assert err.value.failure_class == row["expect_failure"]
    # bit-identical AND reference-identical restoration
    assert log == row["log"] and request == row["request"]
    assert _deep_ids(log, []) + _deep_ids(request, []) == before_ids
    # the valid follow-up on the very same objects succeeds as pinned
    assert _export(render_document, log, request) == row["expect"]
    assert log == row["log"] and request == row["request"]


def test_rollback_expect_matches_happy_pin_for_same_log():
    happy = {json.dumps(r["log"], sort_keys=True): r["expect"]
             for r in CASES["happy"]}
    for row in CASES["rollback"]:
        key = json.dumps(row["log"], sort_keys=True)
        if key in happy:
            assert row["expect"] == happy[key]


def test_rollback_exporters_are_all_exercised():
    assert {r["rejected_exporter"] for r in CASES["rollback"]} == set(
        ROLLBACK_EXPORTERS)




def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    value = {"k": {"x": 1}}
    before = _deep_ids(value, [])
    value["k"] = {"x": 1}
    value["k"] = {"x": 1}
    assert _deep_ids(value, []) != before
