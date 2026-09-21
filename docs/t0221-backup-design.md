# T0221: store backup contract - design notes (docs-only; the contract yaml is normative)

## Scope
Content-addressed backup receipts over a WAL-logged canonical
graph state. The WAL owns the durable mutation journal; backup
OWNS the point-in-time snapshot receipt and its integrity
verification. Restore, incremental and offsite replication are
explicitly not scope (restore is its own contract).

## Model
- Receipt: exact four-field record {backup_id, head, state_id,
  entry_count} plus the canonical bundle string. head is the
  validated source log's WAL tip (wal1: id, genesis wal0:0...0 on
  an empty log); state_id is the replayed state's gs1: content
  digest (graph-diff semantics); entry_count is the exact applied
  entry count; backup_id is content-addressed (bck1: sha256 over
  head + state_id + entry_count + canonical bundle), DERIVED,
  never caller-supplied.
- backup(log): the source log is validated and replayed through
  the LINKED WAL machinery (any WAL-level rejection surfaces as
  corrupt_source, typed, never raw); the replayed state is frozen
  into a detached snapshot BEFORE the serializer runs; the bundle
  serializer is UNTRUSTED input (exactly one call per backup,
  exact built-in-str output or fail closed); the receipt derives
  only from the frozen snapshot; the source log is restored
  bit-identical on every exit - backup never mutates it.
- verify(receipt): LOCAL and total (no oracle): exact shape and
  types, pinned grammars, head/count consistency (an empty source
  pins the genesis head and the empty-state id; a non-empty
  source never pins genesis), then recomputed backup id vs
  stored - divergence fails closed as divergent_backup.

## Failures (closed)
- malformed_backup_record: receipt grammar/type violations, or a
  source log that is not a list at all.
- corrupt_source: the source log fails linked WAL validation or
  chain re-derivation.
- divergent_snapshot: serializer raising ANY BaseException (KeyboardInterrupt/SystemExit/GeneratorExit included - the boundary catches BaseException so the untrusted oracle can never escape raw) or returning a
  non-exact-built-in-str.
- divergent_backup: recomputed backup id differs from the stored
  one, or head/count consistency is violated (even with a
  re-forged self-consistent id).

## Properties
- total over hostile logs/receipts/serializers; atomic (rejected
  backup or verify leaves every input bit-identical; backup never
  mutates the source log on success either);
  deterministic (same log, same receipt); rollback: staged
  receipt only, committed after full validation.
