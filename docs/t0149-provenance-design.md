# T0149 provenance contract - design draft (v0, authoring in progress)

Mirror the T0122/T0131/T0140 gold standard: structured yaml + exact
lint pins + fully contract-derived reference (ProvenanceTable merge
semantics) + mutation battery.

## Product grounding (docs/plan/product-report-v5.md)

- Line 398: "comments, engine analysis and source games retrieved by
  position, with full provenance, regardless of which file they came
  from" - provenance is the evidence-grade record of ORIGIN for every
  graph record, and it must survive cross-file retrieval.
- Line 429 (vs Lichess): "evidence-grade provenance" is named product
  differentiation.
- Line 457 (rights): lichess-dump attribution is CARRIED as
  provenance (CC0 / CC BY-SA 4.0) - provenance is also the rights
  attribution surface, so it must fail closed on unknown sources
  exactly like the import contract (docs/rights-policy.md).

## Scope decision (grounded 13:45)

The dev plan's provenance chain (T0149-T0157) sits inside the Graph
fork, so the contract covers GRAPH records: which source
observations (imported games through the import capability, and the
user's own edits as a first-class source class) produced each graph
record. It pins the provenance record shape, identity, merge
algebra, and failure classes - not the source-record model itself,
which the linked import contract already owns.

## Design decisions

- Target kinds: CLOSED enum [transposition_node, route_edge,
  opening_context] - exactly the graph records main pins contracts
  for today. A target outside the enum fails closed
  (unknown_target_kind); adding a kind is a NEW contract version,
  never in-place drift.
- Target identity: EXACTLY as the owning sibling pins it - node
  identity = the transposition-node contract's canonical identity,
  edge identity = the route-edge contract's four-tuple, context
  identity = (variant, path_moves) per the opening-context contract.
  The lint reads each sibling; nothing restated here.
- Source entry: EXACTLY [source_id, game_id, first_observed_at].
  source_id from the linked import contract's sources registry
  (unknown fails closed). game_id is the import record's stored
  game_id form (free-form per import contract identity rule; pinned
  here as nonempty printable ASCII). first_observed_at is a pinned
  RFC3339-UTC grammar (ASCII, seconds precision, Z suffix),
  validated as an ACTUAL calendar instant (Gregorian leap-year
  February, 30/31-day months); LEAP-SECOND POLICY: pinned
  NON-LEAP profile - :60 rejected, never normalized (verifier
  remediation v2).
- Provenance record: EXACTLY [target_kind, target, sources].
  sources is a NONEMPTY set of source entries (set semantics: order
  free, duplicates collapsed; empty set is malformed - a record
  with no source is no provenance). target is the target identity
  fields verbatim per kind.
- Identity: keyed by (target_kind, target). One target carries
  EXACTLY ONE provenance record; the source set is the complete
  origin evidence for that target.
- Merge: insert-or-union, ATOMIC with full preflight. New target ->
  create; existing target -> sources := union(sources). Union makes
  merge IDEMPOTENT, COMMUTATIVE and ASSOCIATIVE by construction.
  NO same-key conflict class exists: two provenance records for the
  same target NEVER conflict; the merged record is always the
  union (pinned explicitly - this is the one graph table where a
  same-key pair is a merge, not a contradiction). House standard
  still applies: exact stored source records are consumed and
  validated (validate_record against the destination's linked
  docs) BEFORE staging; never laundered through reconstruction.
- Additive only: a source entry, once merged, is never removed by
  any merge. Removal/retraction would be a NEW contract version.
- Failures: [malformed_provenance_record, unknown_target_kind,
  malformed_target_identity, unknown_source] onto closed error
  enum [malformed_request, unknown_target_kind, unknown_source,
  internal]; retryable true ONLY for internal.
- Rollback: a rejected insert or merge leaves the destination table
  bit-identical - over failing batches BOTH merge orders reject
  with identical pre-merge destinations, no valid-prefix residue.
- Totality: target validation is TOTAL - explicit field-type
  guards (str/list) run BEFORE any sibling call, so no non-string
  shape (None, bools, ints, lists, mappings) ever escapes sibling
  machinery as a raw exception; every such case is
  malformed_target_identity (verifier remediation v2, Cartesian
  battery across all three kinds' fields).
- Versioning: /graph/provenance/v1; normative change = new version.

## Test plan (>=25 mutants)

- Happy: single-source insert; multi-source insert; union merge of
  overlapping records; full-batch atomic merge.
- Boundary: one-entry sources set (minimum); set-order-insensitive
  equality; duplicate entries within one record collapse.
- Malformed single-defect battery: wrong field set; target_kind
  outside enum; empty sources; malformed source entry (each field);
  bad timestamp grammar; target identity failing each sibling's
  validation (node, edge, context); unknown source_id (fail closed).
- Merge algebra: idempotence, commutativity, associativity, both
  merge orders over failing batches reject bit-identical, internal
  batch conflicts (same entry, malformed later row), atomic
  preflight (no valid prefix committed).
- Behavioral mutant: reconstruction-laundering guard - a merge that
  rebuilds provenance from (target, sources) under the receiver
  instead of validating the exact stored record gets caught, plus
  the counter-test that a valid exact record merges.
- Linkage battery: every linked claim re-verified against the
  ACTUAL sibling artifacts (import sources registry, node/edge/
  context identity sections).
