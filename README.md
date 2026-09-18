# OpenChess

Every week, OpenChess turns your own games into the three highest-impact things to train, gives you drills from the positions that caused them, and checks whether the learning transfers to later games.

Local-first. Open source (AGPL-3.0). Bring your own keys: OpenRouter, OpenAI, Anthropic, Ollama, llama.cpp, or MLX - no hard vendor dependency.

## Status

Early foundation phase. The canonical work board is the task DAG in `tasks/dag.json` (4,825 atomic tasks, dependency-ordered). The governing documents:

- `docs/plan/product-report-v5.md` - product specification
- `docs/plan/development-plan-v9.md` - development, validation and continuity plan
- `scope/v0.1.yaml` - frozen V0.1 scope (five capabilities, named deferrals)

## Repository layout

| Path | Contents |
|---|---|
| `tasks/dag.json` | Canonical task DAG and board (status, evidence, SHA per task) |
| `tools/` | Repo tooling: DAG board operations, license audit |
| `tests/` | Tooling and library tests |
| `scope/` | Machine-readable scope freezes |
| `docs/` | Plan, architecture decision records, runbooks |
| `data/` | Data-pipeline code (corpus acquisition, normalization) |

## Development

Requires Python 3.12+.

```
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest            # tests
ruff check .      # lint
python tools/dag.py verify            # DAG integrity
python tools/license_audit.py         # dependency license audit
```

Done requires acceptance, an exact SHA and evidence. UNVERIFIED is never done.

## License

AGPL-3.0-only. Dependencies are restricted to permissive licenses (MIT / Apache-2.0 / BSD); see `tools/license_audit.py` and `docs/licensing.md`.
