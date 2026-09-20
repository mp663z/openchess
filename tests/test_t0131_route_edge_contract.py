"""T0131: chess route-edge contract behavior battery.

Reference implementation FULLY DERIVED from data/contracts/
route_edge.yaml plus the linked siblings (variant, FEN,
position-digest, transposition-node, legal-moves): the edge
identity four-tuple composes the NODE contract's canonical
identity tuple (imported, never restated) with the move text, the
record shape comes from the record section with snapshots
validated THROUGH the node contract's own record validator, the
move grammar comes from the linked legal-moves move_model, and the
merge semantics and failure classes come from the merge and
failures sections. Nothing about chess is hardcoded here. Happy,
path-invariance, boundary, merge-algebra, determinism/conflict,
malformed, rollback, collision, lint-mutant and sibling-linkage
batteries below.
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
    parse_fen,
)
from tests.test_t0113_position_digest_contract import (  # noqa: E402
    DigestError,
    digest_fen,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    AFTER_E4,
    KINGS,
    LEGAL_EP,
    STARTPOS,
    NodeError,
    _identity_named,
    _identity_tuple,
    _snapshot_fen,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    _docs as _node_docs,
)
from tests.test_t0122_transposition_node_contract import (  # noqa: E402
    validate_record as _validate_node_record,
)
from tools.route_edge_contract_lint import (  # noqa: E402
    CONTRACT,
    ERROR_ENUM,
    FAILURE_MAPPING,
    lint,
)
from tools.variant_contract_lint import ContractError  # noqa: E402

VARIANT = ROOT / "data" / "contracts" / "variant.yaml"
FEN = ROOT / "data" / "contracts" / "fen.yaml"
DIGEST = ROOT / "data" / "contracts" / "position_digest.yaml"
NODE = ROOT / "data" / "contracts" / "transposition_node.yaml"
LEGAL_MOVES = ROOT / "data" / "contracts" / "legal_moves.yaml"


def _docs():
    nc, vc, dc, epc, fc = _node_docs()
    return (yaml.safe_load(CONTRACT.read_text())["contract"],
            vc, dc, epc, fc,
            yaml.safe_load(LEGAL_MOVES.read_text())["contract"],
            nc)


class EdgeError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(contract, cls):
    raise EdgeError(cls, contract["failures"]["mapping"][cls])


def _move_parts(lc, move):
    """Parse a long-algebraic move text per the linked legal-moves
    contract's move_model: exactly square then square then an
    optional promotion letter - square grammar, promotion enum and
    from/to distinctness all READ from the sibling, never restated
    here. Returns (from_square, to_square, promotion_or_None) or
    raises ValueError."""
    shape = lc["move_model"]["shape"]
    grammar = lc["move_model"]["square_grammar"]
    promo_enum = shape["types"]["promotion"]["enum"]
    if not isinstance(move, str) or not move.isascii():
        raise ValueError("move must be an ascii string")
    squares_len = 2 * len(shape["required"])  # two square fields
    if len(move) not in (squares_len, squares_len + 1):
        raise ValueError("move length drift")
    squares = (move[:2], move[2:4])
    for square in squares:
        if (square[0] not in grammar["files"]
                or square[1] not in grammar["ranks"]):
            raise ValueError(f"square grammar drift: {square!r}")
    if shape["from_to_distinct"] and squares[0] == squares[1]:
        raise ValueError("from and to squares must be distinct")
    promotion = None
    if len(move) == squares_len + 1:
        if move[4] not in promo_enum:
            raise ValueError(f"promotion enum drift: {move[4]!r}")
        promotion = move[4]
    return squares[0], squares[1], promotion


def _identity_of(ec, vc, dc, epc, fc, lc, nc, variant_id, fen_text):
    """The canonical node identity for one endpoint, composed THROUGH
    the imported node-contract identity machinery."""
    position = parse_fen(fc, fen_text)
    return _identity_tuple(nc, vc, dc, epc, fc, variant_id, position)


def _make_record(ec, vc, dc, epc, fc, lc, nc, digest_fn,
                 variant_id, move, from_fen, to_fen):
    """Build the exact four-field edge record - nothing else is
    stored, returned, or compared. Move APPLICATION (legality,
    which position the move produces) is delegated to the legal
    moves / position runtime layer; the endpoints arrive as
    positions and are pinned to their canonical identities and
    snapshots here."""
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if variant_id not in ids:
        _fail(ec, "unknown_variant")
    try:
        _move_parts(lc, move)
    except ValueError:
        _fail(ec, "malformed_edge_record")
    try:
        from_position = parse_fen(fc, from_fen)
        to_position = parse_fen(fc, to_fen)
    except FenError:
        _fail(ec, "malformed_position")
    from_identity = _identity_tuple(nc, vc, dc, epc, fc,
                                    variant_id, from_position)
    to_identity = _identity_tuple(nc, vc, dc, epc, fc,
                                  variant_id, to_position)
    return {
        "variant": variant_id,
        "move": move,
        "from_snapshot_fen": _snapshot_fen(nc, vc, fc, from_identity),
        "to_snapshot_fen": _snapshot_fen(nc, vc, fc, to_identity),
    }


def _record_identity(ec, vc, dc, epc, fc, lc, nc, record):
    """Canonical edge identity four-tuple DERIVED from the exact
    four-field record (variant + move + both EP-normalized
    snapshots) - the only equality basis; no cache can disagree
    with the record because none is kept."""
    return (
        record["variant"],
        record["move"],
        _identity_of(ec, vc, dc, epc, fc, lc, nc,
                     record["variant"], record["from_snapshot_fen"]),
        _identity_of(ec, vc, dc, epc, fc, lc, nc,
                     record["variant"], record["to_snapshot_fen"]),
    )


def validate_record(ec, vc, dc, epc, fc, lc, nc, record,
                    digest_fn=digest_fen):
    """A stored edge record must satisfy the record section exactly:
    EXACTLY the declared field set, known variant, move shape per
    the linked move_model, and both snapshots satisfying the NODE
    contract's record snapshot pins - checked THROUGH the node
    contract's own record validator, never restated here."""
    if set(record.keys()) != set(ec["record"]["fields"]):
        _fail(ec, "malformed_edge_record")
    ids = [e["id"] for e in vc["variants"]["entries"]]
    if record["variant"] not in ids:
        _fail(ec, "unknown_variant")
    try:
        _move_parts(lc, record["move"])
    except ValueError:
        _fail(ec, "malformed_edge_record")
    for key in ("from_snapshot_fen", "to_snapshot_fen"):
        try:
            node_record = {
                "variant": record["variant"],
                "digest": digest_fn(record["variant"], record[key]),
                "snapshot_fen": record[key],
            }
            _validate_node_record(nc, vc, dc, epc, fc, node_record,
                                  digest_fn)
        except (NodeError, DigestError):
            _fail(ec, "malformed_edge_record")
    return record


def _validate(record, digest_fn=digest_fen):
    return validate_record(*_docs(), record, digest_fn)


class EdgeTable:
    """The merge semantics of the contract's merge section: buckets
    keyed by the from-digest-plus-move accelerator, equality by the
    canonical four-tuple derived from the stored four-field records
    ONLY, insert-or-return-existing, and the pinned determinism
    rule: one from-identity plus one move never reaches two
    different target identities - that is a conflicting_edge
    rejection, never a second record. The digest oracle is
    injectable so collision behavior is tested with VALID
    records."""

    def __init__(self, docs, digest_fn=digest_fen):
        (self.ec, self.vc, self.dc, self.epc, self.fc, self.lc,
         self.nc) = docs
        self.digest_fn = digest_fn
        self.buckets = {}

    def _identity(self, record):
        return _record_identity(self.ec, self.vc, self.dc, self.epc,
                                self.fc, self.lc, self.nc, record)

    def insert(self, variant_id, move, from_fen, to_fen):
        rec = _make_record(self.ec, self.vc, self.dc, self.epc,
                           self.fc, self.lc, self.nc, self.digest_fn,
                           variant_id, move, from_fen, to_fen)
        bucket_key = (self.digest_fn(variant_id,
                                     rec["from_snapshot_fen"]), move)
        new_identity = self._identity(rec)
        bucket = self.buckets.get(bucket_key)
        if bucket is not None:
            for existing in bucket:
                existing_identity = self._identity(existing)
                if existing_identity == new_identity:
                    return existing  # same edge, never a second one
                if existing_identity[:3] == new_identity[:3]:
                    # same from-identity plus same move but a
                    # different target: determinism enforced
                    _fail(self.ec, "conflicting_edge")
        # no bucket yet, or no equal/conflicting edge in it
        self.buckets.setdefault(bucket_key, []).append(rec)
        return rec

    def merge(self, other):
        """Table-to-table merge is iterated insertion of the exact
        stored records (snapshots are canonical inputs) into a
        STAGED COPY, committed only when the whole union is
        compatible - the contract's atomic pin: conflicts internal
        to the source batch or against this table reject the
        entire merge and leave this table bit-identical, and over
        conflicting tables both merge orders reject rather than
        leaving first-writer residue."""
        staged = EdgeTable(
            (self.ec, self.vc, self.dc, self.epc, self.fc, self.lc,
             self.nc), self.digest_fn)
        staged.buckets = copy.deepcopy(self.buckets)
        for rec in other.records():
            # the source mapping is validated AS A RECORD against
            # the destination's linked docs and digest oracle
            # (original field set and canonical snapshot text)
            # before any staging - never laundered through
            # reconstruction
            validate_record(self.ec, self.vc, self.dc, self.epc,
                            self.fc, self.lc, self.nc, rec,
                            self.digest_fn)
            staged.insert(rec["variant"], rec["move"],
                          rec["from_snapshot_fen"],
                          rec["to_snapshot_fen"])
        self.buckets = staged.buckets
        return self

    def records(self):
        return [rec for bucket in self.buckets.values()
                for rec in bucket]

    def serialize(self):
        return sorted(
            (r["variant"], r["move"], r["from_snapshot_fen"],
             r["to_snapshot_fen"])
            for r in self.records())


def _table(digest_fn=digest_fen):
    return EdgeTable(_docs(), digest_fn)


# -- pinned vectors -------------------------------------------------------

E2E4_TO = AFTER_E4.replace(" e3 ", " - ")  # phantom target collapses
KINGS_E1E2_TO = "4k3/8/8/8/8/8/4K3/8 b - - 0 1"
KINGS_E1D1_TO = "4k3/8/8/8/8/8/8/3K4 b - - 0 1"
PROMO_FROM = "4k3/P7/8/8/8/8/8/4K3 w - - 0 1"
PROMO_TO = "Q3k3/8/8/8/8/8/8/4K3 b - - 0 1"
PAWN_E2 = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"
PAWN_E2E4_TO = "4k3/8/8/8/8/4P3/8/4K3 b - - 0 1"
KINGS_ALT_TARGET = "8/4k3/8/8/8/8/4K3/8 b - - 0 1"


def test_lint_clean():
    lint()


def test_happy_insert_records_exact():
    t = _table()
    rec = t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    assert rec["variant"] == "standard"
    assert rec["move"] == "e2e4"
    assert rec["from_snapshot_fen"] == STARTPOS  # already canonical
    assert rec["to_snapshot_fen"] == E2E4_TO  # phantom ep collapses
    assert set(rec.keys()) == {"variant", "move", "from_snapshot_fen",
                               "to_snapshot_fen"}
    _validate(rec)
    # promotion letter survives in the move text
    rec = t.insert("standard", "a7a8q", PROMO_FROM, PROMO_TO)
    assert rec["move"] == "a7a8q"
    assert rec["to_snapshot_fen"] == PROMO_TO
    _validate(rec)
    # a capturable target survives in the endpoint snapshot
    rec = t.insert("standard", "e4d5", LEGAL_EP, LEGAL_EP)
    assert rec["from_snapshot_fen"].split(" ")[3] == "e3"
    _validate(rec)


def test_path_invariance_same_edge():
    """Every path to one edge identity reaches the SAME edge: raw
    clocks and uncapturable targets on either endpoint never fork
    it, and the move-order path is not part of the edge at all."""
    t = _table()
    a = t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    b = t.insert("standard", "e2e4",
                 STARTPOS.replace(" 0 1", " 7 42"),
                 AFTER_E4.replace(" e3 ", " - ").replace(
                     " 0 1", " 3 9"))
    assert a is b
    c = t.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    d = t.insert("standard", "e1e2",
                 KINGS.replace(" 0 1", " 5 17"),
                 KINGS_E1E2_TO)
    assert c is d
    assert len(t.records()) == 2


def test_boundary_distinct_edges_from_one_node():
    t = _table()
    a = t.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    b = t.insert("standard", "e1d1", KINGS, KINGS_E1D1_TO)
    assert a is not b  # different moves: different edges
    assert len(t.records()) == 2
    # same move text from a different from-node is a different edge
    c = t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    d = t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
    assert c is not d
    assert len(t.records()) == 4


def test_merge_algebra():
    """Idempotent, commutative, associative: merge is STRUCTURALLY
    iterated insertion of exact four-field records, so all
    groupings of independently built tables agree."""
    edges = [("e2e4", STARTPOS, AFTER_E4),
             ("e1e2", KINGS, KINGS_E1E2_TO),
             ("e1d1", KINGS, KINGS_E1D1_TO),
             ("a7a8q", PROMO_FROM, PROMO_TO),
             ("e2e4", PAWN_E2, PAWN_E2E4_TO),
             ("e2e4", STARTPOS.replace(" 0 1", " 9 77"),
              AFTER_E4.replace(" e3 ", " - "))]  # duplicate of edge 1
    groups = [edges[:2], edges[2:4], edges[4:]]

    def built(edge_list):
        t = _table()
        for move, frm, to in edge_list:
            t.insert("standard", move, frm, to)
        return t

    t1 = built(edges)
    t1.insert("standard", "e2e4", STARTPOS, AFTER_E4)  # reinsert
    t2 = built(list(reversed(edges)))
    assert t1.serialize() == t2.serialize()
    assert len(t1.records()) == 5  # clock/phantom duplicate folds

    left = _table().merge(built(groups[0])).merge(
        built(groups[1])).merge(built(groups[2]))
    right = _table().merge(built(groups[0])).merge(
        _table().merge(built(groups[1])).merge(built(groups[2])))
    assert left.serialize() == right.serialize() == t1.serialize()
    import itertools
    results = set()
    for order in itertools.permutations(groups):
        t = _table()
        for group in order:
            t.merge(built(group))
        results.add(tuple(t.serialize()))
    assert len(results) == 1
    # the duplicate edge (raw and canonical forms in DIFFERENT
    # groups: edge 1 in group a, its twin in group c) folds to
    # exactly one record under every grouping and permutation
    canonical = ("standard", "e2e4", STARTPOS, E2E4_TO)
    for grouped in (t1, t2, left, right):
        assert sum(1 for r in grouped.records()
                   if (r["variant"], r["move"], r["from_snapshot_fen"],
                       r["to_snapshot_fen"]) == canonical) == 1


class _Batch:
    """A merge source that is just a bag of stored records - the
    contract's merge input type. Built by direct construction, as
    any deserialized batch could be, it may carry conflicts an
    EdgeTable could never hold through insert()."""

    def __init__(self, records):
        self._records = records

    def records(self):
        return list(self._records)


def _edge_record(move, frm, to):
    return _make_record(*_docs(), digest_fen, "standard", move,
                        frm, to)


def test_conflicting_tables_both_orders_reject():
    """Commutativity over conflicting tables means BOTH merge
    orders reject and leave the same pre-merge destinations -
    never first-writer residue."""
    a = _table()
    a.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    b = _table()
    b.insert("standard", "e1e2", KINGS, KINGS_ALT_TARGET)
    a_before, b_before = a.serialize(), b.serialize()
    a_buckets = copy.deepcopy(a.buckets)
    b_buckets = copy.deepcopy(b.buckets)
    with pytest.raises(EdgeError) as exc:
        a.merge(b)
    assert exc.value.failure_class == "conflicting_edge"
    with pytest.raises(EdgeError) as exc:
        b.merge(a)
    assert exc.value.failure_class == "conflicting_edge"
    assert a.serialize() == a_before
    assert a.buckets == a_buckets
    assert b.serialize() == b_before
    assert b.buckets == b_buckets


def test_batch_rollback_no_partial_commit():
    """A merge whose batch first yields a valid unrelated edge and
    THEN a conflict against the destination commits NOTHING - not
    even the valid prefix."""
    t = _table()
    t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    before = t.serialize()
    before_buckets = copy.deepcopy(t.buckets)
    batch = _Batch([
        _edge_record("e1e2", KINGS, KINGS_E1E2_TO),  # unrelated valid
        _edge_record("e2e4", STARTPOS, KINGS),  # conflicts with stored
    ])
    with pytest.raises(EdgeError) as exc:
        t.merge(batch)
    assert exc.value.failure_class == "conflicting_edge"
    assert t.serialize() == before
    assert t.buckets == before_buckets


def test_conflict_internal_to_source_batch_rejected():
    """A single source batch carrying two records with the same
    (variant, move, from-identity) but different targets conflicts
    WITHIN ITSELF: the merge rejects wholesale even against an
    empty destination."""
    t = _table()
    batch = _Batch([
        _edge_record("e1e2", KINGS, KINGS_E1E2_TO),
        _edge_record("e1e2", KINGS, KINGS_ALT_TARGET),
    ])
    with pytest.raises(EdgeError) as exc:
        t.merge(batch)
    assert exc.value.failure_class == "conflicting_edge"
    assert t.records() == []
    assert t.buckets == {}


def test_identity_follows_sibling_canonical_order():
    """Mutation witness: permuting the sibling variant contract's
    canonical_fields order IN MEMORY permutes the endpoint identity
    tuples the model builds, while the named snapshot
    reconstruction still reads the same components by their linked
    names - the executable model is DRIVEN by the sibling source of
    truth, not by a restated literal order."""
    ec, vc, dc, epc, fc, lc, nc = _docs()
    mutated_vc = copy.deepcopy(vc)
    mutated_vc["identity"]["canonical_fields"] = list(
        reversed(vc["identity"]["canonical_fields"]))
    rec_base = _make_record(ec, vc, dc, epc, fc, lc, nc, digest_fen,
                            "standard", "e2e4", STARTPOS, AFTER_E4)
    rec_mut = _make_record(ec, mutated_vc, dc, epc, fc, lc, nc,
                           digest_fen, "standard", "e2e4",
                           STARTPOS, AFTER_E4)
    assert rec_mut == rec_base  # snapshots read by linked names
    id_base = _record_identity(ec, vc, dc, epc, fc, lc, nc, rec_base)
    id_mut = _record_identity(ec, mutated_vc, dc, epc, fc, lc, nc,
                              rec_mut)
    assert id_mut[0] == id_base[0] and id_mut[1] == id_base[1]
    assert id_mut[2] == tuple(reversed(id_base[2]))
    assert id_mut[3] == tuple(reversed(id_base[3]))
    assert _identity_named(mutated_vc, id_mut[2]) == _identity_named(
        vc, id_base[2])


def test_records_exact_four_fields_and_rebuild():
    """Adversarial witness: no hidden cache exists or can diverge -
    every stored/returned record has EXACTLY the four declared
    fields, and a table rebuilt from only those records is
    identical."""
    t = _table()
    for move, frm, to in (("e2e4", STARTPOS, AFTER_E4),
                          ("e1e2", KINGS, KINGS_E1E2_TO),
                          ("a7a8q", PROMO_FROM, PROMO_TO)):
        rec = t.insert("standard", move, frm, to)
        assert set(rec.keys()) == {"variant", "move",
                                   "from_snapshot_fen",
                                   "to_snapshot_fen"}
    for rec in t.records():
        assert set(rec.keys()) == {"variant", "move",
                                   "from_snapshot_fen",
                                   "to_snapshot_fen"}
    rebuilt = _table().merge(t)
    assert rebuilt.serialize() == t.serialize()
    # a tampered record is rejected, never silently diverging:
    # reintroducing the phantom EP target violates the node
    # contract's pinned identity normalization
    rec = dict(t.records()[0])
    rec["to_snapshot_fen"] = rec["to_snapshot_fen"].replace(
        " - 0 1", " e3 0 1")
    with pytest.raises(EdgeError):
        _validate(rec)


def test_conflicting_target_rejected():
    """Determinism: one from-identity plus one move NEVER reaches two
    different target identities - a second such insert is a
    conflicting_edge rejection and changes nothing."""
    t = _table()
    t.insert("standard", "e1e2", KINGS, KINGS_E1E2_TO)
    before = t.serialize()
    before_buckets = copy.deepcopy(t.buckets)
    with pytest.raises(EdgeError) as exc:
        t.insert("standard", "e1e2", KINGS, KINGS_ALT_TARGET)
    assert exc.value.failure_class == "conflicting_edge"
    assert exc.value.code == FAILURE_MAPPING["conflicting_edge"]
    assert t.serialize() == before
    assert t.buckets == before_buckets
    # the conflict survives clock/phantom normalization differences
    with pytest.raises(EdgeError):
        t.insert("standard", "e1e2", KINGS.replace(" 0 1", " 2 8"),
                 KINGS_ALT_TARGET)
    assert t.serialize() == before
    # a DIFFERENT move to that same target is fine
    t.insert("standard", "e1d2", KINGS, KINGS_ALT_TARGET)
    assert len(t.records()) == 2


MALFORMED_INSERTS = [
    ("chess960", "e2e4", STARTPOS, AFTER_E4, "unknown_variant"),
    ("standard", "e2e4", "8/8/8/8/8/8/8/4K3 w - - 0 1", KINGS_E1E2_TO,
     "malformed_position"),
    ("standard", "e2e4", STARTPOS, "garbage w - - 0 1",
     "malformed_position"),
    ("standard", "e2e", STARTPOS, AFTER_E4, "malformed_edge_record"),
    ("standard", "e2e4q5", STARTPOS, AFTER_E4,
     "malformed_edge_record"),
    ("standard", "e2e2", STARTPOS, AFTER_E4, "malformed_edge_record"),
    ("standard", "i2e4", STARTPOS, AFTER_E4, "malformed_edge_record"),
    ("standard", "e0e4", STARTPOS, AFTER_E4, "malformed_edge_record"),
    ("standard", "e2e4k", STARTPOS, AFTER_E4,
     "malformed_edge_record"),
    ("standard", "E2e4", STARTPOS, AFTER_E4, "malformed_edge_record"),
    ("standard", "e2e4 ", STARTPOS, AFTER_E4, "malformed_edge_record"),
]


@pytest.mark.parametrize("variant,move,frm,to,cls", MALFORMED_INSERTS)
def test_malformed_insert_rejected(variant, move, frm, to, cls):
    t = _table()
    with pytest.raises(EdgeError) as exc:
        t.insert(variant, move, frm, to)
    assert exc.value.failure_class == cls
    assert exc.value.code == FAILURE_MAPPING[cls]
    assert exc.value.code in ERROR_ENUM


def _valid_record():
    return _make_record(*_docs(), digest_fen, "standard", "e2e4",
                        STARTPOS, AFTER_E4)


def _record_cases():
    base = _valid_record()
    cases = []

    def add(name, mutate, cls="malformed_edge_record"):
        rec = copy.deepcopy(base)
        mutate(rec)
        cases.append((name, rec, cls))

    add("extra-field", lambda r: r.__setitem__("note", "x"))
    add("missing-move", lambda r: r.pop("move"))
    add("unknown-variant", lambda r: r.__setitem__("variant", "c960"),
        "unknown_variant")
    add("move-not-string", lambda r: r.__setitem__("move", 234))
    add("move-same-squares", lambda r: r.__setitem__("move", "e2e2"))
    add("move-bad-promotion", lambda r: r.__setitem__("move", "e7e8k"))
    add("from-clocks-not-normalized", lambda r: r.__setitem__(
        "from_snapshot_fen", r["from_snapshot_fen"].replace(
            " 0 1", " 3 9")))
    add("to-snapshot-grammar", lambda r: r.__setitem__(
        "to_snapshot_fen", "garbage w - - 0 1"))

    def phantom_ep(r):
        # stored-form target with no legal capture: the node
        # contract's identity demands the none sentinel.
        r["to_snapshot_fen"] = AFTER_E4
    add("to-ep-not-identity-value", phantom_ep)
    return cases


def test_malformed_records_rejected():
    for name, rec, cls in _record_cases():
        with pytest.raises(EdgeError) as exc:
            _validate(rec)
        assert exc.value.failure_class == cls, name
        assert exc.value.code == FAILURE_MAPPING[cls]


def test_rollback_bit_identical():
    t = _table()
    t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    before = t.serialize()
    before_buckets = copy.deepcopy(t.buckets)
    for variant, move, frm, to, _cls in MALFORMED_INSERTS:
        with pytest.raises(EdgeError):
            t.insert(variant, move, frm, to)
    with pytest.raises(EdgeError):  # conflicting target as well
        t.insert("standard", "e2e4", STARTPOS, KINGS)
    assert t.serialize() == before
    assert t.buckets == before_buckets


def test_collision_separated_by_field_comparison():
    """A digest collision never merges edges: with an injected digest
    oracle that maps BOTH from-positions to one (valid) digest, two
    edges with the same move text share one bucket and stay two
    edges - separated by canonical field comparison derived from
    the four-field records."""
    oracle = lambda variant, fen: (  # noqa: E731
        "pdv1:" + "0" * 64)
    t = _table(digest_fn=oracle)
    a = t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    b = t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
    _validate(a, digest_fn=oracle)
    _validate(b, digest_fn=oracle)
    assert a is not b
    assert len(t.records()) == 2  # same bucket, different edges
    assert len(t.buckets) == 1
    assert t._identity(a) != t._identity(b)
    # reinsertion under collision still finds the right edge
    assert t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO) is b
    assert t.insert("standard", "e2e4", STARTPOS, AFTER_E4) is a
    assert len(t.records()) == 2
    # collision inside table merge as well
    left = _table(digest_fn=oracle)
    left.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    right = _table(digest_fn=oracle)
    right.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
    merged = _table(digest_fn=oracle).merge(left).merge(right)
    assert len(merged.records()) == 2



def _bad_source_records():
    good = _edge_record("e2e4", STARTPOS, AFTER_E4)
    cases = []

    def add(name, mutate, cls="malformed_edge_record"):
        rec = copy.deepcopy(good)
        mutate(rec)
        cases.append((name, rec, cls))

    add("extra-field", lambda r: r.__setitem__("note", "hidden"))
    add("missing-field", lambda r: r.pop("move"))
    add("non-normalized-clocks", lambda r: r.__setitem__(
        "from_snapshot_fen",
        r["from_snapshot_fen"].replace(" 0 1", " 7 42")))
    add("phantom-ep", lambda r: r.__setitem__(
        "to_snapshot_fen", AFTER_E4))
    add("malformed-move", lambda r: r.__setitem__("move", "e2e2"))
    add("unknown-variant", lambda r: r.__setitem__("variant", "c960"),
        "unknown_variant")
    add("malformed-fen", lambda r: r.__setitem__(
        "to_snapshot_fen", "garbage w - - 0 1"))
    return cases


def test_merge_rejects_malformed_source_records():
    """Batch negatives: each malformed source record rejects the
    whole merge with the pinned failure class, ALONE and after a
    valid prefix, leaving the destination bit-identical."""
    valid = _edge_record("e1d1", KINGS, KINGS_E1D1_TO)
    for name, rec, cls in _bad_source_records():
        for label, prefix in (("alone", []), ("after-valid-prefix",
                                              [valid])):
            t = _table()
            t.insert("standard", "e2e4", PAWN_E2, PAWN_E2E4_TO)
            before = t.serialize()
            before_buckets = copy.deepcopy(t.buckets)
            with pytest.raises(EdgeError) as exc:
                t.merge(_Batch(prefix + [rec]))
            assert exc.value.failure_class == cls, (name, label)
            assert t.serialize() == before, (name, label)
            assert t.buckets == before_buckets, (name, label)


def test_mixed_batch_failure_precedence():
    """Pinned externally observable precedence: the FIRST defect in
    batch order wins - a conflicting entry surfaces when it precedes
    the malformed one, and vice versa."""
    valid = _edge_record("e1d1", KINGS, KINGS_E1D1_TO)
    conflicting = _edge_record("e2e4", STARTPOS, KINGS)
    malformed = _edge_record("e2e4", STARTPOS, AFTER_E4)
    malformed["note"] = "hidden"
    t = _table()
    t.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    before = t.serialize()
    with pytest.raises(EdgeError) as exc:
        t.merge(_Batch([valid, conflicting, malformed]))
    assert exc.value.failure_class == "conflicting_edge"
    assert t.serialize() == before
    t2 = _table()
    t2.insert("standard", "e2e4", STARTPOS, AFTER_E4)
    with pytest.raises(EdgeError) as exc:
        t2.merge(_Batch([valid, malformed, conflicting]))
    assert exc.value.failure_class == "malformed_edge_record"
    assert t2.serialize() == before


def test_mutant_reconstruction_merge_would_launder():
    """Behavioral mutant: the v2-defect merge shape (rebuild from
    extracted values without source validation) LAUNDERS a malformed
    record into a valid one - pinned here so the battery proves the
    real merge is not that shape. Counter-test: a valid exact record
    still merges through the real path."""
    def mutant_merge(self, other):
        for rec in other.records():
            self.insert(rec["variant"], rec["move"],
                        rec["from_snapshot_fen"],
                        rec["to_snapshot_fen"])
        return self

    bad = _edge_record("e1e2", KINGS, KINGS_E1E2_TO)
    bad["note"] = "hidden"  # extra field: must be rejected
    t = _table()
    with pytest.raises(EdgeError) as exc:
        t.merge(_Batch([bad]))
    assert exc.value.failure_class == "malformed_edge_record"
    assert t.records() == []
    # the mutant accepts and silently launders the same record
    t2 = _table()
    mutant_merge(t2, _Batch([bad]))
    assert len(t2.records()) == 1
    assert set(t2.records()[0].keys()) == {
        "variant", "move", "from_snapshot_fen", "to_snapshot_fen"}
    # counter-test: the valid exact record still merges for real
    t3 = _table()
    good = _edge_record("e1e2", KINGS, KINGS_E1E2_TO)
    t3.merge(_Batch([good]))
    assert len(t3.records()) == 1
    assert t3.records()[0] == good


# -- mutation battery ------------------------------------------------------


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
    add("direction drift", ["contract", "role", "direction"],
        "undirected-node-pair")
    add("annotations by path", ["contract", "role",
                                "annotations_attach"],
        "by-first-path")
    add("identity components drift", ["contract", "identity",
                                      "components"],
        ["variant", "from_node", "to_node"])
    add("identity source drift", ["contract", "identity", "source"],
        "restated-here")
    add("equality drift", ["contract", "identity", "equality"],
        "digest-comparison")
    add("accelerator drift", ["contract", "identity", "accelerator"],
        "digest-decides-equality")
    add("exclusions drift", ["contract", "identity", "excluded"],
        ["move_order_path"])
    add("move form drift", ["contract", "move", "form"],
        "san-text")
    add("move same squares allowed", ["contract", "move",
                                      "from_to_distinct"], False)
    add("move application drift", ["contract", "move", "application"],
        "validated-here")
    add("determinism drift", ["contract", "move", "determinism"],
        "last-writer-wins")
    add("record fields drift", ["contract", "record", "fields"],
        ["variant", "move", "from_snapshot_fen"])
    add("extra record field", ["contract", "record", "fields"],
        ["variant", "move", "from_snapshot_fen", "to_snapshot_fen",
         "weight"])
    add("variant form drift", ["contract", "record", "variant_form"],
        "lower-cased")
    add("derived stored drift", ["contract", "record",
                                 "derived_fields_stored"],
        "target-digest")
    add("insert drift", ["contract", "merge", "insert"],
        "always-create")
    add("idempotence dropped", ["contract", "merge", "idempotent"],
        False)
    add("commutativity dropped", ["contract", "merge", "commutative"],
        False)
    add("associativity dropped", ["contract", "merge", "associative"],
        False)
    add("two edges allowed", ["contract", "merge",
                              "same_edge_never_two_records"], False)
    add("atomic dropped", ["contract", "merge", "atomic"], False)
    add("batch conflict partial commit", ["contract", "merge",
                                          "conflict_in_batch"],
        "valid-prefix-commits")
    add("source validation dropped", ["contract", "merge",
                                      "source_validation"],
        "rebuild-from-values")
    add("conflict tolerated", ["contract", "merge",
                               "conflicting_target"],
        "last-writer-wins")
    add("rejected insert mutates", ["contract", "merge",
                                    "rejected_insert_changes_nothing"],
        False)
    add("failure class dropped", ["contract", "failures", "classes"],
        ["malformed_edge_record", "unknown_variant"])
    add("failure mapping drift", ["contract", "failures", "mapping",
                                  "conflicting_edge"],
        "malformed_request")
    add("failures open", ["contract", "failures", "closed"], False)
    add("error enum drift", ["contract", "errors", "closed_enum"],
        ["malformed_request", "internal"])
    add("retryable drift", ["contract", "errors", "shape",
                            "retryable_true_only_for"],
        ["internal", "conflicting_edge"])
    add("property drift", ["contract", "properties",
                           "path_invariance"],
        "path-changes-edge")
    add("rollback property drift", ["contract", "properties",
                                    "rollback"], "best-effort")
    add("link drift", ["contract", "links", "legal_moves_contract"],
        "data/contracts/san.yaml")
    add("base path drift", ["contract", "versioning", "base_path"],
        "/graph/route-edge/v0")
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
    assert covered >= {"role", "identity", "move", "record", "merge",
                       "failures", "errors", "properties", "links",
                       "versioning"}


# -- linkage battery -------------------------------------------------------


def _lint_doc(tmp_path, name, mutate, target="variant"):
    docs = {"variant": VARIANT, "fen": FEN, "digest": DIGEST,
            "node": NODE, "legal_moves": LEGAL_MOVES}
    paths = {}
    for key, src in docs.items():
        data = yaml.safe_load(src.read_text())
        if key == target:
            mutate(data)
        dst = tmp_path / f"{name}-{key}.yaml"
        dst.write_text(yaml.safe_dump(data))
        paths[key] = dst
    return paths


def _lint_with(paths):
    lint(CONTRACT, variant_path=paths["variant"],
         fen_path=paths["fen"], digest_path=paths["digest"],
         node_path=paths["node"],
         legal_moves_path=paths["legal_moves"])


def test_linkage_clean_copy_passes(tmp_path):
    paths = _lint_doc(tmp_path, "clean", lambda d: None)
    _lint_with(paths)


def test_linkage_variant_grammar_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["variants"]["id_grammar"]["pattern"] = (
            "^[a-z]+$")
    paths = _lint_doc(tmp_path, "v", drift)
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_fen_field_order_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["fields"]["order"] = list(
            reversed(d["contract"]["fields"]["order"]))
    paths = _lint_doc(tmp_path, "f", drift, target="fen")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_digest_role_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["role"]["kind"] = "identity"
    paths = _lint_doc(tmp_path, "d", drift, target="digest")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_node_snapshot_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["record"]["snapshot_clock_normalization"] = (
            "first-writer-wins")
    paths = _lint_doc(tmp_path, "n", drift, target="node")
    with pytest.raises(ContractError):
        _lint_with(paths)


def test_linkage_legal_moves_promotion_drift_fails(tmp_path):
    def drift(d):
        d["contract"]["move_model"]["shape"]["types"]["promotion"][
            "enum"] = ["q", "r", "b"]
    paths = _lint_doc(tmp_path, "l", drift, target="legal_moves")
    with pytest.raises(ContractError):
        _lint_with(paths)
