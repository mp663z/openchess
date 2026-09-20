"""T0122: chess transposition-node contract behavior battery.

Reference implementation FULLY DERIVED from data/contracts/
transposition_node.yaml plus the linked siblings (variant,
position-digest, FEN, en-passant): node identity is the variant
contract's canonical_fields tuple computed through the digest
contract's derivations, the record shape and clock/ep normalization
come from the record section, the merge semantics from the merge
section, and the failure classes and their mapping from the failures
section. Nothing about chess is hardcoded here. Happy, transposition,
boundary, merge-algebra, malformed, rollback, collision, lint-mutant
and sibling-linkage batteries below.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_t0086_fen_contract import (  # noqa: E402
    FenError,
    emit_fen,
    parse_fen,
)
from tests.test_t0113_position_digest_contract import (  # noqa: E402
    _ep_identity,
    digest_fen,
)
from tools.transposition_node_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
DIGEST = ROOT / "data" / "contracts" / "position_digest.yaml"
EN_PASSANT = ROOT / "data" / "contracts" / "en_passant.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"


def _docs():
    return (yaml.safe_load(CONTRACT.read_text())["contract"],
            yaml.safe_load(VARIANT.read_text())["contract"],
            yaml.safe_load(DIGEST.read_text())["contract"],
            yaml.safe_load(EN_PASSANT.read_text())["contract"],
            yaml.safe_load(FEN.read_text())["contract"])


class NodeError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(contract, cls):
    raise NodeError(cls, contract["failures"]["mapping"][cls])


def _identity_tuple(nc, vc, dc, ec, fc, variant_id, position):
    """The linked variant contract's canonical_fields tuple, each
    component serialized per the linked contracts - the en-passant
    component is the IDENTITY value (legal-capture target or none)."""
    board, color, rights, ep, half, full = position
    placement = emit_fen(fc, position).split(" ")[0]
    return (variant_id, placement, color,
            rights or fc["castling"]["none_sentinel"],
            _ep_identity(dc, ec, fc, position))


def _snapshot_fen(nc, fc, identity):
    """Canonical six-field snapshot: identity components plus the
    normalized clocks pinned by the record section."""
    variant_id, placement, color, castling, ep_value = identity
    norm = nc["record"]["snapshot_clock_normalization"]
    assert norm == "halfmove-0-fullmove-1"
    return f"{placement} {color} {castling} {ep_value} 0 1"


def _make_record(nc, vc, dc, ec, fc, variant_id, fen_text):
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if variant_id not in ids:
        _fail(nc, "unknown_variant")
    try:
        position = parse_fen(fc, fen_text)
    except FenError:
        _fail(nc, "malformed_position")
    identity = _identity_tuple(nc, vc, dc, ec, fc, variant_id, position)
    return {
        "variant": variant_id,
        "digest": digest_fen(variant_id, fen_text),
        "snapshot_fen": _snapshot_fen(nc, fc, identity),
        "_identity": identity,
    }


class NodeTable:
    """The merge semantics of the contract's merge section: buckets
    keyed by the digest accelerator, equality by canonical field
    comparison ONLY, insert-or-return-existing."""

    def __init__(self, docs):
        self.nc, self.vc, self.dc, self.ec, self.fc = docs
        self.buckets = {}

    def insert(self, variant_id, fen_text):
        rec = _make_record(self.nc, self.vc, self.dc, self.ec,
                           self.fc, variant_id, fen_text)
        bucket = self.buckets.setdefault(rec["digest"], [])
        for existing in bucket:
            if existing["_identity"] == rec["_identity"]:
                return existing  # same node, never a second one
        bucket.append(rec)
        return rec

    def records(self):
        return [rec for bucket in self.buckets.values()
                for rec in bucket]

    def serialize(self):
        return sorted(
            (r["variant"], r["digest"], r["snapshot_fen"])
            for r in self.records())


def _table():
    return NodeTable(_docs())


def validate_record(nc, vc, dc, ec, fc, record):
    """A stored node record must satisfy the record section exactly:
    field set, known variant, pinned digest format, canonical
    snapshot with identity ep value and normalized clocks, and the
    digest consistent with the identity inside the snapshot."""
    if set(record.keys()) - {"_identity"} != set(
            nc["record"]["fields"]):
        _fail(nc, "malformed_node_record")
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if record["variant"] not in ids:
        _fail(nc, "unknown_variant")
    fmt = dc["digest"]["format"]["regex"]
    import re
    if re.fullmatch(fmt, record["digest"]) is None:
        _fail(nc, "malformed_node_record")
    try:
        position = parse_fen(fc, record["snapshot_fen"])
    except FenError:
        _fail(nc, "malformed_node_record")
    fields = record["snapshot_fen"].split(" ")
    if fields[4:] != ["0", "1"]:
        _fail(nc, "malformed_node_record")  # clock normalization
    identity = _identity_tuple(nc, vc, dc, ec, fc,
                               record["variant"], position)
    if fields[3] != identity[4]:
        _fail(nc, "malformed_node_record")  # ep identity value
    if record["digest"] != digest_fen(record["variant"],
                                      record["snapshot_fen"]):
        _fail(nc, "malformed_node_record")  # digest consistency
    return record


def _validate(record):
    nc, vc, dc, ec, fc = _docs()
    return validate_record(nc, vc, dc, ec, fc, record)


# -- pinned vectors -----------------------------------------------------

STARTPOS = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
LEGAL_EP = "rnbqkbnr/ppp1pppp/8/8/3pP3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
KINGS = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"
STARTPOS_DIGEST = (
    "pdv1:66157a24a6668babbcec26794a4cf7449818d5cfc7769cdaae2cae32"
    "a8b88ffe")


def test_lint_clean():
    lint()


def test_happy_insert_records_exact():
    t = _table()
    rec = t.insert("standard", STARTPOS)
    assert rec["variant"] == "standard"
    assert rec["digest"] == STARTPOS_DIGEST
    assert rec["snapshot_fen"] == STARTPOS  # clocks already 0 1
    _validate(rec)
    # phantom target collapses to the none sentinel in the snapshot
    rec = t.insert("standard", AFTER_E4)
    assert rec["snapshot_fen"].split(" ")[3] == "-"
    _validate(rec)
    # a capturable target survives in the snapshot
    rec = t.insert("standard", LEGAL_EP)
    assert rec["snapshot_fen"].split(" ")[3] == "e3"
    _validate(rec)


def test_transposition_path_invariance():
    """Every path to one identity reaches the SAME node: clocks and
    uncapturable targets never fork it."""
    t = _table()
    a = t.insert("standard", STARTPOS)
    b = t.insert("standard",
                 STARTPOS.replace(" 0 1", " 7 42"))  # clocks differ
    assert a is b
    c = t.insert("standard", AFTER_E4)
    d = t.insert("standard", AFTER_E4.replace(" e3 ", " - "))
    assert c is d  # phantom target: same node
    assert len(t.records()) == 2
    assert len({r["snapshot_fen"] for r in t.records()}) == 2


def test_boundary_minimal_and_distinct():
    t = _table()
    kings = t.insert("standard", KINGS)
    assert kings["snapshot_fen"] == KINGS
    other = t.insert("standard", KINGS.replace(" w ", " b "))
    assert other is not kings  # side to move is identity
    assert len(t.records()) == 2
    ep_a = t.insert("standard", LEGAL_EP)
    ep_b = t.insert("standard", LEGAL_EP.replace(" e3 ", " - "))
    assert ep_b is not ep_a  # capturable target: different node
    assert len(t.records()) == 4


def test_merge_algebra():
    """Idempotent, commutative, associative: the table is a function
    of the SET of identities inserted."""
    positions = [STARTPOS, AFTER_E4, LEGAL_EP, KINGS,
                 KINGS.replace(" w ", " b ")]
    t1 = _table()
    for fen in positions:
        t1.insert("standard", fen)
    t1.insert("standard", STARTPOS)  # idempotent reinsert
    t2 = _table()
    for fen in reversed(positions):
        t2.insert("standard", fen)
    assert t1.serialize() == t2.serialize()
    assert len(t1.records()) == len(positions)


MALFORMED_INSERTS = [
    ("chess960", STARTPOS, "unknown_variant"),
    ("standard", "8/8/8/8/8/8/8/4K3 w - - 0 1", "malformed_position"),
    ("standard", "garbage w - - 0 1", "malformed_position"),
]


@pytest.mark.parametrize("variant,fen,cls", MALFORMED_INSERTS)
def test_malformed_insert_rejected(variant, fen, cls):
    t = _table()
    with pytest.raises(NodeError) as exc:
        t.insert(variant, fen)
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert exc.value.code in ERROR_ENUM


def _valid_record():
    return _make_record(*_docs(), "standard", STARTPOS)


MALFORMED_RECORDS = []


def _record_cases():
    base = _valid_record()
    cases = []

    def add(name, mutate, cls="malformed_node_record"):
        rec = copy.deepcopy(base)
        mutate(rec)
        cases.append((name, rec, cls))

    add("extra-field", lambda r: r.__setitem__("note", "x"))
    add("missing-digest", lambda r: r.pop("digest"))
    add("unknown-variant", lambda r: r.__setitem__("variant", "c960"),
        "unknown_variant")
    add("digest-format", lambda r: r.__setitem__("digest", "pdv2:abc"))
    add("digest-mismatch", lambda r: r.__setitem__(
        "digest", "pdv1:" + "0" * 64))
    add("clocks-not-normalized", lambda r: r.__setitem__(
        "snapshot_fen", r["snapshot_fen"].replace(" 0 1", " 3 9")))
    add("snapshot-grammar", lambda r: r.__setitem__(
        "snapshot_fen", "garbage w - - 0 1"))

    def phantom_ep(r):
        # stored-form target with no legal capture: identity demands
        # the none sentinel.
        rec = _make_record(*_docs(), "standard", AFTER_E4)
        r["snapshot_fen"] = AFTER_E4
        r["digest"] = rec["digest"]
    add("ep-not-identity-value", phantom_ep)
    return cases


def test_malformed_records_rejected():
    for name, rec, cls in _record_cases():
        with pytest.raises(NodeError) as exc:
            _validate(rec)
        assert exc.value.failure_class == cls, name
        assert exc.value.code == FAILURE_MAPPING[cls]


def test_rollback_bit_identical():
    t = _table()
    t.insert("standard", STARTPOS)
    before = t.serialize()
    before_buckets = copy.deepcopy(t.buckets)
    for variant, fen in (("chess960", STARTPOS),
                         ("standard", "8/8/8/8/8/8/8/4K3 w - - 0 1"),
                         ("standard", "garbage w - - 0 1")):
        with pytest.raises(NodeError):
            t.insert(variant, fen)
    assert t.serialize() == before
    assert t.buckets == before_buckets


def test_collision_separated_by_field_comparison():
    """A digest collision never merges nodes: canonical field
    comparison separates unequal identities in one bucket."""
    t = _table()
    # force every insert into one bucket by rigging the accelerator
    real_make = _make_record

    def rigged(nc, vc, dc, ec, fc, variant_id, fen_text):
        rec = real_make(nc, vc, dc, ec, fc, variant_id, fen_text)
        rec["digest"] = STARTPOS_DIGEST  # collide everything
        return rec

    import tests.test_t0122_transposition_node_contract as self_mod
    self_mod._make_record = rigged
    try:
        a = t.insert("standard", STARTPOS)
        b = t.insert("standard", KINGS)
    finally:
        self_mod._make_record = real_make
    assert a is not b
    assert len(t.records()) == 2  # same bucket, different nodes
    assert len(t.buckets) == 1


# -- mutation battery ----------------------------------------------------


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

    add("role kind drift", ["contract", "role", "kind"],
        "graph-node-over-path")
    add("transposition drift", ["contract", "role", "transposition"],
        "each-path-own-node")
    add("annotations by path", ["contract", "role",
                                "annotations_attach"],
        "by-first-path")
    add("identity source drift", ["contract", "identity", "source"],
        "restated-here")
    add("accelerator drift", ["contract", "identity", "accelerator"],
        "digest-decides-equality")
    add("equality drift", ["contract", "identity", "equality"],
        "digest-comparison")
    add("exclusions drift", ["contract", "identity", "excluded"],
        ["halfmove_clock", "fullmove_number"])
    add("record fields drift", ["contract", "record", "fields"],
        ["variant", "digest"])
    add("extra record field", ["contract", "record", "fields"],
        ["variant", "digest", "snapshot_fen", "first_seen"])
    add("ep stored not identity", ["contract", "record",
                                   "snapshot_ep_value"],
        "stored-target-verbatim")
    add("clock normalization drift", ["contract", "record",
                                      "snapshot_clock_normalization"],
        "first-writer-wins")
    add("derived drift", ["contract", "record",
                          "derived_fields_regenerable"], [])
    add("insert drift", ["contract", "merge", "insert"],
        "always-create")
    add("idempotence dropped", ["contract", "merge", "idempotent"],
        False)
    add("commutativity dropped", ["contract", "merge", "commutative"],
        False)
    add("associativity dropped", ["contract", "merge", "associative"],
        False)
    add("two nodes allowed", ["contract", "merge",
                              "same_identity_never_two_nodes"], False)
    add("collision first-writer-wins", ["contract", "merge",
                                        "digest_collision"],
        "first-writer-wins")
    add("rejected insert mutates", ["contract", "merge",
                                    "rejected_insert_changes_nothing"],
        False)
    add("failure class dropped", ["contract", "failures", "classes"],
        ["malformed_node_record", "malformed_position"])
    add("failure mapping drift", ["contract", "failures", "mapping",
                                  "unknown_variant"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"], False)
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "malformed_request"])
    add("property drift", ["contract", "properties",
                           "path_invariance"],
        "path-changes-node")
    add("rollback property drift", ["contract", "properties",
                                    "rollback"], "best-effort")
    add("link drift", ["contract", "links", "fen_contract"],
        "data/contracts/san.yaml")
    add("base path drift", ["contract", "versioning", "base_path"],
        "/graph/transposition-node/v0")
    return out


def test_mutations_fail_lint(tmp_path):
    mutants = _mutants()
    assert len(mutants) >= 25
    for _name, m in mutants:
        path = tmp_path / "mutant.yaml"
        path.write_text(yaml.safe_dump(m))
        with pytest.raises(ContractError):
            lint(path)


def test_mutants_never_silent_subset():
    covered = set()
    for _name, m in _mutants():
        for section, content in m["contract"].items():
            if content != yaml.safe_load(
                    CONTRACT.read_text())["contract"].get(section):
                covered.add(section)
    assert covered >= {"role", "identity", "record", "merge",
                       "failures", "errors", "properties", "links",
                       "versioning"}


# -- linkage battery -----------------------------------------------------


def _lint_doc(tmp_path, name, mutate, target="variant"):
    docs = {"variant": VARIANT, "digest": DIGEST, "fen": FEN,
            "en_passant": EN_PASSANT}
    paths = {}
    for key, src in docs.items():
        data = yaml.safe_load(src.read_text())
        if key == target:
            mutate(data)
        dst = tmp_path / f"{name}-{key}.yaml"
        dst.write_text(yaml.safe_dump(data))
        paths[key] = dst
    return paths


def test_linkage_clean_copy_passes(tmp_path):
    paths = _lint_doc(tmp_path, "clean", lambda d: None)
    lint(CONTRACT, variant_path=paths["variant"],
         digest_path=paths["digest"], fen_path=paths["fen"],
         en_passant_path=paths["en_passant"])


def test_linkage_variant_fields_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["canonical_fields"] = [
            "variant", "board", "side_to_move", "castling_rights"]
    paths = _lint_doc(tmp_path, "v", drift)
    with pytest.raises(ContractError):
        lint(CONTRACT, variant_path=paths["variant"],
             digest_path=paths["digest"], fen_path=paths["fen"],
             en_passant_path=paths["en_passant"])


def test_linkage_digest_format_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["digest"]["format"]["prefix"] = "pdv2:"
    paths = _lint_doc(tmp_path, "d", drift, target="digest")
    with pytest.raises(ContractError):
        lint(CONTRACT, variant_path=paths["variant"],
             digest_path=paths["digest"], fen_path=paths["fen"],
             en_passant_path=paths["en_passant"])


def test_linkage_digest_exclusions_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["identity"]["excluded"] = ["halfmove_clock"]
    paths = _lint_doc(tmp_path, "d2", drift, target="digest")
    with pytest.raises(ContractError):
        lint(CONTRACT, variant_path=paths["variant"],
             digest_path=paths["digest"], fen_path=paths["fen"],
             en_passant_path=paths["en_passant"])


def test_linkage_fen_field_order_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["fields"]["order"] = list(
            reversed(d["contract"]["fields"]["order"]))
    paths = _lint_doc(tmp_path, "f", drift, target="fen")
    with pytest.raises(ContractError):
        lint(CONTRACT, variant_path=paths["variant"],
             digest_path=paths["digest"], fen_path=paths["fen"],
             en_passant_path=paths["en_passant"])


def test_linkage_ep_identity_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["target"]["storage_vs_identity"][
            "identity_value"] = "stored-target-always"
    paths = _lint_doc(tmp_path, "e", drift, target="en_passant")
    with pytest.raises(ContractError):
        lint(CONTRACT, variant_path=paths["variant"],
             digest_path=paths["digest"], fen_path=paths["fen"],
             en_passant_path=paths["en_passant"])
