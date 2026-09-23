# T0266: store corruption contract - design notes (docs-only; the contract yaml is normative)

## Scope
Corruption scan and salvage of a WAL-logged store: find the longest
prefix that still passes full linked WAL validation, quarantine the
corrupt suffix, and cut the log back to that prefix. WAL owns the
journal, rollback owns target-chosen truncation, restore owns state
snapshots; corruption OWNS the scan receipt and suffix quarantine.
Repairing corrupt entries in place and target-chosen rollback are
explicitly not scope.

## Model
- Receipt: exact six-field record {scan_id, verdict, verified_head,
  verified_count, quarantined_count, quarantine_token}. verdict is
  closed: clean or salvaged. scan_id is content-addressed (crp1:
  sha256 over verdict, head, counts and token), DERIVED, never
  caller-supplied. quarantine_token is null when clean.
- scan(log, request): request {max_loss} - an exact built-in int
  >= 0 under an exact built-in dict with exactly one exact-str key.
- Scan domain (closed): the log must be an exact built-in, acyclic,
  alias-free tree of dict/list/str/int/bool/None, str keys only,
  UTF-8-encodable strings, ints of at most 256 bits (checked before
  any int-to-text conversion, so huge ints never hit the interpreter
  digit limit), nesting depth at most 8. Only exact
  built-in types are inspected, so no caller code runs during the
  scan. Anything outside is malformed_corruption_record. Inside the
  domain, any value is legal input: garbage entries, torn writes and
  tampered fields are corruption, not malformed requests.
- Detection: every prefix is judged by the LINKED WAL engine
  (validation plus chain re-derivation). WAL validation is
  prefix-monotone, so the verified prefix ends right before the
  first failing entry, found by binary search over prefixes.
  Salvage is a PREFIX, never a filter: an
  intact entry behind a corrupt one is quarantined too.
- Clean: empty corrupt suffix - no sink call, null token, log
  untouched.
- Loss bound: quarantined_count > max_loss is excessive_loss
  (inclusive bound); the sink is never called.
- Quarantine: the log and request are FROZEN (detached canonical
  copy) before the sink runs; the sink gets a DETACHED copy of the
  suffix and is called EXACTLY ONCE. Its token must equal the LOCAL
  canonical token byte-for-byte: sha256 over the domain-separated,
  type-tagged, length-framed canonical encoding of the entire
  frozen suffix. The live log and request are restored bit-identical
  (reference-preserving) after the call, whatever the sink did.
- Commit LAST: remove exactly the corrupt suffix.

## Failures (closed)
- malformed_corruption_record: request grammar or type violation,
  or a log outside the scan domain.
- excessive_loss: corrupt suffix longer than max_loss.
- divergent_quarantine: sink raising ANY BaseException
  (KeyboardInterrupt/SystemExit/GeneratorExit included), returning a
  non-exact-string, wrong-grammar or non-UTF-8 token, or a token that
  differs from the local canonical suffix token.

## Properties
- total over hostile requests/logs/sinks; atomic (rejected scan
  leaves every input bit-identical); deterministic (same inputs,
  same receipt and verified prefix); rollback: a committed salvage
  removes exactly the corrupt suffix, nothing else.
