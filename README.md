# Decision Council Lab

Domain-agnostic multi-agent decision council prototype. Slice 2.1 improves dossier quality (decision type, chair judgment fields, richer agent briefs) on top of mock and OpenAI providers.

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
uv run python main.py "Your question" --save-prompt-debug
```

- `--save-prompt-debug` — writes `runs/<run_id>/prompt_debug.md` (prompts only; secrets redacted)

## Run artifacts

Each council run is saved under `runs/<run_id>/`:

| File | Purpose |
|------|---------|
| `run.json` | Structured record (`schema_version` 1.2, dossier with `decision_type`, chair fields, agent briefs) |
| `run.md` | Human-readable dossier with Chair Judgment section (`raw_response` and secrets omitted) |
| `prompt_debug.md` | Optional prompt capture when `--save-prompt-debug` is set |

## Provider contract

- `LLMProvider.complete(ProviderRequest) → ProviderResponse` is the integration point.
- Supported modes: `mock`, `openai` (`SUPPORTED_LLM_MODES`).
- OpenAI uses the Responses API with strict JSON schema output; malformed output raises `ProviderResponseError`.
- API keys are never written to logs, artifacts, or error messages.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

## Troubleshooting (OpenAI)

Controlled provider errors print a **single clean message** (no Python traceback). Use `--quiet` for a one-line error on stderr.

| Symptom | What to do |
|---------|------------|
| `OpenAI authentication failed. Check OPENAI_API_KEY.` | Invalid or missing key. Set a real key in `.env` or your shell (see below). No key value is ever printed. |
| `OpenAI rate limit exceeded...` | Wait and retry, or switch to a lower-cost model via `DEFAULT_MODEL_OPENAI`. |
| `OpenAI request failed due to a network or connection issue.` | Check connectivity, VPN, and firewall rules. |
| Other `OpenAI API call failed...` | Re-run with `--save-prompt-debug` to inspect prompts (secrets redacted). |

### Run with a real OpenAI key

```bash
# PowerShell
$env:LLM_MODE = "openai"
$env:OPENAI_API_KEY = "sk-..."   # your real key
uv run python main.py "Your decision question"
```

```bash
# bash
export LLM_MODE=openai
export OPENAI_API_KEY=sk-...     # your real key
uv run python main.py "Your decision question"
```

### Switch back to mock mode (no API key)

```bash
# PowerShell
$env:LLM_MODE = "mock"
Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue

# bash
export LLM_MODE=mock
unset OPENAI_API_KEY
```

Or set `LLM_MODE=mock` in `.env` and omit `OPENAI_API_KEY`.

### Verify invalid-key CLI behavior (no traceback)

```powershell
$env:LLM_MODE = "openai"
$env:OPENAI_API_KEY = "your-key-here"
uv run python main.py "Test auth error"
# Expect exit code 1 and only: OpenAI authentication failed. Check OPENAI_API_KEY.
```

## Validate

```bash
uv run python -m compileall main.py council tests
uv run pytest
uv run ruff check .
```

## Build order

See [docs/BUILD_ORDER.md](docs/BUILD_ORDER.md). Next up: Slice 3 (Anthropic/Gemini providers).
