from __future__ import annotations

from pathlib import Path

from council.models import CouncilRunResult
from council.preset_economics import get_preset_economics
from council.verdict_quality import decision_label, format_verdict_sections_markdown
from council.markdown_format import (
    bullet_section,
    format_agent_brief_markdown,
    format_debate_round_markdown,
    proposed_metrics_section,
)


def _question_title(question: str, *, max_len: int = 80) -> str:
    text = " ".join(question.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _format_cost_estimate_markdown(
    lines: list[str],
    result: CouncilRunResult,
) -> None:
    estimate = result.cost_estimate
    if estimate is None:
        return
    lines.extend(
        [
            "",
            "## Cost Estimate",
            "",
            f"- **Routing mode:** {estimate.routing_mode}",
            f"- **Debate rounds:** {estimate.debate_rounds}",
            f"- **Planned LLM calls:** {estimate.llm_call_count}",
            f"- **Estimated USD:** ${estimate.estimated_cost_usd:.4f} (estimate only)",
            f"- **Range (low–high):** ${estimate.estimated_cost_usd_low:.4f} – ${estimate.estimated_cost_usd_high:.4f}",
            "",
        ]
    )


def _format_role_table(lines: list[str], result: CouncilRunResult) -> None:
    lines.extend(["", "## Role & Model Assignments", ""])
    if result.routing_mode:
        lines.append(f"**Routing mode:** {result.routing_mode}")
        lines.append("")
    if result.role_play_warning:
        lines.extend([f"> {result.role_play_warning}", ""])
    lines.extend(
        [
            "| Role | Preset | Provider | Model | Cost tier |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in result.role_assignments:
        tier = get_preset_economics(item.preset).cost_tier
        lines.append(
            f"| {item.slot} | {item.preset} | {item.provider_name} | "
            f"`{item.model_name}` | {tier} |"
        )
    lines.append("")


def _format_debate_section(lines: list[str], result: CouncilRunResult) -> None:
    transcript = result.debate_transcript
    if transcript is None or not transcript.rounds:
        lines.extend(["", "## Debate Rounds", "", "_No debate rounds recorded._", ""])
        return
    lines.extend(
        [
            "",
            "## Debate Rounds",
            "",
            f"**Rounds completed:** {transcript.rounds_completed}",
            "",
        ]
    )
    for debate_round in transcript.rounds:
        format_debate_round_markdown(lines, debate_round)
    if transcript.final_unresolved_disagreements:
        lines.extend(["**Unresolved disagreements (final):**", ""])
        lines.extend(f"- {item}" for item in transcript.final_unresolved_disagreements)
        lines.append("")


def _format_chair_verdict(lines: list[str], result: CouncilRunResult) -> None:
    dossier = result.dossier
    confidence_pct = f"{dossier.confidence_score:.0%}"

    lines.extend(
        [
            "",
            "## Chair Verdict",
            "",
            f"**Confidence:** {confidence_pct} ({dossier.confidence_score:.2f})",
            "",
            f"**Executive summary:** {dossier.recommendation}",
            "",
            f"**Deciding factor:** {dossier.deciding_factor}",
            "",
            f"**Disagreement resolution:** {dossier.disagreement_resolution}",
            "",
            f"**Strongest argument for:** {dossier.strongest_argument_for}",
            "",
            f"**Strongest argument against:** {dossier.strongest_argument_against}",
            "",
            f"**Confidence rationale:** {dossier.confidence_rationale}",
            "",
        ]
    )
    format_verdict_sections_markdown(lines, dossier)
    bullet_section(lines, "Evidence Gaps", dossier.evidence_gaps)
    bullet_section(lines, "Kill Criteria", dossier.kill_criteria)
    bullet_section(lines, "Open Questions", dossier.open_questions)
    proposed_metrics_section(lines, "Proposed Metrics", dossier.proposed_metrics)
    bullet_section(lines, "Unsupported Assumptions", dossier.unsupported_assumptions)


def _format_pack_section(lines: list[str], pack_paths: list[Path]) -> None:
    lines.extend(["", "## Implementation Pack", ""])
    if not pack_paths:
        lines.extend(["", "_No implementation pack generated for this run._", ""])
        return
    lines.append("")
    for path in sorted(pack_paths, key=lambda p: p.name):
        lines.append(f"- `{path.name}`")
    lines.append("")


def _format_next_command(lines: list[str], run_id: str) -> None:
    lines.extend(
        [
            "",
            "## Next Suggested Command",
            "",
            "```bash",
            f"uv run python main.py runs show {run_id}",
            "```",
            "",
            "List recent runs:",
            "",
            "```bash",
            "uv run python main.py runs list",
            "```",
            "",
        ]
    )


def format_council_run_markdown(
    result: CouncilRunResult,
    *,
    implementation_pack_paths: list[Path] | None = None,
) -> str:
    dossier = result.dossier
    meta = result.provider_metadata
    confidence_pct = f"{dossier.confidence_score:.0%}"
    decision_label_text = decision_label(dossier.decision_type)
    multi_label = "yes" if result.multi_model else "no (role-play)"
    direct = dossier.direct_answer.strip() or dossier.recommendation.split("\n", 1)[0]

    routing_label = result.routing_mode or "economy"
    lines = [
        f"# Council Session — {_question_title(dossier.decision_question)}",
        "",
        "## Direct Answer",
        "",
        direct,
        "",
        "## Council Session Summary",
        "",
        f"- **Run ID:** `{dossier.run_id}`",
        f"- **Timestamp (UTC):** {dossier.timestamp.isoformat()}",
        f"- **Council mode:** {result.council_mode or 'multi'}",
        f"- **Routing mode:** {routing_label}",
        f"- **Multi-model debate:** {multi_label}",
        f"- **Schema version:** {result.schema_version}",
        "",
        f"**Decision:** {decision_label_text}",
        "",
        f"**Confidence:** {confidence_pct} ({dossier.confidence_score:.2f})",
        "",
        f"**Chair model:** {meta.provider_name} / `{meta.model_name}`",
        "",
        "**Decision question:**",
        "",
        dossier.decision_question,
    ]

    _format_cost_estimate_markdown(lines, result)

    if result.role_assignments:
        _format_role_table(lines, result)

    _format_debate_section(lines, result)
    _format_chair_verdict(lines, result)
    _format_pack_section(lines, implementation_pack_paths or [])
    _format_next_command(lines, dossier.run_id)

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
        format_agent_brief_markdown(lines, brief, model_label=model_label)

    lines.extend(
        [
            "",
            "## Run Metadata",
            "",
            f"- **Provider (chair):** {meta.provider_name}",
            f"- **Model (chair):** {meta.model_name}",
            f"- **Mode:** {meta.mode}",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"
