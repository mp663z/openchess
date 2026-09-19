# S1 (CBH import) - public-feasibility research (2026-09-18)

Commissioned in place of waiting on user samples: what is publicly known about
the ChessBase CBH format, what import paths exist, and the legal posture.

## Format knowledge (public)

- CBH (ChessBase 6, 1996) is proprietary and officially undocumented; a
  database is a set of ~10+ files: .cbh (game headers), .cbg (moves +
  variations), .cba (annotations), plus indices. .cbv is a zip archive of one
  database's file set, not a separate game format.
  Source: chessprogramming.org wiki "ChessBase (Database)".
- ChessBase itself documents that classic CBH "is reaching its limits" and is
  being succeeded by 2CBH in current versions (help.chessbase.com CBase/17).
  Import must tolerate both; the installed base is overwhelmingly classic CBH.

## Public reverse-engineering implementations

| Project | Lang | License | Coverage | Posture for us |
|---|---|---|---|---|
| asdfjkl/cbh2pgn | Python | MIT | moves + variations + basic meta (Event/Site/Date/Round/players/result/Elos); NO annotations, NO Chess960 | usable as reference and fallback import path |
| Yarin78/morphy | Java | NONE (all rights reserved) | full: annotations, Chess960, ships real CBH test fixtures (World-ch, Hedgehog, cbh_test) | readable as format documentation only; no copying, no fixture use; actively maintained (pushed 2026-09) |
| scidb format docs | C++ | GPL (app) | documents CBH family on par with above | documentation value only |

Morphy's test suite proves complete read coverage of the format is achievable
from public knowledge. Its lack of license means our implementation must be
clean-room: write from format understanding + the MIT codebase, not by
translating morphy. Optional unlock: ask the morphy author for an MIT fixture
grant (unrequested so far).

## Sample files for tests (T2220 substitute analysis)

- No legally redistributable CBH sample database was found in the public
  searches above; morphy's fixtures are all-rights-reserved, ChessBase Mega
  samples are paid products. We will NOT source pirated databases.
- Consequence: T2220 stays open until the user provides their own purchased
  files (or the morphy author grants fixture rights). Encoder-less: we cannot
  synthesize CBH fixtures ourselves (readers exist, writers don't).
- Interim verification strategy for a read-only parser: golden tests against
  expected PGN output using the MIT cbh2pgn on any user-provided file, plus
  unit tests over documented binary structures.

## Legal posture (documented per directive)

- Importing a user's own purchased ChessBase files, on their machine, at their
  request, is ordinary interoperability - the same thing cbh2pgn, morphy,
  scidb and every PGN exporter do. This is the only acquisition path we
  implement.
- We never download, buy-from-reseller, or torrent database content; T2220's
  "no unlicensed copy" stands, and unknown licenses fail closed (see
  data/datasets/rights-manifest.yaml).
- CBV handling = unzip + same reader; no separate format work.
- EULA review (T2221) input: ChessBase EULA not located in public docs during
  this pass; counsel packet should confirm the EULA's interoperability and
  backup clauses before S1 go/no-go (T2228). Fail-closed until then.

## Feasibility verdict

Read-only CBH import is technically feasible from public knowledge at two
levels: (a) moves + metadata via the MIT cbh2pgn approach - low risk; (b) full
fidelity incl. annotations via clean-room implementation informed by public
format knowledge - more work, proven achievable. Chess960 and 2CBH are known
gaps to size. S1 can proceed to counsel review without waiting on samples;
golden-file testing still needs one authorized user database (T2220).

## Cited permission statement (rights manifest)

The rights manifest (data/datasets/public-source-rights.yaml,
user_own_cbh_files) cites this statement verbatim:

> Interoperability import of the user's own purchased files on their machine at their request; {{PRODUCT_NAME}} never acquires database content itself.
