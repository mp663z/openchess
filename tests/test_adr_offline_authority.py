"""T2795: ADR-0003 structural battery v2 - offline authority. Front
matter normative AND root-closed: the exact key set is pinned, every
mapping level is container-checked. The offline model is a complete
state machine: explicit events, a full state x event transition table
(destination + action or exact named refusal, guards where declared),
per-state readable/writable/queueable capability pins. The reconnect
log model pins entry identity, per-writer sequence, base-revision
links, deterministic merge ordering (never wall-clock/arrival),
concurrency detection, and conflict-before-winner semantics. Cross-ADR
consistency with ADR-0004 (web_offline_capable) and ADR-0005 (capability
owners) is checked by parsing both documents; mutating either fails.
The prose body is pinned byte-for-byte. No vocabulary checks."""

from pathlib import Path

import yaml

ADR_DIR = Path(__file__).resolve().parent.parent / "docs" / "adr"
ADR = (ADR_DIR / "ADR-0003-offline-authority.md").read_text()
ADR4 = (ADR_DIR / "ADR-0004-asymmetric-architecture.md").read_text()
ADR5 = (ADR_DIR / "ADR-0005-platform-ownership.md").read_text()

ROOT_KEYS = [
    "adr", "status", "scope", "authority", "options_compared",
    "decision", "axes", "links", "offline_states", "events",
    "state_semantics", "capabilities", "transitions", "write_log",
    "drivers", "invariants",
]
OPTIONS = ["A-desktop-authority", "B-server-authority-lww",
           "C-p2p-crdt"]
AXES = ["offline-desktop", "offline-web-state", "reconnect-conflicts",
        "failure-modes"]
STATES = ["ONLINE", "OFFLINE-CACHED", "OFFLINE-QUEUED"]
EVENTS = ["connectivity-lost", "connectivity-restored",
          "sync-confirmed", "write-attempted", "approval-attempted",
          "queue-persistence-unavailable"]
CAPS = ["queue", "diff", "approval", "quiet-week", "drills"]
STATE_SEMANTICS = {
    "ONLINE": {"writes": "enabled", "source": "live-sync"},
    "OFFLINE-CACHED": {
        "writes": "disabled", "refusal": "named-state",
        "reads": "cached"},
    "OFFLINE-QUEUED": {
        "writes": "intent-entries", "visibility": "pending-until-sync",
        "reads": "cached"},
}
CAPABILITIES = {
    "ONLINE": {"readable": CAPS, "writable": ["approval", "drills"],
               "queueable": []},
    "OFFLINE-CACHED": {"readable": CAPS, "writable": [],
                       "queueable": []},
    "OFFLINE-QUEUED": {"readable": CAPS, "writable": [],
                       "queueable": ["approval"]},
}
TRANSITIONS = {
    "ONLINE": {
        "connectivity-lost": {"to": "OFFLINE-CACHED",
                              "action": "freeze-cache"},
        "connectivity-restored": {"to": "ONLINE", "action": "no-op"},
        "sync-confirmed": {"to": "ONLINE", "action": "no-op"},
        "write-attempted": {"to": "ONLINE", "action": "apply"},
        "approval-attempted": {"to": "ONLINE", "action": "apply"},
        "queue-persistence-unavailable": {"to": "ONLINE",
                                          "action": "no-op"},
    },
    "OFFLINE-CACHED": {
        "connectivity-lost": {"to": "OFFLINE-CACHED",
                              "action": "no-op"},
        "connectivity-restored": {"to": "ONLINE",
                                  "action": "resume-live-sync"},
        "sync-confirmed": {"to": "OFFLINE-CACHED", "action": "no-op"},
        "write-attempted": {"to": "OFFLINE-CACHED",
                            "action": "refuse-named-state"},
        "approval-attempted": {
            "guard": "queue-persistence-available",
            "then": {"to": "OFFLINE-QUEUED", "action": "record-intent"},
            "else": {"to": "OFFLINE-CACHED",
                     "action": "refuse-named-state"}},
        "queue-persistence-unavailable": {"to": "OFFLINE-CACHED",
                                          "action": "no-op"},
    },
    "OFFLINE-QUEUED": {
        "connectivity-lost": {"to": "OFFLINE-QUEUED",
                              "action": "no-op"},
        "connectivity-restored": {"to": "OFFLINE-QUEUED",
                                  "action": "begin-sync"},
        "sync-confirmed": {
            "guard": "all-intents-applied-and-conflicts-surfaced",
            "then": {"to": "ONLINE", "action": "resume-live-sync"},
            "else": {"to": "OFFLINE-QUEUED",
                     "action": "remain-pending"}},
        "write-attempted": {"to": "OFFLINE-QUEUED",
                            "action": "refuse-named-state"},
        "approval-attempted": {
            "guard": "queue-persistence-available",
            "then": {"to": "OFFLINE-QUEUED", "action": "record-intent"},
            "else": {"to": "OFFLINE-QUEUED",
                     "action": "refuse-named-state"}},
        "queue-persistence-unavailable": {
            "to": "OFFLINE-QUEUED", "action": "refuse-new-intents"},
    },
}
WRITE_LOG = {
    "type": "append-only",
    "addressing": "content-addressed",
    "entry": {
        "id": "content-hash-of-canonical-entry-envelope",
        "envelope_fields": [
            "domain-separator", "contract-version", "writer",
            "writer_sequence", "parents", "object", "object_revision",
            "operation", "payload"],
        "writer": "writer-device-id",
        "writer_sequence": "per-writer-strictly-monotonic-unique-integer",
        "duplicate_writer_sequence": "reject",
        "parents": "parent-entry-ids-list-updated-on-every-append",
        "merge_entry_parents": "multiple-parents-join-histories",
        "object": "object-identity",
        "object_revision": "object-revision-written",
        "operation": "the-operation",
        "payload": "the-write",
    },
    "merge": {
        "ordering": "topological-by-parent-links-then-writer-id-"
                    "writer-sequence-lexicographic",
        "ordering_never": ["wall-clock", "arrival-order"],
        "concurrency": "neither-entry-is-an-ancestor-of-the-other-"
                       "via-parent-links",
        "same_object": "concurrent-entries-writing-the-same-object",
        "conflict_rule": "named-review-queue-item-with-both-diffs-"
                         "before-any-winner-materialized",
        "ambiguous_concurrent_writes":
            "unresolved-until-logged-user-resolution",
    },
    "dedup": "replay-of-existing-entry-id-is-idempotent-no-op",
    "resolution": "user-resolves",
    "resolution_entry": "merge-resolution-entry-with-multiple-parents",
    "silent_resolution": "forbidden",
}
ACTIONS = {"freeze-cache", "no-op", "apply", "resume-live-sync",
           "refuse-named-state", "record-intent", "begin-sync",
           "remain-pending", "refuse-new-intents"}
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
    "merge-ordering-deterministic-never-wall-clock",
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
"""


def _parse(adr: str):
    assert adr.startswith("---\n"), "YAML front matter required"
    fm = yaml.safe_load(adr.split("---\n", 2)[1])
    body = adr.split("---\n", 2)[2]
    return fm, body


def _check_transition(entry) -> None:
    """One transition entry: unconditional {to, action} or guarded
    {guard, then, else}; destinations are declared states, actions are
    declared, guards are non-empty strings."""
    if "guard" in entry:
        assert set(entry) == {"guard", "then", "else"}
        assert type(entry["guard"]) is str and entry["guard"].strip()
        for branch in ("then", "else"):
            _check_transition(entry[branch])
        return
    assert set(entry) == {"to", "action"}
    assert entry["to"] in STATES
    assert entry["action"] in ACTIONS


def _check(adr: str, adr4: str | None = None,
           adr5: str | None = None) -> None:
    fm, body = _parse(adr)
    fm4, _ = _parse(adr4 if adr4 is not None else ADR4)
    fm5, _ = _parse(adr5 if adr5 is not None else ADR5)
    # Root closure: exact key set, nothing silently authoritative.
    assert sorted(fm) == sorted(ROOT_KEYS), sorted(fm)
    # Container shapes at every mapping level.
    for key in ("authority", "links", "state_semantics",
                "capabilities", "transitions", "write_log"):
        assert type(fm[key]) is dict, key
    for key in ("options_compared", "axes", "offline_states",
                "events", "drivers", "invariants"):
        assert type(fm[key]) is list, key
    assert type(fm["write_log"]["entry"]) is dict
    assert type(fm["write_log"]["merge"]) is dict
    # Exact structured pins.
    assert fm["adr"] == "ADR-0003"
    assert fm["status"] == "proposed"
    assert fm["scope"] == "offline-authority-and-reconnect-semantics"
    assert fm["authority"] == {
        "document_of_record": "desktop-core",
        "surface": "explicit-offline-state-machine"}
    assert fm["options_compared"] == OPTIONS
    assert fm["decision"] == "A-desktop-authority"
    assert fm["axes"] == AXES
    assert fm["links"] == {
        "adr0004": "docs/adr/ADR-0004-asymmetric-architecture.md",
        "adr0005": "docs/adr/ADR-0005-platform-ownership.md"}
    assert fm["offline_states"] == STATES
    assert fm["events"] == EVENTS
    assert fm["state_semantics"] == STATE_SEMANTICS
    assert fm["capabilities"] == CAPABILITIES
    assert fm["transitions"] == TRANSITIONS
    assert fm["write_log"] == WRITE_LOG
    assert fm["drivers"] == DRIVERS
    assert fm["invariants"] == INVARIANTS
    # Complete state machine: every state x event pair, structurally.
    assert set(fm["transitions"]) == set(STATES)
    for state in STATES:
        entries = fm["transitions"][state]
        assert set(entries) == set(EVENTS), state
        for event in EVENTS:
            _check_transition(entries[event])
    # Capability pins: every declared capability readable in every
    # state; only approval queueable, only in OFFLINE-QUEUED; writes
    # only ONLINE.
    for state in STATES:
        caps = fm["capabilities"][state]
        assert set(caps) == {"readable", "writable", "queueable"}
        assert caps["readable"] == CAPS, state
    # Cross-ADR: ADR-0004 requires queue/approval/training
    # offline-capable on web; the ADR-0005 capabilities realizing
    # those operations must be offline-readable here, and approval
    # offline-queueable.
    assert fm4["web_offline_capable"] == ["queue", "approval",
                                          "training"]
    caps5 = fm5["capabilities"]
    for op in fm4["web_offline_capable"]:
        realizing = [c for c, s in caps5.items()
                     if op in s["crosswalk"]]
        assert realizing, op
        for cap in realizing:
            assert cap in CAPS, (op, cap)
            for state in ("OFFLINE-CACHED", "OFFLINE-QUEUED"):
                assert cap in fm["capabilities"][state]["readable"], (
                    op, cap, state)
    assert "approval" in fm["capabilities"]["OFFLINE-QUEUED"][
        "queueable"]
    # Cross-ADR: ADR-0005 desktop authoritative state for every
    # capability matches ADR-0003's desktop-core document of record;
    # the habit capabilities are web-owned on compute+presentation.
    assert fm["authority"]["document_of_record"] == "desktop-core"
    for cap in CAPS:
        spec = caps5[cap]
        assert spec["authoritative_state_owner"] == "desktop", cap
        assert spec["compute_owner"] == "web", cap
        assert spec["presentation_owner"] == "web", cap
    # Byte-for-byte prose pinning.
    assert body == BODY, "body differs from pinned approved prose"


def _bad(mutated: str, mutated4: str | None = None,
         mutated5: str | None = None) -> None:
    try:
        _check(mutated, mutated4, mutated5)
    except (AssertionError, KeyError, TypeError, yaml.YAMLError):
        return
    raise AssertionError("mutation passed - the check has a hole")



def _model(log_spec):
    """Executable reference model of the fm write-log semantics. The
    model is DERIVED from the front matter: envelope fields name the
    id hash inputs, parents carry ancestry, duplicate policy and
    dedup come from the declared fields. A spec mutation that breaks
    a semantic guarantee fails the witness tests below."""
    import hashlib
    import json as _json

    entry_spec = log_spec["entry"]
    fields = entry_spec["envelope_fields"]
    assert entry_spec["id"] == "content-hash-of-canonical-entry-envelope"
    for required in ("domain-separator", "contract-version", "writer",
                     "writer_sequence", "parents", "object", "payload"):
        assert required in fields, required
    assert entry_spec["writer_sequence"] == (
        "per-writer-strictly-monotonic-unique-integer")
    assert entry_spec["duplicate_writer_sequence"] == "reject"
    assert entry_spec["parents"] == (
        "parent-entry-ids-list-updated-on-every-append")
    assert log_spec["dedup"] == (
        "replay-of-existing-entry-id-is-idempotent-no-op")
    assert log_spec["merge"]["concurrency"].endswith("via-parent-links")
    assert log_spec["merge"]["conflict_rule"].endswith(
        "before-any-winner-materialized")
    assert log_spec["merge"]["ambiguous_concurrent_writes"] == (
        "unresolved-until-logged-user-resolution")
    assert log_spec["resolution_entry"] == (
        "merge-resolution-entry-with-multiple-parents")

    class Log:
        def __init__(self):
            self.entries = {}
            self.last_seq = {}
            self.conflicts = []
            self.winners = {}

        def make(self, writer, seq, parents, obj, rev, op, payload):
            e = {"domain-separator": "write-log-v1",
                 "contract-version": 1, "writer": writer,
                 "writer_sequence": seq, "parents": list(parents),
                 "object": obj, "object_revision": rev,
                 "operation": op, "payload": payload}
            blob = _json.dumps({k: e[k] for k in fields},
                               sort_keys=True).encode()
            e["id"] = hashlib.sha256(blob).hexdigest()
            return e

        def ancestors(self, entry):
            seen = set()
            stack = list(entry["parents"])
            while stack:
                pid = stack.pop()
                if pid not in seen:
                    seen.add(pid)
                    if pid in self.entries:
                        stack.extend(self.entries[pid]["parents"])
            seen.discard(entry["id"])
            return seen

        def concurrent(self, a, b):
            return (a["id"] not in self.ancestors(b)
                    and b["id"] not in self.ancestors(a))

        def append(self, e):
            if e["id"] in self.entries:
                return False  # idempotent replay of a known entry
            last = self.last_seq.get(e["writer"])
            if last is not None and e["writer_sequence"] <= last:
                raise ValueError("duplicate (writer, sequence)")
            self.last_seq[e["writer"]] = e["writer_sequence"]
            conflict = False
            for other in self.entries.values():
                if (other["object"] == e["object"]
                        and len(e["parents"]) == 1
                        and len(other["parents"]) == 1
                        and self.concurrent(e, other)):
                    conflict = True
                    self.conflicts.append(
                        {"object": e["object"],
                         "entries": sorted([e["id"], other["id"]])})
            if conflict:
                self.winners.pop(e["object"], None)
            elif len(e["parents"]) <= 1:
                self.winners[e["object"]] = e["id"]
            self.entries[e["id"]] = e
            return True

        def resolve(self, writer, seq, conflicting_ids, obj, rev,
                    payload):
            r = self.make(writer, seq, sorted(conflicting_ids), obj,
                          rev, "user-resolution", payload)
            assert len(r["parents"]) > 1, "resolution joins histories"
            self.append(r)
            self.winners[obj] = r["id"]
            return r

    return Log()


def test_witness_sequential_offline_writes_are_ancestors():
    fm, _ = _parse(ADR)
    log = _model(fm["write_log"])
    head = log.make("A", 6, [], "repertoire", 6, "sync", "H")
    log.append(head)
    p = log.make("A", 7, [head["id"]], "repertoire", 7, "add", "P")
    log.append(p)
    q = log.make("A", 8, [p["id"]], "repertoire", 8, "add", "Q")
    log.append(q)
    assert q["parents"] == [p["id"]], "second write descends from first"
    assert p["id"] in log.ancestors(q)
    assert not log.concurrent(p, q), (
        "sequential same-writer writes must never be concurrent")
    assert log.conflicts == []
    assert log.winners["repertoire"] == q["id"]


def test_witness_identical_payloads_distinct_ids():
    fm, _ = _parse(ADR)
    log = _model(fm["write_log"])
    head = log.make("A", 1, [], "repertoire", 1, "sync", "H")
    log.append(head)
    a = log.make("A", 2, [head["id"]], "repertoire", 2, "add", "P")
    b = log.make("B", 2, [head["id"]], "repertoire", 2, "add", "P")
    assert a["id"] != b["id"], (
        "identical payload from two writers must be distinct entries")
    a2 = log.make("A", 3, [a["id"]], "repertoire", 3, "add", "P")
    assert a2["id"] != a["id"], (
        "repeated payload at a new sequence must be a distinct entry")


def test_witness_replay_is_idempotent_dedup():
    fm, _ = _parse(ADR)
    log = _model(fm["write_log"])
    head = log.make("A", 1, [], "repertoire", 1, "sync", "H")
    assert log.append(head) is True
    assert log.append(head) is False, "replayed entry id must dedup"
    assert len(log.entries) == 1
    assert log.winners["repertoire"] == head["id"]


def test_witness_genuine_fork_conflicts_before_any_winner():
    fm, _ = _parse(ADR)
    log = _model(fm["write_log"])
    head = log.make("A", 1, [], "repertoire", 1, "sync", "H")
    log.append(head)
    a = log.make("A", 2, [head["id"]], "repertoire", 2, "edit", "A-edit")
    log.append(a)
    assert log.winners["repertoire"] == a["id"]
    b = log.make("B", 1, [head["id"]], "repertoire", 2, "edit", "B-edit")
    log.append(b)
    assert log.concurrent(a, b)
    assert log.conflicts == [
        {"object": "repertoire",
         "entries": sorted([a["id"], b["id"]])}], (
        "fork on the same object must surface a named conflict")
    assert "repertoire" not in log.winners, (
        "no winner materializes before user resolution")
    r = log.resolve("A", 3, [a["id"], b["id"]], "repertoire", 3,
                    "merged-choice")
    assert log.winners["repertoire"] == r["id"]
    assert {a["id"], b["id"]} <= log.ancestors(r)


def test_model_mutations_fail():
    fm, _ = _parse(ADR)
    import copy as _copy

    def broken(mutate):
        spec = _copy.deepcopy(fm["write_log"])
        mutate(spec)
        try:
            log = _model(spec)
        except (AssertionError, KeyError):
            return True  # rejected at build
        # a model that BUILT from a broken spec must fail a witness
        try:
            head = log.make("A", 1, [], "o", 1, "sync", "H")
            log.append(head)
            a = log.make("A", 2, [head["id"]], "o", 2, "add", "P")
            b = log.make("B", 2, [head["id"]], "o", 2, "add", "P")
            assert a["id"] != b["id"]
            q = log.make("A", 3, [a["id"]], "o", 3, "add", "Q")
            log.append(a)
            log.append(q)
            assert a["id"] in log.ancestors(q)
            assert log.append(head) is False
        except (AssertionError, KeyError, ValueError):
            return True
        return False

    assert broken(lambda s: s["entry"]["envelope_fields"].remove(
        "writer")), "envelope without writer must break id uniqueness"
    assert broken(lambda s: s["entry"]["envelope_fields"].remove(
        "writer_sequence")), "envelope without sequence must break"
    assert broken(lambda s: s["entry"].__setitem__(
        "id", "content-hash-of-entry-payload"))
    assert broken(lambda s: s["entry"].__setitem__(
        "parents", "last-synced-entry-only"))
    assert broken(lambda s: s.__setitem__(
        "dedup", "replay-appends-duplicate"))
    assert broken(lambda s: s["merge"].__setitem__(
        "concurrency", "neither-entry-is-an-ancestor-via-base-revision"))

def test_real_adr_passes():
    _check(ADR)


def test_root_closure_mutations_fail():
    # extra normative key silently authoritative
    _bad(ADR.replace("drivers:", "rogue: silently-authoritative\n"
                     "drivers:"))
    # required key removed
    _bad(ADR.replace("transitions:\n", ""))
    # wrong container at every mapping level
    _bad(ADR.replace("authority:\n  document_of_record: desktop-core\n"
                     "  surface: explicit-offline-state-machine",
                     "authority: [desktop-core]"))
    _bad(ADR.replace("write_log:\n  type: append-only",
                     "write_log: append-only"))
    _bad(ADR.replace("transitions:\n  ONLINE:",
                     "transitions:\n  - ONLINE"))
    _bad(ADR.replace("capabilities:\n  ONLINE:",
                     "capabilities: all-online"))
    _bad(ADR.replace("state_semantics:\n  ONLINE:",
                     "state_semantics:\n  - ONLINE"))
    _bad(ADR.replace("  entry:\n    id:", "  entry: [id]"))
    _bad(ADR.replace("  merge:\n    ordering:", "  merge: ordered"))


def test_authority_mutations_fail():
    _bad(ADR.replace("document_of_record: desktop-core",
                     "document_of_record: server"))
    _bad(ADR.replace("decision: A-desktop-authority",
                     "decision: B-server-authority-lww"))
    _bad(ADR.replace("surface: explicit-offline-state-machine",
                     "surface: implicit-convergence"))


def test_state_and_capability_mutations_fail():
    _bad(ADR.replace(" OFFLINE-QUEUED]",
                     " OFFLINE-QUEUED, OFFLINE-SYNCING]"))
    _bad(ADR.replace("OFFLINE-CACHED: {writes: disabled",
                     "OFFLINE-CACHED: {writes: enabled"))
    _bad(ADR.replace("visibility: pending-until-sync",
                     "visibility: hidden"))
    _bad(ADR.replace("refusal: named-state", "refusal: silent"))
    _bad(ADR.replace("    queueable: [approval]",
                     "    queueable: []"))
    _bad(ADR.replace("    writable: []\n    queueable: []\n"
                     "  OFFLINE-QUEUED:",
                     "    writable: [approval]\n    queueable: []\n"
                     "  OFFLINE-QUEUED:"))
    _bad(ADR.replace("    readable: [queue, diff, approval, quiet-week,"
                     " drills]\n    writable: []\n"
                     "    queueable: [approval]",
                     "    readable: [queue, diff, quiet-week, drills]\n"
                     "    writable: []\n    queueable: [approval]"))


def test_transition_mutations_fail():
    _bad(ADR.replace("{to: OFFLINE-CACHED, action: freeze-cache}",
                     "{to: SYNCING, action: freeze-cache}"))
    _bad(ADR.replace("    write-attempted: {to: OFFLINE-CACHED,"
                     " action: refuse-named-state}",
                     "    write-attempted: {to: OFFLINE-CACHED,"
                     " action: apply}"))
    _bad(ADR.replace("guard: all-intents-applied-and-conflicts-"
                     "surfaced",
                     "guard: always"))
    _bad(ADR.replace("    queue-persistence-unavailable: {to:"
                     " OFFLINE-QUEUED, action: refuse-new-intents}",
                     "    queue-persistence-unavailable: {to:"
                     " OFFLINE-QUEUED, action: record-intent}"))
    _bad(ADR.replace("    connectivity-restored: {to: ONLINE,"
                     " action: resume-live-sync}",
                     "    connectivity-restored: {to: ONLINE,"
                     " action: begin-sync}"))


def test_write_log_mutations_fail():
    _bad(ADR.replace("silent_resolution: forbidden",
                     "silent_resolution: allowed"))
    _bad(ADR.replace("resolution: user-resolves",
                     "resolution: automatic"))
    _bad(ADR.replace("ordering: topological-by-parent-links-then-"
                     "writer-id-writer-sequence-lexicographic",
                     "ordering: wall-clock"))
    _bad(ADR.replace("ordering_never: [wall-clock, arrival-order]",
                     "ordering_never: []"))
    _bad(ADR.replace("conflict_rule: named-review-queue-item-with-"
                     "both-diffs-before-any-winner-materialized",
                     "conflict_rule: winner-materialized-then-notified"))
    _bad(ADR.replace("ambiguous_concurrent_writes: unresolved-until-"
                     "logged-user-resolution",
                     "ambiguous_concurrent_writes: auto-resolved"))
    _bad(ADR.replace("id: content-hash-of-canonical-entry-envelope",
                     "id: content-hash-of-entry-payload"))
    _bad(ADR.replace("writer_sequence: per-writer-strictly-monotonic-"
                     "unique-integer",
                     "writer_sequence: per-writer-monotonic-integer"))
    _bad(ADR.replace("duplicate_writer_sequence: reject",
                     "duplicate_writer_sequence: last-wins"))
    _bad(ADR.replace("parents: parent-entry-ids-list-updated-on-every-"
                     "append",
                     "parents: last-synced-entry-only"))
    _bad(ADR.replace("via-parent-links", "via-base-revision"))
    _bad(ADR.replace("dedup: replay-of-existing-entry-id-is-idempotent-"
                     "no-op",
                     "dedup: replay-appends-duplicate"))
    _bad(ADR.replace("resolution_entry: merge-resolution-entry-with-"
                     "multiple-parents",
                     "resolution_entry: single-parent-entry"))
    _bad(ADR.replace("type: append-only", "type: mutable"))


def test_driver_invariant_option_axis_mutations_fail():
    _bad(ADR.replace("no-silent-writes, ", ""))
    _bad(ADR.replace("desktop-core-is-sole-authority",
                     "desktop-core-is-primary-authority"))
    _bad(ADR.replace("conflicts-user-resolved-never-silent",
                     "conflicts-auto-resolved-when-safe"))
    _bad(ADR.replace("merge-ordering-deterministic-never-wall-clock",
                     "merge-ordering-usually-deterministic"))
    _bad(ADR.replace(", C-p2p-crdt]", "]"))
    _bad(ADR.replace("reconnect-conflicts, failure-modes",
                     "reconnect-conflicts"))


def test_cross_adr_mutations_fail():
    # ADR-0004 drops offline approval
    _bad(ADR, ADR4.replace("web_offline_capable:\n  - queue\n"
                           "  - approval\n  - training",
                           "web_offline_capable:\n  - queue\n"
                           "  - training"))
    # ADR-0005 flips a habit capability's compute owner
    _bad(ADR, ADR5.replace("  queue:\n    compute_owner: web",
                           "  queue:\n    compute_owner: desktop"))
    # ADR-0005 moves authoritative state off the desktop
    _bad(ADR, ADR5.replace("  approval:\n    compute_owner: web\n"
                           "    presentation_owner: web\n"
                           "    authoritative_state_owner: desktop",
                           "  approval:\n    compute_owner: web\n"
                           "    presentation_owner: web\n"
                           "    authoritative_state_owner: web"))
    # ADR-0005 loses a capability ADR-0003 pins
    _bad(ADR, ADR5.replace("  drills:\n", "  practice:\n"))


def test_prose_mutations_fail():
    anchor = "exactly as the v5 zero-knowledge design requires."
    assert anchor in ADR
    for clause in [
        "The server decides conflicts when both sides are busy.",
        "Offline writes are applied silently on reconnect.",
        "OFFLINE-CACHED approvals are queued without being shown.",
        "The desktop defers to the surface when they disagree.",
        "Conflicting writes resolve by arrival time.",
    ]:
        _bad(ADR.replace(anchor, anchor + " " + clause))
    _bad(ADR.replace("the desktop core is the sole authority",
                     "the desktop core is the sole authority on"
                     " weekdays"))


def test_mutation_status_fails():
    _bad(ADR.replace("status: proposed", "status: accepted"))
