"""Production transposition-node table for
data/contracts/transposition_node.yaml (contract id
chess-transposition-node).

A node is the merge point for position identity: every move-order
path reaching one identity reaches ONE node. Identity is the linked
variant contract's canonical_fields tuple; the position digest is the
bucket key only and never decides equality. Records are exactly
{variant, digest, snapshot_fen} with the en-passant IDENTITY value and
clocks normalized to "0 1". Links only the shipped graph.fen and
graph.position_digest runtimes; never imports tests.

The digest oracle is injectable (a collision seam). It is UNTRUSTED:
every call runs behind a BaseException boundary and its output must
be an exact str matching the linked digest format, otherwise the
record it would produce is malformed (malformed_node_record) - a
forged typed error from the oracle never passes through as its own
class.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from graph.fen import FenError, emit_fen, parse_fen
from graph.position_digest import _ep_identity, digest_fen

__all__ = ["FAILURE_MAPPING", "NodeError", "NodeTable", "load_docs",
           "validate_record"]

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "data" / "contracts" / "transposition_node.yaml"
_LINKED = ("variant.yaml", "position_digest.yaml", "en_passant.yaml",
           "fen.yaml")


def load_docs():
    """(node, variant, digest, en_passant, fen) contract documents,
    freshly parsed - callers may never mutate a shared copy."""
    docs = [yaml.safe_load(CONTRACT.read_text())["contract"]]
    for name in _LINKED:
        path = ROOT / "data" / "contracts" / name
        docs.append(yaml.safe_load(path.read_text())["contract"])
    return tuple(docs)


FAILURE_MAPPING = dict(load_docs()[0]["failures"]["mapping"])


class NodeError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(nc, cls):
    raise NodeError(cls, nc["failures"]["mapping"][cls])


def _exact_dict(obj):
    """Exact dict whose every key is an exact str, checked before any
    set build, membership test or lookup."""
    return type(obj) is dict and all(type(k) is str for k in dict.keys(obj))


def _variant_ids(vc):
    return [e["id"] for e in vc["variants"]["entries"]]


def _identity_tuple(vc, dc, ec, fc, variant_id, position):
    """The linked canonical_fields tuple in its linked order."""
    _board, color, rights, _ep, _half, _full = position
    components = {
        "variant": variant_id,
        "board": emit_fen(fc, position).split(" ")[0],
        "side_to_move": color,
        "castling_rights": rights or fc["castling"]["none_sentinel"],
        "en_passant": _ep_identity(dc, ec, fc, position),
    }
    fields = vc["identity"]["canonical_fields"]
    if set(components) != set(fields):
        raise AssertionError("identity components drifted from the "
                             "linked canonical_fields")
    return tuple(components[name] for name in fields)


def _snapshot_fen(nc, vc, identity):
    if nc["record"]["snapshot_clock_normalization"] != "halfmove-0-fullmove-1":
        raise AssertionError("clock normalization drifted")
    named = dict(zip(vc["identity"]["canonical_fields"], identity,
                     strict=True))
    return (f"{named['board']} {named['side_to_move']} "
            f"{named['castling_rights']} {named['en_passant']} 0 1")


def _oracle_digest(nc, dc, digest_fn, variant_id, fen_text):
    """UNTRUSTED digest oracle boundary: any BaseException (including a
    forged NodeError/FenError/DigestError) or an output that is not an
    exact str in the linked digest format fails closed with a FRESH,
    unchained error (the forged object is never raised or linked)."""
    failed = False
    try:
        out = digest_fn(variant_id, fen_text)
    except BaseException:  # noqa: BLE001 - untrusted oracle boundary
        failed = True
    if failed:
        _fail(nc, "malformed_node_record")
    if type(out) is not str or re.fullmatch(
            dc["digest"]["format"]["regex"], out) is None:
        _fail(nc, "malformed_node_record")
    return out


def _parse(nc, fc, text, cls):
    """The linked parser's input rejections - its FenError and the raw
    ValueError int() raises on a clock beyond the interpreter's
    integer-string limit - map to the caller's class, FRESH and
    unchained."""
    failed = False
    try:
        return parse_fen(fc, text)
    except (FenError, ValueError):
        failed = True
    if failed:
        _fail(nc, cls)


def _make_record(nc, vc, dc, ec, fc, digest_fn, variant_id, fen_text):
    if type(variant_id) is not str or variant_id not in _variant_ids(vc):
        _fail(nc, "unknown_variant")
    if type(fen_text) is not str:
        _fail(nc, "malformed_position")
    position = _parse(nc, fc, fen_text, "malformed_position")
    identity = _identity_tuple(vc, dc, ec, fc, variant_id, position)
    return {
        "variant": variant_id,
        "digest": _oracle_digest(nc, dc, digest_fn, variant_id, fen_text),
        "snapshot_fen": _snapshot_fen(nc, vc, identity),
    }


def validate_record(nc, vc, dc, ec, fc, record, digest_fn=digest_fen):
    """A stored record must satisfy the record section exactly; returns
    the record unchanged or raises NodeError."""
    if not _exact_dict(record) or set(dict.keys(record)) != set(
            nc["record"]["fields"]):
        _fail(nc, "malformed_node_record")
    variant, digest, snapshot = (record["variant"], record["digest"],
                                 record["snapshot_fen"])
    if type(variant) is not str or type(digest) is not str or \
            type(snapshot) is not str:
        _fail(nc, "malformed_node_record")
    if variant not in _variant_ids(vc):
        _fail(nc, "unknown_variant")
    if re.fullmatch(dc["digest"]["format"]["regex"], digest) is None:
        _fail(nc, "malformed_node_record")
    position = _parse(nc, fc, snapshot, "malformed_node_record")
    fen_named = dict(zip(fc["fields"]["order"], snapshot.split(" "),
                         strict=True))
    if fen_named["halfmove_clock"] != "0" or \
            fen_named["fullmove_number"] != "1":
        _fail(nc, "malformed_node_record")
    identity = _identity_tuple(vc, dc, ec, fc, variant, position)
    named = dict(zip(vc["identity"]["canonical_fields"], identity,
                     strict=True))
    if fen_named["en_passant"] != named["en_passant"]:
        _fail(nc, "malformed_node_record")
    if digest != _oracle_digest(nc, dc, digest_fn, variant, snapshot):
        _fail(nc, "malformed_node_record")
    return record


class NodeTable:
    """Digest-bucketed node table; equality is canonical field
    comparison of the stored records only; insert-or-return-existing.
    Every mutation commits last, so a rejection changes nothing."""

    def __init__(self, docs=None, digest_fn=digest_fen):
        docs = load_docs() if docs is None else docs
        self.nc, self.vc, self.dc, self.ec, self.fc = docs
        self.digest_fn = digest_fn
        self.buckets = {}

    def _identity(self, record):
        position = parse_fen(self.fc, record["snapshot_fen"])
        return _identity_tuple(self.vc, self.dc, self.ec, self.fc,
                               record["variant"], position)

    def insert(self, variant_id, fen_text):
        rec = _make_record(self.nc, self.vc, self.dc, self.ec, self.fc,
                           self.digest_fn, variant_id, fen_text)
        new_identity = self._identity(rec)
        bucket = self.buckets.get(rec["digest"], [])
        for existing in bucket:
            if self._identity(existing) == new_identity:
                return existing
        self.buckets.setdefault(rec["digest"], []).append(rec)  # commit
        return rec

    def merge(self, other):
        """Freeze the source (a detached list of detached exact-dict
        copies), validate EVERY frozen record before any insertion,
        then insert the frozen records in order into a STAGED copy of
        the buckets; the table adopts the staged buckets only after
        every insert succeeded, so a rejected merge - even one the
        untrusted oracle fails midway - changes nothing, and an oracle
        that rewrites the caller's live records cannot change what is
        inserted. `other` is the caller's own argument, not an
        untrusted component: a raising other.records() propagates."""
        source = other.records()
        if type(source) is not list:
            _fail(self.nc, "malformed_node_record")
        frozen = []
        for rec in list(source):
            if not _exact_dict(rec):
                _fail(self.nc, "malformed_node_record")
            frozen.append(dict(rec))
        for rec in frozen:
            validate_record(self.nc, self.vc, self.dc, self.ec, self.fc,
                            rec, self.digest_fn)
        staged = NodeTable((self.nc, self.vc, self.dc, self.ec, self.fc),
                           self.digest_fn)
        staged.buckets = {key: list(bucket)
                          for key, bucket in self.buckets.items()}
        for rec in frozen:
            staged.insert(rec["variant"], rec["snapshot_fen"])
        self.buckets = staged.buckets  # commit
        return self

    def records(self):
        return [rec for bucket in self.buckets.values() for rec in bucket]

    def serialize(self):
        return sorted((r["variant"], r["digest"], r["snapshot_fen"])
                      for r in self.records())
