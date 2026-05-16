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
Debate Loop (optional, default 2 rounds: Advocate / Skeptic / Moderator per round)
    ↓
Chair/Judge Agent (provider.synthesize_dossier + debate transcript)
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
| `run_debate_round(...)` | One structured debate round (advocate, skeptic, moderator) |
| `generate_brief(...)` | Convenience wrapper over `complete()` |

### Request / response models

- **ProviderRequest** — `role`, `question`, `prior_briefs`, optional `run_id`
- **ProviderResponse** — `brief`, optional `token_usage`, `latency_ms`, `raw_response`
- **raw_response** is persisted in `run.json` only; Markdown dossiers omit it.

### Agent brief fields (schema 1.5)

Each brief includes: `role`, `headline`, `role_specific_finding`, `evidence_basis`, `uncertainty`, `decision_implication`, `reasoning`, `confidence`, `source_refs`, `evidence_gaps`, `proposed_metrics`, `unsupported_assumptions`.

Chair dossiers add: `decision_type`, `disagreement_resolution`, `strongest_argument_for`, `strongest_argument_against`, `deciding_factor`, `confidence_rationale`, plus consolidated guardrail fields.

`CouncilRunResult` also includes optional `debate_transcript` (`DebateRound` list with advocate, skeptic, risk officer, moderator per round) and `role_assignments` for multi-model runs.

Shared prompts: `council/prompts.py` (agents + chair), `council/debate_prompts.py` (debate rounds).

Debate orchestration: `council/debate.py`.

### CLI shell (Slice 4.2)

Subcommands via `main.py` (legacy positional question still maps to `run`):

| Command | Purpose |
|---------|---------|
| `run QUESTION` | Full council pipeline + artifacts |
| `presets` | List model presets (`--list-presets` legacy alias) |
| `doctor` | Config checks; `--preset`, optional `--live` init only |
| `version` | App + schema version |
| `config` | Local profiles in `.dcouncil/config.toml` (no secrets) |
| `secrets` | OS keyring for `OPENAI_API_KEY` / `LLM_API_KEY` |
| `compare` / `benchmark` | Same question across multiple presets/profiles; comparison report |
| `setup` | Interactive first-run wizard; optional `--non-interactive --profile NAME` |
| `council` | Multi-model council: per-role preset routing, cross-model debate, optional implementation pack |
| `runs` | `runs list` (last 10) and `runs show RUN_ID` — inspect artifacts without dumping full markdown |

Runtime flags on `run`: `--timeout-seconds`, `--max-retries`, `--fast`, `--debate-rounds`, `--quiet` (suppresses progress).

### Multi-model council (Slice 5.2)

```text
Question + role presets (--council-presets or --*-preset)
    ↓
build_council_routing() → RoleAssignment per slot (metadata from presets)
    ↓
Agent briefs (researcher, skeptic, risk, operator) — each via provider_for_slot()
    ↓
run_multi_model_debate() — advocate ↔ skeptic, risk challenges both (per round)
    ↓
Chair (separate preset) — synthesize_dossier resolves disagreements
    ↓
run.json / run.md (+ role_assignments table)
    ↓
Optional implementation pack (template markdown, no extra LLM calls)
    ↓
runs list | runs show RUN_ID
```

Modules: `council/role_routing.py`, `council/council_session.py`, `council/multi_debate.py`, `council/debate_runner.py`, `council/implementation_pack.py`, `council/council_markdown.py`, `council/run_catalog.py`.

### Cost-aware routing (Slice 5.4)

| Module | Role |
|--------|------|
| `council/routing_modes.py` | `economy` / `balanced` / `premium` / `manual` slot maps and debate defaults |
| `council/preset_economics.py` | Per-preset `cost_tier`, `estimated_cost_per_call_usd`, role fit, local/free flags |
| `council/costing.py` | LLM call counting, USD estimates, `--max-cost-usd` / `--max-llm-calls` enforcement |

Before a council run: `plan_council_session()` builds routing + cost estimate. `--dry-run-cost` prints the table and exits. Budget caps block unless `--allow-over-budget`.

Explicit `--council-presets` or `--*-preset` flags keep your slot assignment; routing mode still sets debate defaults unless `--debate-rounds` is set.

### Council usability (Slice 5.3)

- **run.md (council):** `Council Session Summary` → role/model table → debate rounds → chair verdict → implementation pack files → next suggested `runs show` command.
- **Implementation pack:** six markdown files with project name, question, source `run_id`, scope boundaries, assumptions, acceptance criteria, and explicit approval gates per artifact.
- **CLI:** council completion prints run ID, verdict, confidence, role-play warning, artifact paths, and `uv run python main.py runs show <run_id>`.
- **Run catalog:** `list_recent_runs()` scans `runs/<id>/run.json`; distinguishes `council` vs `standard` via `council_mode` / `role_assignments`.

`CouncilRunResult` schema 1.5 fields: `council_mode`, `multi_model`, `role_play_warning`, `role_assignments`, optional `debate_transcript.risk_officer`.

### Setup wizard (Slice 5.1)

`python main.py setup` guides provider → preset → profile → optional keyring secret → `.dcouncil/config.toml` → optional doctor/smoke.

| Layer | Role |
|-------|------|
| **Presets** | Built-in routing (`council/model_presets.py`); wizard lists relevant presets per provider |
| **Profiles** | Named entries in `.dcouncil/config.toml` (`mode`, `provider_name`, `model`, or `preset = "..."`) |
| **Secrets** | `OPENAI_API_KEY` / `LLM_API_KEY` via env or OS keyring only — never in config |
| **Doctor** | Validates credential source and config; optional after setup |
| **Smoke** | End-to-end check with `debate_rounds=0`, `timeout_seconds=60`; default for mock/Ollama when doctor passes |

Resolution at run time is unchanged: active profile → optional `--preset` → CLI flags; env beats keyring for secrets.

Progress stages: context → research → skeptic → risk → operator → debate round N → chair → storage.

### Config profiles (Slice 4.3)

- File: `.dcouncil/config.toml` (project-local, gitignored)
- Commands: `config init`, `config list`, `config show PROFILE`, `config use PROFILE`
- Profiles hold mode/provider/model/runtime fields — never API keys
- Presets remain built-in routing shortcuts; profile may reference `preset = "openai-mini"`
- Resolution: defaults → active profile → `--profile` → `--preset` → CLI flags; secrets from env (highest) or OS keyring

### Secrets (Slice 4.4)

- Module: `council/secrets.py` — `OPENAI_API_KEY`, `LLM_API_KEY` via OS keyring (`keyring` package)
- CLI: `secrets set|get|list|delete NAME` — `set` uses `getpass`; `get`/`list`/`doctor` never print values
- Resolution: environment variable → keyring → `MissingProviderCredentialError`
- Never stored in `.dcouncil/config.toml`, presets, or run artifacts
- `prompt_debug` redacts resolved secrets passed from settings

### Run comparison (Slice 4.5)

- Module: `council/compare.py` — sequential multi-target runs with per-target failure capture
- CLI: `compare` and `benchmark` (alias); `--presets` and/or `--profiles` (comma-separated)
- Each successful run saves normal artifacts under `runs/<run_id>/`
- Comparison artifacts: `runs/comparisons/<comparison_id>/comparison.json` and `comparison.md`
- Report: per-target success/failure, dossier summary fields, disagreement notes, rule-based evaluator (no extra LLM call)
- Provider failures are recorded safely; comparison continues unless config is invalid upfront
- Secrets redacted from failure messages; never written to comparison artifacts

### JSON repair and debug (Slice 4.7)

- `council/json_extract.py` — fenced-block and first-object JSON extraction before validation
- Parse failures write redacted `runs/<run_id>/raw_response.txt`
- `--repair-json` — one stricter retry (openai_compatible only) after parse failure
- Smoke reports `failure_reason` categories

### API mode fallback (Slice 4.8)

- `council/providers/api_mode.py` — preference `responses` | `chat` | `auto` (default `auto`)
- `OpenAICompatibleProvider` uses Responses API with strict `json_schema` when available
- `auto`: try Responses once per run, fall back to `/v1/chat/completions` on compatible failures (Ollama, 404, etc.); locks to chat for remaining stages in that run
- `chat`: system+user messages with JSON schema in system prompt; same parsing/repair path
- `ProviderMetadata.api_mode_preference` / `api_mode_used` recorded on runs; `doctor` and `smoke` report them

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
- Secrets never stored in presets — `OPENAI_API_KEY` / `LLM_API_KEY` from env or keyring
- Unknown preset → `UnknownModelPresetError` (clean CLI error, no traceback)

### Ollama presets (Slice 3.2)

- Reuse `openai_compatible` + `OpenAICompatibleProvider` with `base_url=http://localhost:11434/v1`
- Presets: `ollama-qwen`, `ollama-qwen35`, `ollama-qwen3`, `ollama-qwen25`, `ollama-mistral`, `ollama-llama3`, `ollama-deepseek-coder`
- `LLM_API_KEY=ollama` is sufficient (dummy); defaults to `ollama` when unset for Ollama presets
- Preset model strings must exactly match `ollama list` NAME column — edit `council/model_presets.py` if yours differ

### OpenAI-compatible stack (Slice 2 + 3)

- Shared implementation: `OpenAICompatibleProvider` in `council/providers/openai_compatible.py`
- `OpenAIProvider` subclasses/wraps it for backward-compatible `LLM_MODE=openai`
- Uses `openai` Python SDK with optional `base_url` for third-party gateways
- Structured JSON via Responses API + strict `json_schema`
- Safe errors via `openai_errors.py` (provider-name-aware, no raw payloads or key fragments)
- Streaming disabled (`supports_streaming = false`)

## Run artifacts

- `schema_version` **1.4** on `CouncilRunResult`
- `debate_transcript` when `--debate-rounds` > 0
- optional `prompt_debug.md` per run when CLI flag is set
- `provider_metadata` and `provider_responses` included in JSON
- Markdown: Debate Transcript (if present), Chair Judgment, evidence sections, agent briefs
