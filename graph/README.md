# graph

Chess knowledge and weakness graph: positions, patterns, error links, and
transfer edges derived from the user's games. Data plane only - all
diagnosis claims ship through evidence-gated tasks, never straight from
graph heuristics to the user.

`graph/transposition_node.py` implements `data/contracts/transposition_node.yaml`:
the digest-bucketed node table where every path to one position identity
reaches one node; the injectable digest oracle is untrusted and fails closed.
