from __future__ import annotations

import json
from pathlib import Path

from council.config import Settings
from council.models import AgentBrief, CouncilRunResult, DebateRound


def _bullet_section(lines: list[str], heading: str, items: list[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append("_None recorded._")


def _format_proposed_metric(item: str) -> str:
    lowered = item.strip().lower()
    if lowered.startswith("proposed:"):
        detail = item.split(":", 1)[1].strip()
        return f"**Proposed:** {detail}"
    return f"**Proposed:** {item.strip()}"


def _proposed_metrics_section(lines: list[str], heading: str, items: list[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    if items:
        lines.extend(f"- {_format_proposed_metric(item)}" for item in items)
    else:
        lines.append("_None recorded._")


def _format_debate_round_markdown(lines: list[str], debate_round: DebateRound) -> None:
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
    lines.extend(
        [
            "**Moderator**",
            "",
        ]
    )
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


def _format_role_assignments_markdown(lines: list[str], result: CouncilRunResult) -> None:
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


def _format_debate_transcript_markdown(lines: list[str], result: CouncilRunResult) -> None:
    transcript = result.debate_transcript
    if transcript is None or not transcript.rounds:
        return
    lines.extend(["", "## Debate Transcript", ""])
    for debate_round in transcript.rounds:
        _format_debate_round_markdown(lines, debate_round)
    if transcript.final_unresolved_disagreements:
        lines.extend(["**Unresolved disagreements (final):**", ""])
        lines.extend(f"- {item}" for item in transcript.final_unresolved_disagreements)
        lines.append("")


def _format_agent_brief_markdown(
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
        lines.extend(f"- {_format_proposed_metric(metric)}" for metric in brief.proposed_metrics)
        lines.append("")
    if brief.unsupported_assumptions:
        lines.extend(["**Unsupported assumptions:**", ""])
        lines.extend(f"- {item}" for item in brief.unsupported_assumptions)
        lines.append("")
    if brief.source_refs:
        lines.append("**Sources:**")
        lines.extend(f"- {ref}" for ref in brief.source_refs)
        lines.append("")


def _format_markdown(result: CouncilRunResult) -> str:
    dossier = result.dossier
    meta = result.provider_metadata
    confidence_pct = f"{dossier.confidence_score:.0%}"
    decision_type_label = dossier.decision_type.value.replace("_", " ")

    lines = [
        "# Decision Council Dossier",
        "",
        "## Executive Summary",
        "",
        dossier.recommendation,
        "",
        f"**Decision type:** {decision_type_label}",
        f"**Confidence:** {confidence_pct} ({dossier.confidence_score:.2f})",
    ]

    _format_debate_transcript_markdown(lines, result)
    _format_role_assignments_markdown(lines, result)

    lines.extend(
        [
            "",
            "## Chair Judgment",
            "",
            f"**Strongest argument for:** {dossier.strongest_argument_for}",
            "",
            f"**Strongest argument against:** {dossier.strongest_argument_against}",
            "",
            f"**Deciding factor:** {dossier.deciding_factor}",
            "",
            f"**Disagreement resolution:** {dossier.disagreement_resolution}",
            "",
            f"**Confidence rationale:** {dossier.confidence_rationale}",
            "",
        ]
    )

    _bullet_section(lines, "Evidence Gaps", dossier.evidence_gaps)
    _proposed_metrics_section(lines, "Proposed Metrics", dossier.proposed_metrics)
    _bullet_section(lines, "Unsupported Assumptions", dossier.unsupported_assumptions)

    lines.extend(
        [
            "",
            "## Run Metadata",
            "",
            f"- **Run ID:** `{dossier.run_id}`",
            f"- **Timestamp (UTC):** {dossier.timestamp.isoformat()}",
            f"- **Schema version:** {result.schema_version}",
            f"- **Provider:** {meta.provider_name}",
            f"- **Model:** {meta.model_name}",
            f"- **Mode:** {meta.mode}",
            "",
            "## Decision Question",
            "",
            dossier.decision_question,
        ]
    )

    _bullet_section(lines, "Assumptions", dossier.assumptions)
    _bullet_section(lines, "Arguments For", dossier.arguments_for)
    _bullet_section(lines, "Arguments Against", dossier.arguments_against)
    _bullet_section(lines, "Risks", dossier.risks)

    lines.extend(
        [
            "",
            "## Recommendation",
            "",
            dossier.recommendation,
            "",
            "## Confidence Score",
            "",
            f"{confidence_pct} ({dossier.confidence_score:.2f})",
        ]
    )

    _bullet_section(lines, "Kill Criteria", dossier.kill_criteria)
    _bullet_section(lines, "Next Actions", dossier.next_actions)
    _bullet_section(lines, "Open Questions", dossier.open_questions)

    lines.extend(["", "## Agent Briefs", ""])
    slot_by_role = {
        "research": "researcher",
        "skeptic": "skeptic",
        "risk": "risk",
        "operator": "operator",
    }
    assignment_by_slot = {item.slot: item for item in result.role_assignments}
    for brief in result.agent_briefs:
        slot = slot_by_role.get(brief.role.value)
        model_label = None
        if slot and slot in assignment_by_slot:
            item = assignment_by_slot[slot]
            model_label = f"{item.provider_name}/{item.model_name}"
        _format_agent_brief_markdown(lines, brief, model_label=model_label)

    return "\n".join(lines).rstrip() + "\n"


def save_run(result: CouncilRunResult, settings: Settings | None = None) -> tuple[Path, Path]:
    settings = settings or Settings.from_env()
    run_dir = settings.runs_dir / result.dossier.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / "run.json"
    md_path = run_dir / "run.md"

    payload = result.model_dump(mode="json")
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_format_markdown(result), encoding="utf-8")

    return json_path, md_path
