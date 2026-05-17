# Build Order

Slice 1 — Mock runnable engine
Slice 1.1 — Tighten CLI + docs + run artifact quality
Slice 1.2 — Provider contract hardening
Slice 2 — OpenAI provider (done)
Slice 2.1 — Output quality hardening (done)
Slice 2.2 — Provider error hardening (done)
Slice 2.3 — CLI graceful error handling (done)
Slice 3 — OpenAI-compatible provider abstraction (done)
Slice 3.1 — Model routing presets (done)
Slice 3.2 — Ollama local model presets (done)
Slice 3.3 — Local model quality guardrails (done)
Slice 3.4 — Test isolation + schema consistency hardening (done)
Slice 4 — Research + debate loop (done)
Slice 4.1 — Type hygiene + docs drift cleanup (done)
Slice 4.2 — CLI app shell, doctor, and runtime controls (done)
Slice 4.3 — Config profiles (done)
Slice 4.4 — Secrets management (OS keyring; env overrides) (done)
Slice 4.5 — Run comparison / benchmark (done)
Slice 4.6 — Live-provider smoke harness (done)
Slice 4.7 — Local model JSON repair and debug capture (done)
Slice 4.8 — Chat Completions compatibility fallback (done)
Slice 4.9 — Stabilization: timeouts, Ollama doctor/tags, smoke preflight, hang fixes; Ollama dummy LLM_API_KEY (done)
Slice 5 — Free/cheap hosted provider presets (NVIDIA NIM, Groq, Cerebras, OpenRouter free) (done)
Slice 5.1 — Interactive setup wizard (`setup` command) (done)
Slice 5.2 — Multi-model council session (`council` command, per-role presets, implementation pack) (done)
Slice 5.3 — Council session usability + implementation pack quality (done)
Slice 5.4 — Cost-aware council routing (routing modes, budget flags, cost estimates) (done)
Slice 5.4.1 — Live credential validation + provider availability guard (done)
Slice 5.5 — Verdict quality hardening (done)
Slice 5.5.1 — Direct answer cleanup + runs show readability (done)
Slice 5.5.2 — System prompt architecture (done)
Slice 5.6 — Interactive chat session (done)
Slice 5.6.1 — Stabilization: atomic config writes, duplicate-key guard, /profile, /status, shell-paste guard, bare-slash crash, runs-show path display (done)
Slice 5.7 — Profile-aware doctor and recovery loop (done)
Slice 5.8 — Decision threads and contextual follow-ups (done)
Slice 5.9 — Anthropic/Gemini native SDK providers
Slice 6 — Memory/revisit system
Slice 7 — CLI enhancements
Slice 8 — UI integration later
