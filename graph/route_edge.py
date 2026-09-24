"""Production route-edge table for data/contracts/route_edge.yaml
(contract id chess-route-edge).

An edge is exactly {variant, move, from_snapshot_fen, to_snapshot_fen}:
a move text per the linked legal-moves move_model between two
canonical node snapshots (the transposition-node record's snapshot
pins). Equality is the canonical four-tuple (variant, move,
from-identity, to-identity); the from-digest-plus-move bucket is a
lookup accelerator only. One from-identity plus one move never reaches
two targets (conflicting_edge). Merge is atomic: every source record is
validated, then the union is staged and adopted only when complete.

Links only the shipped graph.fen, graph.position_digest and
graph.transposition_node runtimes; never imports tests. The digest
oracle is injectable and UNTRUSTED: every call runs behind a
BaseException boundary; a raise or an output that is not an exact str
in the linked digest format fails closed as a FRESH, unchained
malformed_edge_record.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from graph import transposition_node as _node
from graph.fen import FenError, parse_fen
from graph.position_digest import digest_fen

__all__ = ["FAILURE_MAPPING", "EdgeError", "EdgeTable", "load_docs",
           "validate_record"]

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "route_edge.yaml"
LEGAL_MOVES = ROOT / "data" / "contracts" / "legal_moves.yaml"
_FIELDS = ("variant", "move", "from_snapshot_fen", "to_snapshot_fen")


def load_docs():
    """(edge, variant, digest, en_passant, fen, legal_moves, node)
    contract documents, freshly parsed per call."""
    nc, vc, dc, epc, fc = _node.load_docs()
    return (yaml.safe_load(CONTRACT.read_text())["contract"], vc, dc, epc,
            fc, yaml.safe_load(LEGAL_MOVES.read_text())["contract"], nc)


FAILURE_MAPPING = dict(load_docs()[0]["failures"]["mapping"])


class EdgeError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(ec, cls):
    raise EdgeError(cls, ec["failures"]["mapping"][cls])


def _exact_dict(obj):
    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))


def _move_ok(lc, move):
    """Exact ascii str: square, square, optional promotion letter - the
    grammar, promotion enum and distinctness read from move_model."""
    shape = lc["move_model"]["shape"]
    grammar = lc["move_model"]["square_grammar"]
    if type(move) is not str or not move.isascii():
        return False
    squares_len = 2 * len(shape["required"])
    if len(move) not in (squares_len, squares_len + 1):
        return False
    squares = (move[:2], move[2:4])
    for square in squares:
        if square[0] not in grammar["files"] or square[1] not in grammar["ranks"]:
            return False
    if shape["from_to_distinct"] and squares[0] == squares[1]:
        return False
    return len(move) == squares_len or (
        move[4] in shape["types"]["promotion"]["enum"])


def _identity(vc, dc, epc, fc, variant, fen_text):
    return _node._identity_tuple(vc, dc, epc, fc, variant,
                                 parse_fen(fc, fen_text))


def _oracle(ec, nc, dc, digest_fn, variant, fen_text):
    """The untrusted oracle boundary, reusing the node module's
    fresh fail-closed boundary and re-typing its failure."""
    failed = False
    try:
        out = _node._oracle_digest(nc, dc, digest_fn, variant, fen_text)
    except _node.NodeError:
        failed = True
    if failed:
        _fail(ec, "malformed_edge_record")
    return out


def _make_record(docs, variant, move, from_fen, to_fen):
    ec, vc, dc, epc, fc, lc, nc = docs
    if type(variant) is not str or variant not in [
            e["id"] for e in vc["variants"]["entries"]]:
        _fail(ec, "unknown_variant")
    if type(move) is not str:
        _fail(ec, "malformed_edge_record")
    if type(from_fen) is not str or type(to_fen) is not str:
        _fail(ec, "malformed_position")
    if not _move_ok(lc, move):
        _fail(ec, "malformed_edge_record")
    failed = False
    try:
        ends = [parse_fen(fc, from_fen), parse_fen(fc, to_fen)]
    except (FenError, ValueError):  # ValueError: clock over int-str limit
        failed = True
    if failed:
        _fail(ec, "malformed_position")
    snaps = [_node._snapshot_fen(nc, vc, _node._identity_tuple(
        vc, dc, epc, fc, variant, pos)) for pos in ends]
    return {"variant": variant, "move": move,
            "from_snapshot_fen": snaps[0], "to_snapshot_fen": snaps[1]}


def validate_record(ec, vc, dc, epc, fc, lc, nc, record, digest_fn=digest_fen):
    """A stored edge record must satisfy the record section exactly;
    both snapshots are checked THROUGH the node record validator."""
    if not _exact_dict(record) or set(dict.keys(record)) != set(
            ec["record"]["fields"]):
        _fail(ec, "malformed_edge_record")
    if not all(type(record[f]) is str for f in _FIELDS):
        _fail(ec, "malformed_edge_record")
    if record["variant"] not in [e["id"] for e in vc["variants"]["entries"]]:
        _fail(ec, "unknown_variant")
    if not _move_ok(lc, record["move"]):
        _fail(ec, "malformed_edge_record")
    for key in ("from_snapshot_fen", "to_snapshot_fen"):
        failed = False
        try:
            node_record = {
                "variant": record["variant"],
                "digest": _node._oracle_digest(nc, dc, digest_fn,
                                               record["variant"], record[key]),
                "snapshot_fen": record[key],
            }
            _node.validate_record(nc, vc, dc, epc, fc, node_record, digest_fn)
        except _node.NodeError:
            failed = True
        if failed:
            _fail(ec, "malformed_edge_record")
    return record


class EdgeTable:
    """Buckets keyed by (from digest, move); equality by the canonical
    four-tuple of stored records; insert-or-return-existing; a same
    from-identity and move with another target is conflicting_edge."""

    def __init__(self, docs=None, digest_fn=digest_fen):
        docs = load_docs() if docs is None else docs
        (self.ec, self.vc, self.dc, self.epc, self.fc, self.lc,
         self.nc) = docs
        self.digest_fn = digest_fn
        self.buckets = {}

    def _docs(self):
        return (self.ec, self.vc, self.dc, self.epc, self.fc, self.lc,
                self.nc)

    def _identity(self, record):
        return (record["variant"], record["move"],
                _identity(self.vc, self.dc, self.epc, self.fc,
                          record["variant"], record["from_snapshot_fen"]),
                _identity(self.vc, self.dc, self.epc, self.fc,
                          record["variant"], record["to_snapshot_fen"]))

    def insert(self, variant_id, move, from_fen, to_fen):
        rec = _make_record(self._docs(), variant_id, move, from_fen, to_fen)
        bucket_key = (_oracle(self.ec, self.nc, self.dc, self.digest_fn,
                              variant_id, rec["from_snapshot_fen"]), move)
        new_identity = self._identity(rec)
        for existing in self.buckets.get(bucket_key, []):
            existing_identity = self._identity(existing)
            if existing_identity == new_identity:
                return existing
            if existing_identity[:3] == new_identity[:3]:
                _fail(self.ec, "conflicting_edge")
        self.buckets.setdefault(bucket_key, []).append(rec)  # commit
        return rec

    def merge(self, other):
        """Iterated insertion of each validated source record into a
        STAGED copy, adopted only when the whole union is compatible;
        per record, validation precedes staging (the reference
        order), so the first failing record decides the class."""
        source = other.records()
        if type(source) is not list:
            _fail(self.ec, "malformed_edge_record")
        source = list(source)  # the batch is read once
        staged = EdgeTable(self._docs(), self.digest_fn)
        staged.buckets = {key: list(bucket)
                          for key, bucket in self.buckets.items()}
        for rec in source:
            if not _exact_dict(rec) or set(dict.keys(rec)) != set(
                    self.ec["record"]["fields"]):
                _fail(self.ec, "malformed_edge_record")
            # one read of the four values; validation and staging
            # both see exactly this copy
            rec = {f: dict.__getitem__(rec, f) for f in _FIELDS}
            validate_record(*self._docs(), rec, self.digest_fn)
            staged.insert(rec["variant"], rec["move"],
                          rec["from_snapshot_fen"], rec["to_snapshot_fen"])
        self.buckets = staged.buckets  # commit
        return self

    def records(self):
        return [rec for bucket in self.buckets.values() for rec in bucket]

    def serialize(self):
        return sorted((r["variant"], r["move"], r["from_snapshot_fen"],
                       r["to_snapshot_fen"]) for r in self.records())
