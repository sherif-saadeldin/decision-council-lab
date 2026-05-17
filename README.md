# Decision Council Lab

Domain-agnostic multi-agent decision council prototype. Specialists research the question, debate in structured rounds (Advocate / Skeptic / Risk / Chair), then the chair produces a decision dossier. Schema 1.11 includes centralized **system prompts** (`council/system_prompts/`), multi-model `council` mode, guided intake, decision threads, review lifecycle, structured verdict fields, optional implementation packs, and local **source packs**.

## Architecture boundary (Slice 6.1)

The app remains Python CLI-first, but orchestration is now routed through an
application service layer:

```text
CLI / Chat / future UI
  -> council/services/*
  -> council engine + domain models
  -> RunStore
  -> filesystem JSON/Markdown artifacts
```

Current boundaries:

- `council/services/` owns use-case orchestration such as council execution,
  review transitions, pack generation, run queries, intake transitions, and
  provider recovery planning.
- `council/storage/run_store.py` defines `RunStore`; `FileRunStore` keeps the
  existing `runs/<id>/run.json` and `run.md` layout.
- `council/rendering/` contains Rich presentation helpers. Services do not use
  Rich and should be callable by future web/desktop surfaces.
- `council/chat_state.py` holds chat session state so `chat.py` can remain a
  coordinator instead of a storage/orchestration god object.

Future UI strategy: keep the Python core and expose service calls to a
TypeScript web/desktop layer later. Do not rewrite the council engine just to
add a UI.

## Setup

```bash
uv sync --extra dev
```

### First setup (recommended)

Run the interactive wizard to create `.dcouncil/config.toml`, set an active profile, optionally store API keys in the OS keyring, and run doctor/smoke checks:

```bash
uv run python main.py setup
```

Non-interactive shortcuts (no prompts):

```bash
uv run python main.py setup --non-interactive --profile mock
uv run python main.py setup --non-interactive --profile ollama-local
uv run python main.py setup --non-interactive --profile openai-mini
uv run python main.py setup --non-interactive --profile openrouter-sonnet
```

Secrets are never written to `config.toml` — use `uv run python main.py secrets set LLM_API_KEY` or `OPENAI_API_KEY` when prompted.

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

## Chat mode (Slice 5.6 through 6.0)

Interactive CLI session — no TUI, no shell execution:

```bash
uv run python main.py chat
```

**The conversation is the product. The council is the engine.** Type naturally. Chat opens a guided intake (goal → mode → context → constraints → success → risks), confirms a summary, then runs the council. Results come back human-first: direct answer + three reasons + biggest warning + next step. The full panel is one keystroke away.

Slash commands: `/intake`, `/edit`, `/clear-intake`, `/mode`, `/summary`, `/sources`, `/source` (`scan/use/show/clear`), `/council` (skip intake), `/run`, `/compare`, `/doctor`, `/presets`, `/setup`, `/runs`, `/show`, `/pack`, `/prompts`, `/profile`, `/status`, `/context`, `/use`, `/forget`, `/thread`, `/approve`, `/reject`, `/revise`, `/review`, `/archive`, `/help`, `/exit`. `/council Q` is the power-user bypass that skips the intake conversation entirely.

## Source packs and relevance (Slice 6.3)

Source packs are local-only context bundles for folder/file inputs. They are scanned safely (text files only), summarized with rule-based logic (no embeddings/vector DB), and attached to council prompts as concise redacted context.

```bash
uv run python main.py sources scan PATH --name my-pack
uv run python main.py sources list
uv run python main.py sources show SOURCE_PACK_ID
uv run python main.py sources query SOURCE_PACK_ID "question"
uv run python main.py sources remove SOURCE_PACK_ID
```

Attach sources to runs:

```bash
uv run python main.py run "Question?" --source-pack SOURCE_PACK_ID
uv run python main.py run "Question?" --source-path ./docs
uv run python main.py council "Question?" --source-pack SOURCE_PACK_ID
```

Deterministic ranking philosophy:

- question-aware scoring uses keyword overlap, filename/path/heading weighting, extension weighting, exact-phrase boosts, and simple frequency scoring
- ranking is deterministic and inspectable (`score`, `matched terms`, `why selected`) for every selected file
- no embeddings/vector DB/RAG framework in this slice by design

Safety defaults:

- supported extensions only: `.md`, `.txt`, `.json`, `.csv`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, `.ts`, `.tsx`
- binary files skipped
- symlinks not followed by default
- max file size + max total source size enforced
- ignored dirs: `.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, `build`, `runs`, `.dcouncil`
- secret-like lines are redacted before persistence

Stored packs live at `.dcouncil/sources/<source_pack_id>.json`. Future web upload should map to the same `SourcePack` model; PDF/OCR/vector search are intentionally deferred.

## Human-first UX stabilization (Slice 6.4)

This slice keeps architecture unchanged and improves trust, tone, and safe behavior:

- new operational profiles: `offline`, `cheap`, `balanced`, `hosted`
- chat startup auto-selects the safest mode and reports it in plain language
- pre-execution safe degradation prevents mid-conversation hosted auth failures
- source explanations now read like analyst notes instead of infrastructure logs
- intake answers normalize shorthand fragments into structured constraints/risks
- deterministic relevance now boosts strategic docs (`README`, `ARCHITECTURE`, `ROADMAP`, `SPEC`, `BUILD_ORDER`, `PLAN`, `PRODUCT`, `VISION`, `TODO`) and de-prioritizes implementation-noise files unless explicitly requested

You can choose an operational profile from the CLI:

```bash
uv run python main.py chat --operational-profile offline
uv run python main.py run "Question?" --operational-profile cheap
uv run python main.py council "Question?" --operational-profile balanced
```

## Cognitive simplicity and source identity UX (Slice 6.5)

Slice 6.5 keeps all capabilities but hides complexity by default:

- chat startup is intentionally minimal (mode + active sources + simple next step)
- `/help` is tiered: default, `/help sources`, `/help lifecycle`, `/help advanced`
- source packs support human aliases (for example `repo`, `startup-plan`) while UUID IDs remain fully supported
- source grounding language is conversational (`I reviewed:` categories) instead of operational
- decision mode labels are humanized for emotional clarity rather than tooling terms

This follows a **hidden complexity** principle: advanced controls remain available via explicit help/status paths, while first-run experience stays calm and focused.

### Guided decision conversation (Slice 6.0)

Example session:

```text
chat> I want to build an AI movie startup.
[dim]Let's think this through together. I'll ask a few quick questions,
summarize what I heard, then run the council.[/dim]
Goal noted: I want to build an AI movie startup.
How should I help you think? (number or name)
  1. Fast answer — Quick verdict. No debate rounds. Lean on local/free presets.
  2. Deep analysis — Multi-model debate. Balanced council; widest perspective.
  3. Pressure test — Stress-test assumptions. Skeptic and risk roles weighted heavier.
  4. Build plan — Concrete next steps. Operator role guides scope and sequencing.
  5. Risk review — Surfaces failure modes and kill criteria. Risk-led.
  6. Execution roadmap — Operator + implementation focus. Encourages pack generation.

chat> 3
What's the relevant context — team, market, stage?

chat> Solo founder, $50k runway, no prior film experience.
What are your main constraints? Think time, money, legal, skills, ...

chat> time, money, solo, no domain network
What does success look like 3–6 months from now?

chat> One paying customer or one partnership LOI.
What's the biggest risk or fear you have about this?

chat> Burnout chasing a market I don't understand.
Anything else I should know? (Press Enter to skip.)

chat>
+-- Decision intake --------------------------+
| Here's my understanding:                    |
|                                             |
| Goal              : I want to build an AI...|
| Mode              : Pressure test           |
| Context           : Solo founder, $50k ...  |
| Constraints       :                         |
|   - time                                    |
|   - money                                   |
|   - solo                                    |
|   - no domain network                       |
| Success criteria  : One paying customer ... |
| Biggest risk      :                         |
|   - Burnout chasing a market I don't ...    |
+---------------------------------------------+
Run council with this context? [Y/n] y
Using guided intake context (goal, constraints, mode).
... council runs (chair prompt carries the structured intake block) ...

+-- Direct Answer ----------------------------+
| Proceed with constraints — validate on a    |
| 30-day paid pilot before any equity moves.  |
|                                             |
| Why:                                        |
|   • Domain-network gap is the deciding fact |
|   • Solo+runway combo favors learning over  |
|     scaling                                 |
|   • A paid pilot turns the risk into a     |
|     bounded experiment                      |
|                                             |
| Biggest warning: Don't pre-commit equity ...|
| Next step: Land one paid pilot conversation |
|                                             |
| Run ID: <run-id>                            |
+---------------------------------------------+
Show full council breakdown? [y/N] n
Decision state: draft
Approve now? [y/N] n
Create implementation pack? [y/N] n
```

Decision modes map to existing routing knobs:

| Mode | Routing | Debate | Council emphasis |
| --- | --- | --- | --- |
| Fast answer | economy | 0 | Cheapest viable chair, no debate |
| Deep analysis | balanced | 2 | Full council, multi-model debate |
| Pressure test | balanced | 2 | Skeptic + risk emphasized |
| Build plan | balanced | 1 | Operator emphasized |
| Risk review | balanced | 1 | Risk-led, kill criteria heavy |
| Execution roadmap | balanced | 1 | Operator + implementation focus |

Intake fields persist on `CouncilRunResult.intake` (schema 1.10). `run.md` gains a `## Decision Intake` section so the chair's reasoning is auditable against the situation the user actually described.

### Profile-aware doctor and recovery loop (Slice 5.7)

`/doctor` reports against the **active config profile**: profile, provider/mode, model, credential source, API mode, and availability. It caches the result on session state so `/status` shows current health without re-running checks.

```text
chat> /doctor
┌── Doctor — active profile ──┐
│ Profile        : mock        │
│ Provider/mode  : mock (mock) │
│ Model          : mock-...    │
│ Credential src : not_required│
│ API mode       : auto        │
│ Availability   : available   │
│ Mode           : preflight   │
└──────────────────────────────┘
... doctor table ...
```

`/doctor --live` and `/doctor --live-completion` run live validation; chat prints elapsed time and the configured timeout. Raw payloads and credentials are never printed.

`/status` shows cached health (`healthy` / `warning` / `failed` / `unchecked`), last-doctor timestamp, and last-failure summary alongside profile, routing, provider, preset, model, credential source, API mode, and last run id.

When a hosted provider fails during `/council`, `/run`, or a natural question, chat classifies the error (`auth_failure`, `timeout`, `parse_failure`, `network_failure`, `api_failure`, `unknown`) and prints a three-line recovery block:

```text
Reason (auth_failure): missing credential: LLM_API_KEY.
Fix: Set LLM_API_KEY via `uv run python main.py secrets set LLM_API_KEY`, or `/profile mock`.
Try: /profile mock  /setup  /doctor

Fallback to mock profile? [Y/n]
```

Accepting switches to the offline `mock` profile, rebuilds chat context, invalidates the doctor cache, and retries the original action. Declining keeps your profile and your session alive — chat survives repeated provider failures without exiting.

### Decision threads and contextual follow-ups (Slice 5.8)

Chat now keeps lightweight session memory across council runs (`last_run_id`, `last_question`, `last_direct_answer`, `last_decision_type`, `last_pack_paths`, `last_profile`, `last_routing_mode`, `current_context_run_id`, `current_thread_id`) so follow-up questions can reuse a prior decision without re-running discovery.

When you type a natural-language line that looks like a follow-up (`what if ...`, `make it cheaper`, `what about GCC?`, `revise that`, `improve it`, `continue`, `reconsider`, ...), chat asks:

```text
Use previous decision context from <run_id>? [Y/n]
```

Accept to attach a compact structured summary of the prior decision — original question, direct answer, decision type, top-3 next actions, top-3 do-not-do, approval gate, top-3 evidence gaps — as a `Using previous decision context` block in the next council prompt. The new run's `run.json` and `run.md` record `parent_run_id`, `thread_id`, and the `context_summary` so the chain is reproducible.

Topic-change questions (no follow-up phrase) never auto-attach — the matcher is conservative on purpose.

New commands:

| Command | Behavior |
| --- | --- |
| `/context` | Show the active decision context (run id, thread id, question, decision type, direct answer, top next actions, do-not-do, approval gate, evidence gaps) |
| `/use <run_id>` | Load any previous run as the active context |
| `/forget` | Clear active context and session memory (active profile and doctor cache preserved) |
| `/thread` | List every run on the current thread, oldest first |

`runs list` adds a `Thread` column; `runs show` adds `Thread:` and `Parent:` lines when present. Standalone runs render unchanged.

### Decision review loop and approval lifecycle (Slice 5.9)

Every council run is now a governed decision object with explicit lifecycle states: `draft`, `under_review`, `approved`, `rejected`, `superseded`, `archived`. New runs start in `draft`. Implementation packs are allowed only for approved runs (or with the explicit `--allow-unapproved-pack` / `--allow-unapproved` override).

After a council run:

```text
Decision state: draft
? Approve now? [y/N] y
Approved by alice.
? Create implementation pack? [y/N] y
Implementation pack written.
```

If you decline the inline approval and then try to generate a pack, chat refuses with a recovery panel:

```text
┌── Pack blocked ──────────────────────────────────────┐
│ Decision is not approved yet.                        │
│ Try one of:                                          │
│   /approve last                                      │
│   /review last                                       │
│ Or override with: /pack <run_id> --allow-unapproved  │
└──────────────────────────────────────────────────────┘
```

New chat commands:

| Command | Behavior |
| --- | --- |
| `/approve <run_id\|last> [note]` | Mark a decision approved (optional note becomes the review reason) |
| `/reject  <run_id\|last> <reason>` | Mark a decision rejected — reason required |
| `/revise  <run_id\|last> [follow-up question]` | Load the parent run as decision context. If a follow-up question is supplied, runs council inline and marks the new run as a revision of the parent |
| `/review  <run_id\|last>` | Show lifecycle state, actors, timestamps, supersession links, and full audit history |
| `/archive <run_id\|last> [note]` | Archive a decision (further review transitions are blocked until manually unarchived) |

When a revision is approved, the parent automatically transitions to `superseded` and links forward via `superseded_by_run_id`. The full audit history (every transition, actor, timestamp, note) lives on `review.history` in `run.json` and as a `## Review Status` section in `run.md`.

`runs list` shows a color-coded `Status` column (approved runs are prefixed with `[v]`); `runs show` adds `Status:` / `Revision of:` / `Superseded by:` lines when present. `/thread` annotates each entry with markers: `[root]`/`[child]` + `[revision]` (if applicable) + lifecycle marker (`[approved]`, `[superseded]`, etc.).

The actor label is local-only (no auth): resolves from `DCOUNCIL_REVIEW_ACTOR` env var → `USER`/`USERNAME` → `local`. Set `DCOUNCIL_REVIEW_ACTOR` if you want a stable name across shell sessions.

### CLI lifecycle commands (Slice 5.10)

Every chat lifecycle verb is now also a `main.py` subcommand for CI / scripts / cron — no interactive chat required:

```bash
uv run python main.py approve <run_id> --note "shipping" --actor alice
uv run python main.py reject  <run_id> --reason "scope too broad" --actor bob
uv run python main.py archive <run_id> --note "EOL"
uv run python main.py review  <run_id>
uv run python main.py pack    <run_id>                  # blocked on draft
uv run python main.py pack    <run_id> --allow-unapproved
```

All five reuse `council/review.py` directly. `reject` requires `--reason`. `--actor` falls back to `DCOUNCIL_REVIEW_ACTOR` / `USER` / `USERNAME` / `local`. Approving a revision still auto-supersedes the parent. `archive` blocks further transitions. `pack` honors the lifecycle gate exactly like chat's `/pack`.

Sample lifecycle:

```bash
$ uv run python main.py council "Should we ship?" --council-presets mock,mock,mock,mock,mock,mock --routing-mode manual --debate-rounds 0 --quiet
runs/<id>/run.json
runs/<id>/run.md
uv run python main.py runs show <id>

$ uv run python main.py review <id>
+-- Decision Review --+
| Status : draft      |
+---------------------+

$ uv run python main.py pack <id>
Decision is not approved yet. Run `uv run python main.py approve <id>` first,
or re-run pack with --allow-unapproved.

$ uv run python main.py approve <id> --note "shipping" --actor alice
Approved <id> by alice.

$ uv run python main.py pack <id>
Implementation pack:
  runs/<id>/mvp_scope.md
  runs/<id>/implementation_plan.md
  ...
```

## System prompts (Slice 5.5.2)

Role identity lives in `council/system_prompts/` (`base.md` + per-role files). Profiles in `council/system_profiles/` map roles to files (only `default` today). JSON schemas and evidence guardrails stay in code (`council/prompts.py`).

```bash
uv run python main.py prompts
uv run python main.py "Your question?" --preset mock --system-profile default
```

Each `run.json` includes `prompt_metadata` (`prompt_files`, `prompt_versions`, `prompt_hash`).

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
| `ollama-qwen` | openai_compatible | ollama | qwen3.5:9b |
| `ollama-qwen35` | openai_compatible | ollama | qwen3.5:9b |
| `ollama-qwen3` | openai_compatible | ollama | qwen3:8b |
| `ollama-qwen25` | openai_compatible | ollama | qwen2.5:7b-instruct |
| `ollama-mistral` | openai_compatible | ollama | mistral:7b |
| `ollama-llama3` | openai_compatible | ollama | llama3:8b |
| `ollama-deepseek-coder` | openai_compatible | ollama | deepseek-coder:6.7b-instruct |
| `nvidia-nemotron` | openai_compatible | nvidia | nvidia/nvidia-nemotron-nano-9b-v2 |
| `nvidia-deepseek` | openai_compatible | nvidia | deepseek-ai/deepseek-r1-distill-qwen-7b |
| `nvidia-qwen` | openai_compatible | nvidia | qwen/qwen-2.5-7b-instruct |
| `groq-llama` | openai_compatible | groq | llama-3.3-70b-versatile |
| `groq-mixtral` | openai_compatible | groq | mixtral-8x7b-32768 |
| `cerebras-qwen` | openai_compatible | cerebras | qwen-3-235b-a22b-instruct-2507 |
| `openrouter-free-qwen` | openai_compatible | openrouter | qwen/qwen3-235b-a22b:free |
| `openrouter-free-deepseek` | openai_compatible | openrouter | deepseek/deepseek-r1-distill-qwen-14b:free |

`--preset` overrides `LLM_MODE` / model-related env defaults. `OPENAI_API_KEY` or `LLM_API_KEY` remains required for live providers (except Ollama — see below).

Model IDs for hosted presets live in `council/model_presets.py` and may change — verify against each provider’s model catalog before running.

### Free/cheap hosted providers (Slice 5)

All use the existing `openai_compatible` path (no native SDKs). Store one shared key as `LLM_API_KEY` (env or keyring):

```bash
uv run python main.py secrets set LLM_API_KEY
uv run python main.py secrets list
```

| Provider | Preset examples | Base URL | Get a key |
|----------|-----------------|----------|-----------|
| NVIDIA NIM | `nvidia-nemotron`, `nvidia-deepseek`, `nvidia-qwen` | `https://integrate.api.nvidia.com/v1` | [build.nvidia.com](https://build.nvidia.com/) |
| Groq | `groq-llama`, `groq-mixtral` | `https://api.groq.com/openai/v1` | [console.groq.com](https://console.groq.com/) |
| Cerebras | `cerebras-qwen` | `https://api.cerebras.ai/v1` | [cloud.cerebras.ai](https://cloud.cerebras.ai/) |
| OpenRouter (free tier) | `openrouter-free-qwen`, `openrouter-free-deepseek` | `https://openrouter.ai/api/v1` | [openrouter.ai/keys](https://openrouter.ai/keys) |

NVIDIA, Groq, and Cerebras use **chat completions** by default (`auto` resolves to `chat`). OpenRouter paid presets may still try Responses API under `auto`.

```bash
# PowerShell — example: Groq
$env:LLM_API_KEY = "your-groq-key"
uv run python main.py doctor --preset groq-llama
uv run python main.py run "Your question?" --preset groq-llama --api-mode chat --debate-rounds 0

# NVIDIA NIM
uv run python main.py doctor --preset nvidia-qwen
uv run python main.py smoke --preset nvidia-nemotron --timeout-seconds 90

# OpenRouter free models
uv run python main.py run "Your question?" --preset openrouter-free-qwen --debate-rounds 0
```

`doctor` checks credential **source** only (env/keyring/missing) and never prints key values. Use `doctor --live` only when you want provider initialization validation.

### Ollama local presets (Slice 3.2)

Uses the existing `openai_compatible` path against Ollama’s OpenAI API (`http://localhost:11434/v1`). No native Ollama SDK.

1. Install [Ollama](https://ollama.com) and pull a model, e.g. `ollama pull qwen3.5:9b`
2. Run `ollama list` and confirm the **NAME** column matches the preset model exactly (e.g. `qwen3.5:9b`, not `qwen2.5:7b`)
3. Set `LLM_API_KEY=ollama` (dummy value — Ollama does not validate it), or omit it (presets default to `ollama`)
4. Run:

```bash
uv run python main.py "Your question" --preset ollama-qwen
```

If smoke or run fails with a model-not-found error, compare the preset model in `council/model_presets.py` to `ollama list` output character-for-character, or override with `LLM_MODEL=<exact-name>` and `openai_compatible` mode.

#### Local model troubleshooting (JSON / Ollama)

Local models often return markdown fences or prose around JSON. The pipeline:

1. **Extracts** fenced ` ```json ` blocks and the first balanced `{...}` object before failing validation.
2. **Saves** redacted raw output on parse failure to `runs/<run_id>/raw_response.txt` for inspection.
3. **Optional repair** — pass `--repair-json` on `run`, `compare`, or `smoke` to retry once (openai_compatible only) with stricter JSON-only instructions.

```bash
uv run python main.py smoke --preset ollama-qwen --repair-json
uv run python main.py run "Your question" --preset ollama-qwen --repair-json --debate-rounds 0
```

`smoke` reports a **failure reason** (`parse_failure`, `timeout`, `network_failure`, `auth_failure`, etc.) without printing secrets. Invalid structures still fail after repair — nothing is silently accepted.

#### API mode (Responses vs chat completions)

Ollama and some gateways implement `/v1/chat/completions` more reliably than the OpenAI Responses API. Use `--api-mode`:

| Mode | Behavior |
|------|----------|
| `auto` (default) | Try Responses API first; fall back once to chat completions on compatible failures. For Ollama, NVIDIA, Groq, and Cerebras, `auto` resolves to `chat` immediately (no Responses probe). |
| `responses` | Responses API only (OpenAI direct, OpenRouter when supported) |
| `chat` | Chat completions only (`/v1/chat/completions`) |

```bash
uv run python main.py smoke --preset ollama-qwen --api-mode auto
uv run python main.py run "Your question" --preset ollama-qwen --api-mode chat --repair-json
uv run python main.py doctor --preset ollama-qwen
```

`doctor` and successful `smoke` runs report **API mode (preference)** and **API mode (used)** (`responses` or `chat`).

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
uv run python main.py sources scan ./docs --name docs-pack
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

Runs **doctor preflight** for live providers (reachability, Ollama `/api/tags`, model match) before any LLM calls. Reports **failed stage** on error (`preflight`, agent stage, `chair`, etc.). Uses `debate-rounds 0` and fast mode by default. Exit `0` on success, `1` on failure. Secrets are never printed.

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

### Multi-model council (Slice 5.2)

Run a council where each role can use a different model preset (real multi-model debate, not one model role-playing every agent):

```bash
uv run python main.py council "Should I build X?"
uv run python main.py council "Should I build X?" \
  --council-presets mock,openrouter-free-qwen,groq-llama,nvidia-nemotron
```

### Cost-aware routing (Slice 5.4)

Default routing is **economy** (cheapest presets, fewer debate rounds). Other modes: `balanced`, `premium`, or `manual` (explicit `--council-presets` / `--*-preset`).

```bash
# Estimate only — no provider calls
uv run python main.py council "Should I build X?" --dry-run-cost

# Auto economy routing (mock/free tiers; chair may use a free hosted preset)
uv run python main.py council "Should I build X?" --routing-mode economy

# Budget guards (estimates from preset metadata — not live billing)
uv run python main.py council "Question?" --routing-mode premium --max-cost-usd 0.05
uv run python main.py council "Question?" --max-llm-calls 6 --max-debate-rounds 1
uv run python main.py council "Question?" --allow-over-budget   # bypass caps
```

| Mode | Behavior |
|------|----------|
| `economy` | Free/cheap presets for all roles; chair may use a stronger free preset; default 0 debate rounds |
| `balanced` | Cheap researcher/operator/risk; medium advocate/skeptic; premium chair; default 1 debate round |
| `premium` | Strongest configured presets; default 2 debate rounds |
| `manual` | Your `--council-presets` or per-role presets (unchanged from 5.2) |

Cost metadata lives in `council/preset_economics.py` (`cost_tier`, `estimated_cost_per_call_usd`). Estimates appear in CLI output and council `run.md`.

### Provider validation (Slice 5.4.1)

```bash
# Initialize provider only (no completion)
uv run python main.py doctor --preset openrouter-sonnet --live

# One minimal live JSON completion (15s timeout; no secrets in output)
uv run python main.py doctor --preset openrouter-sonnet --live-completion

# Council: skip hosted chair when LLM_API_KEY is missing (falls back to mock)
uv run python main.py council "Question?" --routing-mode economy

# Council: require live completion for every hosted preset before run
uv run python main.py council "Question?" --routing-mode balanced --require-live-providers

# Dry-run shows credential source + availability per preset
uv run python main.py council "Question?" --dry-run-cost
```

Automatic routing (`economy`, `balanced`, `premium`) falls back to **mock chair** when a hosted chair preset needs `LLM_API_KEY` but the key is missing — with warning: `Hosted chair unavailable; falling back to mock.` No live network calls unless you pass `--require-live-providers`.

Per-role routing (overrides `--council-presets` when set):

```bash
uv run python main.py council "Question?" \
  --researcher-preset mock \
  --advocate-preset groq-llama \
  --skeptic-preset nvidia-nemotron \
  --risk-preset cerebras-qwen \
  --operator-preset mock \
  --chair-preset openrouter-free-qwen
```

- If only one distinct preset is used, the CLI warns that this is **role-play debate**, not multi-model debate.
- Artifacts include full debate transcript, **model used per role**, and a structured verdict: **Direct Answer** (one clean sentence with stance + main constraint; must not quote the question), decision type, Do Next / Do Not Do, approval gate, evidence gaps, kill criteria.
- After the verdict, you are prompted: `Create implementation pack? [y/N]` — or pass `--create-pack` / `--yes-pack` for non-interactive pack generation (blocked if verdict quality checks fail).
- Council `run.md` leads with **Direct Answer**, then session summary, role/model table, debate rounds, chair verdict, pack listing, and next suggested command.
- `runs show` prints full artifact paths (`Open: …`) for `run.md` and `run.json` without truncating.
- Implementation pack files (Slice 5.3): `mvp_scope.md`, `implementation_plan.md`, `task_breakdown.md`, `cursor_build_prompt.md`, `risk_register.md`, `approval_checklist.md` — each includes source `run_id`, scope boundaries, assumptions, and approval gates.

```bash
uv run python main.py council "Question?" --council-presets mock,mock,mock,mock,mock,mock --debate-rounds 1 --create-pack
uv run python main.py runs list
uv run python main.py runs show <run_id>
```

### Mock with debate (no API key)

```bash
uv run python main.py "Should we build an internal council tool first?" --preset mock --debate-rounds 2
```

### Ollama with debate (local)

```bash
# Requires Ollama running with model pulled, e.g. ollama pull qwen3.5:9b
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
| `run.json` | Structured record (`schema_version` 1.11, agent briefs, `debate_transcript`, dossier, `prompt_metadata`, lifecycle/intake/thread metadata, source relevance metadata) |
| `run.md` | Human-readable dossier with Debate Transcript (when rounds > 0), Chair Judgment, evidence sections |
| `prompt_debug.md` | Optional prompt capture when `--save-prompt-debug` is set |
| `comparisons/<id>/comparison.json` | Multi-preset/profile comparison (from `compare` / `benchmark`) |
| `comparisons/<id>/comparison.md` | Human-readable comparison report |
| `<run_id>/raw_response.txt` | Redacted provider output when JSON parsing fails |

## Provider contract

- `LLMProvider.complete(ProviderRequest) → ProviderResponse` is the integration point.
- Supported modes: `mock`, `openai`, `openai_compatible`.
- `openai` and `openai_compatible` share `OpenAICompatibleProvider` (Responses API + strict JSON schema).
- `openai_compatible` sets `metadata.provider_name` from `LLM_PROVIDER_NAME`.
- API keys and endpoint secrets are never written to logs, artifacts, or error messages.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full contract.

## Troubleshooting (providers)

Controlled provider errors print a **single clean message** (no Python traceback). Use `--quiet` for a one-line error on stderr.

### Stuck or slow Ollama runs

| Symptom | What to do |
|---------|------------|
| Command hangs with no output | Press **Ctrl+C** once and wait a few seconds. If the shell still feels blocked, open a new terminal. |
| Smoke/run never finishes | Run `doctor` first: `uv run python main.py doctor --preset ollama-qwen` — checks `/api/tags`, model name, and reachability. |
| Wrong model name | Doctor lists installed models from Ollama. Match preset model to `ollama list` exactly, or `ollama pull <model>`. |
| Each stage very slow | Lower per-request timeout: `--timeout-seconds 60`. Smoke defaults to `debate-rounds 0` and skips debate in fast mode. |
| JSON repair doubles latency | Omit `--repair-json` unless parsing fails; repair adds one extra LLM call per failed stage. |
| Responses API timeouts | Use `--api-mode chat` (Ollama `auto` already uses chat). |

```bash
uv run python main.py doctor --preset ollama-qwen
uv run python main.py smoke --preset ollama-qwen --api-mode chat --timeout-seconds 60 --debate-rounds 0
```

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

See [docs/BUILD_ORDER.md](docs/BUILD_ORDER.md).
