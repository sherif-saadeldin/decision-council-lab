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

### Agent brief fields

Each brief includes: `role`, `headline`, `reasoning`, `confidence`, `source_refs`.

### Supported modes (Slice 2)

| Mode | Implementation | Notes |
|------|----------------|-------|
| `mock` | `MockProvider` | Deterministic, no API key |
| `openai` | `OpenAIProvider` | Responses API + strict JSON schema |

Other `LLM_MODE` values raise `UnsupportedProviderModeError`. Missing `OPENAI_API_KEY` for OpenAI mode raises `MissingProviderCredentialError`.

### OpenAI provider (Slice 2)

- Uses `openai` Python SDK (`client.responses.create`)
- Structured JSON via `text.format.type = json_schema` with `strict: true`
- Parses agent briefs and chair dossiers with Pydantic guards
- Raises `ProviderResponseError` on malformed model output (no silent fallback)
- Streaming disabled (`supports_streaming = false`)
- API keys are redacted from error messages and never stored in artifacts

## Run artifacts

- `schema_version` **1.1** on `CouncilRunResult`
- `provider_metadata` and `provider_responses` included in JSON
- Markdown reflects dossier spec section order and agent brief summaries
