# T0293: jobs idempotency contract - design notes (docs-only; the contract yaml is normative)

## Scope
Exactly-once application of queue-contract (T0284) transitions under
caller-supplied idempotency keys, over ONE queue state and ONE receipt
ledger. This layer owns the idempotency key, the request fingerprint
and the receipt ledger. Key expiry, cross-state deduplication,
distributed locking and the queue's own enqueue dedupe_key mechanics
are not in scope - the queue contract owns those.

## Model
- Request: exactly `{idempotency_key, op, payload}`. The key is an
  opaque caller-supplied token
  (`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`). `op` is one of the queue
  contract's four ops; `payload` is that op's queue request MINUS the
  op field, validated through the linked queue machinery, never
  restated here.
- Receipt: exactly `{receipt_id, idempotency_key,
  request_fingerprint, prior_state_id, state_id, sequence}`.
  - `request_fingerprint` is `jif1:` + sha256 over the
    domain-separated, length-framed canonical serialization of every
    exact frozen request field EXCEPT the key (the op and the
    canonical JSON of the whole payload).
  - `prior_state_id` / `state_id` are the queue contract's `qs1:`
    state ids of the pre- and post-apply state (grammar imported, not
    restated).
  - `sequence` is the receipt's position, contiguous from 0.
  - `receipt_id` is `jir1:` + sha256 over key, fingerprint, prior
    state id, state id and sequence. DERIVED, never caller supplied.
- Outcomes (closed): `applied` (unseen key: exactly one queue
  transition, one receipt) and `replayed` (seen key, identical
  fingerprint: the stored receipt, no transition).

## Order of operations
Every apply: exact request validation, full linked queue-state
validation, full ledger validation bound to the live state (every
receipt re-derives: shape, grammars, receipt-id, unique keys,
contiguous sequences, prior/state chaining, last receipt names the
live state). THEN snapshots freeze request, state and ledger; exactly
one untrusted-fingerprinter call on a detached copy; the token is
shape-validated and bound BYTE-EXACT to the local derivation;
key-lookup-then-fingerprint deduplication; the transition stages on
the frozen state; state and receipt commit together LAST. A seen key
with a different fingerprint is `key_conflict`, never an overwrite. A
request the queue engine rejects on its own domain rules propagates
the queue's typed error and commits no receipt.

## Failure classes (closed)
`malformed_idempotency_request`, `corrupt_queue`, `corrupt_ledger`,
`key_conflict`, `divergent_fingerprint`; the error enum is
`malformed_request`, `corrupt_queue`, `corrupt_ledger`,
`key_conflict`, `divergent_fingerprint`, `internal` (only `internal`
is retryable). Hostile requests, states, ledgers and fingerprinters
fail closed typed, never raw: rejection leaves state, ledger and
request bit-identical.

## Error domain (composed, explicit)
This layer's own failures use the closed enum in `errors`. A
queue-DOMAIN rejection (unknown_job, lease_conflict,
dedupe_conflict, capacity_exceeded) propagates the queue
contract's typed error UNCHANGED - the surfaced error domain is
the composition declared in `error_composition`, not the closed
enum alone. The queue's malformed_request and corrupt_queue never
surface here: this layer's own request and state validation runs
first and intercepts them into its own classes.

## Graph disjointness
The request must be a SELF-CONTAINED graph: no container shared
with the state or the ledger. The witness that forced this: an
enqueue payload value that IS the live state object (`{"jobs": [],
"next_seq": 0}` is an admissible payload) - the commit's
state.clear/update rewrote the request's nested payload through
the alias, after the input-preserving restore had already run.
Same for the payload value aliasing the ledger list (the receipt
append mutated the request). The guard rejects any shared
container typed (malformed_idempotency_request) after validation
and BEFORE the oracle; equal-by-value detached content is
unaffected. This mirrors the queue contract's own alias rejection
inside payloads.

## Rollback (qualified to content addressing)
The ledger's terminal binding makes a state rolled back TO A
DIFFERENT DIGEST past a receipt fail closed as `corrupt_ledger`
on every apply; the stale receipt is never replayed. Trimming the
ledger to the surviving prefix restores service, and the
rolled-back key re-applies deterministically to the identical
receipt. Boundary the contract names honestly: a STATE-NEUTRAL
transition (a no-op claim on an empty queue) has
prior_state_id == state_id, so restoring the pre-apply state is
a digest-IDENTICAL rollback that content addressing cannot
distinguish from no rollback - the same key replays. The battery
pins this boundary so any change is deliberate; no epoch
authority is invented to hide it.

## Links
- data/contracts/jobs_idempotency.yaml (normative)
- data/contracts/queue.yaml (linked T0284 contract)
- tests/test_t0293_jobs_idempotency_contract.py (reference battery)
- the battery carries the mutation account: 7 NAMED HAND-BUILT
  behavioral counter-tests (surrogate algorithms and oracle
  fakes, NOT source-edit mutants over a generated population -
  they show each named failure mode is caught, nothing more) and
  54 lint mutants, which ARE real document checks: each edited
  contract yaml is fed to the real lint and must be rejected,
  with subset-coverage proof over every contract section
