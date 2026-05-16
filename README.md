# Decision Council Lab

Domain-agnostic multi-agent decision council prototype. Specialists research the question, debate in structured rounds (Advocate / Skeptic / Moderator), then the chair produces a decision dossier. Schema 1.4 adds `debate_transcript` to run artifacts.

## Setup

```bash
uv sync --extra dev
```

Copy `.env.example` to `.env` and configure as needed:

| Variable | Purpose |
|----------|---------|
| `LLM_MODE` | `mock` (default), `openai`, or `openai_compatible` |
| `OPENAI_API_KEY` | Required when `LLM_MODE=openai` (or set via `secrets set`) |
| `DEFAULT_MODEL_OPENAI` | OpenAI model id (default `gpt-4.1-mini`) |
| `LLM_PROVIDER_NAME` | Label for compatible provider (e.g. `openrouter`) |
| `LLM_BASE_URL` | Base URL for `openai_compatible` (e.g. OpenRouter API root) |
| `LLM_API_KEY` | API key for `openai_compatible` (or set via `secrets set`) |
| `LLM_MODEL` | Model id for `openai_compatible` |
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

## Run (OpenAI-compatible — OpenRouter, Groq, Together, etc.)

Uses the same code path as OpenAI direct, with a custom `base_url`:

```bash
# PowerShell — OpenRouter example
$env:LLM_MODE = "openai_compatible"
$env:LLM_PROVIDER_NAME = "openrouter"
$env:LLM_BASE_URL = "https://openrouter.ai/api/v1"
$env:LLM_API_KEY = "your-openrouter-key"
$env:LLM_MODEL = "anthropic/claude-sonnet-4.5"
uv run python main.py "Your decision question"
```

```bash
# bash
export LLM_MODE=openai_compatible
export LLM_PROVIDER_NAME=openrouter
export LLM_BASE_URL=https://openrouter.ai/api/v1
export LLM_API_KEY=your-openrouter-key
export LLM_MODEL=anthropic/claude-sonnet-4.5
uv run python main.py "Your decision question"
```

Swap `LLM_BASE_URL` and `LLM_MODEL` for Groq, Together, Fireworks, DeepInfra, or other OpenAI-compatible endpoints.

## Model presets (Slice 3.1)

Use `--preset` to apply named routing without setting mode/model env vars each time. API keys come from env (preferred) or the OS keyring.

```bash
uv run python main.py --list-presets
uv run python main.py "Your question" --preset mock
uv run python main.py "Your question" --preset openai-mini      # needs OPENAI_API_KEY
uv run python main.py "Your question" --preset openrouter-sonnet  # needs LLM_API_KEY
```

| Preset | Mode | Provider | Model |
|--------|------|----------|-------|
| `mock` | mock | mock | mock-council-v1 |
| `openai-mini` | openai | openai | gpt-4.1-mini |
| `openrouter-sonnet` | openai_compatible | openrouter | anthropic/claude-sonnet-4.5 |
| `openrouter-gemini` | openai_compatible | openrouter | google/gemini-2.5-pro-preview |
| `openrouter-deepseek` | openai_compatible | openrouter | deepseek/deepseek-chat-v3-0324 |
| `openrouter-qwen` | openai_compatible | openrouter | qwen/qwen-2.5-72b-instruct |
| `ollama-qwen` | openai_compatible | ollama | qwen2.5:7b |
| `ollama-phi` | openai_compatible | ollama | phi3:mini |
| `ollama-gemma` | openai_compatible | ollama | gemma3:4b |
| `ollama-deepseek-coder` | openai_compatible | ollama | deepseek-coder:6.7b |

`--preset` overrides `LLM_MODE` / model-related env defaults. `OPENAI_API_KEY` or `LLM_API_KEY` remains required for live providers (except Ollama — see below).

### Ollama local presets (Slice 3.2)

Uses the existing `openai_compatible` path against Ollama’s OpenAI API (`http://localhost:11434/v1`). No native Ollama SDK.

1. Install [Ollama](https://ollama.com) and pull a model, e.g. `ollama pull qwen2.5:7b`
2. Set `LLM_API_KEY=ollama` (dummy value — Ollama does not validate it), or omit it (presets default to `ollama`)
3. Run:

```bash
uv run python main.py "Your question" --preset ollama-qwen
```

Default model tags in presets (`qwen2.5:7b`, `phi3:mini`, etc.) match common pulls. If your `ollama list` names differ, edit `council/model_presets.py` or override with manual `LLM_MODEL` env + `openai_compatible` mode.

### CLI commands

```bash
uv run python main.py --help
uv run python main.py presets          # list model presets
uv run python main.py config init      # create .dcouncil/config.toml (no secrets)
uv run python main.py config list
uv run python main.py config show mock
uv run python main.py config use ollama-local
uv run python main.py secrets list
uv run python main.py secrets set OPENAI_API_KEY
uv run python main.py doctor           # check mode, credentials, Ollama reachability
uv run python main.py doctor --profile ollama-local
uv run python main.py doctor --preset ollama-qwen
uv run python main.py version
uv run python main.py run "Your question" --profile mock
uv run python main.py run "Your question" --preset mock
uv run python main.py compare "Your question" --presets mock,ollama-qwen --debate-rounds 1
uv run python main.py smoke --preset mock
```

### Compare / benchmark

Run the same question across multiple presets or config profiles. Runs are sequential; a provider failure on one target does not stop the rest. A rule-based evaluator summarizes outcomes (no extra LLM call).

```bash
uv run python main.py compare "Should I keep this CLI-first?" \
  --presets mock,ollama-qwen --debate-rounds 1

uv run python main.py compare "Question?" --profiles mock,ollama-local

# benchmark is an alias for compare
uv run python main.py benchmark "Question?" --presets mock -q
```

Artifacts:

| Path | Purpose |
|------|---------|
| `runs/<run_id>/run.json` | Per-target council run (on success) |
| `runs/comparisons/<comparison_id>/comparison.json` | Structured comparison report |
| `runs/comparisons/<comparison_id>/comparison.md` | Human-readable comparison |

Exit code `0` if at least one target succeeds; `1` if all targets fail or config is invalid.

### Live smoke test (manual)

Deliberate end-to-end check against a **real** provider. Not used by `pytest` (unit tests stay offline). Uses a fixed safe question by default; debate defaults to `0` for speed.

```bash
uv run python main.py smoke --preset mock
uv run python main.py smoke --preset ollama-qwen
uv run python main.py smoke --preset openai-mini
uv run python main.py smoke --preset openrouter-sonnet

# Overrides
uv run python main.py smoke --preset ollama-qwen --question "Your question?"
uv run python main.py smoke --preset openai-mini --timeout-seconds 90 --debate-rounds 0
```

Reports provider, model, elapsed time, run paths, decision summary, and quality-field presence. Exit `0` on success, `1` on failure. Secrets are never printed.

### Secrets (OS keyring)

Store API keys locally without putting them in `.env`, config files, or run artifacts. Environment variables override keyring values.

```bash
uv run python main.py secrets set OPENAI_API_KEY   # secure prompt (getpass)
uv run python main.py secrets get OPENAI_API_KEY   # reports set/not set only
uv run python main.py secrets list                 # supported names + source
uv run python main.py secrets delete OPENAI_API_KEY
```

Supported names: `OPENAI_API_KEY`, `LLM_API_KEY`. Values are never printed by CLI, doctor, or errors.

### Config profiles (`.dcouncil/config.toml`)

Non-secret profiles stored project-locally. API keys use env or keyring only — never written to config.

Precedence: defaults → `active_profile` → `--profile` → `--preset` → CLI flags; credentials: env → keyring.

```bash
uv run python main.py config init
uv run python main.py config use ollama-local
uv run python main.py run "Your question"              # uses active profile
uv run python main.py run "Your question" --profile mock
```

Legacy (still supported):

```bash
uv run python main.py "Your question" --preset mock
uv run python main.py --list-presets
```

### Run options

```bash
uv run python main.py run "Your question" --runs-dir ./runs
uv run python main.py run "Your question" --quiet
uv run python main.py run "Your question" --save-prompt-debug
uv run python main.py run "Your question" --debate-rounds 2
uv run python main.py run "Your question" --debate-rounds 0
uv run python main.py run "Your question" --timeout-seconds 120 --max-retries 1
uv run python main.py run "Your question" --fast
```

- `--debate-rounds N` — debate rounds before chair (default `2`; `0` skips; `--fast` forces `0`)
- `--timeout-seconds` — per-request LLM timeout (default `120`; live providers only)
- `--max-retries` — API retry count on failure (default `0`)
- `--fast` — skip debate, concise prompts, labeled in output
- `--quiet` — artifact paths only; suppresses progress lines
- `--save-prompt-debug` — writes `runs/<run_id>/prompt_debug.md` (no secrets)

### Mock with debate (no API key)

```bash
uv run python main.py "Should we build an internal council tool first?" --preset mock --debate-rounds 2
```

### Ollama with debate (local)

```bash
# Requires Ollama running with model pulled, e.g. ollama pull qwen2.5:7b
uv run python main.py "Your decision question" --preset ollama-qwen --debate-rounds 2
```

### Debate disabled

```bash
uv run python main.py "Your decision question" --preset mock --debate-rounds 0
```

## Run artifacts

Each council run is saved under `runs/<run_id>/`:

| File | Purpose |
|------|---------|
| `run.json` | Structured record (`schema_version` 1.4, agent briefs, `debate_transcript`, dossier) |
| `run.md` | Human-readable dossier with Debate Transcript (when rounds > 0), Chair Judgment, evidence sections |
| `prompt_debug.md` | Optional prompt capture when `--save-prompt-debug` is set |
| `comparisons/<id>/comparison.json` | Multi-preset/profile comparison (from `compare` / `benchmark`) |
| `comparisons/<id>/comparison.md` | Human-readable comparison report |

## Provider contract

- `LLMProvider.complete(ProviderRequest) → ProviderResponse` is the integration point.
- Supported modes: `mock`, `openai`, `openai_compatible`.
- `openai` and `openai_compatible` share `OpenAICompatibleProvider` (Responses API + strict JSON schema).
- `openai_compatible` sets `metadata.provider_name` from `LLM_PROVIDER_NAME`.
- API keys and endpoint secrets are never written to logs, artifacts, or error messages.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

## Troubleshooting (providers)

Controlled provider errors print a **single clean message** (no Python traceback). Use `--quiet` for a one-line error on stderr.

| Symptom | What to do |
|---------|------------|
| `OpenAI authentication failed. Check OPENAI_API_KEY.` | Invalid/missing key for `LLM_MODE=openai`. |
| `{provider} authentication failed. Check LLM_API_KEY.` | Invalid/missing key for `openai_compatible` (e.g. OpenRouter). |
| `Missing required setting LLM_BASE_URL` | Set base URL for compatible mode. |
| `Missing required setting LLM_MODEL` | Set model id for compatible mode. |
| Rate limit / network messages | Retry, check connectivity, or use a lower-cost model. |
| Other API call failed | Re-run with `--save-prompt-debug` (secrets redacted). |

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

## Testing

Tests are isolated from your shell environment and `.env` provider settings:

- Autouse fixtures clear `LLM_MODE`, API keys, compatible-provider URLs, and use an in-memory keyring before each test.
- The `smoke` command is manual-only; tests patch `run_smoke` / `run_council` and never call live providers.
- `Settings.from_env()` is pinned to mock mode during tests unless a test opts into `real_settings_from_env`.
- Live OpenAI/Ollama/OpenRouter network calls are blocked; provider unit tests pass `client=mock_client`.
- Use the `mock_settings` fixture (or `run_mock_council`) when calling `run_council` explicitly.

```bash
uv run pytest
```

## Validate

```bash
uv run python -m compileall main.py council tests
uv run pytest
uv run ruff check .
uv run mypy council main.py
```

Type-check `council` and `main.py` only. `uv run mypy .` is not supported because pytest package discovery conflicts with the `tests/` tree; use the scoped command above.

## Build order

See [docs/BUILD_ORDER.md](docs/BUILD_ORDER.md). Next up: Slice 5 (Anthropic/Gemini native SDK providers).
