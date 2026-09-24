"""T0248: store export contract behavior battery.

Reference engine FULLY DERIVED from data/contracts/export.yaml:
a canonical, read-only export of the current state of a
WAL-logged graph store. The source log validates and replays
through the LINKED WAL machinery (imported, never restated); the
state id is the linked migration state digest; the document
exporter is UNTRUSTED input behind the boundary (single
evaluation per export, detached argument copy, frozen log, state
and request, exact built-in-str UTF-8-encodable output bound
byte-for-byte to the LOCAL canonical rendering). Export is
read-only: every export, accepted or rejected, leaves the log and
the request bit-identical.
"""

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

from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    STARTPOS,
)
from tests.test_t0194_migration_contract import (  # noqa: E402
    state_id,
)
from tests.test_t0212_wal_contract import (  # noqa: E402
    GENESIS,
    WalEngine,
    WalError,
    _log_of,
    canonical_payload,
)
from tools.export_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

_CC = yaml.safe_load(CONTRACT.read_text())["contract"]
_FIELDS = _CC["record"]["fields"]
_REQUEST_FIELDS = _CC["request"]["fields"]
_FORMATS = tuple(_CC["request"]["formats"])
_EXPORT_RE = re.compile(_CC["identifiers"]["export_id"]["grammar"])
_HEAD_RE = re.compile(_CC["identifiers"]["head"]["grammar"])
_STATE_RE = re.compile(_CC["identifiers"]["state_id"]["grammar"])

_WAL = WalEngine(canonical_payload)  # linked, trusted


class ExportError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise ExportError(cls, FAILURE_MAPPING[cls])


def render_document(state, fmt):
    """THE pinned canonical rendering: one line per live record,
    sorted by identity; each line is canonical JSON (sorted keys,
    compact separators) of {"identity": ..., "record": {...}}."""
    assert fmt == "jsonl-v1", fmt
    lines = []
    for key in sorted(state):
        lines.append(
            json.dumps(
                {"identity": key, "record": dict(state[key])},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        )
    return "".join(lines)


class ExportEngine:
    """The contract's pinned export: exact request shape, closed
    format list, full linked-WAL validation and replay, frozen
    log/state/request, exactly one untrusted exporter call on a
    DETACHED state copy whose output must equal the LOCAL
    canonical rendering byte-for-byte; read-only commit."""

    def __init__(self, exporter):
        self.exporter = exporter  # UNTRUSTED

    def _export(self, frozen_state, fmt):
        """THE exporter boundary: raising ANY BaseException
        (including KeyboardInterrupt/SystemExit/GeneratorExit),
        non-exact-str or UTF-8-inencodable output fails closed as
        divergent_export."""
        try:
            out = self.exporter({key: dict(rec) for key, rec in frozen_state.items()}, fmt)
        except BaseException:
            # fail closed against the FULL BaseException surface
            _fail("divergent_export")
        if type(out) is not str:
            _fail("divergent_export")
        try:
            out.encode("utf-8")
        except UnicodeEncodeError:
            _fail("divergent_export")
        return out

    @staticmethod
    def _derive_export_id(head, sid, count, fmt, document):
        return (
            "exp1:"
            + hashlib.sha256(f"{head}\n{sid}\n{count}\n{fmt}\n{document}".encode()).hexdigest()
        )

    def export(self, log, request):
        """READ-ONLY and ATOMIC: exact request, closed format,
        linked-WAL replay, frozen snapshots taken BEFORE the one
        exporter call, document bound byte-for-byte to the local
        canonical rendering; log and request restored
        bit-identical on every exit."""
        if type(log) is not list:
            _fail("malformed_export_request")
        if type(request) is not dict:
            _fail("malformed_export_request")
        # KEY-TYPE GUARD before any set/hash comparison: a hostile key
        # whose __hash__ collides with a field name and whose __eq__
        # raises must fail closed typed, never escape raw
        if not all(type(key) is str for key in dict.keys(request)):
            _fail("malformed_export_request")
        if set(request.keys()) != set(_REQUEST_FIELDS):
            _fail("malformed_export_request")
        fmt = request["format"]
        if type(fmt) is not str:
            _fail("malformed_export_request")
        if fmt not in _FORMATS:
            _fail("unsupported_format")
        try:
            replayed = _WAL.replay(log)
        except WalError:
            _fail("corrupt_source")
        # INPUT PRESERVATION snapshot (reference-preserving) +
        # FREEZE of state and request, both BEFORE the exporter.
        saved_container, saved_entries = WalEngine._snapshot_log(log)
        saved_req = dict(request)
        frozen_state = {key: dict(rec) for key, rec in replayed["state"].items()}
        head = replayed["head"]
        sid = replayed["state_id"]
        try:
            document = self._export(frozen_state, fmt)
            # DOCUMENT BINDING: a valid-shaped but divergent
            # document (other state, other order, other encoding,
            # stateful variation) fails closed.
            if document != render_document(frozen_state, fmt):
                _fail("divergent_export")
        finally:
            WalEngine._restore_log(log, saved_container, saved_entries)
            request.clear()
            request.update(saved_req)
        count = len(frozen_state)
        return {
            "export_id": self._derive_export_id(head, sid, count, fmt, document),
            "head": head,
            "state_id": sid,
            "record_count": count,
            "format": fmt,
            "document": document,
        }


def _engine(exporter=render_document):
    return ExportEngine(exporter)


def _req(fmt="jsonl-v1"):
    return {"format": fmt}


def _three():
    return _log_of(("put", STARTPOS), ("put", KINGS), ("put", AFTER_E4))


def _raises(cls, fn, *args):
    with pytest.raises(ExportError) as err:
        fn(*args)
    assert err.value.failure_class == cls
    assert err.value.code == FAILURE_MAPPING[cls]
    assert err.value.code in ERROR_ENUM


# -- lint + happy path --------------------------------------------------------


def test_lint_clean():
    lint()


def test_happy_export_receipt():
    log = _three()
    before = copy.deepcopy(log)
    receipt = _engine().export(log, _req())
    assert list(receipt) == _FIELDS
    assert _EXPORT_RE.fullmatch(receipt["export_id"])
    assert _HEAD_RE.fullmatch(receipt["head"])
    assert _STATE_RE.fullmatch(receipt["state_id"])
    assert receipt["head"] == log[-1]["entry_id"]
    assert receipt["record_count"] == 3
    assert receipt["format"] == "jsonl-v1"
    lines = receipt["document"].splitlines()
    assert len(lines) == 3
    identities = [json.loads(line)["identity"] for line in lines]
    assert identities == sorted(identities)
    replayed = _WAL.replay(log)
    assert receipt["state_id"] == replayed["state_id"]
    assert receipt["document"] == render_document(replayed["state"], "jsonl-v1")
    assert log == before  # read-only


def test_empty_log_export():
    receipt = _engine().export([], _req())
    assert receipt["head"] == GENESIS
    assert receipt["state_id"] == state_id({})
    assert receipt["record_count"] == 0
    assert receipt["document"] == ""


def test_record_count_counts_live_records_not_entries():
    log = _log_of(("put", STARTPOS), ("put", KINGS), ("delete", KINGS))
    receipt = _engine().export(log, _req())
    assert receipt["record_count"] == 1
    assert len(receipt["document"].splitlines()) == 1
    assert receipt["head"] == log[-1]["entry_id"]


def test_export_id_binds_every_field():
    receipt = _engine().export(_three(), _req())
    base = ExportEngine._derive_export_id(
        receipt["head"],
        receipt["state_id"],
        receipt["record_count"],
        receipt["format"],
        receipt["document"],
    )
    assert base == receipt["export_id"]
    variants = [
        (GENESIS, receipt["state_id"], 3, "jsonl-v1", receipt["document"]),
        (receipt["head"], state_id({}), 3, "jsonl-v1", receipt["document"]),
        (receipt["head"], receipt["state_id"], 2, "jsonl-v1", receipt["document"]),
        (receipt["head"], receipt["state_id"], 3, "jsonl-v2", receipt["document"]),
        (receipt["head"], receipt["state_id"], 3, "jsonl-v1", receipt["document"] + "\n"),
    ]
    for args in variants:
        assert ExportEngine._derive_export_id(*args) != base


def test_determinism():
    a = _engine().export(_three(), _req())
    b = _engine().export(_three(), _req())
    assert a == b


def test_same_state_different_history_same_document():
    a = _engine().export(_log_of(("put", STARTPOS), ("put", KINGS)), _req())
    b = _engine().export(_log_of(("put", KINGS), ("put", STARTPOS)), _req())
    assert a["document"] == b["document"]
    assert a["state_id"] == b["state_id"]
    assert a["head"] != b["head"]
    assert a["export_id"] != b["export_id"]


def test_one_exporter_call_per_export():
    calls = []

    def counting(state, fmt):
        calls.append(fmt)
        return render_document(state, fmt)

    _engine(counting).export(_three(), _req())
    assert calls == ["jsonl-v1"]
    calls.clear()
    corrupt = _three()
    corrupt[0]["op"] = "upsert"
    for log, req in (
        ({}, _req()),
        (_three(), _req("csv")),
        (_three(), {"format": 1}),
        (corrupt, _req()),
    ):
        with pytest.raises(ExportError):
            _engine(counting).export(log, req)
    assert calls == []  # rejected before the boundary


# -- hostile requests, formats and logs ---------------------------------------


class _StrSub(str):
    pass


@pytest.mark.parametrize(
    "req",
    [
        None,
        [],
        "jsonl-v1",
        {},
        {"fmt": "jsonl-v1"},
        {"format": "jsonl-v1", "extra": 1},
        {"format": None},
        {"format": 1},
        {"format": b"jsonl-v1"},
        {"format": _StrSub("jsonl-v1")},
        {"format": ["jsonl-v1"]},
    ],
)
def test_total_over_hostile_requests(req):
    _raises("malformed_export_request", _engine().export, _three(), req)


@pytest.mark.parametrize(
    "fmt",
    [
        "",
        "jsonl",
        "jsonl-v2",
        "JSONL-V1",
        " jsonl-v1",
        "jsonl-v1\n",
        "csv",
        "pgn",
    ],
)
def test_unsupported_format(fmt):
    _raises("unsupported_format", _engine().export, _three(), _req(fmt))


@pytest.mark.parametrize("log", [None, {}, "log", 3, (1, 2)])
def test_total_over_hostile_log_types(log):
    _raises("malformed_export_request", _engine().export, log, _req())


def _corrupt_sources():
    def seq_gap(log):
        log[1]["sequence"] = 5

    def chain_break(log):
        log[2]["prior_entry_id"] = log[0]["entry_id"]

    def entry_tamper(log):
        log[1]["payload"]["record"]["snapshot_fen"] = STARTPOS

    def unknown_op(log):
        log[0]["op"] = "upsert"

    def entry_shape(log):
        log[1] = "not-an-entry"

    return [
        ("seq_gap", seq_gap),
        ("chain_break", chain_break),
        ("entry_tamper", entry_tamper),
        ("unknown_op", unknown_op),
        ("entry_shape", entry_shape),
    ]


@pytest.mark.parametrize("name,mutate", _corrupt_sources())
def test_corrupt_source_log(name, mutate):
    log = _three()
    mutate(log)
    before = copy.deepcopy(log)
    _raises("corrupt_source", _engine().export, log, _req())
    assert log == before, name


# -- hostile exporters ----------------------------------------------------------


def _hostile_exporters():
    def raising(state, fmt):
        raise RuntimeError("boom")

    def raising_keyboard_interrupt(state, fmt):
        raise KeyboardInterrupt

    def raising_system_exit(state, fmt):
        raise SystemExit(1)

    def raising_generator_exit(state, fmt):
        raise GeneratorExit

    def none(state, fmt):
        return None

    def as_bytes(state, fmt):
        return render_document(state, fmt).encode()

    def str_subclass(state, fmt):
        return _StrSub(render_document(state, fmt))

    def lone_surrogate(state, fmt):
        return render_document(state, fmt) + "\ud800"

    def empty(state, fmt):
        return ""

    def reversed_order(state, fmt):
        lines = render_document(state, fmt).splitlines(keepends=True)
        return "".join(reversed(lines))

    def other_state(state, fmt):
        return render_document({}, fmt) if state else render_document({"x": {"a": "1"}}, fmt)

    def dropped_record(state, fmt):
        return "".join(render_document(state, fmt).splitlines(keepends=True)[1:])

    def ascii_escaped(state, fmt):
        return "".join(
            json.dumps(json.loads(line), sort_keys=True, separators=(",", ":")) + "\n"
            for line in render_document(state, fmt).splitlines()
        ).replace("/", "\\/")

    def spaced_json(state, fmt):
        return "".join(
            json.dumps(json.loads(line), sort_keys=True) + "\n"
            for line in render_document(state, fmt).splitlines()
        )

    def no_trailing_newline(state, fmt):
        return render_document(state, fmt).rstrip("\n")

    return [
        (f.__name__, f)
        for f in (
            raising,
            raising_keyboard_interrupt,
            raising_system_exit,
            raising_generator_exit,
            none,
            as_bytes,
            str_subclass,
            lone_surrogate,
            empty,
            reversed_order,
            other_state,
            dropped_record,
            ascii_escaped,
            spaced_json,
            no_trailing_newline,
        )
    ]


@pytest.mark.parametrize("name,exporter", _hostile_exporters())
def test_hostile_exporter(name, exporter):
    log = _three()
    req = _req()
    before_log, before_req = copy.deepcopy(log), copy.deepcopy(req)
    _raises("divergent_export", _engine(exporter).export, log, req)
    assert log == before_log, name
    assert req == before_req, name


def test_stateful_alternating_exporter_never_accepts_divergence():
    calls = {"n": 0}

    def alternating(state, fmt):
        calls["n"] += 1
        honest = render_document(state, fmt)
        return honest if calls["n"] % 2 else honest + "\n"

    engine = _engine(alternating)
    first = engine.export(_three(), _req())
    assert first["document"] == render_document(_WAL.replay(_three())["state"], "jsonl-v1")
    _raises("divergent_export", engine.export, _three(), _req())


def test_exporter_mutating_its_state_argument_is_inert():
    def mutating(state, fmt):
        honest = render_document(state, fmt)
        for rec in state.values():
            rec["snapshot_fen"] = "evil"
        state.clear()
        return honest

    log = _three()
    receipt = _engine(mutating).export(log, _req())
    replayed = _WAL.replay(log)
    assert receipt["state_id"] == replayed["state_id"]
    assert receipt["document"] == render_document(replayed["state"], "jsonl-v1")


def test_exporter_mutating_log_and_request_during_call():
    log = _three()
    req = _req()
    before_log = copy.deepcopy(log)

    def meddling(state, fmt):
        honest = render_document(state, fmt)
        log[0]["payload"]["record"]["snapshot_fen"] = "evil"
        log.append({"junk": True})
        del log[1]
        req["format"] = "csv"
        req["extra"] = 1
        return honest

    receipt = _engine(meddling).export(log, req)
    assert log == before_log
    assert req == {"format": "jsonl-v1"}
    assert receipt["record_count"] == 3
    assert receipt["format"] == "jsonl-v1"


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


def _pins(items):
    """Keep-alive identity tokens of ITEMS, in order: the one helper every
    reference-preservation site and its replace-twice probe share."""
    return [_Pin(item) for item in items]


def test_rejected_export_leaves_inputs_bit_identical():
    log = _three()
    req = _req()
    entry_refs = _pins(log)
    before_log, before_req = copy.deepcopy(log), copy.deepcopy(req)

    def meddling_then_raise(state, fmt):
        log.clear()
        req.clear()
        raise ValueError("late")

    _raises("divergent_export", _engine(meddling_then_raise).export, log, req)
    assert log == before_log
    assert _pins(log) == entry_refs  # reference-preserving
    assert req == before_req


def test_successful_export_never_mutates_source():
    log = _three()
    refs = _pins(log)
    before = copy.deepcopy(log)
    _engine().export(log, _req())
    assert log == before
    assert _pins(log) == refs


# -- mutants the battery must kill ---------------------------------------------


def test_mutant_grammar_only_document_validation():
    """A mutant that only type-checks the exporter output accepts
    a divergent document; the pinned engine rejects it."""

    def mutant_export(engine, log, request):
        replayed = _WAL.replay(log)
        return engine._export(replayed["state"], request["format"])

    def divergent(state, fmt):
        return render_document({}, fmt)

    assert mutant_export(_engine(divergent), _three(), _req()) == ""
    _raises("divergent_export", _engine(divergent).export, _three(), _req())


def test_mutant_export_skipping_source_validation():
    log = _three()
    log[1]["payload"]["record"]["snapshot_fen"] = STARTPOS

    def mutant_export(log, request):
        state = {}
        for entry in log:
            state[entry["payload"]["identity"]] = entry["payload"]["record"]
        return render_document(state, request["format"])

    assert mutant_export(log, _req())  # the mutant exports corruption
    _raises("corrupt_source", _engine().export, log, _req())


def test_mutant_live_request_reread_after_exporter():
    req = _req()

    def switching(state, fmt):
        req["format"] = "csv"
        return render_document(state, fmt)

    def mutant_export(engine, log, request):
        replayed = _WAL.replay(log)
        doc = engine._export(replayed["state"], request["format"])
        return {"format": request["format"], "document": doc}

    assert mutant_export(_engine(switching), _three(), req)["format"] == "csv"
    req = _req()
    receipt = _engine(switching).export(_three(), req)
    assert receipt["format"] == "jsonl-v1"
    assert req == {"format": "jsonl-v1"}


def test_mutant_unfrozen_state_passed_to_exporter():
    """A mutant handing the exporter the live replayed state lets
    the exporter rewrite what the receipt derives from."""

    def poisoning(state, fmt):
        state.clear()
        return render_document(state, fmt)

    replayed = _WAL.replay(_three())
    live = replayed["state"]
    ExportEngine(poisoning).exporter(live, "jsonl-v1")
    assert live == {}  # the mutant's derivation input is gone
    _raises("divergent_export", _engine(poisoning).export, _three(), _req())


# -- lint mutants ---------------------------------------------------------------


def _mutants():
    doc = yaml.safe_load(CONTRACT.read_text())
    out = []

    def add(name, path, value):
        m = copy.deepcopy(doc)
        node = m
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
        out.append((name, m))

    c = ["contract"]
    add("role kind drift", c + ["role", "kind"], "free-form-dump")
    add("not_scope drift", c + ["role", "not_scope"], "import-owned-here")
    add("record fields drift", c + ["record", "fields"], ["export_id"])
    add("record exact drift", c + ["record", "exact"], False)
    add(
        "document source drift",
        c + ["record", "field_definitions", "document", "source"],
        "exporter-output-trusted",
    )
    add("request fields drift", c + ["request", "fields"], ["format", "filter"])
    add("request exact drift", c + ["request", "exact"], False)
    add("format list widened", c + ["request", "formats"], ["jsonl-v1", "csv"])
    add("export id grammar drift", c + ["identifiers", "export_id", "grammar"], "^.*$")
    add("export id supplied", c + ["identifiers", "export_id", "source"], "caller-supplied")
    add(
        "export id derivation drift",
        c + ["identifiers", "export_id", "derivation"],
        "sha256-over-document",
    )
    add("head grammar drift", c + ["identifiers", "head", "grammar"], "^.*$")
    add("state id grammar drift", c + ["identifiers", "state_id", "grammar"], "^.*$")
    add("source validation dropped", c + ["semantics", "source_validation"], "log-trusted-as-given")
    add("format resolution drift", c + ["semantics", "format_resolution"], "nearest-format")
    add("rendering drift", c + ["semantics", "rendering"], "exporter-chooses")
    add("binding dropped", c + ["semantics", "binding"], "exporter-document-shape-checked-only")
    add("commit drift", c + ["semantics", "commit"], "export-may-compact-source")
    add("oracle trusted", c + ["oracle_boundary", "role"], "exporter-always-honest")
    add("single evaluation dropped", c + ["oracle_boundary", "single_evaluation"], "retry-allowed")
    add("frozen dropped", c + ["oracle_boundary", "frozen_snapshots"], "live-log-re-read")
    add("output validation dropped", c + ["oracle_boundary", "output_validation"], "any-output")
    add("failure class dropped", c + ["failures", "classes"], ["malformed_export_request"])
    add("failure trigger drift", c + ["failures", "triggers", "unsupported_format"], "never-fails")
    add("failure mapping drift", c + ["failures", "mapping", "divergent_export"], "internal")
    add("failures open", c + ["failures", "closed"], False)
    add("enum drift", c + ["errors", "closed_enum"], ["internal"])
    add(
        "retryable widened",
        c + ["errors", "shape", "retryable_true_only_for"],
        ["internal", "divergent_export"],
    )
    add("property drift", c + ["properties", "atomic"], "best-effort")
    add("base path drift", c + ["versioning", "base_path"], "/store/export/v0")
    add("link drift", c + ["links", "wal_contract"], "data/contracts/san.yaml")
    add("link missing file", c + ["links", "backup_contract"], "data/contracts/nope.yaml")
    add("extra section", c + ["notes"], "hidden-semantics")
    add("schema version drift", ["schema_version"], 2)
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 30
    for name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)
            pytest.fail(name)


def test_mutants_never_silent_subset():
    base = yaml.safe_load(CONTRACT.read_text())["contract"]
    covered = set()
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != base.get(section):
                covered.add(section)
    assert covered >= set(base) - {"id"}


def test_engine_error_codes_are_the_closed_enum():
    assert set(FAILURE_MAPPING.values()) | {"internal"} == set(ERROR_ENUM)
    assert list(FAILURE_MAPPING) == _CC["failures"]["classes"]


# -- hostile dict keys ------------------------------------------------------------


class _CollidingKey:
    """Hashes like a real field name; comparing it raises."""

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")


def _colliding_requests():
    return [
        ("only_colliding", lambda: {_CollidingKey("format"): "jsonl-v1"}),
        (
            "real_plus_colliding_other",
            lambda: {"format": "jsonl-v1", _CollidingKey("extra"): "jsonl-v1"},
        ),
        ("int_key", lambda: {1: "jsonl-v1"}),
    ]


@pytest.mark.parametrize("name,build", _colliding_requests())
def test_hostile_request_keys_fail_closed_typed(name, build):
    request = build()
    keys = list(dict.keys(request))
    log = _three()
    before_log = copy.deepcopy(log)
    _raises("malformed_export_request", _engine().export, log, request)
    assert list(dict.keys(request)) == keys, name
    assert log == before_log, name


def test_mutant_without_key_type_guard_escapes_raw():
    request = {_CollidingKey("format"): "jsonl-v1"}
    with pytest.raises(RuntimeError):
        set(request.keys()) != set(_REQUEST_FIELDS)  # noqa: B015 - the unguarded comparison
    _raises("malformed_export_request", _engine().export, _three(), request)


def test_fingerprint_pins_replaced_objects():
    """A replace-twice engine: the first swap frees the original and the
    second can land on its freed address, which a bare id() would miss.
    The fingerprint pins the original, so the swap goes red."""
    log = [{"a": 1}]
    ids = _pins(log)
    log[0] = {"a": 1}
    log[0] = {"a": 1}
    assert _pins(log) != ids
