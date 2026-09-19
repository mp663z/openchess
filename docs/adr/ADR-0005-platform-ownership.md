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
    consumes: []
    crosswalk: [import]
  index:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    consumes: []
    crosswalk: [index]
  stockfish:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    consumes: []
    crosswalk: [stockfish]
  models:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    consumes: []
    crosswalk: [model-inference]
  delta:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    consumes: []
    crosswalk: [delta]
  export:
    compute_owner: desktop
    presentation_owner: desktop
    authoritative_state_owner: desktop
    consumes: []
    crosswalk: [export]
  queue:
    compute_owner: web
    presentation_owner: web
    authoritative_state_owner: desktop
    consumes: [delta, index]
    crosswalk: [queue]
  diff:
    compute_owner: web
    presentation_owner: web
    authoritative_state_owner: desktop
    consumes: [delta]
    crosswalk: [queue]
    sub_capability_of: queue
  approval:
    compute_owner: web
    presentation_owner: web
    authoritative_state_owner: desktop
    consumes: [delta]
    crosswalk: [approval]
  quiet-week:
    compute_owner: web
    presentation_owner: web
    authoritative_state_owner: desktop
    consumes: [model-inference, delta]
    crosswalk: [training]
    sub_capability_of: training
  drills:
    compute_owner: web
    presentation_owner: web
    authoritative_state_owner: desktop
    consumes: [model-inference]
    crosswalk: [training]
  transfer:
    compute_owner: split
    presentation_owner: web
    authoritative_state_owner: desktop
    consumes: []
    crosswalk: [sync-encrypt, sync-decrypt]
    phases:
      sync-encrypt: {actor: desktop, sends: ciphertext}
      relay: {actor: server, stores: ciphertext-only, decrypts: never}
      sync-decrypt: {actor: web, decrypts: local-only}
invariants:
  - every-capability-has-exactly-one-presentation-owner
  - compute-and-presentation-match-adr0004-operation-owner
  - authoritative-state-is-desktop-for-every-capability
  - server-owns-no-capability
  - transfer-splits-encrypt-desktop-decrypt-web
  - hosted-byom-is-invocation-exception-never-ownership
---

# ADR-0005: Platform ownership (T2793)

Status: proposed
Date: 2026-09-20

## Context

ADR-0004 fixes the asymmetric split and its operation/owner matrix,
where operation ownership means implementation-and-execution
ownership. This ADR refines it: it assigns every product capability a
concrete home per ownership dimension and pins a normative crosswalk
from each capability to the ADR-0004 operations it realizes, so no
second vocabulary drifts from the first. Scope: this ADR decides
presentation-and-implementation ownership only; ADR-0004 retains
architecture authority and data-flow ownership. The split follows
ADR-0004's deciding axes: heavy compute and private data stay on
hardware the user owns; the daily habit loop lives on the surface
the user actually carries.

## Decision

The capability matrix in this document's YAML front matter is
normative; this prose mirrors it. The ownership table below is
generated from that matrix; ownership statements live only there and
in the front matter.

| capability | compute | presentation | authoritative_state | consumes | realizes |
| --- | --- | --- | --- | --- | --- |
| pgn | desktop | desktop | desktop | - | import |
| index | desktop | desktop | desktop | - | index |
| stockfish | desktop | desktop | desktop | - | stockfish |
| models | desktop | desktop | desktop | - | model-inference |
| delta | desktop | desktop | desktop | - | delta |
| export | desktop | desktop | desktop | - | export |
| queue | web | web | desktop | delta, index | queue |
| diff | web | web | desktop | delta | queue (sub-capability of queue) |
| approval | web | web | desktop | delta | approval |
| quiet-week | web | web | desktop | model-inference, delta | training (sub-capability of training) |
| drills | web | web | desktop | model-inference | training |
| transfer | split | web | desktop | - | sync-encrypt, sync-decrypt; phases: sync-encrypt desktop sends ciphertext, relay server stores ciphertext-only decrypts never, sync-decrypt web decrypts local-only |

### Desktop capabilities

- **pgn** - PGN import, parse and storage (realizes ADR-0004 import).
- **index** - the local game/position index (realizes index).
- **stockfish** - engine analysis (realizes stockfish).
- **models** - local model weights and inference (realizes
  model-inference). Hosted BYOM is an optional invocation exception
  under ADR-0004's declared policy - never capability ownership and
  never a default.
- **delta** - the delta engine (realizes delta).
- **export** - Anki/Chessable export generation (realizes export).

### Web capabilities

- **queue** - the review queue (realizes queue; runs offline-capable
  in the PWA over artifacts produced by delta and index).
- **diff** - the review diff presentation (sub-capability of queue;
  consumes delta output).
- **approval** - the approval gate (realizes approval; consumes delta
  output).
- **quiet-week** - the quiet-week screen (sub-capability of training;
  consumes model-inference and delta output).
- **drills** - training drills (realizes training; consumes
  model-inference output).
- **transfer** - movement between surfaces over the encrypted relay,
  split by phase: sync-encrypt implemented on desktop, a relay that
  stores ciphertext only and never decrypts, sync-decrypt local-only
  on web (realizes sync-encrypt and sync-decrypt).

### Relay handling

The relay stores ciphertext blobs and account entitlements (ADR-0004
data flow) and never decrypts anything. No capability is implemented
or executed by the server.

## Alternatives considered

- **Shared compute for analysis results**: rejected - two writers for
  one capability recreates the conflict and divergence costs the
  asymmetric split exists to avoid; transfer is the sync story, not
  shared production.
- **Hosted drills execution**: rejected - drills execute in the PWA
  over artifacts produced by the desktop model-inference capability;
  nothing in the loop requires hosted compute.
- **A single web home for transfer**: rejected - it obscures the
  security boundary ADR-0004 draws between sync-encrypt (desktop) and
  sync-decrypt (web); the phase split is the normative shape.

## Consequences

- Every capability has exactly one presentation owner; nothing is
  co-presented.
- Compute and presentation ownership match ADR-0004 operation
  ownership exactly: desktop implements and runs import, index,
  stockfish, model-inference, delta and export; the web/PWA
  implements and runs queue, approval and training, offline-capable
  over its synced decrypted cache; transfer is split by phase.
- Web capabilities consume desktop-produced artifacts (named in
  consumes) and never re-own that production.
- Authoritative state is desktop for every capability.
- The relay stores only ciphertext and never decrypts; no capability
  is owned, implemented or executed by the server.
- Hosted BYOM is an optional invocation exception, never capability
  ownership and never a default.
- New capabilities must join this matrix with a crosswalk in a future
  ADR revision before implementation tasks may claim them.
