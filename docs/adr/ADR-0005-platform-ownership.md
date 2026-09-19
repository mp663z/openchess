---
adr: ADR-0005
status: proposed
ownership:
  pgn: desktop
  index: desktop
  stockfish: desktop
  models: desktop
  delta: desktop
  queue: web
  diff: web
  approval: web
  quiet-week: web
  drills: web
  transfer: web
invariants:
  - every-capability-has-exactly-one-owner
  - desktop-owns-every-heavy-compute-capability
  - web-owns-every-habit-loop-capability
  - no-capability-is-shared
---

# ADR-0005: Platform ownership (T2793)

Status: proposed
Date: 2026-09-20

## Context

ADR-0004 fixes the asymmetric split: a local-first desktop core as
the authoritative compute and data engine, a thin responsive web/PWA
habit surface, and a zero-knowledge ciphertext relay between them.
That decision leaves one question open: which concrete capabilities
live on which side. An ownership matrix answers it once, in
structured form, so every later task in the chain can check a
capability's home instead of re-litigating it. The split follows the
deciding axes from ADR-0004: heavy compute and private data stay on
hardware the user owns (zero per-user cost, zero-knowledge privacy);
the daily habit loop lives on the surface the user actually carries.

## Decision

The ownership matrix in this document's YAML front matter is
normative; this prose mirrors it.

### Desktop owns (heavy compute, authoritative data)

- **pgn** - PGN import, parse and storage of the user's own files.
- **index** - the local game/position index.
- **stockfish** - engine analysis.
- **models** - local model weights and inference.
- **delta** - the delta engine computing what changed.

### Web owns (review and training habit loop)

- **queue** - the review queue.
- **diff** - the review diff presentation.
- **approval** - the approval gate.
- **quiet-week** - the quiet-week screen.
- **drills** - training drills.
- **transfer** - transfer between surfaces over the encrypted relay.

## Alternatives considered

- **Shared ownership of analysis results**: rejected - two writers
  for one capability recreates the conflict and divergence costs the
  asymmetric split exists to avoid; transfer is the sync story, not
  shared ownership.
- **Web-hosted drills compute**: rejected - drills are a habit-loop
  surface, but their compute rides on desktop-produced artifacts; the
  web side presents and schedules, never computes engine output.

## Consequences

- Every capability has exactly one owner; no capability is shared.
- Desktop owns every heavy-compute capability; web carries none.
- Web owns every habit-loop capability; the desktop surfaces none of
  the daily loop.
- New capabilities must join this matrix in a future ADR revision
  before implementation tasks may claim them.
