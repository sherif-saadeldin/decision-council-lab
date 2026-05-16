# Decision Council Dossier

## Executive Summary

Build the decision council engine as an internal tool first.

**Confidence:** 78% (0.78)

## Run Metadata

- **Run ID:** `cc05b62f-7f3e-4531-8e00-79a1da2c4a91`
- **Timestamp (UTC):** 2026-05-16T16:09:54.276085+00:00
- **Schema version:** 1.1
- **Provider:** mock
- **Model:** mock-council-v1

## Decision Question

Should I build a decision council engine as an internal tool first?

## Assumptions

- The team can iterate quickly without external customer commitments.
- Mock-mode council output is sufficient to shape early product direction.
- Provider integration can be swapped in without rewriting agent roles.

## Arguments For

- Internal tooling de-risks architecture before UI or SaaS packaging.
- Structured dossiers create an audit trail for important decisions.
- Agent roles map cleanly to executive decision workflows.

## Arguments Against

- Without real LLM calls, conclusions may feel generic until providers land.
- Manual CLI usage limits adoption until later slice enhancements.
- Parallel specialist debate is simplified in the first mock slice.

## Risks

- Scope creep into SaaS, auth, or trading features before core engine is stable.
- Overfitting mock heuristics to sample questions instead of real reasoning.
- Underestimating effort to add provider parity across vendors.

## Recommendation

Build the decision council engine as an internal tool first.

## Confidence Score

78% (0.78)

## Kill Criteria

- Two consecutive runs fail to produce a complete dossier on representative questions.
- Structured output validation errors exceed an agreed error budget.
- Internal users stop referencing saved run artifacts within one sprint.

## Next Actions

- Run the council on three real product decisions and review dossier quality.
- Wire OpenAI provider in Slice 2 behind the existing provider interface.
- Add tests that lock dossier schema and storage formats.

## Open Questions

- Which decisions require multi-round debate versus a single council pass?
- What retention policy should apply to runs stored on disk?
- When should confidence scores block automated downstream actions?

## Agent Briefs

### Context Agent

**Headline:** Frame the decision as an engineering leverage bet.

- Decision under review: Should I build a decision council engine as an internal tool first?
- Stakeholders likely include product, engineering, and future operators.
- Success means faster, higher-quality decisions with traceable rationale.

### Research Agent

**Headline:** Favor a narrow internal engine before external packaging.

- Comparable systems start as CLI or notebook workflows, then add UI.
- Structured JSON + Markdown artifacts support async review.
- Question emphasis: Should I build a decision council engine as an internal tool first?

### Skeptic Agent

**Headline:** Challenge premature platform expansion.

- A mock council can look complete while hiding weak reasoning quality.
- Adding providers late may require revisiting prompt contracts.
- Internal-only scope should stay enforced until dossier quality is proven.

### Risk Agent

**Headline:** Primary risks are scope drift and false confidence.

- Shipping UI or auth early splits focus from the council core.
- Low-cost mock runs may encourage decisions without human review.
- Missing kill criteria can let weak recommendations persist too long.

### Operator Agent

**Headline:** Operational path: CLI now, providers next, UI last.

- Persist every run under a unique run_id for reproducibility.
- Keep agent roles explicit in code and saved artifacts.
- Use uv-managed workflows for local execution and CI parity.
