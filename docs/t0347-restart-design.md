# T0347: jobs restart contract - design notes (docs-only; the contract yaml is normative)

## Scope
When a worker restarts, it releases its live leases in one queue state, over an
explicit logical clock (`now` in milliseconds, supplied by the caller).
The transition is pure and commits last, in place, like the queue's own
transitions. Claim ordering, dead-marking, ack, retry backoff,
idempotency, cancellation and multi-queue routing are out of scope.

## Model
- Request (closed, exact keys): `restart {op, worker, now}`; worker uses
  the queue's worker grammar; now 0..2^53-1.
- State: exactly one queue state (data/contracts/queue.yaml `state`),
  re-validated in full: shape, grammar, bounds, strictly increasing seq
  below next_seq, at most max_jobs (10000), lease fields only on leased
  jobs, dead only at max_attempts (5), done only after an attempt,
  payload admission with alias detection shared across all payloads.
- Release: status leased, lease_owner == worker, now < lease_expires_at
  -> status ready, lease_owner null, lease_expires_at null.
- Receipt: exactly `{op, worker, restarted_at, released, prior_state_id,
  state_id, restart_id}`.
- state_id is the queue's: `qs1:` + hex sha256 of UTF-8 canonical JSON
  (sort_keys, compact separators, non-ASCII kept) of the state.
  restart_id is `rst1:` + hex sha256 of `b"rst1\x00"` + canonical JSON
  (ensure_ascii) of the receipt without restart_id. A golden restart
  pins the full receipt and state. A cross-check against the queue
  battery's state_id_for and QueueEngine binds the sibling.

## Fail-closed readings (the spec is silent)
1. Attempts are kept (nack semantics). No refund, no charge.
2. An expired lease is left alone. The queue claim already reclaims it.
3. A released job at max_attempts goes back to ready. The next queue
   claim marks it dead.
4. A corrupt state is refused, never repaired.
5. Validation order is request -> state.
6. Every key must be an exact str before any key-set comparison.

## Flagged
Stale-epoch fencing and cancelling a restarted worker's jobs need
T0293/T0302 (parked).
