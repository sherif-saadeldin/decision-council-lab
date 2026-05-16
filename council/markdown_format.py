from __future__ import annotations

from council.models import AgentBrief, CouncilRunResult, DebateRound


def bullet_section(lines: list[str], heading: str, items: list[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append("_None recorded._")


def format_proposed_metric(item: str) -> str:
    lowered = item.strip().lower()
    if lowered.startswith("proposed:"):
        detail = item.split(":", 1)[1].strip()
        return f"**Proposed:** {detail}"
    return f"**Proposed:** {item.strip()}"


def proposed_metrics_section(lines: list[str], heading: str, items: list[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    if items:
        lines.extend(f"- {format_proposed_metric(item)}" for item in items)
    else:
        lines.append("_None recorded._")


def format_debate_round_markdown(lines: list[str], debate_round: DebateRound) -> None:
    lines.extend(
        [
            f"### Round {debate_round.round_number}",
            "",
            "**Advocate**",
            "",
            debate_round.advocate.argument,
            "",
            f"- **Cites briefs:** {', '.join(debate_round.advocate.cited_roles) or '(none)'}",
            f"- **Responds to:** {debate_round.advocate.responds_to_prior}",
            f"- **Uncertainty:** {debate_round.advocate.uncertainty}",
            "",
            "**Skeptic**",
            "",
            debate_round.skeptic.argument,
            "",
            f"- **Cites briefs:** {', '.join(debate_round.skeptic.cited_roles) or '(none)'}",
            f"- **Responds to:** {debate_round.skeptic.responds_to_prior}",
            f"- **Uncertainty:** {debate_round.skeptic.uncertainty}",
            "",
        ]
    )
    if debate_round.risk_officer is not None:
        lines.extend(
            [
                "**Risk Officer**",
                "",
                debate_round.risk_officer.argument,
                "",
                f"- **Cites briefs:** {', '.join(debate_round.risk_officer.cited_roles) or '(none)'}",
                f"- **Responds to:** {debate_round.risk_officer.responds_to_prior}",
                f"- **Uncertainty:** {debate_round.risk_officer.uncertainty}",
                "",
            ]
        )
    lines.extend(["**Moderator**", ""])
    if debate_round.moderator.resolved_points:
        lines.append("*Resolved this round:*")
        lines.extend(f"- {item}" for item in debate_round.moderator.resolved_points)
        lines.append("")
    if debate_round.moderator.unresolved_points:
        lines.append("*Unresolved this round:*")
        lines.extend(f"- {item}" for item in debate_round.moderator.unresolved_points)
        lines.append("")
    if debate_round.moderator.deciding_tensions:
        lines.append("*Deciding tensions:*")
        lines.extend(f"- {item}" for item in debate_round.moderator.deciding_tensions)
        lines.append("")
    if debate_round.moderator.evidence_gaps:
        lines.append("*Evidence gaps:*")
        lines.extend(f"- {item}" for item in debate_round.moderator.evidence_gaps)
        lines.append("")


def format_role_assignments_markdown(lines: list[str], result: CouncilRunResult) -> None:
    if not result.role_assignments:
        return
    lines.extend(["", "## Multi-Model Council", ""])
    if result.role_play_warning:
        lines.extend([f"> {result.role_play_warning}", ""])
    lines.extend(["| Role | Preset | Provider | Model |", "| --- | --- | --- | --- |"])
    for item in result.role_assignments:
        lines.append(
            f"| {item.slot} | {item.preset} | {item.provider_name} | `{item.model_name}` |"
        )
    lines.append("")


def format_debate_transcript_markdown(lines: list[str], result: CouncilRunResult) -> None:
    transcript = result.debate_transcript
    if transcript is None or not transcript.rounds:
        return
    lines.extend(["", "## Debate Transcript", ""])
    for debate_round in transcript.rounds:
        format_debate_round_markdown(lines, debate_round)
    if transcript.final_unresolved_disagreements:
        lines.extend(["**Unresolved disagreements (final):**", ""])
        lines.extend(f"- {item}" for item in transcript.final_unresolved_disagreements)
        lines.append("")


def format_agent_brief_markdown(
    lines: list[str],
    brief: AgentBrief,
    *,
    model_label: str | None = None,
) -> None:
    role_label = brief.role.value.replace("_", " ").title()
    title = f"### {role_label} Agent"
    if model_label:
        title = f"{title} (`{model_label}`)"
    lines.extend(
        [
            title,
            "",
            f"**Headline:** {brief.headline}",
            f"**Confidence:** {brief.confidence:.0%} ({brief.confidence:.2f})",
            "",
            f"**Role-specific finding:** {brief.role_specific_finding}",
            "",
            f"**Evidence basis:** {brief.evidence_basis}",
            "",
            f"**Uncertainty:** {brief.uncertainty}",
            "",
            f"**Decision implication:** {brief.decision_implication}",
            "",
            f"**Reasoning:** {brief.reasoning}",
            "",
        ]
    )
    if brief.evidence_gaps:
        lines.extend(["**Evidence gaps:**", ""])
        lines.extend(f"- {gap}" for gap in brief.evidence_gaps)
        lines.append("")
    if brief.proposed_metrics:
        lines.extend(["**Proposed metrics:**", ""])
        lines.extend(f"- {format_proposed_metric(metric)}" for metric in brief.proposed_metrics)
        lines.append("")
    if brief.unsupported_assumptions:
        lines.extend(["**Unsupported assumptions:**", ""])
        lines.extend(f"- {item}" for item in brief.unsupported_assumptions)
        lines.append("")
    if brief.source_refs:
        lines.append("**Sources:**")
        lines.extend(f"- {ref}" for ref in brief.source_refs)
        lines.append("")
