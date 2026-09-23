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


def _identity_components(nc, vc, dc, ec, fc, variant_id, position):
    """Named serializers for the canonical identity components, each
    derived from the linked contracts: the board is the canonical
    placement from the linked FEN serializer, castling the canonical
    rights-or-sentinel, and en-passant the IDENTITY value
    (legal-capture target or none) from the linked en-passant
    contract."""
    board, color, rights, ep, half, full = position
    placement = emit_fen(fc, position).split(" ")[0]
    return {
        "variant": variant_id,
        "board": placement,
        "side_to_move": color,
        "castling_rights": rights or fc["castling"]["none_sentinel"],
        "en_passant": _ep_identity(dc, ec, fc, position),
    }


def _identity_tuple(nc, vc, dc, ec, fc, variant_id, position):
    """The linked variant contract's canonical_fields tuple, DRIVEN by
    vc["identity"]["canonical_fields"]: the component serializers must
    cover exactly the linked field set, and the tuple order is the
    linked order - no field name or order is restated here."""
    canonical_fields = vc["identity"]["canonical_fields"]
    components = _identity_components(
        nc, vc, dc, ec, fc, variant_id, position)
    assert set(components) == set(canonical_fields), (
        "component serializers must cover exactly the linked "
        "canonical field set")
    return tuple(components[name] for name in canonical_fields)


def _identity_named(vc, identity):
    """Reconstruct the named mapping from an identity tuple by the
    linked field names - never positional destructuring."""
    return dict(zip(vc["identity"]["canonical_fields"], identity,
                    strict=True))


def _snapshot_fen(nc, vc, fc, identity):
    """Canonical six-field snapshot: identity components read by their
    linked names plus the normalized clocks pinned by the record
    section."""
    named = _identity_named(vc, identity)
    norm = nc["record"]["snapshot_clock_normalization"]
    assert norm == "halfmove-0-fullmove-1"
    return (f"{named['board']} {named['side_to_move']} "
            f"{named['castling_rights']} {named['en_passant']} 0 1")


def _make_record(nc, vc, dc, ec, fc, digest_fn, variant_id, fen_text):
    """Build the exact three-field node record - nothing else is
    stored, returned, or compared."""
    ids = [e["id"] for e in vc["variants"]["entries"]]
    # EXACT str before membership / parsing: a str subclass can raise
    # from == inside `in`, and only an exact str is flat
    if type(variant_id) is not str or variant_id not in ids:
        _fail(nc, "unknown_variant")
    if type(fen_text) is not str:
        _fail(nc, "malformed_position")
    try:
        position = parse_fen(fc, fen_text)
    except FenError:
        _fail(nc, "malformed_position")
    identity = _identity_tuple(nc, vc, dc, ec, fc, variant_id, position)
    return {
        "variant": variant_id,
        "digest": digest_fn(variant_id, fen_text),
        "snapshot_fen": _snapshot_fen(nc, vc, fc, identity),
    }


def _record_identity(nc, vc, dc, ec, fc, record):
    """Canonical identity tuple DERIVED from the exact three-field
    record (variant + EP-normalized snapshot) - the only equality
    basis; no cache can disagree with the record because none is
    kept."""
    position = parse_fen(fc, record["snapshot_fen"])
    return _identity_tuple(nc, vc, dc, ec, fc,
                           record["variant"], position)


class NodeTable:
    """The merge semantics of the contract's merge section: buckets
    keyed by the digest accelerator, equality by canonical field
    comparison derived from the stored three-field records ONLY,
    insert-or-return-existing. The digest oracle is injectable so
    collision behavior is tested with VALID records."""

    def __init__(self, docs, digest_fn=digest_fen):
        self.nc, self.vc, self.dc, self.ec, self.fc = docs
        self.digest_fn = digest_fn
        self.buckets = {}

    def _identity(self, record):
        return _record_identity(self.nc, self.vc, self.dc, self.ec,
                                self.fc, record)

    def insert(self, variant_id, fen_text):
        rec = _make_record(self.nc, self.vc, self.dc, self.ec,
                           self.fc, self.digest_fn,
                           variant_id, fen_text)
        bucket = self.buckets.setdefault(rec["digest"], [])
        new_identity = self._identity(rec)
        for existing in bucket:
            if self._identity(existing) == new_identity:
                return existing  # same node, never a second one
        bucket.append(rec)
        return rec

    def merge(self, other):
        """Table-to-table merge IS iterated insertion of the exact
        stored records (snapshots are canonical inputs) - the
        structural definition that makes associativity a witness,
        not a claim."""
        source = other.records()
        if type(source) is not list:
            _fail(self.nc, "malformed_node_record")
        # every source record's container, keys and field types are
        # checked BEFORE any lookup or insertion: a hostile record
        # fails closed, never raw, and never after a partial merge
        for rec in source:
            if not _exact_dict(rec) or \
                    set(rec.keys()) != set(self.nc["record"]["fields"]) or \
                    type(rec["variant"]) is not str or \
                    type(rec["digest"]) is not str or \
                    type(rec["snapshot_fen"]) is not str:
                _fail(self.nc, "malformed_node_record")
            # the source record is validated AS A RECORD (digest
            # included) against this table's docs and digest oracle
            # before any insert - never laundered through re-derivation
            validate_record(self.nc, self.vc, self.dc, self.ec, self.fc,
                            rec, self.digest_fn)
        for rec in source:
            self.insert(rec["variant"], rec["snapshot_fen"])
        return self

    def records(self):
        return [rec for bucket in self.buckets.values()
                for rec in bucket]

    def serialize(self):
        return sorted(
            (r["variant"], r["digest"], r["snapshot_fen"])
            for r in self.records())


def _table(digest_fn=digest_fen):
    return NodeTable(_docs(), digest_fn)


def _exact_dict(obj):
    """EXACT built-in dict whose every key is an EXACT str - checked
    before any set build, membership test or lookup, so a subclass
    cannot lie about its content and a key with a colliding hash and a
    raising __eq__ fails closed instead of escaping raw."""
    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))


def validate_record(nc, vc, dc, ec, fc, record, digest_fn=digest_fen):
    """A stored node record must satisfy the record section exactly:
    EXACTLY the declared field set, known variant, pinned digest
    format, canonical snapshot with identity ep value and
    normalized clocks, and the digest consistent with the identity
    inside the snapshot under the table's digest oracle."""
    if not _exact_dict(record) or \
            set(record.keys()) != set(nc["record"]["fields"]):
        _fail(nc, "malformed_node_record")
    # EXACT str for every field BEFORE membership, regex or parse
    if type(record["variant"]) is not str or \
            type(record["digest"]) is not str or \
            type(record["snapshot_fen"]) is not str:
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
    # Named FEN-field mapping driven by the LINKED FEN contract's
    # field order - no positional index into either representation
    # (the FEN field order itself stays lint-pinned; only the
    # identity tuple claims runtime order independence).
    fen_named = dict(zip(fc["fields"]["order"],
                         record["snapshot_fen"].split(" "),
                         strict=True))
    if (fen_named["halfmove_clock"] != "0"
            or fen_named["fullmove_number"] != "1"):
        _fail(nc, "malformed_node_record")  # clock normalization
    identity = _identity_tuple(nc, vc, dc, ec, fc,
                               record["variant"], position)
    named = _identity_named(vc, identity)
    if fen_named["en_passant"] != named["en_passant"]:
        _fail(nc, "malformed_node_record")  # ep identity value
    if record["digest"] != digest_fn(record["variant"],
                                     record["snapshot_fen"]):
        _fail(nc, "malformed_node_record")  # digest consistency
    return record


def _validate(record, digest_fn=digest_fen):
    nc, vc, dc, ec, fc = _docs()
    return validate_record(nc, vc, dc, ec, fc, record, digest_fn)


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
    """Idempotent, commutative, associative: merge is STRUCTURALLY
    iterated insertion of exact three-field records, so all
    groupings of independently built tables agree."""
    phantom = AFTER_E4.replace(" e3 ", " - ")  # same identity
    positions = [STARTPOS, AFTER_E4, LEGAL_EP, KINGS,
                 KINGS.replace(" w ", " b "),
                 STARTPOS.replace(" 0 1", " 7 42"),  # clock duplicate
                 phantom]  # phantom-EP duplicate, group c (AFTER_E4: a)
    a_positions, b_positions, c_positions = (positions[:2],
                                             positions[2:4],
                                             positions[4:])
    t1 = _table()
    for fen in positions:
        t1.insert("standard", fen)
    t1.insert("standard", STARTPOS)  # idempotent reinsert
    t2 = _table()
    for fen in reversed(positions):
        t2.insert("standard", fen)
    assert t1.serialize() == t2.serialize()
    assert len(t1.records()) == 5  # clock + phantom duplicates fold

    def built(fens):
        t = _table()
        for fen in fens:
            t.insert("standard", fen)
        return t

    left = _table().merge(built(a_positions)).merge(
        built(b_positions)).merge(built(c_positions))
    right = _table().merge(built(a_positions)).merge(
        _table().merge(built(b_positions)).merge(built(c_positions)))
    assert left.serialize() == right.serialize() == t1.serialize()
    # permutations
    import itertools
    results = set()
    for order in itertools.permutations(
            [a_positions, b_positions, c_positions]):
        t = _table()
        for group in order:
            t.merge(built(group))
        results.add(tuple(t.serialize()))
    assert len(results) == 1
    # the phantom pair lives in DIFFERENT independently built groups
    # (AFTER_E4 in a, its '-' twin in c): both associativity
    # groupings and every group permutation must still yield exactly
    # ONE node for their shared identity - alongside the clock
    # duplicate that STARTPOS/"7 42" already exercises
    phantom_snapshot = AFTER_E4.replace(" e3 ", " - ")
    for grouped in (t1, t2, left, right):
        assert sum(1 for r in grouped.records()
                   if r["snapshot_fen"] == phantom_snapshot) == 1
    for order in itertools.permutations(
            [a_positions, b_positions, c_positions]):
        t = _table()
        for group in order:
            t.merge(built(group))
        assert sum(1 for r in t.records()
                   if r["snapshot_fen"] == phantom_snapshot) == 1


def test_identity_follows_sibling_canonical_order():
    """Mutation witness: permuting the sibling variant contract's
    canonical_fields order IN MEMORY permutes the identity tuple the
    model builds, while the named snapshot reconstruction still reads
    the same components by their linked names - the executable model
    is DRIVEN by the sibling source of truth, not by a restated
    literal order (the linter separately rejects any drift in the
    sibling FILE, but the tuple order here follows the data)."""
    nc, vc, dc, ec, fc = _docs()
    position = parse_fen(fc, AFTER_E4)
    base = _identity_tuple(nc, vc, dc, ec, fc, "standard", position)
    mutated_vc = copy.deepcopy(vc)
    mutated_vc["identity"]["canonical_fields"] = list(
        reversed(vc["identity"]["canonical_fields"]))
    mutated = _identity_tuple(
        nc, mutated_vc, dc, ec, fc, "standard", position)
    assert mutated == tuple(reversed(base))
    # named reconstruction by linked names is order-independent
    assert _identity_named(mutated_vc, mutated) == _identity_named(
        vc, base)
    assert _snapshot_fen(
        nc, mutated_vc, fc, mutated) == _snapshot_fen(nc, vc, fc, base)
    # a field-set mismatch between serializers and sibling is
    # rejected, never silently partial
    broken_vc = copy.deepcopy(vc)
    broken_vc["identity"]["canonical_fields"] = (
        list(vc["identity"]["canonical_fields"]) + ["mystery"])
    with pytest.raises(AssertionError):
        _identity_tuple(nc, broken_vc, dc, ec, fc, "standard",
                        position)
    # the claim holds across the ACTUAL record path: a record made
    # under the mutated sibling order validates under that same
    # order - the digest oracle is over (variant, fen text), so it
    # is consistent for both orders
    rec_mut = _make_record(nc, mutated_vc, dc, ec, fc, digest_fen,
                           "standard", AFTER_E4)
    rec_base = _make_record(nc, vc, dc, ec, fc, digest_fen,
                            "standard", AFTER_E4)
    assert rec_mut == rec_base  # snapshot reads by linked names
    assert validate_record(nc, mutated_vc, dc, ec, fc, rec_mut,
                           digest_fen) is rec_mut
    assert validate_record(nc, vc, dc, ec, fc, rec_base,
                           digest_fen) is rec_base


def test_records_exact_three_fields_and_rebuild():
    """Adversarial witness: no hidden cache exists or can diverge -
    every stored/returned record has EXACTLY the three declared
    fields, and a table rebuilt from only those records is
    identical."""
    t = _table()
    for fen in (STARTPOS, AFTER_E4, LEGAL_EP, KINGS):
        rec = t.insert("standard", fen)
        assert set(rec.keys()) == {"variant", "digest",
                                   "snapshot_fen"}
    for rec in t.records():
        assert set(rec.keys()) == {"variant", "digest",
                                   "snapshot_fen"}
    rebuilt = _table().merge(t)
    assert rebuilt.serialize() == t.serialize()
    # a tampered record is rejected, never silently diverging
    rec = dict(t.records()[0])
    rec["snapshot_fen"] = rec["snapshot_fen"].replace(" w ", " b ")
    with pytest.raises(NodeError):
        _validate(rec)


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
    return _make_record(*_docs(), digest_fen, "standard", STARTPOS)


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
        rec = _make_record(*_docs(), digest_fen, "standard", AFTER_E4)
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
    """A digest collision never merges nodes: with an injected
    digest oracle that maps BOTH positions to one (valid) digest,
    the two records validate, share one bucket, and stay two
    nodes - separated by canonical field comparison derived from
    the three-field records."""
    oracle = lambda variant, fen: STARTPOS_DIGEST  # noqa: E731
    t = _table(digest_fn=oracle)
    a = t.insert("standard", STARTPOS)
    b = t.insert("standard", KINGS)
    # both records carry the same VALID digest under the oracle
    _validate(a, digest_fn=oracle)
    _validate(b, digest_fn=oracle)
    assert a["digest"] == b["digest"] == STARTPOS_DIGEST
    assert a is not b
    assert len(t.records()) == 2  # same bucket, different nodes
    assert len(t.buckets) == 1
    # equality basis is the reconstructed canonical tuple
    assert t._identity(a) != t._identity(b)
    # reinsertion under collision still finds the right node
    assert t.insert("standard", KINGS) is b
    assert t.insert("standard", STARTPOS) is a
    assert len(t.records()) == 2
    # collision inside table merge as well
    left = _table(digest_fn=oracle)
    left.insert("standard", STARTPOS)
    right = _table(digest_fn=oracle)
    right.insert("standard", KINGS)
    merged = _table(digest_fn=oracle).merge(left).merge(right)
    assert len(merged.records()) == 2


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


# -- totality sweep: hostile keys, values and container subclasses -----------


class _SK(str):
    """str subclass: hashes like the text it imitates, raises on ==."""

    def __hash__(self):
        return str.__hash__(str(self))

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    __ne__ = __eq__


class _HK:
    """Non-str key with a colliding hash and a raising ==."""

    def __init__(self, text):
        self.text = text

    def __hash__(self):
        return hash(self.text)

    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    __ne__ = __eq__


class _DictSub(dict):
    pass


class _ListSub(list):
    pass


class _Src:
    """A merge source that hands back exactly the given records."""

    def __init__(self, records):
        self._records = records

    def records(self):
        return list(self._records)


def _rekey(d, field, key_type):
    return {key_type(k) if k == field else k: v for k, v in d.items()}


_KEY_TYPES = pytest.mark.parametrize("key_type", [_SK, _HK], ids=["SK", "HK"])


def _sweep_valid_record():
    return dict(_table().insert("standard", AFTER_E4))


def _sweep_expect(call, table=None):
    before = table.serialize() if table is not None else None
    with pytest.raises(NodeError) as exc:
        call()
    assert exc.value.failure_class == "malformed_node_record"
    assert exc.value.code == FAILURE_MAPPING["malformed_node_record"]
    if table is not None:
        assert table.serialize() == before


@_KEY_TYPES
@pytest.mark.parametrize("field", ["variant", "digest", "snapshot_fen"])
def test_merge_total_over_hostile_record_keys(key_type, field):
    dst = _table()
    dst.insert("standard", STARTPOS)
    hostile = _rekey(_sweep_valid_record(), field, key_type)
    _sweep_expect(lambda: dst.merge(_Src([hostile])), dst)


@_KEY_TYPES
@pytest.mark.parametrize("field", ["variant", "digest", "snapshot_fen"])
def test_validate_record_total_over_hostile_record_keys(key_type, field):
    hostile = _rekey(_sweep_valid_record(), field, key_type)
    _sweep_expect(lambda: validate_record(*_docs(), hostile))


def test_merge_rejects_subclasses_and_hostile_values():
    dst = _table()
    dst.insert("standard", STARTPOS)
    rec = _sweep_valid_record()
    _sweep_expect(lambda: dst.merge(_Src([_DictSub(rec)])), dst)
    _sweep_expect(lambda: dst.merge(_Src([dict(rec, variant=_SK("standard"))])), dst)
    _sweep_expect(lambda: dst.merge(_Src([None])), dst)
    _sweep_expect(lambda: validate_record(*_docs(), _DictSub(rec)))
    _sweep_expect(lambda: validate_record(*_docs(), None))


@_KEY_TYPES
def test_hostile_record_key_escapes_raw_without_guard(key_type):
    hostile = _rekey(_sweep_valid_record(), "variant", key_type)
    with pytest.raises(RuntimeError):
        hostile["variant"]  # noqa: B018


@pytest.mark.parametrize("variant,fen,cls", [
    (_SK("standard"), STARTPOS, "unknown_variant"),
    (_HK("standard"), STARTPOS, "unknown_variant"),
    ("standard", _SK(STARTPOS), "malformed_position"),
    ("standard", ["x"], "malformed_position")],
    ids=["SK-variant", "HK-variant", "SK-fen", "list-fen"])
def test_insert_total_over_hostile_arguments(variant, fen, cls):
    t = _table()
    t.insert("standard", STARTPOS)
    before = t.serialize()
    with pytest.raises(NodeError) as exc:
        t.insert(variant, fen)
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert t.serialize() == before


def test_hostile_variant_escapes_raw_without_guard():
    ids = ["standard"]
    with pytest.raises(RuntimeError):
        _SK("standard") in ids  # noqa: B015


# -- totality sweep: hostile VALUES per field and per entry point ------------


class _RaisingList(list):
    def __iter__(self):
        raise RuntimeError("hostile __iter__")


def _sweep_expect_cls(call, table, cls):
    before = table.serialize() if table is not None else None
    with pytest.raises(NodeError) as exc:
        call()
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    if table is not None:
        assert table.serialize() == before


def _sk_value_records():
    rec = _sweep_valid_record()
    out = [
        (f"SK-{f}", dict(rec, **{f: _SK(rec[f])}), cls)
        for f, cls in {
            "variant": "malformed_node_record",
            "digest": "malformed_node_record",
            "snapshot_fen": "malformed_node_record",
        }.items()
    ]
    out += []
    return out


_SK_VALUE_RECORDS = _sk_value_records()
_SK_IDS = [c[0] for c in _SK_VALUE_RECORDS]


@pytest.mark.parametrize("name,record,cls", _SK_VALUE_RECORDS, ids=_SK_IDS)
def test_validate_record_total_over_hostile_values(name, record, cls):
    _sweep_expect_cls(lambda: validate_record(*_docs(), record), None, cls)


@pytest.mark.parametrize("name,record,cls", _SK_VALUE_RECORDS, ids=_SK_IDS)
@pytest.mark.parametrize("existing", [False, True], ids=["new-key", "existing-key"])
def test_merge_total_over_hostile_values(name, record, cls, existing):
    dst = _table()
    dst.insert("standard", STARTPOS)
    if existing:
        dst.insert("standard", AFTER_E4)
    _sweep_expect_cls(lambda: dst.merge(_Src([record])), dst, "malformed_node_record")


@pytest.mark.parametrize(
    "args,cls",
    [
        ((_SK("standard"), STARTPOS), "unknown_variant"),
        (("standard", _SK(STARTPOS)), "malformed_position"),
    ],
    ids=["SK-variant", "SK-fen"],
)
def test_insert_total_over_hostile_values(args, cls):
    t = _table()
    t.insert("standard", STARTPOS)
    _sweep_expect_cls(lambda: t.insert(*args), t, cls)


@pytest.mark.parametrize(
    "records", [None, _RaisingList([1]), (), {}], ids=["None", "raising-list", "tuple", "dict"]
)
def test_merge_rejects_non_list_sources(records):
    dst = _table()
    dst.insert("standard", STARTPOS)

    class _Bad:
        def records(self):
            return records

    _sweep_expect_cls(lambda: dst.merge(_Bad()), dst, "malformed_node_record")


def _selfref():
    loop = []
    loop.append(loop)
    return loop


def _deep(n=100_000):
    root = cur = []
    for _ in range(n):
        nxt = []
        cur.append(nxt)
        cur = nxt
    return root


def _structural_value_records():
    rec = _sweep_valid_record()
    return [
        (f"{kind}-{f}", dict(rec, **{f: make()}), cls)
        for f, cls in {
            f: "malformed_node_record" for f in ("variant", "digest", "snapshot_fen")
        }.items()
        for kind, make in (("selfref", _selfref), ("deep", _deep), ("hugeint", lambda: 10**5000))
    ]


_STRUCT_RECORDS = _structural_value_records()
_STRUCT_IDS = [c[0] for c in _STRUCT_RECORDS]


@pytest.mark.parametrize("name,record,cls", _STRUCT_RECORDS, ids=_STRUCT_IDS)
def test_validate_record_total_over_structural_values(name, record, cls):
    _sweep_expect_cls(lambda: validate_record(*_docs(), record), None, cls)


@pytest.mark.parametrize("name,record,cls", _STRUCT_RECORDS, ids=_STRUCT_IDS)
def test_merge_total_over_structural_values(name, record, cls):
    dst = _table()
    dst.insert("standard", STARTPOS)
    _sweep_expect_cls(lambda: dst.merge(_Src([record])), dst, "malformed_node_record")


@pytest.mark.parametrize("field", ["variant", "digest", "snapshot_fen"])
def test_hostile_value_escapes_raw_without_guard(field):
    """Guardless demo: the pre-sweep isinstance check admits the SK value,
    and the next == / in against it raises raw."""
    value = _SK(_sweep_valid_record()[field])
    assert isinstance(value, str)
    with pytest.raises(RuntimeError):
        value in [str(value)]  # noqa: B015


def test_merge_rejects_well_formed_wrong_digest():
    """merge validates each source record as a record: a digest in the
    right format but for another position is rejected, never re-derived."""
    dst = _table()
    dst.insert("standard", STARTPOS)
    rec = _sweep_valid_record()
    wrong = dict(rec, digest=_table().insert("standard", KINGS)["digest"])
    _sweep_expect_cls(lambda: dst.merge(_Src([wrong])), dst, "malformed_node_record")
