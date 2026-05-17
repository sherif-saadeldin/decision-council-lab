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

## Application boundaries (Slice 6.1)

Slice 6.1 keeps the Python CLI product intact while inserting explicit
application boundaries so future chat, web, or desktop surfaces do not need to
know filesystem and orchestration details.

```text
CLI / Chat / future UI
    ↓ input parsing only
Application services (`council/services/*`)
    ↓ orchestration + use-case requests
Domain engine (`engine.py`, `council_session.py`, `intake.py`, `review.py`)
    ↓ result models
RunStore (`council/storage/run_store.py`)
    ↓ current implementation
Filesystem runs (`runs/<id>/run.json`, `run.md`, pack files)

Renderers (`council/rendering/*`) sit beside the flow:
service result → renderer → Rich Console / future UI component
```

Boundary rules:

- `chat.py` coordinates session flow, but should not directly mutate run
  storage for new features.
- Services do not import Rich or write to consoles.
- Renderers build presentation only; they do not call providers, mutate
  lifecycle state, or save artifacts.
- Storage does not know about CLI/chat commands.
- Future UI work should call services and `RunStore`, not scrape `run.md` or
  shell out to chat internals.

New service modules:

| Module | Responsibility |
| --- | --- |
| `council/services/council_service.py` | Standard council runs, chat council bridge, multi-model council persistence |
| `council/services/review_service.py` | Approve/reject/archive/revision use cases |
| `council/services/run_service.py` | Load, summarize, list, and thread-query runs |
| `council/services/pack_service.py` | Lifecycle-gated implementation pack generation |
| `council/services/intake_service.py` | Console-free intake transitions |
| `council/services/provider_service.py` | Provider failure recovery planning |
| `council/sources/service.py` | Local source pack scan/list/show/remove and context building |
| `council/sources/relevance.py` | Deterministic question-aware scoring and ranking |
| `council/sources/source_context_builder.py` | Ranked snippet selection, caps, dedupe, explainability |

`council/storage/run_store.py` defines the `RunStore` protocol and
`FileRunStore`. JSON/Markdown files remain the storage format; the seam exists
so later web/desktop work can use the same core without baking in filesystem
layout assumptions.

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

### System prompt architecture (Slice 5.5.2)

Role identity and council philosophy live in markdown under `council/system_prompts/`:

| File | Role |
|------|------|
| `base.md` | Global council behavior |
| `researcher.md` | Research agent |
| `advocate.md` | Debate advocate |
| `skeptic.md` | Skeptic agent / debate skeptic |
| `risk.md` | Risk agent |
| `operator.md` | Operator agent |
| `chair.md` | Chair philosophy |

Profiles in `council/system_profiles/*.toml` map runtime roles to files (default: `default.toml`). `council/prompt_loader.py` loads base + role markdown, caches by path, and composes the final system prompt. Programmatic constraints (JSON schemas, evidence guardrails, verdict structure) stay in `council/prompts.py` and `council/debate_prompts.py`.

Each `run.json` records `prompt_metadata`: `system_profile`, `prompt_files`, `prompt_versions`, and `prompt_hash` (bundle checksum). Inspect with `uv run python main.py prompts`. Override profile on runs with `--system-profile default`.

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
| `prompts` | List system prompt files, versions, SHA-256 checksums, and profile bundle hash |
| `chat` | Interactive REPL over existing commands (no TUI); thin shell, no shell execution |

### Chat mode (Slice 5.6)

`council/chat.py` provides a readline-style loop (`chat>` prompt) over the same services as the CLI subcommands. It does not execute shell commands or autonomous code. Natural-language lines prompt for council confirmation; `/council` runs multi-model council with safe operational defaults and preflight degradation when hosted credentials are unavailable; pack generation always requires explicit confirmation. Session state lives in `council/chat_state.py`; rendering helpers live under `council/rendering/`.

### Guided decision conversation (Slice 6.0)

**The conversation layer is the product. The council layer is the engine.** Natural input in chat no longer fires the council directly — it opens a short structured intake.

`council/intake.py` is pure-logic, no I/O:
- `DecisionMode` enum: `fast_answer`, `deep_analysis`, `pressure_test`, `build_plan`, `risk_review`, `execution_roadmap`. Each has a `ModeProfile` mapping to existing routing knobs (`routing_mode`, `debate_rounds`, free-text `slot_emphasis`).
- `DecisionIntake` Pydantic model: `goal`, `context`, `constraints: list[str]`, `risks: list[str]`, `success_definition`, `preferred_mode: DecisionMode | None`, `notes`.
- Flow helpers: `INTAKE_QUESTIONS` (ordered), `next_intake_question`, `apply_intake_answer`, `is_intake_complete`, `parse_mode` (numbers or keywords), `format_intake_summary`, `compose_question_with_intake`.

The chat session ([council/chat.py](council/chat.py)) drives it:

1. **Natural input** — if no intake is active, the first line becomes the goal and the conversation starts. If a follow-up phrase matches a recent run (Slice 5.8), that branch wins first and skips intake.
2. **Subsequent lines** — answer the current question (mode → context → constraints → success → risks → optional notes). Bad mode answers re-ask without advancing.
3. **Summary panel** — once all required fields are answered, render `"Here's my understanding"` and ask `"Ready for me to run the council analysis?"`. Decline → offer `Edit intake first?` → pick a field to edit; second decline discards the draft.
4. **Council run** — the intake is prepended to the chair question via `compose_question_with_intake`, the mode's routing/debate defaults override the session defaults, and the intake is persisted on `CouncilRunResult.intake`.
5. **Human-first result** — `render_chat_verdict_short` shows only direct answer + top 3 reasons + biggest warning + next step. The full panel (`Do next`, `Do not do`, `Approval gate`) is one confirm away: `Want the full council breakdown?`.

New chat commands:

| Command | Behavior |
| --- | --- |
| `/intake` | Show current intake, or start a fresh one |
| `/edit [field]` | Edit a single intake field (goal, mode, context, constraints, success, risks, notes) |
| `/clear-intake` | Discard the in-flight intake draft |
| `/mode [name]` | Show or set the decision mode; affects routing/debate defaults |
| `/summary` | Show the current intake summary |
| `/council <question>` | **Skip intake**; go straight to council. Power-user bypass for direct queries. |

`RUN_SCHEMA_VERSION` bumped **1.9 → 1.10**. `CouncilRunResult.intake: DecisionIntake | None` is optional so non-chat callers (CLI `run`, `council`, `compare`, `smoke`) keep working unchanged — `intake` is `null` on those runs. Council markdown gains a `## Decision Intake` section when the field is set.

### CLI surface for the review lifecycle (Slice 5.10)

Slice 5.9 wired the lifecycle into the chat slash verbs. Slice 5.10 lifts every verb to a first-class `main.py` subcommand so CI, scripts, cron, and other coding tools can govern decisions without an interactive chat session.

| Subcommand | Mirrors chat | Flags |
| --- | --- | --- |
| `approve <run_id>` | `/approve` | `--note "..."`, `--actor NAME`, `--runs-dir PATH` |
| `reject <run_id>` | `/reject` | `--reason "..."` (required), `--actor NAME`, `--runs-dir PATH` |
| `archive <run_id>` | `/archive` | `--note "..."`, `--actor NAME`, `--runs-dir PATH` |
| `review <run_id>` | `/review` | `--runs-dir PATH` |
| `pack <run_id>` | `/pack` | `--allow-unapproved`, `--runs-dir PATH` |

All five route through `ReviewService` / `PackService` so CLI and chat share lifecycle behavior while keeping rendering separate.

Actor resolution is the same as Slice 5.9: `--actor` flag → `DCOUNCIL_REVIEW_ACTOR` env → `USER` / `USERNAME` → `local`. Approving a run whose `is_revision_of` is set still triggers automatic parent supersession; archived runs still refuse further transitions; `pack` honors the same gate as the chat surface (and the same `--allow-unapproved` override). `reject` requires `--reason` at the argparse level so omitting it fails fast with `exit 2`.

Side effect: `main.py "Q"` legacy positional question still routes to `run` — the new verbs were added to `CLI_COMMANDS` so `normalize_argv` recognises them and doesn't capture their first argument as a question.

### Decision review loop & approval lifecycle (Slice 5.9)

Council runs are now governed decision objects with explicit lifecycle states. Three layers:

1. **Model — `council/review_model.py`.** Pure types: `LifecycleState` enum (`draft`, `under_review`, `approved`, `rejected`, `superseded`, `archived`), `ReviewAction` enum for audit history, `ReviewEvent`, and `DecisionReview` (status + actor fields + history list). Helpers: `default_review()` (a fresh draft block), `resolve_actor(explicit)` (free-text local username; pulls from `DCOUNCIL_REVIEW_ACTOR` → `USER` → `USERNAME` → `local` — no auth system), `is_pack_allowed(review, override)`.

2. **Storage — `council/review.py`.** Atomic load + mutate + re-render on `runs/<id>/run.json` and `run.md`. Functions: `approve_run`, `reject_run` (reason required), `archive_run`, `mark_revision_of(child, parent)`, `load_run_result`. Approving a run whose `review.is_revision_of` is set automatically transitions the parent to `superseded` and records the forward link in `superseded_by_run_id`. Archive is a soft block: further transitions raise `ReviewTransitionError`.

3. **Surfaces — `council/chat.py`, `council/cli.py`, `council/council_markdown.py`, `council/run_catalog.py`.** New chat commands `/approve`, `/reject`, `/revise`, `/review`, `/archive`. `/pack` and the council CLI gate on `is_pack_allowed`; the override flag is `--allow-unapproved-pack` (CLI) / `/pack last --allow-unapproved` (chat). `/runs` adds a `Status` column with color-coded labels (`approved` gets a `[v]` prefix); `/runs show` adds `Status:` / `Revision of:` / `Superseded by:`. Council markdown gains a `## Review Status` section with status, actors, timestamps, supersession links, and full audit history. `/thread` annotates each entry with `\[root]`/`\[child]` + `\[revision]` + lifecycle marker (`\[approved]`, `\[superseded]`, etc.).

After a council run, chat prints `Decision state: draft`, then asks `Approve now? [y/N]`. If accepted, the inline approval flows into the existing `Create implementation pack?` prompt — pack generation is allowed only after approval (or with the explicit override).

`RUN_SCHEMA_VERSION` bumped to **1.9**. `CouncilRunResult.review: DecisionReview` defaults to a draft block; legacy runs (pre-1.9 on disk) surface as `draft` via the run catalog fallback.

### Decision threads & contextual follow-ups (Slice 5.8)

Chat is now a continuous decision workspace. Three layers:

1. **Pure logic — `council/decision_thread.py`.**
   - `looks_like_follow_up(text)` matches a conservative phrase set (`what if`, `make it cheaper`, `what about ...`, `revise that`, `improve it`, `continue`, `reconsider`, etc.).
   - `summarize_for_context(result)` → `DecisionContext` (parent run id, original question, prior direct answer, decision type, top-3 next actions, top-3 do-not-do, approval gate, top-3 evidence gaps). Compact by design — no agent briefs, no debate transcript, no raw payloads.
   - `compose_question_with_context(q, ctx)` prepends a stable `Using previous decision context` block; `derive_thread_id(parent)` anchors first-generation threads on the parent run id and inherits the existing thread id for grandchildren.

2. **Run model — `council/models.py`.** `RUN_SCHEMA_VERSION` bumped to **1.8**. `CouncilRunResult` gains optional `decision_thread: DecisionThreadMeta | None`. `DecisionThreadMeta` carries `parent_run_id`, `thread_id`, and the structured `context_summary`. Persists into `run.json` and renders into council markdown as a `Previous Context Used` section.

3. **Session memory — `council/chat_state.py`.** `ChatSessionState` carries runtime-only fields: `last_question`, `last_direct_answer`, `last_decision_type`, `last_pack_paths`, `last_profile`, `last_routing_mode`, `current_context_run_id`, `current_context`, `current_thread_id`. After every successful council run chat refreshes these fields and anchors the thread id (using the parent thread if continuing, the run's own id if starting fresh).

`/run` is unchanged; `/council` and the natural-language path now both honor `current_context`. When a natural line matches a follow-up phrase AND a `last_run_id` exists, chat asks **"Use previous decision context from <run_id>? [Y/n]"** (default yes). Accepting loads the run from `runs_dir`, summarises it, and threads it into the next request. Topic-change questions never auto-attach (the phrase matcher is conservative on purpose).

New chat commands:

| Command | Behavior |
| --- | --- |
| `/context` | Show the active context: run id, thread id, question, decision type, direct answer, top next actions, do-not-do, approval gate, evidence gaps |
| `/use <run_id>` | Load a previous run from `runs_dir` as the active context. Anchors the thread on that run unless the loaded run already carries one |
| `/forget` | Clear `current_context`, `current_context_run_id`, `current_thread_id`, and all `last_*` fields. Doctor cache and active profile are preserved |
| `/thread` | List every run on the current thread (root + children), oldest first, using `run_catalog.list_thread_runs(runs_dir, thread_id)` |

`runs list` shows a `Thread` column (8-char prefix of `thread_id`); `runs show` adds `Thread:` and `Parent:` lines when present. Standalone runs render the same as before.

### Profile-aware doctor & recovery loop (Slice 5.7)

`/doctor` in chat now reports against the **active config profile** explicitly:

| Field | Source |
|-------|--------|
| Profile | `ChatSessionState.config_profile_name` |
| Provider / mode | `ctx.settings.llm_provider_name`, `llm_mode` |
| Model | profile model / preset / mock model |
| Credential source | `credential_source_for_preset()` or `credential_source(env_var)` |
| API mode | `ctx.runtime.api_mode` |
| Availability | `estimate_preset_availability()` (offline estimate) |

Each `/doctor` invocation updates a `DoctorCacheEntry` on session state (profile name, health rollup `healthy`/`warning`/`failed`, run timestamp, ok/warn/fail counts, latency for live runs). `/status` reads from that cache and never re-runs checks. The cache is invalidated automatically when the active profile changes (manually via `/profile use ...` or via the recovery fallback).

`/doctor` also accepts `--live` and `--live-completion`. Live runs print elapsed time and the configured timeout; raw payloads and credentials are still never printed (existing `live_completion.py` redacts before returning).

Provider failures during `/council`, `/run`, or a natural question are funnelled through `_handle_provider_failure`. `council/recovery.py` classifies the exception into a `ClassifiedFailure` (one of `auth_failure`, `timeout`, `parse_failure`, `network_failure`, `api_failure` (rate-limit promoted), `unknown`) and emits three lines:

```
Reason (auth_failure): missing credential: LLM_API_KEY.
Fix: Set LLM_API_KEY via `uv run python main.py secrets set LLM_API_KEY`, or `/profile mock`.
Try: /profile mock  /setup  /doctor
```

When the failure is hosted-shaped and the user is not already on `mock`, chat offers a single confirmation: **"Fallback to mock profile? [Y/n]"** (default yes). Accepting switches the active profile via `set_active_profile()`, rebuilds the chat context, invalidates the doctor cache, and retries the original action. The fallback is never offered if the user is already on mock, or for non-recoverable shapes.

Modules: `council/recovery.py` (pure classification, no I/O), `council/providers/failures.py` (existing `FailureKind` taxonomy), `council/chat.py` (cache + UX).

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

### Live validation & availability (Slice 5.4.1)

| Surface | Behavior |
|---------|----------|
| `doctor --live-completion` | Minimal JSON ping via `live_ping()`; reports ok/fail without raw payload or keys |
| `council --require-live-providers` | Live ping each hosted preset before run; fails with `HostedProviderUnavailableError` |
| Auto routing guards | Missing `LLM_API_KEY` for hosted chair → mock chair + warning (no network unless `--require-live-providers`) |
| `council --dry-run-cost` | Shows credential source and availability: available / missing key / live unchecked |
| `smoke` auth failures | Sets `auth_failure`, `credential_source` (env/keyring/missing) on report |

Modules: `council/live_completion.py`, `council/provider_availability.py`.

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

## Source packs (Slice 6.2)

Local source context is modeled as `SourcePack` and scanned from filesystem paths
only (CLI-first). Scan output is persisted in `.dcouncil/sources/<id>.json` and
contains redacted summaries/snippets, not raw whole-file dumps.

Safety defaults:

- extension allow-list only (`.md`, `.txt`, `.json`, `.csv`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, `.ts`, `.tsx`)
- symlinks skipped by default
- binary files skipped
- per-file and total-size caps
- common folders ignored (`.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, `build`, `runs`, `.dcouncil`)
- obvious secret-like lines redacted before persistence and prompt injection

CLI and chat attach source context through service-layer requests:
`source_pack_ids` + `source_context_summary`. Council prompt injection is
concise and capped; no embeddings/vector search/PDF/OCR/web scraping are used.

## Deterministic source relevance (Slice 6.3)

Before prompt injection, source files are ranked deterministically using:

- keyword overlap with question/intake/constraints
- filename/path weighting
- heading/title weighting
- extension weighting
- simple term-frequency scoring
- exact phrase boosts
- recency hook placeholder (fixed `0` for now)

Selection is hard-capped by files/snippets/chars with duplicate snippet
suppression and explainability metadata (`score`, `matched_terms`,
`why_selected`). `run.md` now includes a `## Why These Sources Were Prioritized` section with
selected and excluded files.

Embeddings/vector DB are intentionally deferred to keep this slice cheap,
inspectable, and local-first.

## Human-first UX stabilization (Slice 6.4)

Slice 6.4 intentionally avoids adding infrastructure and instead stabilizes
trust and conversation quality:

- **Operational profiles** (`council/runtime_profiles.py`): `offline`, `cheap`, `balanced`, `hosted`.
- **Safe degradation before execution**: when hosted credentials are unavailable, chat/council degrade to offline local reasoning before the run starts.
- **Conversational rendering**: startup, source recaps, and source relevance explanations use user-facing phrasing instead of provider/routing internals.
- **Intake normalization** (`council/intake_normalizer.py`): shorthand fragments are normalized into structured constraints/risks.
- **Strategic relevance weighting**: deterministic ranking boosts strategic docs (`README`, `ARCHITECTURE`, `ROADMAP`, `SPEC`, `BUILD_ORDER`, `PLAN`, `PRODUCT`, `VISION`, `TODO`) and de-prioritizes implementation-noise files (`tests`, prompt files, renderers, cache/generated files) unless explicitly requested.

## Run artifacts

- `schema_version` **1.11** on `CouncilRunResult` (1.8 added `decision_thread`; 1.9 added `review`; 1.10 added `intake`; 1.11 adds `source_pack_ids` and `source_context_summary`)
- `debate_transcript` when `--debate-rounds` > 0
- optional `prompt_debug.md` per run when CLI flag is set
- `provider_metadata` and `provider_responses` included in JSON
- Markdown: Debate Transcript (if present), Chair Judgment, evidence sections, agent briefs
