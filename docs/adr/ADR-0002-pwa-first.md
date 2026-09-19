# ADR-0002: PWA first (T2794)

Status: proposed
Date: 2026-09-20

## Context

The v5 architecture (product report v5, section 14) splits the product
asymmetrically: a local-first desktop core carries all heavy compute,
and a thin web/mobile habit surface owns the loop - the review queue,
the approval gate, the quiet-week screen and training - joined to the
core by end-to-end encrypted sync. The report defers a native mobile
app explicitly: the web surface covers the behaviour at a fraction of
the cost. Distribution adds a second constraint (v5 section 11): the
app-store/AGPL terms conflict means direct signed downloads ship first
and there is no store discovery to lose by staying off the stores. The
standing product directives require the UI to stay neutral and
swappable, so the choice of shell must not leak into the surface's
code or into the core API.

## Options compared on the required axes

### Option A: Responsive PWA as the only mobile baseline

- Cost: one codebase serves desktop-browser, tablet and phone form
  factors; a small team can hold it.
- Offline: service-worker cache holds the decrypted local cache and the
  offline state machine (ADR-0003); review and training read fine
  offline.
- Distribution: no store review, no store terms conflict with AGPL,
  installable via Add-to-Home-Screen; matches the direct-download
  distribution decision.
- Push/background: web push covers the review-queue nudge on Android
  and desktop; iOS web push exists but is less reliable - accepted as
  the known gap, the loop never depends on push.
- Evidence gate: native reopens only under the operational rule in
  the Native-reopen gate section below, never on anecdote.

### Option B: Native iOS + Android from day one

- Cost: two more codebases (or a cross-platform framework), store
  review cycles, signing and release trains the team cannot staff
  before launch.
- Distribution: store terms vs AGPL remains unresolved; store discovery
  is already decided away, so the stores' main benefit does not apply.
- Offline: strongest, but the habit surface's offline needs are
  reads plus queued approvals - well inside PWA capability.

### Option C: WebView wrapper (Capacitor / React Native shell)

- Middle path: one web codebase in a native wrapper for store
  presence.
- Cost: carries store review and wrapper maintenance for a presence
  the distribution decision says the product does not use; adds a
  second shell to keep neutral before evidence demands it.
- Offline: inherits the web offline story with extra wrapper failure
  modes (service worker inside a shell), buying nothing the PWA
  lacks for this surface.
- Distribution: store presence the go-to-market plan explicitly
  forgoes, at the price of store review and the AGPL terms
  question.

## Decision drivers and proposed choice

The deciding axes are team cost, the AGPL/store terms conflict, and the
absence of store discovery in the go-to-market plan. All three favor
Option A; B and C buy capabilities the habit surface does not need yet.

**Proposed: Option A - the responsive PWA is the mobile baseline and the only habit-surface shell.** Native mobile is deferred until later evidence
under the operational gate below justifies it; the UI-neutrality
directive keeps that reopening cheap.

## Native-reopen gate

Native-shell evaluation reopens only when ALL of the following hold,
with the cohort query and result recorded in the decision evidence:

- (a) Measurement definitions: install completion is the share of
  onboarded beta users with the PWA installed, confirmed by the
  install event in client telemetry; notification delivery is the
  share of opted-in push notifications confirmed delivered by the client receipt; retention is week-8 active usage, defined as at
  least one completed review session in the eighth week after
  onboarding.
- (b) Cohort and window: the full beta cohort, minimum N = 30 users,
  minimum observation window W = 8 weeks per user.
- (c) Baseline: the unaffected sub-cohort - users with install
  completed and notification delivery confirmed - measured over the
  same window.
- (d) Reopen condition: PWA install-completion below 70%, OR opted-in
  notification delivery below 80%, AND the affected cohort's week-8
  retention at least 15 percentage points below the unaffected
  cohort's week-8 retention.

Anecdotes, individual complaints and unmeasured impressions never
satisfy this gate; only the recorded query result against these
thresholds does.

## Consequences

- The habit surface ships as an installable responsive PWA with a
  service worker; no native store binaries are built or promised.
- Web push is best-effort; the weekly loop is designed so a missed
  push costs nothing (the queue waits).
- The core API and the surface code make no PWA-specific assumptions
  beyond the documented shell boundary, so a later native shell is an
  addition, not a rewrite.
- The native-reopen gate above is part of this decision: the deferred
  native question stays a decision with a falsifiable, recorded
  trigger, not a drift.
