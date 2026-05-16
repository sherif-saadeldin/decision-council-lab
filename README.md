# Decision Council Lab

Domain-agnostic multi-agent decision council prototype. Slice 1 delivers a mock council engine; Slice 1.1 tightens the CLI and run artifacts; Slice 1.2 hardens the provider contract; Slice 2 adds OpenAI behind the same interface.

## Setup

```bash
uv sync --extra dev
```

Copy `.env.example` to `.env` and configure as needed:

| Variable | Purpose |
|----------|---------|
| `LLM_MODE` | `mock` (default) or `openai` |
| `OPENAI_API_KEY` | Required when `LLM_MODE=openai` |
| `DEFAULT_MODEL_OPENAI` | OpenAI model id (default `gpt-4.1-mini`) |
| `RUNS_DIR` | Artifact output directory |

## Run (mock mode)

```bash
uv run python main.py "Should I build a decision council engine as an internal tool first?"
```

## Run (OpenAI mode)

```bash
# PowerShell
$env:LLM_MODE="openai"
$env:OPENAI_API_KEY="your-key-here"
uv run python main.py "Should I build a decision council engine as an internal tool first?"
```

```bash
# bash
export LLM_MODE=openai
export OPENAI_API_KEY=your-key-here
uv run python main.py "Should I build a decision council engine as an internal tool first?"
```

Or set values in `.env` and run the same `main.py` command.

### CLI options

```bash
uv run python main.py --help
uv run python main.py "Your question" --runs-dir ./runs
uv run python main.py "Your question" --quiet
```

## Run artifacts

Each council run is saved under `runs/<run_id>/`:

| File | Purpose |
|------|---------|
| `run.json` | Structured record (`schema_version` 1.1, dossier, agent briefs, `provider_metadata`, `provider_responses`) |
| `run.md` | Human-readable dossier (`raw_response` and secrets omitted) |

## Provider contract

- `LLMProvider.complete(ProviderRequest) → ProviderResponse` is the integration point.
- Supported modes: `mock`, `openai` (`SUPPORTED_LLM_MODES`).
- OpenAI uses the Responses API with strict JSON schema output; malformed output raises `ProviderResponseError`.
- API keys are never written to logs, artifacts, or error messages.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

## Validate

```bash
uv run python -m compileall main.py council tests
uv run pytest
uv run ruff check .
```

## Build order

See [docs/BUILD_ORDER.md](docs/BUILD_ORDER.md). Next up: Slice 3 (Anthropic/Gemini providers).
