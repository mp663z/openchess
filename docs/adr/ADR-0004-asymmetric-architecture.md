---
adr: ADR-0004
status: proposed
roles:
  desktop_core: authoritative-compute-and-data-engine
  web_pwa: review-and-training-habit-surface
  server: zero-knowledge-ciphertext-relay
  export: interoperability-feature
invariants:
  - desktop-runs-full-loop-offline
  - web-carries-no-heavy-compute
  - server-stores-no-plaintext
  - export-is-a-feature-not-the-phone-story
---

# ADR-0004: Asymmetric architecture (T2792)

Status: proposed
Date: 2026-09-20

## Context

The v5 architecture (product report v5, section 14) reconciles the
privacy posture, the per-user cost model and the reference-machine
budgets into one shape: an asymmetric split. The desktop core does
every byte of heavy compute - local models, Stockfish, the indexer,
the delta engine - private by default and free for the business to
serve; a thin responsive web/PWA habit surface owns the loop (review
queue, approval gate, quiet-week screen, training); end-to-end
encrypted sync joins them with the server storing opaque blobs only.
Training is owned in-house; Anki/Chessable export remains as
interoperability, a feature rather than the phone story. The owner's
standing rulings reinforce this: local-runnable product, auth-free
imports, UI neutral and swappable.

## Options compared on the required axes

### Option A: Asymmetric split - local-first desktop core, thin web/PWA habit surface

- Authority: the local-first desktop is the authoritative compute and
  data engine; the document of record lives on the user's machine.
- Cost: per-user compute cost to the business is zero; engines and
  models run on hardware the user owns.
- Privacy: the server is a zero-knowledge ciphertext relay; content
  never leaves devices in plaintext.
- Habit: the review/training loop lives on the thin responsive
  web/PWA surface the user actually carries (ADR-0002).
- Interoperability: export to Anki/Chessable ships as a feature, not
  as the training story.

### Option B: Symmetric full stack on every platform

- Authority: every platform holds the full engine; no single
  authoritative store - conflict and divergence costs multiply.
- Cost: heavy compute on phones drains battery and blows the memory
  budgets the reference machine pins; server-side compute for mobile
  reintroduces per-user cost.
- Privacy: more plaintext copies on more devices.
- Habit: good, but bought at unsustainable cost.
- Interoperability: unchanged.

### Option C: Server-centric (web app with hosted engine)

- Authority: the server is authoritative - the user's preparation
  lives on someone else's machine.
- Cost: per-user engine/model compute is exactly what the EUR 8
  price cannot carry at the ~90% margin guardrail.
- Privacy: contradicts the zero-knowledge commitment outright.
- Habit: good.
- Interoperability: unchanged.

## Decision drivers and proposed choice

The deciding axes are per-user cost at the committed price, the
zero-knowledge privacy commitment, and a single authoritative store.
All three favor Option A; B multiplies authority and cost, C breaks
privacy and cost together.

**Proposed: Option A - asymmetric split: the local-first desktop core is the authoritative compute and data engine, the thin responsive web/PWA owns the review and training habit, export is interoperability.** The roles and invariants in this document's YAML front matter are normative; this prose mirrors them.

## Consequences

- The desktop runs the full loop offline; no core-loop operation may
  require the network (owner's local-runnable ruling).
- The web/PWA surface carries no heavy compute: queue, diffs,
  approval, quiet-week, drills and transfer only (ownership matrix in
  ADR-0005).
- The server stores and relays ciphertext only; the control plane is
  content-blind by construction.
- Anki/Chessable export is an interoperability feature with its own
  roadmap tasks; it is never the phone story.
- Offline authority and reconnect semantics follow ADR-0003.
