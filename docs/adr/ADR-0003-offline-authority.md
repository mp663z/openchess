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

### Option A: Desktop core is the sole authority; the surface holds an explicit offline state machine

- Offline desktop: complete - the full local loop (import, index,
  diagnosis, plan, drills, training, review) runs with no network; the
  server is only ever a relay.
- Offline web state: explicit - the surface is exactly one of ONLINE
  (live against sync), OFFLINE-CACHED (decrypted local cache; review
  queue, training and plans readable; writes disabled and visibly so),
  or OFFLINE-QUEUED (approvals recorded locally as intent entries,
  visibly pending, applied on reconnect). No fourth state exists and no
  state is implicit.
- Reconnect conflicts: explicit - both sides append to the same
  content-addressed write log; a reconnect merges histories by log
  order, and any two writes touching the same object revision surface
  as a named conflict in the review queue (with both diffs) instead of
  a silent resolution. The user resolves; the product never picks.
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

The deciding axes are the zero-knowledge server (it cannot arbitrate),
the no-silent-writes invariant (merges must be visible), and the
owner's local-runnable ruling (the core loop must never need the
network). All three favor Option A; B arbitrates blindly and C merges
silently.

**Proposed: Option A - the desktop core is the sole authority for the document of record. The web/mobile surface carries an explicit
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
