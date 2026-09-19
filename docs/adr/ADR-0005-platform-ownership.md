---
adr: ADR-0005
status: proposed
scope: presentation-and-implementation-ownership
authority_note: ADR-0004 retains architecture authority and data-flow ownership
dimensions: [compute_owner, presentation_owner, authoritative_state_owner]
capabilities:
  pgn:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    crosswalk: [import]
  index:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    crosswalk: [index]
  stockfish:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    crosswalk: [stockfish]
  models:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    crosswalk: [model-inference]
  delta:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    crosswalk: [delta]
  export:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    crosswalk: [export]
  queue:
    compute_owner: desktop
    presentation_owner: web
    authoritative_state_owner: desktop
    crosswalk: [queue]
  diff:
    compute_owner: desktop
    presentation_owner: web
    authoritative_state_owner: desktop
    crosswalk: [queue]
    sub_capability_of: queue
  approval:
    compute_owner: desktop
    presentation_owner: web
    authoritative_state_owner: desktop
    crosswalk: [approval]
  quiet-week:
    compute_owner: desktop
    presentation_owner: web
    authoritative_state_owner: desktop
    crosswalk: [training]
    sub_capability_of: training
  drills:
    compute_owner: desktop
    presentation_owner: web
    authoritative_state_owner: desktop
    crosswalk: [training]
  transfer:
    compute_owner: split
    presentation_owner: web
    authoritative_state_owner: desktop
    crosswalk: [sync-encrypt, sync-decrypt]
    phases:
      sync-encrypt: {actor: desktop, sends: ciphertext}
      relay: {actor: server, stores: ciphertext-only, decrypts: never}
      sync-decrypt: {actor: web, decrypts: local-only}
invariants:
  - every-capability-has-exactly-one-presentation-owner
  - compute-owner-is-desktop-for-every-non-transfer-capability
  - authoritative-state-is-desktop-for-every-capability
  - server-owns-no-capability
  - transfer-splits-encrypt-desktop-decrypt-web
  - hosted-byom-is-invocation-exception-never-ownership
---

# ADR-0005: Platform ownership (T2793)

Status: proposed
Date: 2026-09-20

## Context

ADR-0004 fixes the asymmetric split and its operation/owner matrix.
This ADR refines it: it assigns every product capability a concrete
home per ownership dimension and pins a normative crosswalk from each
capability to the ADR-0004 operations it realizes, so no second
vocabulary drifts from the first. Scope: this ADR decides
presentation-and-implementation ownership only; ADR-0004 retains
architecture authority and data-flow ownership. The split follows
ADR-0004's deciding axes: heavy compute and private data stay on
hardware the user owns; the daily habit loop lives on the surface
the user actually carries.

## Decision

The capability matrix in this document's YAML front matter is
normative; this prose mirrors it.

### Desktop owns (heavy compute, authoritative data)

- **pgn** - PGN import, parse and storage (realizes ADR-0004 import).
- **index** - the local game/position index (realizes index).
- **stockfish** - engine analysis (realizes stockfish).
- **models** - local model weights and inference (realizes
  model-inference). Hosted BYOM is an optional invocation exception
  under ADR-0004's declared policy - never capability ownership and
  never a default.
- **delta** - the delta engine (realizes delta).
- **export** - Anki/Chessable export generation (realizes export).

### Web owns (review and training habit loop)

- **queue** - the review queue (realizes queue; compute rides on
  desktop-produced artifacts).
- **diff** - the review diff presentation (sub-capability of queue).
- **approval** - the approval gate (realizes approval).
- **quiet-week** - the quiet-week screen (sub-capability of training).
- **drills** - training drills (realizes training).
- **transfer** - movement between surfaces over the encrypted relay,
  split by phase: sync-encrypt on desktop, a server relay that stores
  ciphertext only and never decrypts, sync-decrypt local-only on web
  (realizes sync-encrypt and sync-decrypt).

### The server owns no capability

The server stores ciphertext blobs and account entitlements
(ADR-0004 data flow). No capability - including transfer - is owned
by the server, and it never decrypts anything.

## Alternatives considered

- **Shared ownership of analysis results**: rejected - two writers
  for one capability recreates the conflict and divergence costs the
  asymmetric split exists to avoid; transfer is the sync story, not
  shared ownership.
- **Web-hosted drills compute**: rejected - drills are a habit-loop
  surface, but their compute rides on desktop-produced artifacts; the
  web side presents and schedules, never computes engine output.
- **A single web owner for transfer**: rejected - it obscures the
  security boundary ADR-0004 draws between sync-encrypt (desktop) and
  sync-decrypt (web); the phase split is the normative shape.

## Consequences

- Every capability has exactly one presentation owner; nothing is
  co-presented.
- The compute owner is desktop for every non-transfer capability;
  transfer's compute is split by phase (encrypt desktop, decrypt
  web-local), and the relay computes nothing.
- Authoritative state is desktop for every capability; the web
  surfaces work over synced, locally decrypted caches under desktop
  authority (ADR-0004 web_offline_capable).
- The server owns no capability and never holds plaintext or keys.
- Hosted BYOM is an optional invocation exception, never capability
  ownership and never a default.
- New capabilities must join this matrix with a crosswalk in a future
  ADR revision before implementation tasks may claim them.
