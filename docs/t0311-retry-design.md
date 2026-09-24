# T0311: jobs retry contract - design notes (docs-only; the contract yaml is normative)

## Scope
One retry decision for one failed attempt of a queue job, over an
explicit logical clock (`now` in milliseconds, supplied by the caller).
The retry contract owns retry eligibility, the backoff delay and the
decision record. Queue state transitions and leases stay with the queue
contract (data/contracts/queue.yaml). Idempotency keys and cancellation
are out of scope.

## Model
- Request (closed, exact keys): `decide {op, job_id, attempts, failure,
  now, policy}`. `policy` is exactly `{base_delay_ms, multiplier,
  max_delay_ms}`.
- Bounds: attempts 1..max_attempts (the attempts already used,
  including the failed one); now 0..2^53-1; base_delay_ms 1..3600000;
  multiplier 1..10; max_delay_ms base_delay_ms..86400000.
- max_attempts (5) and the job_id grammar come from the queue contract.
  The lint fails if either drifts from the queue contract.
- Record: exactly `{op, job_id, decision, attempts, delay_ms, retry_at,
  decision_id}`. decision is retry or dead. decision_id is `rd1:` +
  hex sha256 of the exact preimage `b"rd1\x00"` + canonical JSON
  (sort_keys, separators `,` `:`, ensure_ascii) of `{"request": ...,
  "record": ...}`, where record omits decision_id. The `rd1` + NUL
  domain prefix is part of the contract (fail-closed reading: ids from
  another digest scheme can never collide with retry ids). A golden
  vector pins it.

## Decision
- permanent failure: dead at any attempt count.
- attempts == max_attempts: dead. This matches the queue's dead-letter
  bound.
- otherwise: retry after `min(max_delay_ms, base_delay_ms *
  multiplier ** (attempts - 1))` ms, at `now + delay`. The arithmetic
  is exact integer arithmetic.
- Dead decisions carry null delay_ms and null retry_at.

## Fail-closed readings (the spec is silent)
1. timeout is retryable exactly like transient. It has no separate
   budget.
2. A retry_at beyond 2^53-1 fails as clock_overflow. It is never
   clamped and never turned into dead.
3. The caller never supplies max_attempts. It is the queue contract's.
4. No jitter. Decisions are deterministic and replayable.
5. Validation order is request shape -> policy -> clock. A malformed
   request wins over a bad policy, and a bad policy wins over a clock
   overflow.
6. Every key must be an exact str before any key-set comparison, on
   both the request and the policy.

## Not covered (flagged)
Retry of a cancelled job, and retry deduplication by idempotency key,
need the T0293/T0302 semantics. Those tasks are parked for the owner.

## Failures
malformed_retry_request -> malformed_request; invalid_retry_policy ->
invalid_policy; clock_overflow -> clock_overflow. `internal` is the only
retryable code.
