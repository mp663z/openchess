# store

Durable state for the chess graph. `store/wal.py` is the production
write-ahead log for `data/contracts/wal.yaml` (contract id `store-wal`):
one ordered, hash-linked log of content-addressed put/delete mutations on
exact graph node records.

Boundary: store owns ordering, chaining, atomic append and replay. It does
not decide what a node means - record validation and identity come from the
shipped `graph.node` runtime and the replay state id from `graph.diff`.
Payload canonicalizers are untrusted input and fail closed. A rejected
append or replay leaves every input unchanged. Production code never
imports `tests.*`.

License: AGPL (`store: agpl` in licensing-boundary.yaml).

`store/corruption.py` implements `data/contracts/corruption.yaml` (contract
id `store-corruption`): it scans a WAL log to the longest prefix the linked
WAL accepts, bounds the loss, and quarantines the corrupt suffix through an
untrusted sink whose token must match the local canonical encoding.

`store/crash_resume.py` implements `data/contracts/crash_resume.yaml`: after
a crash it keeps the longest prefix the linked WAL accepts, requires every
checkpointed entry to survive, and quarantines the torn tail through an
untrusted sink whose token must match the local canonical tail encoding.
