# T0284: jobs queue contract - design notes (docs-only; the contract yaml is normative)

## Scope
A single durable priority job queue with leases, driven by an explicit
logical clock (`now` in milliseconds, supplied by the caller). The
queue OWNS its state transitions, lease semantics and receipts. Job
execution, cron scheduling, retry backoff and routing across queues are
not in scope.

## Model
- State: exactly `{jobs, next_seq}`. Each job is exactly
  `{job_id, seq, priority, payload, status, attempts, lease_owner,
  lease_expires_at}`, with status in ready/leased/done/dead. There are
  at most 10000 jobs and at most 5 attempts per job. next_seq is an
  int from 0 through 2^53-1.
- Requests (closed, exact keys):
  - `enqueue {op, dedupe_key, priority, payload}`
  - `claim {op, worker, now, lease_ms}`
  - `ack {op, job_id, worker, now}`
  - `nack {op, job_id, worker, now}`
- Receipt: exactly `{op, job_id, status, attempts, lease_expires_at,
  state_id}`. state_id is `qs1:` + sha256 over the canonical JSON of
  the committed state. It is DERIVED, never caller supplied.
- job_id is `job1:` + sha256 over the domain-separated dedupe key.
  Callers never supply a job_id on enqueue.

## Transitions
Every transition first validates the full state (shape, types,
grammars, invariants). It then works on a copy and commits LAST.
- enqueue: if the dedupe key already has a job with the same priority
  and the same canonical payload, that job is returned unchanged
  (idempotent). If the priority or canonical payload differs, the
  request fails with dedupe_conflict and nothing is overwritten.
  Otherwise a new ready job gets `next_seq`. That fails with
  capacity_exceeded if the queue holds max jobs or next_seq has
  reached 2^53-1 (seq space exhausted).
- claim: candidates are ready jobs plus leased jobs whose expiry is at
  or before `now`. They are ordered by (priority, seq), where a lower
  priority number is more urgent. A candidate that has already used
  all its attempts becomes dead and is skipped. The first remaining
  candidate is leased to the worker until `now + lease_ms`, and its
  attempts go up by one. If nothing can be claimed, the receipt has a
  null job_id. This still counts as success.
- ack: only the lease owner, with `now` strictly before the expiry,
  can mark the job done (terminal).
- nack: same owner and expiry check. The job goes back to ready and
  keeps its attempt count.

## Failures (closed)
- malformed_queue_request -> malformed_request: bad request shape,
  type, grammar or bound, or an inadmissible payload.
- unknown_job: ack/nack of a job_id that is not in the queue.
- lease_conflict: ack/nack by a non-owner, on a job that is not
  leased, or on an expired lease.
- dedupe_conflict: enqueue of a seen dedupe key whose stored priority
  or canonical payload differs.
- capacity_exceeded: enqueue of a new job into a queue holding max
  jobs or whose next_seq is exhausted.
- corrupt_queue: bad state shape, type, grammar or invariant.

Only `internal` is retryable.

## Properties
Total, atomic (a rejected transition leaves state and request
bit-identical, including references), closed (every successful
transition commits a state that passes full state validation, so the
queue can never brick itself), deterministic, idempotent enqueue of an
identical job, at-least-once delivery, and rollback (nack returns a
leased job to ready).

## Decisions and known consequences
- Dedupe conflicts fail closed. This matches the linked idempotency
  contract (a seen key with a different fingerprint fails closed and
  never overwrites). Silently returning the old job would hide a
  caller bug. Equality is by priority and canonical JSON, so key order
  does not matter, but 1 and 1.0 are different payloads.
- Seq space. The last usable seq is 2^53-2. Once next_seq reaches
  2^53-1, new jobs get capacity_exceeded. Existing jobs keep working,
  and identical re-enqueues still succeed. The same closure rule is
  why a claim whose `now + lease_ms` would pass 2^53-1 is rejected.
- At-least-once, not exactly-once. An expired lease can be reclaimed
  by another worker while the first worker is still running. The first
  worker's late ack then fails with lease_conflict. Consumers must
  make job side effects idempotent (see data/contracts/idempotency.yaml).
- Expiry boundary. A lease whose expiry equals `now` is expired for
  both claim and ack. This gives exactly one owner at every instant.
- Nack keeps attempts. A job that is nacked over and over still
  reaches dead after 5 claims, so a poison job cannot loop forever.
- Dead-lettering happens during claim. A claim that turns jobs dead
  and then finds nothing else still commits those dead transitions
  and returns a null job_id.
- Caller clock. `now` is not checked for monotonicity across calls.
  A caller that moves its clock backwards can make leases live longer.
  The queue does not own the clock, so this is left to the caller.
- Total admission. Payloads are admitted by an iterative walk with a
  global seen set. Cycles, aliases, nesting deeper than 64 and ints
  with more than 4000 decimal digits are malformed_queue_request, so
  hostile payloads never escape as a raw RecursionError or ValueError.
  Request and state keys are checked as exact str before any
  membership, lookup or set comparison. So a key with a colliding hash
  and a raising __eq__ fails closed.
