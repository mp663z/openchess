---
adr: ADR-0004
status: proposed
operations:
  import: desktop
  index: desktop
  stockfish: desktop
  model-inference: desktop
  delta: desktop
  queue: web
  approval: web
  training: web
  sync-encrypt: desktop
  sync-decrypt: web
  export: desktop
offline_required:
  - import
  - index
  - stockfish
  - model-inference
  - delta
  - queue
  - approval
  - training
  - export
data_flow:
  desktop:
    content: store-process
    keys: store
    ciphertext: store
    account-metadata: store
  web:
    content: process-local-only
    keys: store-local-only
    ciphertext: receive-decrypt
    account-metadata: store
  server:
    content: never
    keys: never
    ciphertext: store-relay-only
    account-metadata: store-entitlements
phone_training:
  surface: web-pwa
  export_required: false
web_offline_capable:
  - queue
  - approval
  - training
external_providers:
  hosted-byom:
    allowed: true
    mode: hosted
    opt_in: true
    default: off
    invocation_additional_properties: false
    payload_schema:
      fen: {type: string, max_length: 128, mandatory: true}
      moves: {type: string-list, max_items: 512, item_max_length: 16,
        mandatory: false}
      task: {type: enum, values: [delta-explanation, error-diagnosis,
        weekly-plan, review-conversation], mandatory: true}
      max-tokens: {type: integer, min: 1, max: 4096, mandatory: false}
    never_receives: [account-keys, full-corpus, sync-keys]
    relay_plaintext: false
    local_completeness: true
    critical_path: false
    suspends_reference_claims: false
  local-large-llm:
    allowed: true
    mode: local
    opt_in: true
    default: off
    payload: none-local-only
    suspends_reference_claims: true
invariants:
  - heavy-compute-only-on-desktop
  - full-loop-offline-on-desktop
  - server-never-content-or-keys
  - export-never-required-for-phone-training
  - hosted-byom-opt-in-only
---

# ADR-0004: Asymmetric architecture (T2792)

Status: proposed
Date: 2026-09-20

## Context

The v5 architecture (product report v5, section 14) reconciles the
privacy posture, the per-user cost model and the reference-machine
budgets into one shape: an asymmetric split. The desktop core carries
the small local models, Stockfish and the indexer; the web/mobile
surface carries no heavy compute; the server carries only encrypted
blobs plus account entitlements. Per-user compute cost to the
business is zero because engines and models run on hardware the user
already owns. The owner's standing rulings reinforce this:
local-runnable product, auth-free imports, UI neutral and swappable,
BYOK provider-neutral.

v5 section 14 also keeps an OPTIONAL heavy generative tier, resolved
from v4: hosted BYOM (the user's own provider key, off the critical
path) or an explicit local large-model opt-in whose activation
suspends the reference-machine p95 claims. This ADR declares both in
structured data instead of smoothing them over: neither is a default,
neither is required, and the relay/server never receives plaintext
content or keys under either.

## Options compared on the required axes

### Option A: Asymmetric split - local-first desktop core, thin web/PWA habit surface

- Authority: the local-first desktop is the authoritative compute and
  data engine; the document of record lives on the user's machine.
- Cost: per-user compute cost to the business is zero; engines and
  models run on hardware the user owns.
- Privacy: the server is a zero-knowledge ciphertext relay holding
  only blobs and account entitlements; content never leaves devices
  in plaintext except a declared opt-in provider request (below).
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

**Proposed: Option A - asymmetric split: the local-first desktop core is the authoritative compute and data engine, the thin responsive web/PWA owns the review and training habit, export is interoperability.** The operations matrix, offline_required set, data_flow matrix, phone_training declaration and external_providers exceptions in this document's YAML front matter are normative; this prose mirrors them.

## Consequences

- Heavy compute runs only on desktop: import, index, stockfish,
  model-inference and delta are desktop-owned; web and server run
  none of them.
- The full loop runs offline: the desktop-owned operations import,
  index, stockfish, model-inference, delta and export run on desktop
  with no network, and the web-owned habit operations queue, approval
  and training are offline-capable in the PWA over its synced decrypted
  local cache, synchronized with desktop authority; only sync
  transport needs one.
- The server never receives or stores plaintext content or keys; it
  stores ciphertext blobs and account entitlements only.
- Phone training goes through the web/PWA surface over encrypted
  sync; export is never required for it.
- Hosted BYOM inference is opt-in, default off, never on the
  critical path, with detection, scoring, evidence, diagnosis,
  planning and training fully functional without it. A hosted BYOM
  request payload carries exactly the declared schema fields -
  fen, moves, task, max-tokens - always including fen and task, with
  bounded values, no unknown keys anywhere, and nothing else;
  account keys, the full corpus and sync keys never leave. The local
  large-LLM opt-in is a local-mode provider: it sends no outbound
  payload at all, and its activation suspends the reference-machine
  p95 claims, which the settings screen says.
