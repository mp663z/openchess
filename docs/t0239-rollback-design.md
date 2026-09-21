# T0239: store rollback contract - design notes (docs-only; the contract yaml is normative)

## Scope
Verified truncation rollback of a WAL-logged store: roll the log
back to a prior position. WAL owns the journal, backup/restore
own state snapshots; rollback OWNS the log-truncation receipt and
tail archival. State-level restore and multi-log replication are
explicitly not scope.

## Model
- Receipt: exact five-field record {rollback_id, from_head,
  to_head, truncated_count, archive_token}. rollback_id is
  content-addressed (rbk1: sha256 over from_head + to_head +
  truncated_count + archive_token), DERIVED, never
  caller-supplied.
- rollback(log, request): request {target_sequence} - an exact
  1-based log position, or 0 for genesis. The source log is
  validated through the LINKED WAL machinery first (any rejection
  surfaces as corrupt_source); an out-of-range target is
  unknown_target; the request and the entire log are FROZEN; the
  truncated tail is archived EXACTLY ONCE through the untrusted
  tail archiver (raising, non-exact-str, UTF-8-inencodable or
  wrong-grammar token fails closed as divergent_archive; the
  archiver receives a DETACHED tail copy - mutating its argument
  is inert); only then does the commit remove exactly the tail.
  The surviving prefix chain is unchanged, so to_head stays the
  valid tip; a rejected rollback leaves log and request
  bit-identical.

## Failures (closed)
- malformed_rollback_record: request/log grammar or type
  violations.
- unknown_target: target sequence outside 0..len(log).
- corrupt_source: source log fails linked WAL validation or
  chain re-derivation.
- divergent_archive: archiver raising ANY BaseException (KeyboardInterrupt/SystemExit/GeneratorExit included - the boundary catches BaseException so the untrusted oracle can never escape raw) or returning a
  non-exact-string or wrong-grammar token.

## Properties
- total over hostile requests/logs/archivers; atomic (rejected
  rollback leaves every input bit-identical); deterministic (same
  inputs, same receipt and surviving prefix); rollback: a
  committed rollback removes exactly the tail, nothing else.
