# Dataset inventory, rights and pins (T2710-T2712)

Machine-readable sources of truth:

- `data/datasets/inventory.yaml` - what exists: URLs, cadence, compression, schema, size, checksum mechanism
- `data/datasets/rights-manifest.yaml` - license proof per dataset; missing terms fail closed
- `data/datasets/snapshot-pins.yaml` - immutable snapshot pins; `latest` aliases forbidden

## Measured 2026-09-18

| Dataset | Snapshot | Compressed bytes | Notes |
|---|---|---|---|
| Lichess standard games | 2026-08 (163 monthly files since 2013-01) | 30,145,862,359 | PGN zstd, monthly cadence |
| Lichess puzzles | file dated 2026-09-09, 6,100,952 rows | 304,429,328 | CSV zstd |
| Lichess evaluations | file dated 2026-09-10, 409,710,113 rows | 22,086,532,809 | JSONL zstd |
| Lichess broadcasts | per-event | n/a | JSON API + per-round PGN; license unresolved -> fail closed |
| Lichess Elite Database | through 2024-04 (stale) | ~100-260 MB/month zip | community CC0-derivative, flagged for verifier |

Lichess publishes content-length and ETag but no cryptographic checksums; the
pipeline pins sha256 after first fetch and refuses silent change afterward
(T2712/T2713 enforcement point).

The full standard corpus (163 months x ~30 GB recent) is far beyond this
sandbox; acquisition runs elsewhere under the T2714 budget. Only the pinned
puzzle snapshot is fetched here for fixture work.
