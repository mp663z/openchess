"""Production three-way conflict detector for data/contracts/conflict.yaml
(contract id graph-conflict).

detect(base, left, right) validates every state through the linked node
machinery BEFORE change derivation, derives the state ids from the
validated content (never caller supplied), fails closed as
divergent_base when the base id equals a side id, derives each side's
identity-keyed changes against the base, and reports every
incompatible overlap as a witness keyed by canonical identity, in
canonical order. It never mutates its inputs and never resolves a
conflict. The record digest is format-checked only: it never decides
identity or compatibility.

Links only the shipped graph.node and tools.variant_runtime runtimes
(the same identity projection graph.node and graph.diff key states by)
and never imports tests.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from graph.node import make_record
from graph.position_digest import DigestError
from tools.variant_runtime import VariantError, identity, parse_position

__all__ = ["FAILURE_MAPPING", "ConflictDetector", "ConflictError",
           "load_docs", "state_id"]

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "data" / "contracts"


def load_docs():
    """(conflict, transposition_node, position_digest) contract
    documents, freshly parsed per call."""
    return tuple(
        yaml.safe_load((CONTRACTS / name).read_text())["contract"]
        for name in ("conflict.yaml", "transposition_node.yaml",
                     "position_digest.yaml"))


_CC, _NC, _DC = load_docs()
FAILURE_MAPPING = dict(_CC["failures"]["mapping"])
_RECORD_FIELDS = frozenset(_NC["record"]["fields"])
_DIGEST_RE = re.compile(_DC["digest"]["format"]["regex"])
_MCR = "malformed_conflict_record"


class ConflictError(Exception):
    def __init__(self, failure_class, code):
        super().__init__(failure_class)
        self.failure_class = failure_class
        self.code = code


def _fail(cls):
    raise ConflictError(cls, FAILURE_MAPPING[cls])


def _validated_identity(key, rec):
    """Check one state entry; the key must be the canonical identity
    derived from the record."""
    if type(key) is not str or type(rec) is not dict:
        _fail(_MCR)
    # exact-str keys BEFORE any set build or lookup (a colliding hash
    # with a raising __eq__ must fail closed, never raw)
    rec_keys = list(dict.keys(rec))
    if not all(type(k) is str for k in rec_keys):
        _fail(_MCR)
    if len(rec_keys) != len(_RECORD_FIELDS) or \
            set(rec_keys) != _RECORD_FIELDS:
        _fail(_MCR)
    variant, digest, snapshot = (dict.__getitem__(rec, "variant"),
                                 dict.__getitem__(rec, "digest"),
                                 dict.__getitem__(rec, "snapshot_fen"))
    if type(variant) is not str or type(digest) is not str or \
            type(snapshot) is not str:
        _fail(_MCR)
    failed = False
    try:
        derived = make_record(variant, snapshot)
        derived_key = repr(tuple(identity(
            parse_position(variant, snapshot)).values()))
    except (VariantError, DigestError, ValueError):
        failed = True
    if failed:
        _fail(_MCR)
    if variant != derived["variant"] or snapshot != derived["snapshot_fen"]:
        _fail(_MCR)
    if _DIGEST_RE.fullmatch(digest) is None:
        _fail(_MCR)
    if derived_key != key:
        _fail(_MCR)
    return {"variant": variant, "digest": digest, "snapshot_fen": snapshot}


def _validated_state(state):
    """An exact dict of canonical identity -> exact linked node record;
    returns a detached copy that everything later reads."""
    if type(state) is not dict:
        _fail(_MCR)
    return {key: _validated_identity(key, rec)
            for key, rec in list(dict.items(state))}


def state_id(state):
    """Canonical state content digest over the validated state: sha256
    over the sorted identity -> sorted-field record serialization."""
    frozen = _validated_state(state)
    return _state_id(frozen)


def _state_id(frozen):
    parts = []
    for key in sorted(frozen):
        rec = frozen[key]
        body = "|".join(f"{field}={rec[field]}" for field in sorted(rec))
        parts.append(f"{key}\n{body}\n")
    return "gs1:" + hashlib.sha256("".join(parts).encode()).hexdigest()


def _changes(base, derived):
    """identity -> ("added", rec) | ("removed", rec) | ("changed", rec)"""
    out = {}
    for key, rec in derived.items():
        if key not in base:
            out[key] = ("added", rec)
        elif base[key] != rec:
            out[key] = ("changed", rec)
    for key, rec in base.items():
        if key not in derived:
            out[key] = ("removed", rec)
    return out


class ConflictDetector:
    """Total, deterministic, left-right symmetric and complete; never
    mutates inputs; never auto-resolves."""

    def detect(self, base, left, right):
        states = [_validated_state(s) for s in (base, left, right)]
        base_s, left_s, right_s = states
        base_id, left_id, right_id = (_state_id(s) for s in states)
        # each side must diverge from the base; left == right is an
        # identical outcome, pinned compatible
        if base_id in (left_id, right_id):
            _fail("divergent_base")
        lch, rch = _changes(base_s, left_s), _changes(base_s, right_s)
        conflicts = {}
        for key in sorted(set(lch) & set(rch)):
            (lkind, lrec), (rkind, rrec) = lch[key], rch[key]
            if lkind == rkind == "removed":
                continue  # both removed: identical outcome
            if lkind == rkind:
                if lrec != rrec:
                    conflicts[key] = {
                        "kind": ("added_differently" if lkind == "added"
                                 else "both_changed_differently"),
                        "left": dict(lrec), "right": dict(rrec)}
                continue
            # one side changed, the other removed
            conflicts[key] = {
                "kind": "changed_vs_removed",
                "left": dict(lrec) if lkind == "changed" else None,
                "right": dict(rrec) if rkind == "changed" else None}
        return {"base_id": base_id, "left_id": left_id,
                "right_id": right_id, "conflicts": conflicts}
