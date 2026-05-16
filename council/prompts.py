from __future__ import annotations

from typing import Any

from council.models import AgentBrief, AgentRole
from council.providers.models import ProviderRequest

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
        "source_refs": {"type": "array", "items": {"type": "string"}},
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
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "arguments_for": {"type": "array", "items": {"type": "string"}},
        "arguments_against": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "recommendation": {"type": "string"},
        "confidence_score": {"type": "number", "minimum": 0, "maximum": 1},
        "kill_criteria": {"type": "array", "items": {"type": "string"}},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
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
    ],
    "additionalProperties": False,
}

ROLE_INSTRUCTIONS: dict[AgentRole, str] = {
    AgentRole.CONTEXT: (
        "You are the Context Agent. Define the decision frame: stakeholders, constraints, "
        "timeline, and what 'success' means. Your finding must be specific to the question, "
        "not generic advice."
    ),
    AgentRole.RESEARCH: (
        "You are the Research Agent. Identify viable options, precedents, and evidence. "
        "Cite concrete comparisons or patterns. Avoid vague market platitudes."
    ),
    AgentRole.SKEPTIC: (
        "You are the Skeptic Agent. Stress-test the leading option. Name the weakest "
        "assumptions and what would falsify them."
    ),
    AgentRole.RISK: (
        "You are the Risk Agent. Surface downside scenarios, second-order effects, and "
        "mitigations. Prioritize risks that would change the decision."
    ),
    AgentRole.OPERATOR: (
        "You are the Operator Agent. Describe how this would be executed in the next 2-6 weeks. "
        "Call out dependencies, sequencing, and operational bottlenecks."
    ),
}

AGENT_OUTPUT_RULES = (
    "Return JSON only matching the schema. Every field is required.\n"
    "- role_specific_finding: one crisp sentence unique to your role\n"
    "- evidence_basis: what evidence, analogy, or logic supports the finding\n"
    "- uncertainty: what you are least sure about\n"
    "- decision_implication: how this should influence the final decision\n"
    "- reasoning: 2-4 sentences integrating the above (not a bullet re-list)\n"
    "- confidence: 0.0-1.0 calibrated to uncertainty\n"
    "- source_refs: citations/notes; use [] if none\n"
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
    "6. Include actionable kill_criteria tied to measurable signals.\n"
    "7. recommendation must align with decision_type and deciding_factor.\n"
    "Return JSON only matching the schema."
)


def agent_instructions(role: AgentRole) -> str:
    role_text = ROLE_INSTRUCTIONS.get(
        role,
        "You are a specialist agent in a decision council.",
    )
    return f"{role_text}\n\n{AGENT_OUTPUT_RULES}"


def chair_instructions() -> str:
    return CHAIR_INSTRUCTIONS


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
        "Produce your structured agent brief."
    )


def format_dossier_user_prompt(question: str, briefs: list[AgentBrief]) -> str:
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
                f"Reasoning: {brief.reasoning}",
                f"Sources: {', '.join(brief.source_refs) if brief.source_refs else '(none)'}",
            ]
        )
    lines.append(
        "\nSynthesize the final decision dossier. Resolve conflicts between agents explicitly."
    )
    return "\n".join(lines)
