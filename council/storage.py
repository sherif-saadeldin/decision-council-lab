from __future__ import annotations

import json
from pathlib import Path

from council.config import Settings
from council.models import AgentBrief, CouncilRunResult


def _bullet_section(lines: list[str], heading: str, items: list[str]) -> None:
    lines.extend(["", f"## {heading}", ""])
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append("_None recorded._")


def _format_agent_brief_markdown(lines: list[str], brief: AgentBrief) -> None:
    role_label = brief.role.value.replace("_", " ").title()
    lines.extend(
        [
            f"### {role_label} Agent",
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
    for brief in result.agent_briefs:
        _format_agent_brief_markdown(lines, brief)

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
