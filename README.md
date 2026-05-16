# Decision Council Lab

Domain-agnostic multi-agent decision council prototype. Slice 1 delivers a mock council engine; Slice 1.1 tightens the CLI and run artifacts; Slice 1.2 hardens the provider contract before real LLM backends.

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
| `run.json` | Structured record (`schema_version` 1.1, dossier, agent briefs, `provider_metadata`, `provider_responses` including optional `raw_response`) |
| `run.md` | Human-readable dossier aligned to the decision spec section order (`raw_response` omitted) |

## Provider contract

- `LLMProvider.complete(ProviderRequest) → ProviderResponse` is the integration point for future OpenAI / Anthropic / Gemini backends.
- Only `LLM_MODE=mock` is supported today; other modes raise `UnsupportedProviderModeError`.
- Agent briefs include `reasoning`, `confidence`, and `source_refs` (empty in mock mode).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

## Validate

```bash
uv run python -m compileall main.py council tests
uv run python main.py "Should I build a decision council engine as an internal tool first?"
uv run pytest
uv run ruff check .
```

## Build order

See [docs/BUILD_ORDER.md](docs/BUILD_ORDER.md). Next up: Slice 2 (OpenAI provider) implementing `LLMProvider`.
