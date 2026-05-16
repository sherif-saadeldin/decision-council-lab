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
Chair/Judge Agent (mock synthesis today)
    ↓
Decision Dossier + Agent Briefs
    ↓
Persistent Run Storage (JSON + Markdown)
```

## Provider contract (Slice 1.2)

All LLM backends implement `LLMProvider`:

| Surface | Purpose |
|---------|---------|
| `metadata` | `provider_name`, `model_name`, `mode`, capability flags |
| `complete(ProviderRequest)` | Role-aware call returning `ProviderResponse` |
| `generate_brief(...)` | Convenience wrapper over `complete()` |

### Request / response models

- **ProviderRequest** — `role`, `question`, `prior_briefs`, optional `run_id`
- **ProviderResponse** — `brief`, optional `token_usage`, `latency_ms`, `raw_response`
- **raw_response** is persisted in `run.json` only; Markdown dossiers omit it.

### Agent brief fields

Each brief includes: `role`, `headline`, `reasoning`, `confidence`, `source_refs`.

Mock mode uses empty `source_refs` and deterministic reasoning text.

### Supported modes

Only `mock` is registered today (`SUPPORTED_LLM_MODES`). Other `LLM_MODE` values raise `UnsupportedProviderModeError`.

## Run artifacts

- `schema_version` **1.1** on `CouncilRunResult`
- `provider_metadata` and `provider_responses` included in JSON
- Markdown reflects dossier spec section order and agent brief summaries
