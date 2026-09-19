"""T2795: ADR-0003 structural battery - offline authority. The ADR's
YAML front matter is normative: authority model, options compared,
decision, axes, the exact three offline states with their semantics,
write-log/reconnect semantics, drivers and invariants are pinned as
structured fields. The entire prose body is pinned explanatory text
compared byte-for-byte - any inserted or edited sentence fails on the
byte change, never on vocabulary matching. Mutations of authority,
states, log semantics, drivers, invariants, status or prose fail."""

from pathlib import Path

import yaml

ADR = (Path(__file__).resolve().parent.parent / "docs" / "adr"
       / "ADR-0003-offline-authority.md").read_text()

OPTIONS = ["A-desktop-authority", "B-server-authority-lww",
           "C-p2p-crdt"]
AXES = ["offline-desktop", "offline-web-state", "reconnect-conflicts",
        "failure-modes"]
STATES = ["ONLINE", "OFFLINE-CACHED", "OFFLINE-QUEUED"]
STATE_SEMANTICS = {
    "ONLINE": {"writes": "enabled", "source": "live-sync"},
    "OFFLINE-CACHED": {
        "writes": "disabled", "refusal": "named-state",
        "reads": ["review-queue", "training", "plans"]},
    "OFFLINE-QUEUED": {
        "writes": "intent-entries", "visibility": "pending-until-sync"},
}
WRITE_LOG = {
    "type": "append-only",
    "addressing": "content-addressed",
    "merge": "by-log-order",
    "same_revision_collision": "named-conflict-in-review-queue",
    "resolution": "user-resolves",
    "silent_resolution": "forbidden",
}
DRIVERS = ["zero-knowledge-server-cannot-arbitrate", "no-silent-writes",
           "local-runnable-auth-free"]
INVARIANTS = [
    "desktop-core-is-sole-authority",
    "core-loop-never-requires-network",
    "exactly-three-offline-states",
    "writes-disabled-visibly-in-offline-cached",
    "merge-outcomes-visible-logged-reversible",
    "conflicts-user-resolved-never-silent",
    "server-stores-ciphertext-only",
]

BODY = """
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
"""


def _parse(adr: str):
    assert adr.startswith("---\n"), "YAML front matter required"
    fm = yaml.safe_load(adr.split("---\n", 2)[1])
    body = adr.split("---\n", 2)[2]
    return fm, body


def _check(adr: str) -> None:
    fm, body = _parse(adr)
    assert fm["adr"] == "ADR-0003"
    assert fm["status"] == "proposed"
    assert fm["scope"] == "offline-authority-and-reconnect-semantics"
    assert fm["authority"] == {
        "document_of_record": "desktop-core",
        "surface": "explicit-offline-state-machine"}
    assert fm["options_compared"] == OPTIONS
    assert fm["decision"] == "A-desktop-authority"
    assert fm["axes"] == AXES
    assert fm["offline_states"] == STATES
    assert fm["state_semantics"] == STATE_SEMANTICS
    assert fm["write_log"] == WRITE_LOG
    assert fm["drivers"] == DRIVERS
    assert fm["invariants"] == INVARIANTS
    # Byte-for-byte: any prose insertion or edit fails here.
    assert body == BODY, "body differs from pinned approved prose"


def _bad(mutated: str) -> None:
    try:
        _check(mutated)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")


def test_real_adr_passes():
    _check(ADR)


def test_authority_mutations_fail():
    _bad(ADR.replace("document_of_record: desktop-core",
                     "document_of_record: server"))
    _bad(ADR.replace("decision: A-desktop-authority",
                     "decision: B-server-authority-lww"))
    _bad(ADR.replace("surface: explicit-offline-state-machine",
                     "surface: implicit-convergence"))


def test_state_mutations_fail():
    _bad(ADR.replace("offline_states: [ONLINE, OFFLINE-CACHED,"
                     " OFFLINE-QUEUED]",
                     "offline_states: [ONLINE, OFFLINE-CACHED,"
                     " OFFLINE-QUEUED, OFFLINE-SYNCING]"))
    _bad(ADR.replace("OFFLINE-CACHED: {writes: disabled",
                     "OFFLINE-CACHED: {writes: enabled"))
    _bad(ADR.replace("visibility: pending-until-sync",
                     "visibility: hidden"))
    _bad(ADR.replace("refusal: named-state", "refusal: silent"))


def test_write_log_mutations_fail():
    _bad(ADR.replace("silent_resolution: forbidden",
                     "silent_resolution: allowed"))
    _bad(ADR.replace("resolution: user-resolves",
                     "resolution: automatic"))
    _bad(ADR.replace("merge: by-log-order",
                     "merge: last-writer-wins"))
    _bad(ADR.replace("type: append-only", "type: mutable"))
    _bad(ADR.replace(
        "same_revision_collision: named-conflict-in-review-queue",
        "same_revision_collision: resolved-internally"))


def test_driver_and_invariant_mutations_fail():
    _bad(ADR.replace("no-silent-writes, ", ""))
    _bad(ADR.replace("desktop-core-is-sole-authority",
                     "desktop-core-is-primary-authority"))
    _bad(ADR.replace("conflicts-user-resolved-never-silent",
                     "conflicts-auto-resolved-when-safe"))
    _bad(ADR.replace("server-stores-ciphertext-only",
                     "server-stores-ciphertext-and-metadata"))
    _bad(ADR.replace("core-loop-never-requires-network",
                     "core-loop-degrades-without-network"))


def test_option_axis_mutations_fail():
    _bad(ADR.replace(", C-p2p-crdt]", "]"))
    _bad(ADR.replace("reconnect-conflicts, failure-modes",
                     "reconnect-conflicts"))


def test_prose_mutations_fail():
    anchor = "exactly as the v5 zero-knowledge design requires."
    assert anchor in ADR
    for clause in [
        "The server decides conflicts when both sides are busy.",
        "Offline writes are applied silently on reconnect.",
        "OFFLINE-CACHED approvals are queued without being shown.",
        "The desktop defers to the surface when they disagree.",
    ]:
        _bad(ADR.replace(anchor, anchor + " " + clause))
    _bad(ADR.replace("- **queue**", "- **backlog**")
         if "- **queue**" in ADR else
         ADR.replace("the desktop core is the sole authority",
                     "the desktop core is the sole authority on weekdays"))


def test_mutation_status_fails():
    _bad(ADR.replace("status: proposed", "status: accepted"))
