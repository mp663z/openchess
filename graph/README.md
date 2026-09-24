# graph

Chess knowledge and weakness graph: positions, patterns, error links, and
transfer edges derived from the user's games. Data plane only - all
diagnosis claims ship through evidence-gated tasks, never straight from
graph heuristics to the user.

`graph/transposition_node.py` implements `data/contracts/transposition_node.yaml`:
the digest-bucketed node table where every path to one position identity
reaches one node; the injectable digest oracle is untrusted and fails closed.

`graph/route_edge.py` implements `data/contracts/route_edge.yaml`: exact
four-field edges `{variant, move, from_snapshot_fen, to_snapshot_fen}`
between canonical node snapshots, bucketed by from-digest plus move,
deduplicated by the canonical four-tuple, with one target per
from-identity and move (conflicting_edge) and an atomic staged merge.

`graph/conflict.py` implements `data/contracts/conflict.yaml`: three-way
conflict detection over validated graph states (base, left, right) with
derived state ids, identity-keyed witnesses in canonical order, the
divergent-base check, and no automatic resolution.

`graph/en_passant.py` implements `data/contracts/en_passant.yaml`: move
application with the double-advance target, the en-passant capture with
its preconditions and the resulting-position pin check, the canonical
identity value (target only when a legal capture exists), and the turn
transition; every input is validated as exact types first and fails closed
as a typed `EnPassantError`.
