# T0140 opening context contract - design draft (v0, authoring in progress)

Mirror the T0122 transposition-node gold standard (and T0131 route
edge): structured yaml + exact lint pins + fully contract-derived
reference (context resolution + ContextTable merge semantics) +
mutation battery.

## Product grounding (docs/plan/product-report-v5.md line 224)

The model separates position identity (canonical state), move-order
path (preserved, never collapsed) and repertoire context (which of
the user's lines a position belongs to). "Reached via a different
move order, it may dodge the user's repertoire or land in a
different opening classification." So opening context is the ONE
graph attribution that is keyed by PATH, not by canonical node
identity: the same transposition node reached via two move orders
may carry two different opening contexts. This contract pins that
boundary - it does not weaken the node/edge identity contracts;
it is the explicit, pinned exception for classification.

## Design decisions (grounded 9:26)

- An opening context is the classification of an ORDERED move path
  from the variant's initial position, resolved against a pinned
  opening registry: the registry entry whose move sequence is the
  LONGEST prefix of the path wins; no prefix matches -> the pinned
  none sentinel (unclassified), never an error, never a guess.
- Registry: entries {code, name, moves}. code grammar ECO-style
  [A-E][0-9][0-9] (structured pin, ASCII). name: nonempty printable
  ASCII. moves: ordered list of long-algebraic move texts per the
  linked legal-moves move_model (square grammar, promotion enum,
  from/to distinctness all read from the sibling, never restated).
  Registry invariants (lint-enforced): codes unique, move sequences
  unique (no two entries classify the same path prefix - otherwise
  longest-prefix could tie), names nonempty.
- Resolution determinism PINNED: same (variant, path) always
  resolves to the same context; a longer path never changes the
  context a shorter prefix already resolved (prefix stability:
  resolving a path and then extending it leaves the shorter path's
  own resolution unchanged).
- Context record: EXACTLY four fields [variant, path_moves,
  opening_code, opening_name]. path_moves = the ordered move list
  (empty path is legal and resolves to the none sentinel unless the
  registry declares an empty-sequence entry - pinned: registry move
  sequences are NONEMPTY, so empty path is always unclassified).
  opening_code/opening_name = the resolved entry verbatim, or the
  pinned none sentinel "-" for both when unclassified (sentinel
  pinned, never empty string, never null).
- Derived fields stored: none. The resolution is a pure function of
  (registry, path); records carry exactly the declared fields.
- Merge semantics (context map keyed by (variant, path_moves)):
  insert-or-return-existing, idempotent, commutative, associative;
  same key never two records; a conflicting mapping (same key,
  different context - only possible under registry drift) rejected
  as conflicting_context with bit-identical rollback.
- Move APPLICATION is delegated exactly as in the route-edge
  contract: whether a path's moves are legal from the initial
  position is the legal-moves/position runtime's layer. This
  contract pins GRAMMAR (each move is a well-formed move_model
  text) and RESOLUTION (registry lookup), never legality.
- Failure classes (closed, mapped): malformed_context_record
  (shape, sentinel misuse, code grammar, code/name mismatch vs
  registry), malformed_path (a path entry that is not a well-formed
  move text), unknown_variant, unknown_opening_code (record cites a
  code absent from the registry), conflicting_context.
- Errors: closed enum [malformed_request, unknown_variant,
  unknown_opening_code, conflicting_context, internal]; retryable
  only internal.
- Links: variant (registry id + id_grammar), legal_moves
  (move_model grammar), transposition_node (the identity-vs-path
  boundary: node identity excludes move_order_path; THIS contract
  is where move_order_path is load-bearing).
- Versioning: base path /graph/opening-context/v1.

## The registry itself

The contract pins the registry SHAPE and invariants; a small seed
registry (a handful of well-known lines: 1.e4 e5 open games, 1.e4
c5 Sicilian, 1.d4 d5 closed games, etc., in long-algebraic form)
ships as data under tests/fixtures/opening_context/ so the
reference model and battery run against REAL entries. Registry
content growth is data work, not contract drift - the lint checks
invariants, never specific openings.

## Battery plan

- happy: resolve real lines to their registry entries, exact
  records; longest-prefix wins over a shorter matching entry.
- path-vs-identity witness: two different paths to the SAME
  transposition node resolve to DIFFERENT contexts (the product
  report's Najdorf example, concretely) - this is the contract's
  reason to exist.
- boundary: empty path -> none sentinel; path exactly equal to a
  registry sequence; path extending past every registered sequence
  keeps the longest matched context; promotion move inside a path.
- merge algebra: idempotent/commutative/associative with group
  permutations; conflicting mapping rejected with rollback.
- malformed: per-class single-defect cases incl. wrong sentinel
  form, code grammar violations, code not in registry, name/code
  mismatch, extra/missing record fields, bad move grammar in path.
- mutation battery: >=25 mutants, all sections covered.
- linkage battery: clean copy passes; drift in variant id_grammar
  or legal-moves move_model fails.
