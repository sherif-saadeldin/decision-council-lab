# Decision Council Lab

Domain-agnostic multi-agent decision council prototype. Slice 1 delivers a mock council engine; Slice 1.1 tightens the CLI and run artifacts.

## Setup

```bash
uv sync --extra dev
```

Copy `.env.example` to `.env` if you want to override defaults (`LLM_MODE=mock`, `RUNS_DIR=./runs`).

## Run

```bash
uv run python main.py "Should I build a decision council engine as an internal tool first?"
```

### CLI options

```bash
uv run python main.py --help
uv run python main.py "Your question" --runs-dir ./runs
uv run python main.py "Your question" --quiet
```

- `--runs-dir` — override where artifacts are written
- `--quiet` — print only `run.json` and `run.md` paths

## Run artifacts

Each council run is saved under `runs/<run_id>/`:

| File | Purpose |
|------|---------|
| `run.json` | Structured record (`schema_version` 1.1, dossier, agent briefs, provider metadata) |
| `run.md` | Human-readable dossier aligned to the decision spec section order |

## Validate

```bash
uv run python -m compileall main.py council tests
uv run python main.py "Should I build a decision council engine as an internal tool first?"
uv run pytest
uv run ruff check .
```

## Build order

See [docs/BUILD_ORDER.md](docs/BUILD_ORDER.md). Next up after 1.1: Slice 2 (OpenAI provider), not before.
