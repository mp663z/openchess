---
adr: ADR-0003
status: proposed
scope: offline-authority-and-reconnect-semantics
authority:
  document_of_record: desktop-core
  surface: explicit-offline-state-machine
options_compared: [A-desktop-authority, B-server-authority-lww, C-p2p-crdt]
decision: A-desktop-authority
axes: [offline-desktop, offline-web-state, reconnect-conflicts, failure-modes]
links:
  adr0004: docs/adr/ADR-0004-asymmetric-architecture.md
  adr0005: docs/adr/ADR-0005-platform-ownership.md
offline_states: [ONLINE, OFFLINE-CACHED, OFFLINE-QUEUED]
events: [connectivity-lost, connectivity-restored, sync-confirmed, write-attempted, approval-attempted, queue-persistence-unavailable]
state_semantics:
  ONLINE: {writes: enabled, source: live-sync}
  OFFLINE-CACHED: {writes: disabled, refusal: named-state, reads: cached}
  OFFLINE-QUEUED: {writes: intent-entries, visibility: pending-until-sync, reads: cached}
capabilities:
  ONLINE:
    readable: [queue, diff, approval, quiet-week, drills]
    writable: [approval, drills]
    queueable: []
  OFFLINE-CACHED:
    readable: [queue, diff, approval, quiet-week, drills]
    writable: []
    queueable: []
  OFFLINE-QUEUED:
    readable: [queue, diff, approval, quiet-week, drills]
    writable: []
    queueable: [approval]
transitions:
  ONLINE:
    connectivity-lost: {to: OFFLINE-CACHED, action: freeze-cache}
    connectivity-restored: {to: ONLINE, action: no-op}
    sync-confirmed: {to: ONLINE, action: no-op}
    write-attempted: {to: ONLINE, action: apply}
    approval-attempted: {to: ONLINE, action: apply}
    queue-persistence-unavailable: {to: ONLINE, action: no-op}
  OFFLINE-CACHED:
    connectivity-lost: {to: OFFLINE-CACHED, action: no-op}
    connectivity-restored: {to: ONLINE, action: resume-live-sync}
    sync-confirmed: {to: OFFLINE-CACHED, action: no-op}
    write-attempted: {to: OFFLINE-CACHED, action: refuse-named-state}
    approval-attempted: {guard: queue-persistence-available, then: {to: OFFLINE-QUEUED, action: record-intent}, else: {to: OFFLINE-CACHED, action: refuse-named-state}}
    queue-persistence-unavailable: {to: OFFLINE-CACHED, action: no-op}
  OFFLINE-QUEUED:
    connectivity-lost: {to: OFFLINE-QUEUED, action: no-op}
    connectivity-restored: {to: OFFLINE-QUEUED, action: begin-sync}
    sync-confirmed: {guard: all-intents-applied-and-conflicts-surfaced, then: {to: ONLINE, action: resume-live-sync}, else: {to: OFFLINE-QUEUED, action: remain-pending}}
    write-attempted: {to: OFFLINE-QUEUED, action: refuse-named-state}
    approval-attempted: {guard: queue-persistence-available, then: {to: OFFLINE-QUEUED, action: record-intent}, else: {to: OFFLINE-QUEUED, action: refuse-named-state}}
    queue-persistence-unavailable: {to: OFFLINE-QUEUED, action: refuse-new-intents}
write_log:
  type: append-only
  addressing: content-addressed
  entry:
    id: content-hash-of-canonical-entry-envelope
    envelope_fields: [domain-separator, contract-version, writer, writer_sequence, parents, object, object_revision, operation, payload]
    writer: writer-device-id
    writer_sequence: per-writer-strictly-monotonic-unique-integer
    duplicate_writer_sequence: reject
    parents: parent-entry-ids-list-updated-on-every-append
    merge_entry_parents: multiple-parents-join-histories
    object: object-identity
    object_revision: object-revision-written
    operation: the-operation
    payload: the-write
  merge:
    ordering: topological-by-parent-links-then-writer-id-writer-sequence-lexicographic
    ordering_never: [wall-clock, arrival-order]
    concurrency: neither-entry-is-an-ancestor-of-the-other-via-parent-links
    same_object: concurrent-entries-writing-the-same-object
    conflict_rule: named-review-queue-item-with-both-diffs-before-any-winner-materialized
    ambiguous_concurrent_writes: unresolved-until-logged-user-resolution
  dedup: replay-of-existing-entry-id-is-idempotent-no-op
  resolution: user-resolves
  resolution_entry: merge-resolution-entry-with-multiple-parents
  silent_resolution: forbidden
drivers: [zero-knowledge-server-cannot-arbitrate, no-silent-writes, local-runnable-auth-free]
invariants:
  - desktop-core-is-sole-authority
  - core-loop-never-requires-network
  - exactly-three-offline-states
  - writes-disabled-visibly-in-offline-cached
  - merge-outcomes-visible-logged-reversible
  - conflicts-user-resolved-never-silent
  - server-stores-ciphertext-only
  - merge-ordering-deterministic-never-wall-clock
---


# ADR-0003: Offline authority (T2795)

Status: proposed
Date: 2026-09-20

## Context

The v5 architecture (product report v5, sections 12 and 14) is an
asymmetric split: the desktop core does every byte of heavy compute and
holds the store; the web/mobile habit surface owns the review loop and
decrypts a local cache; the server relays opaque encrypted blobs and
never sees plaintext. Approvals can happen on the phone while the
desktop is offline, and the desktop keeps writing locally while the
phone is away - so the system must say, explicitly, who is authoritative
when a side has been offline, and what a reconnect does with two
histories. The owner's standing rulings reinforce the local-first side:
the product is local-runnable and imports are auth-free (no OAuth
dependency for import flows), so nothing in the core loop may require
reaching a server.

## Options compared on the required axes

### Option A: Desktop core sole authority, surface explicit offline state machine

- Offline desktop: complete - the full local loop (import, index,
  diagnosis, plan, drills, training, review) runs with no network; the
  server is only ever a relay.
- Offline web state: explicit - the surface is exactly one of ONLINE
  (live against sync), OFFLINE-CACHED (decrypted local cache; queue,
  diffs, approvals, training and drills readable; writes disabled and
  visibly so), or OFFLINE-QUEUED (approvals recorded locally as intent
  entries, visibly pending, applied on reconnect; the cache stays
  readable). No fourth state exists and no state is implicit. The
  transition table in the front matter is normative: keyed by explicit
  events, every state/event pair names a destination and action or an
  exact named refusal. An approval attempted in OFFLINE-CACHED moves
  the surface to OFFLINE-QUEUED when intent persistence is available,
  and is refused with the state named when it is not.
- Reconnect conflicts: explicit - both sides append to the same
  content-addressed write log. An entry's id is the content hash of
  its canonical complete envelope: domain separator, contract
  version, writer device id, per-writer strictly monotonic and
  unique sequence (a duplicate (writer, sequence) pair is rejected),
  parent entry ids, object identity and revision, operation and
  payload - so identical payloads from two writers, or repeated by
  one writer at different sequences, are distinct entries. Parent
  links name the writer's head at append time and move on every
  append, so a writer's second offline entry descends from its
  first; a merge-resolution entry lists multiple parents and joins
  histories. Replaying an existing entry id is an idempotent no-op.
  A reconnect merges histories in deterministic topological order
  over parent links, ties broken by (writer id, writer sequence) -
  never wall-clock, never arrival order. Two entries are concurrent
  when neither is an ancestor of the other over parent links;
  concurrent writes to the same object surface as a named conflict
  in the review queue (with both diffs) BEFORE any winner is
  materialized, and ambiguous concurrent writes stay unresolved
  until a logged user resolution entry. The user resolves; the
  product never picks. OFFLINE-QUEUED holds until every intent is
  applied and conflicts are surfaced; only then does sync-confirmed
  return the surface to ONLINE.
- Failure modes: a lost phone loses only its queued intents, which the
  surface shows as pending until sync confirms; a stale desktop never
  overwrites newer surface approvals because the log is append-only.

### Option B: Server as authority (last-writer-wins relay)

- Offline desktop: weakened - authority lives off-device, so offline
  writes are speculative by construction.
- Offline web state: simpler (the surface is just thin), but the
  server becomes a consistency dependency the zero-knowledge relay
  cannot honestly discharge: it cannot see content, so it cannot
  arbitrate content conflicts.
- Reconnect conflicts: silent last-writer-wins - data loss by design;
  unacceptable for preparation a user spent hours on.

### Option C: Peer-to-peer authority with CRDT merge

- Offline desktop: complete.
- Offline web state: implicit convergence - CRDTs remove visible
  conflicts by construction, which for this product means merges the
  user never sees. That violates the no-silent-writes invariant: every
  write and every merge outcome must be visible, logged and
  reversible.
- Reconnect conflicts: automatic but opaque; rehearsal-grade
  preparation needs explicit conflict surfacing, not silent lattice
  merges.

## Decision drivers and proposed choice

The YAML front matter of this document is normative; the prose
below is pinned explanatory text that the contract battery compares
byte-for-byte.

The deciding axes are the zero-knowledge server (it cannot arbitrate),
the no-silent-writes invariant (merges must be visible), and the
owner's local-runnable ruling (the core loop must never need the
network). All three favor Option A; B arbitrates blindly and C merges
silently.

**Proposed: Option A** - the desktop core is the sole authority for
the document of record. The web/mobile surface carries an explicit
three-state offline machine (ONLINE, OFFLINE-CACHED, OFFLINE-QUEUED);
reconnect merges an append-only write log and surfaces same-revision
collisions as named, user-resolved conflicts - never silent
resolution.**

## Consequences

- The desktop never blocks on connectivity for any local-loop
  operation; sync is always a background relay.
- The habit surface ships an explicit offline-state indicator; a write
  attempted in OFFLINE-CACHED is refused with the state named, and
  OFFLINE-QUEUED intents are listed as pending until confirmed.
- Conflict entries are first-class review-queue items carrying both
  diffs; resolving one is itself a logged write.
- The sync protocol, log format and conflict detection become contract
  work under the Architecture v5 milestone; this ADR fixes the
  authority model they implement.
- The server gains no new trust: it stores and relays ciphertext only,
  exactly as the v5 zero-knowledge design requires.
