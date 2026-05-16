# Decision Council Spec

## Required Output (schema 1.3)

### Decision dossier

- Decision question
- Decision type (`proceed` | `proceed_with_constraints` | `pause` | `reject`)
- Disagreement resolution
- Strongest argument for
- Strongest argument against
- Deciding factor
- Confidence rationale
- Assumptions
- Arguments for
- Arguments against
- Risks
- Recommendation
- Confidence score
- Kill criteria
- Next actions
- Open questions
- Evidence gaps
- Proposed metrics (each labeled `proposed:`)
- Unsupported assumptions
- Timestamp
- Run ID

### Agent brief (per specialist)

- Role
- Headline
- Role-specific finding
- Evidence basis
- Uncertainty
- Decision implication
- Reasoning (integrative summary)
- Confidence score
- Source references (citations)
- Evidence gaps
- Proposed metrics (each labeled `proposed:`)
- Unsupported assumptions

## Evidence guardrails

Agents and chair must not invent metrics, timelines, benchmark counts, or thresholds unless provided in the question or prior briefs. Proposed measures belong in `proposed_metrics` with a `proposed:` prefix. Missing facts belong in `evidence_gaps`. Unsupported leaps belong in `unsupported_assumptions`.

## Chair requirements

The Chair/Judge must adjudicate, not merely summarize. It must explicitly resolve disagreements between specialists, align `recommendation` with `decision_type` and `deciding_factor`, and penalize unsupported specificity (lower confidence when evidence gaps are material).
