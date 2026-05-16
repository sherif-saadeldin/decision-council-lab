# Architecture

## Flow

```text
User Question
    ↓
Provider Factory (LLM_MODE → LLMProvider)
    ↓
Context Agent ──► ProviderRequest / ProviderResponse
    ↓
Research Agent
Skeptic Agent
Risk Agent
Operator Agent
    ↓
Chair/Judge Agent (provider.synthesize_dossier)
    ↓
Decision Dossier + Agent Briefs
    ↓
Persistent Run Storage (JSON + Markdown)
```

## Provider contract (Slice 1.2+)

All LLM backends implement `LLMProvider`:

| Surface | Purpose |
|---------|---------|
| `metadata` | `provider_name`, `model_name`, `mode`, capability flags |
| `complete(ProviderRequest)` | Role-aware call returning `ProviderResponse` |
| `synthesize_dossier(...)` | Chair synthesis into `DecisionDossier` |
| `generate_brief(...)` | Convenience wrapper over `complete()` |

### Request / response models

- **ProviderRequest** — `role`, `question`, `prior_briefs`, optional `run_id`
- **ProviderResponse** — `brief`, optional `token_usage`, `latency_ms`, `raw_response`
- **raw_response** is persisted in `run.json` only; Markdown dossiers omit it.

### Agent brief fields (schema 1.2)

Each brief includes: `role`, `headline`, `role_specific_finding`, `evidence_basis`, `uncertainty`, `decision_implication`, `reasoning`, `confidence`, `source_refs`.

Chair dossiers add: `decision_type`, `disagreement_resolution`, `strongest_argument_for`, `strongest_argument_against`, `deciding_factor`, `confidence_rationale`.

Shared prompts: `council/prompts.py` (used by mock and OpenAI).

### Supported modes (Slice 3)

| Mode | Implementation | Notes |
|------|----------------|-------|
| `mock` | `MockProvider` | Deterministic, no API key |
| `openai` | `OpenAIProvider` | OpenAI direct (`OpenAICompatibleProvider` wrapper) |
| `openai_compatible` | `OpenAICompatibleProvider` | Any OpenAI-compatible HTTP API |

Other `LLM_MODE` values raise `UnsupportedProviderModeError`.

Configuration errors:

- `OPENAI_API_KEY` required for `openai` mode
- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` required for `openai_compatible` mode
- `LLM_PROVIDER_NAME` sets metadata `provider_name` (e.g. `openrouter`, `groq`)

### Model presets (Slice 3.1)

- Registry: `council/model_presets.py`
- CLI: `--preset NAME` overrides `LLM_MODE`, provider name, base URL, and model
- CLI: `--list-presets` prints preset table (no API keys required)
- Secrets never stored in presets — `OPENAI_API_KEY` / `LLM_API_KEY` from env only
- Unknown preset → `UnknownModelPresetError` (clean CLI error, no traceback)

### Ollama presets (Slice 3.2)

- Reuse `openai_compatible` + `OpenAICompatibleProvider` with `base_url=http://localhost:11434/v1`
- Presets: `ollama-qwen`, `ollama-phi`, `ollama-gemma`, `ollama-deepseek-coder`
- `LLM_API_KEY=ollama` is sufficient (dummy); defaults to `ollama` when unset for Ollama presets
- Model tags in presets are editable defaults — local `ollama list` names may differ

### OpenAI-compatible stack (Slice 2 + 3)

- Shared implementation: `OpenAICompatibleProvider` in `council/providers/openai_compatible.py`
- `OpenAIProvider` subclasses/wraps it for backward-compatible `LLM_MODE=openai`
- Uses `openai` Python SDK with optional `base_url` for third-party gateways
- Structured JSON via Responses API + strict `json_schema`
- Safe errors via `openai_errors.py` (provider-name-aware, no raw payloads or key fragments)
- Streaming disabled (`supports_streaming = false`)

## Run artifacts

- `schema_version` **1.2** on `CouncilRunResult`
- optional `prompt_debug.md` per run when CLI flag is set
- `provider_metadata` and `provider_responses` included in JSON
- Markdown reflects dossier spec section order and agent brief summaries
