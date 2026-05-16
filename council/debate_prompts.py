from __future__ import annotations

from typing import Any

from council.models import AgentBrief, DebateRound, DebateTranscript
from council.prompts import EVIDENCE_GUARDRAILS

_STRING_ARRAY = {"type": "array", "items": {"type": "string"}}

_POSITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "argument": {"type": "string"},
        "cited_roles": _STRING_ARRAY,
        "responds_to_prior": {"type": "string"},
        "uncertainty": {"type": "string"},
    },
    "required": ["argument", "cited_roles", "responds_to_prior", "uncertainty"],
    "additionalProperties": False,
}

_MODERATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resolved_points": _STRING_ARRAY,
        "unresolved_points": _STRING_ARRAY,
        "deciding_tensions": _STRING_ARRAY,
        "evidence_gaps": _STRING_ARRAY,
    },
    "required": ["resolved_points", "unresolved_points", "deciding_tensions", "evidence_gaps"],
    "additionalProperties": False,
}

DEBATE_ROUND_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "advocate": _POSITION_SCHEMA,
        "skeptic": _POSITION_SCHEMA,
        "moderator": _MODERATOR_SCHEMA,
    },
    "required": ["advocate", "skeptic", "moderator"],
    "additionalProperties": False,
}

DEBATE_RULES = (
    "Debate rules (mandatory):\n"
    "- No theatrical rhetoric, personas, or performative disagreement.\n"
    "- Ground every claim in council agent briefs; cite agent roles in cited_roles.\n"
    "- Do not invent citations, studies, metrics, or timelines not in the question or briefs.\n"
    "- State explicit uncertainty in each position's uncertainty field.\n"
    "- Advocate: strongest evidence-based case FOR proceeding (or the leading option).\n"
    "- Skeptic: strongest evidence-based case AGAINST proceeding; must respond to the "
    "advocate's argument this round, not repeat the initial council skeptic brief verbatim.\n"
    "- Moderator: neutral synthesis only—no new arguments.\n"
    "- Use [] for list fields when none apply."
)

ADVOCATE_INSTRUCTIONS = (
    "You are the Debate Advocate. Build the strongest case for proceeding (or adopting "
    "the leading option) using only evidence from council briefs. "
    "In round 1, cite multiple agent roles. In later rounds, respond directly to the "
    "skeptic's prior argument (responds_to_prior)."
)

SKEPTIC_INSTRUCTIONS = (
    "You are the Debate Skeptic. Build the strongest case against proceeding using only "
    "evidence from council briefs. You must respond to the advocate's argument this round "
    "(responds_to_prior). Do not restate your opening position without addressing the advocate."
)

MODERATOR_INSTRUCTIONS = (
    "You are the Debate Moderator. Summarize this round only:\n"
    "- resolved_points: disagreements narrowed or settled this round\n"
    "- unresolved_points: still contested after this round\n"
    "- deciding_tensions: the core tradeoffs the chair must weigh\n"
    "- evidence_gaps: missing facts that block resolution"
)


def debate_round_instructions() -> str:
    return f"{DEBATE_RULES}\n\n{EVIDENCE_GUARDRAILS}\n\nReturn one JSON object with advocate, skeptic, and moderator."


def _format_briefs_for_debate(briefs: list[AgentBrief]) -> str:
    lines = ["Council agent briefs (only permitted evidence sources):"]
    for brief in briefs:
        lines.extend(
            [
                f"\n[{brief.role.value}]",
                f"Finding: {brief.role_specific_finding}",
                f"Evidence: {brief.evidence_basis}",
                f"Uncertainty: {brief.uncertainty}",
                f"Implication: {brief.decision_implication}",
                f"Gaps: {', '.join(brief.evidence_gaps) or '(none)'}",
            ]
        )
    return "\n".join(lines)


def _format_prior_rounds(prior_rounds: list[DebateRound]) -> str:
    if not prior_rounds:
        return "Prior debate rounds: (none — this is round 1)"
    lines = ["Prior debate rounds:"]
    for prior in prior_rounds:
        lines.extend(
            [
                f"\n--- Round {prior.round_number} ---",
                f"Advocate: {prior.advocate.argument}",
                f"Skeptic: {prior.skeptic.argument}",
                f"Moderator unresolved: {', '.join(prior.moderator.unresolved_points) or '(none)'}",
            ]
        )
    return "\n".join(lines)


def format_debate_round_user_prompt(
    *,
    question: str,
    briefs: list[AgentBrief],
    prior_rounds: list[DebateRound],
    round_number: int,
    total_rounds: int,
) -> str:
    return (
        f"Decision question:\n{question}\n\n"
        f"Debate round {round_number} of {total_rounds}.\n\n"
        f"{_format_briefs_for_debate(briefs)}\n\n"
        f"{_format_prior_rounds(prior_rounds)}\n\n"
        "Produce advocate, skeptic, and moderator for this round. "
        "Advocate and skeptic must cite cited_roles from agent briefs and fill responds_to_prior."
    )


def format_debate_transcript_for_chair(transcript: DebateTranscript | list[DebateRound]) -> str:
    if isinstance(transcript, DebateTranscript):
        rounds = transcript.rounds
    else:
        rounds = transcript
    if not rounds:
        return ""
    lines = ["Debate transcript:"]
    for debate_round in rounds:
        lines.extend(
            [
                f"\n## Round {debate_round.round_number}",
                f"\nAdvocate: {debate_round.advocate.argument}",
                f"Cited: {', '.join(debate_round.advocate.cited_roles) or '(none)'}",
                f"Responds to: {debate_round.advocate.responds_to_prior}",
                f"Uncertainty: {debate_round.advocate.uncertainty}",
                f"\nSkeptic: {debate_round.skeptic.argument}",
                f"Cited: {', '.join(debate_round.skeptic.cited_roles) or '(none)'}",
                f"Responds to: {debate_round.skeptic.responds_to_prior}",
                f"Uncertainty: {debate_round.skeptic.uncertainty}",
                f"\nModerator resolved: {', '.join(debate_round.moderator.resolved_points) or '(none)'}",
                f"Moderator unresolved: {', '.join(debate_round.moderator.unresolved_points) or '(none)'}",
                f"Deciding tensions: {', '.join(debate_round.moderator.deciding_tensions) or '(none)'}",
                f"Evidence gaps: {', '.join(debate_round.moderator.evidence_gaps) or '(none)'}",
            ]
        )
    last = rounds[-1]
    lines.append(
        f"\nFinal unresolved disagreements: "
        f"{', '.join(last.moderator.unresolved_points) or '(none recorded)'}"
    )
    return "\n".join(lines)
