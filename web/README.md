# web

Browser client. Thin companion to `desktop`: same contracts, reduced local
capability (no long-running workers). Neutral, swappable UI; renders from
the shared contracts in `data/contracts`, never from hardcoded copy.

Deployment target is static hosting behind the `server` API; no server-side
rendering secrets, no provider keys in the bundle.
