# ADR-0001: Stack choice (T0004)

Status: proposed for independent review
Date: 2026-09-19

## Context

V0.1 requires (v5 section 11): a cross-platform desktop core with a thin
web/mobile surface, offline-first, crash-safe, E2E encrypted sync, full
export, and p95 budgets on modest hardware. The product is AGPL-3.0;
dependencies must be permissive (MIT/Apache-2.0/BSD/ISC/MPL) per the
day-one license audit.

## Options compared on the required axes

### Option A: Rust core + Tauri shell, TypeScript thin surface

- Local-first: yes - single-process core owns the store (SQLite), no server
  needed offline.
- Crash safety: strong - Rust ownership rules + WAL SQLite; no GC pauses;
  structured error handling; core dump story is simple.
- UI: Tauri renders a system-webview UI (small bundles); accessibility via
  standard web tech; the same web UI serves as the thin web surface.
- a11y: webview a11y is mature (ARIA, OS bridges); needs discipline, not a
  framework fix.
- Packaging: Tauri produces signed installers per OS; auto-update available;
  bundle sizes ~10MB (vs ~150MB Electron).
- Speed: Rust core meets p95 budgets with headroom; webview startup is fast.
- Contributor cost: Rust learning curve is the real cost; mitigated by
  keeping the UI in TypeScript and the domain logic isolated behind a
  documented core API. Tooling (cargo) is excellent.

### Option B: Python core + Qt (PySide6) shell

- Local-first: yes, same SQLite story.
- Crash safety: weaker - interpreter crashes and C++ binding faults are
  harder to contain; packaging must ship a pinned interpreter.
- UI: Qt Widgets/QML, native look.
- a11y: Qt a11y bridges are good on desktop.
- Packaging: PyInstaller-style bundling is fragile across OS versions;
  code signing and update flows are community-maintained.
- Speed: adequate for UI, but engine-adjacent analysis and large-corpus
  scans (millions of positions) fight the GIL and interpreter overhead;
  p95 budgets on modest hardware are at risk.
- Contributor cost: lowest barrier; the chess OSS community skews Python.

## Decision drivers and proposed choice

The deciding axes are crash safety, packaging reliability, and speed under
corpus-scale workloads - all favor Option A. Contributor cost favors B but
is mitigated by the TypeScript surface and a documented core API.

**Proposed: Option A - Rust core, Tauri shell, TypeScript thin surface.**

The already-built Python corpus/backtest pipeline (ingest/, backtest/) stays
Python: it is offline tooling, not the product runtime; its outputs
(normalized extracts, replay results) feed the product through documented
files. That boundary keeps the existing verified work intact.

## Consequences

- New repo areas: core/ (Rust), app/ (Tauri+TS). Python tooling remains for
  data/backtest only, which the license audit already covers.
- The T0005 license boundary and T0006 no-silent-writes invariant apply to
  the new core from day one.
