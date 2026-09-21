# T0212: store WAL contract - design notes (docs-only; the contract yaml is normative)

## Scope
Write-ahead log for the position graph store: a single append-only
log of content-addressed entries over canonical graph-state
mutations (put/delete of exact node records). Detection, diffing,
versioning and migration already exist; the WAL OWNS the durable
mutation journal they all sit on.

## Model
- Entry: exact five-field record {entry_id, sequence, op, payload,
  prior_entry_id}. sequence is exactly the 1-based log position;
  prior_entry_id chains to the previous tip (genesis pins
  wal0:0...0); entry_id is content-addressed (wal1: sha256 over
  sequence + op + canonical payload + prior), DERIVED, never
  caller-supplied.
- Operation registry pinned in the contract: put (upsert identity
  -> exact node record) and delete (remove the identity of an
  exact record, if present). Both payloads are {identity, record};
  the record validates through the linked node machinery and the
  identity must equal its derived canonical identity. No other
  operations are registered.
- append(log, request): request {op, payload} validated and FROZEN
  first; the full existing log validated structurally (shape, op
  registration, sequence positions, id grammars, prior links,
  payload records); then the whole log frozen and every entry id
  re-derived behind the oracle boundary; the new entry staged and
  committed LAST. replay(log): the same validation and chain
  re-derivation, then a fold of the registered ops in sequence
  order over the empty state, returning the state, its gs1: id,
  the head tip (genesis on empty) and the applied count.
- The payload canonicalizer is UNTRUSTED input (exactly one call
  per entry per operation, frozen log/request snapshots before the
  first call, exact built-in-str UTF-8-encodable output or fail
  closed - the collision/migration boundary lessons).

## Failures (closed)
- malformed_wal_entry: request/entry/payload/record grammar
  violations (wrong types, wrong key sets, identity != derived,
  bad id grammars).
- unknown_operation: op not in the pinned registry.
- sequence_conflict: sequence is not the exact 1-based position.
- corrupt_chain: prior-link divergence, or stored entry_id
  differing from the recomputed one (tampered records land here
  even with a re-forged identity).
- divergent_canonicalization: canonicalizer raising ANY BaseException (KeyboardInterrupt/SystemExit/GeneratorExit included - the boundary catches BaseException so the untrusted oracle can never escape raw) or returning
  a non-exact-built-in-str.

## Properties
- total over hostile requests/logs/oracles; atomic (rejected
  append or replay leaves every input bit-identical; a committed
  append changes the log by exactly the staged entry);
  deterministic (same inputs, same entry and replayed state);
  rollback: staged entry only, committed after full validation.
