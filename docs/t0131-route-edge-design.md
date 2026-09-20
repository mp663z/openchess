# T0131 route edge contract - design draft (v0, authoring in progress)

Mirror the T0122 transposition-node gold standard: structured yaml +
exact lint pins + fully contract-derived reference (edge record make/
validate + EdgeTable merge semantics) + mutation battery.

## Design decisions (grounded 9:18)
- A route edge is the repertoire graph's DIRECTED edge: from one
  transposition node to another via ONE move. Annotations attach by
  edge identity, never by path.
- Edge record: EXACTLY four fields [variant, move,
  from_snapshot_fen, to_snapshot_fen]. variant = registry id
  verbatim (linked variant contract id_grammar). move = long
  algebraic from-square-to-square-optional-promotion per the linked
  legal-moves move_model. from/to snapshots = canonical six-field
  FEN with identity EP value and normalized clocks, exactly the
  linked node contract's snapshot form (lint shares the pin).
- Equality: canonical four-tuple (variant, move, from identity, to
  identity) - field comparison only, digests accelerator-only.
  Derived digests NOT stored (regenerable), unlike the node record
  which stores digest as its lookup key - DECISION: edge stores no
  digest fields; the edge table buckets by (from_digest, move).
- Move APPLICATION is delegated: "applying the move is the
  legal-moves/position runtime's layer, not this one" (same
  precedent as the UCI contract's moves tail). The legal-moves
  runtime (tools/legal_moves_runtime) owns a reduced state model
  (occupied + side_to_move; no castling/EP/clocks), so full
  application validation is out of layer here.
- Enforceable semantic at this layer, PINNED:
  move_application_deterministic - the same (from identity, move)
  never maps to two different to-node identities; a second edge
  with the same from+move but a different to identity is a
  conflict rejection with bit-identical rollback.
- Merge semantics (edge table): insert-or-return-existing,
  idempotent, commutative, associative (record fully determined by
  the identity tuple); same edge never twice.
- Failure classes (closed, mapped): malformed_edge_record (shape,
  move grammar, non-canonical snapshots incl. clocks/EP identity),
  malformed_position (FEN reject inside a snapshot, subclass
  preserved), unknown_variant, conflicting_edge (from+move maps to
  a second to-identity).
- Links: variant, fen, position_digest, transposition_node,
  legal_moves.

## Battery plan
happy: build edges from real lines (e4 e5 Nf3 ...), exact records;
transposition convergence: two move-orders reaching the same
identity share from/to nodes. boundary: promotion move, castle move
token, kings-only edges, EP-producing push (to-snapshot carries
identity EP), EP-capture edge. malformed: single-defect per class +
declarative repairs + layer classification. rollback: rejected
insert/validate leaves the edge table bit-identical; conflicting
edge rejected with no trace. mutants: in-memory flattening of
determinism/merge tables, lint mutants for every pinned section.

## v2 amendment (verifier #2 round 1): ATOMIC merge

Merge is pinned ATOMIC (structured: merge.atomic true,
merge.conflict_in_batch whole-merge-rejected-nothing-committed):
the entire union is preflighted in a staged copy and committed only
when fully compatible - conflicts internal to the source batch or
against the destination reject the whole merge and leave the
destination bit-identical; over conflicting tables BOTH merge
orders reject (never first-writer residue). Reference implements
clone-then-adopt; batteries: both-orders-reject,
batch-valid-prefix-then-conflict rollback, conflict internal to a
single source batch, plus mutants for the two new pins.
