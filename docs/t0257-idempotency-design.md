# T0257: store idempotency contract - design notes (docs-only; the contract yaml is normative)

## Scope
Keyed exactly-once apply over a WAL-logged store. WAL owns the
journal and rollback owns log truncation; idempotency OWNS the
idempotency-key receipt semantics and request fingerprinting.
Key expiry, cross-log deduplication and distributed locking are
explicitly not scope.

## Model
- Receipt: exact five-field record {receipt_id, idempotency_key,
  request_fingerprint, entry_id, sequence}. receipt_id is
  content-addressed (idr1: sha256 over key + fingerprint +
  entry_id + sequence), DERIVED, never caller-supplied.
- apply(log, ledger, request): request {idempotency_key, op,
  payload}. The key is an exact built-in str matching
  ^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$; op and payload validate
  through the LINKED WAL machinery. Then the source log validates
  through linked WAL replay (corrupt_source) and the ledger
  validates in full (corrupt_ledger): exact receipt shape and
  grammars, receipt_id re-derivation, unique keys, strictly
  increasing sequences, and every receipt bound to the exact live
  entry at its sequence (entry_id AND the local fingerprint of
  that entry). All of this runs before any untrusted code.
- Hostile-key guard: before any set construction or key
  comparison on a caller-supplied dict (request, payload, record,
  ledger receipt, log entry, log payload, log record) every key
  must be an exact built-in str (checked through the unbound
  dict.keys). A key whose hash collides with a real field name
  and whose __eq__ raises fails closed as the boundary's typed
  class (malformed_request / corrupt_ledger / corrupt_source),
  never raw, and the engine guards log entries itself before
  handing them to the linked WAL.
- Request, log and ledger are FROZEN; the untrusted request
  fingerprinter is called EXACTLY ONCE on a DETACHED copy of
  (op, payload). Raising ANY BaseException, a non-exact-str,
  UTF-8-inencodable or wrong-grammar token, or a token that is
  not byte-for-byte the local canonical fingerprint (sha256 over
  the domain-separated, length-framed serialization of op,
  identity and every node-record field; the key is excluded)
  fails closed as divergent_fingerprint.
- Outcomes (closed): applied - unseen key, one WAL entry and one
  receipt appended together, committed LAST; replayed - seen key
  with identical fingerprint, the stored receipt is returned and
  nothing is appended. A seen key with a different fingerprint is
  key_conflict: never overwritten, never silently replayed.
- A rejected apply leaves log, ledger and request bit-identical,
  including when the fingerprinter mutates them mid-call.

## Rollback interplay
The ledger is bound to the live log. If the log is rolled back
(T0239) past a receipt, that receipt names a vanished or
different entry, so every apply fails closed as corrupt_ledger
and the stale receipt is never replayed. Trimming the ledger to
the surviving prefix restores service; the rolled-back key then
re-applies deterministically.

## Failures (closed)
- malformed_idempotency_request -> malformed_request
- corrupt_source -> corrupt_source
- corrupt_ledger -> corrupt_ledger
- key_conflict -> key_conflict
- divergent_fingerprint -> divergent_fingerprint
Only internal is retryable.

## Properties
total, atomic, deterministic, idempotent (a repeated identical
keyed request returns the identical receipt and appends nothing),
rollback (stale ledger fails closed, never replayed).
