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
