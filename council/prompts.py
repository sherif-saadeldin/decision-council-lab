from __future__ import annotations

from typing import Any

from council.models import AgentBrief, AgentRole, DebateTranscript
from council.providers.models import ProviderRequest

_STRING_ARRAY = {"type": "array", "items": {"type": "string"}}

AGENT_BRIEF_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "role_specific_finding": {"type": "string"},
        "evidence_basis": {"type": "string"},
        "uncertainty": {"type": "string"},
        "decision_implication": {"type": "string"},
        "reasoning": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source_refs": _STRING_ARRAY,
        "evidence_gaps": _STRING_ARRAY,
        "proposed_metrics": _STRING_ARRAY,
        "unsupported_assumptions": _STRING_ARRAY,
    },
    "required": [
        "headline",
        "role_specific_finding",
        "evidence_basis",
        "uncertainty",
        "decision_implication",
        "reasoning",
        "confidence",
        "source_refs",
        "evidence_gaps",
        "proposed_metrics",
        "unsupported_assumptions",
    ],
    "additionalProperties": False,
}

DOSSIER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision_type": {
            "type": "string",
            "enum": ["proceed", "proceed_with_constraints", "pause", "reject"],
        },
        "disagreement_resolution": {"type": "string"},
        "strongest_argument_for": {"type": "string"},
        "strongest_argument_against": {"type": "string"},
        "deciding_factor": {"type": "string"},
        "confidence_rationale": {"type": "string"},
        "assumptions": _STRING_ARRAY,
        "arguments_for": _STRING_ARRAY,
        "arguments_against": _STRING_ARRAY,
        "risks": _STRING_ARRAY,
        "recommendation": {"type": "string"},
        "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
        "kill_criteria": _STRING_ARRAY,
        "next_actions": _STRING_ARRAY,
        "open_questions": _STRING_ARRAY,
        "evidence_gaps": _STRING_ARRAY,
        "proposed_metrics": _STRING_ARRAY,
        "unsupported_assumptions": _STRING_ARRAY,
    },
    "required": [
        "decision_type",
        "disagreement_resolution",
        "strongest_argument_for",
        "strongest_argument_against",
        "deciding_factor",
        "confidence_rationale",
        "assumptions",
        "arguments_for",
        "arguments_against",
        "risks",
        "recommendation",
        "confidence_score",
        "kill_criteria",
        "next_actions",
        "open_questions",
        "evidence_gaps",
        "proposed_metrics",
        "unsupported_assumptions",
    ],
    "additionalProperties": False,
}

EVIDENCE_GUARDRAILS = (
    "Evidence guardrails (mandatory for all agents and chair):\n"
    "- Do NOT invent metrics, percentages, timelines, benchmark counts, revenue figures, "
    "or thresholds unless explicitly provided in the decision question or prior briefs.\n"
    "- If you propose a measurable signal, add it to proposed_metrics starting with 'proposed: '.\n"
    "- List missing information needed to decide in evidence_gaps (use [] if none).\n"
    "- List assumptions you are making without direct support in unsupported_assumptions "
    "(use [] if none).\n"
    "- Prefer qualitative reasoning over fake precision. When uncertain, say so."
)

ROLE_INSTRUCTIONS: dict[AgentRole, str] = {
    AgentRole.CONTEXT: (
        "You are the Context Agent. Define the decision frame: stakeholders, constraints, "
        "and what success means. Your finding must be specific to the question, "
        "not generic advice. Do not invent deadlines or numeric targets unless given."
    ),
    AgentRole.RESEARCH: (
        "You are the Research Agent. Identify viable options, precedents, and evidence. "
        "Cite concrete comparisons or patterns. Avoid vague market platitudes and invented benchmarks."
    ),
    AgentRole.SKEPTIC: (
        "You are the Skeptic Agent. Stress-test the leading option. Name the weakest "
        "assumptions and what would falsify them. Flag unsupported specificity from other agents."
    ),
    AgentRole.RISK: (
        "You are the Risk Agent. Surface downside scenarios, second-order effects, and "
        "mitigations. Do not invent incident rates or SLA percentages without evidence."
    ),
    AgentRole.OPERATOR: (
        "You are the Operator Agent. Describe execution sequencing and dependencies. "
        "Do not invent week counts or team sizes unless provided in the question."
    ),
}

AGENT_OUTPUT_RULES = (
    "Return JSON only matching the schema. Every field is required.\n"
    "- role_specific_finding: one crisp sentence unique to your role\n"
    "- evidence_basis: what evidence, analogy, or logic supports the finding (not invented data)\n"
    "- uncertainty: what you are least sure about\n"
    "- decision_implication: how this should influence the final decision\n"
    "- reasoning: 2-4 sentences integrating the above (not a bullet re-list)\n"
    "- confidence: 0.0-1.0 calibrated to uncertainty\n"
    "- source_refs: citations/notes; use [] if none\n"
    "- evidence_gaps: missing facts needed; use [] if none\n"
    "- proposed_metrics: only proposed measures, each starting with 'proposed: '; use [] if none\n"
    "- unsupported_assumptions: assumptions without evidence; use [] if none\n"
    "Be concrete. Reference the actual decision question."
)

CHAIR_INSTRUCTIONS = (
    "You are the Chair/Judge Agent. You must adjudicate, not summarize.\n"
    "Requirements:\n"
    "1. Explicitly resolve disagreements between specialists (who wins and why).\n"
    "2. Choose decision_type: proceed | proceed_with_constraints | pause | reject.\n"
    "3. Name the single strongest argument for and against.\n"
    "4. State the deciding factor that tips the balance.\n"
    "5. Explain confidence_rationale (why confidence_score is justified).\n"
    "6. Lower confidence when specialists report material evidence_gaps or unsupported_assumptions.\n"
    "7. Penalize unsupported specificity: do not upgrade vague agent claims into precise numbers.\n"
    "8. kill_criteria may only use proposed_metrics (labeled 'proposed:') from council briefs "
    "or your proposed_metrics list—never invent thresholds.\n"
    "9. Consolidate cross-agent evidence_gaps and unsupported_assumptions in dossier fields.\n"
    "10. recommendation must align with decision_type and deciding_factor.\n"
    "11. When a debate transcript is provided, you must weigh advocate vs skeptic exchanges "
    "and the moderator's unresolved points in disagreement_resolution and deciding_factor.\n"
    "Return JSON only matching the schema."
)


def agent_instructions(role: AgentRole) -> str:
    role_text = ROLE_INSTRUCTIONS.get(
        role,
        "You are a specialist agent in a decision council.",
    )
    return f"{role_text}\n\n{EVIDENCE_GUARDRAILS}\n\n{AGENT_OUTPUT_RULES}"


def chair_instructions() -> str:
    return f"{CHAIR_INSTRUCTIONS}\n\n{EVIDENCE_GUARDRAILS}"


def format_agent_user_prompt(request: ProviderRequest) -> str:
    prior_lines: list[str] = []
    for brief in request.prior_briefs:
        prior_lines.append(
            f"- {brief.role.value}: {brief.role_specific_finding} "
            f"(confidence {brief.confidence:.2f}, implication: {brief.decision_implication})"
        )
    prior_section = "\n".join(prior_lines) if prior_lines else "(none yet)"
    return (
        f"Decision question:\n{request.question}\n\n"
        f"Your role: {request.role.value}\n\n"
        f"Prior council briefs:\n{prior_section}\n\n"
        "Produce your structured agent brief. Follow evidence guardrails."
    )


def format_dossier_user_prompt(
    question: str,
    briefs: list[AgentBrief],
    debate_transcript: DebateTranscript | None = None,
) -> str:
    lines = [f"Decision question:\n{question}\n", "Council briefs:"]
    for brief in briefs:
        lines.extend(
            [
                f"\n[{brief.role.value}]",
                f"Headline: {brief.headline}",
                f"Finding: {brief.role_specific_finding}",
                f"Evidence basis: {brief.evidence_basis}",
                f"Uncertainty: {brief.uncertainty}",
                f"Decision implication: {brief.decision_implication}",
                f"Confidence: {brief.confidence:.2f}",
                f"Evidence gaps: {', '.join(brief.evidence_gaps) or '(none)'}",
                f"Proposed metrics: {', '.join(brief.proposed_metrics) or '(none)'}",
                f"Unsupported assumptions: {', '.join(brief.unsupported_assumptions) or '(none)'}",
                f"Reasoning: {brief.reasoning}",
                f"Sources: {', '.join(brief.source_refs) if brief.source_refs else '(none)'}",
            ]
        )
    if debate_transcript and debate_transcript.rounds:
        from council.debate_prompts import format_debate_transcript_for_chair

        lines.extend(["", format_debate_transcript_for_chair(debate_transcript)])
    lines.append(
        "\nSynthesize the final decision dossier. Resolve conflicts explicitly. "
        "Do not invent metrics or timelines. Penalize unsupported specificity."
    )
    return "\n".join(lines)
