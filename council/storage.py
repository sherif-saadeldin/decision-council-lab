from __future__ import annotations

import json
from pathlib import Path

from council.config import Settings
from council.council_markdown import format_council_run_markdown
from council.markdown_format import (
    bullet_section,
    format_agent_brief_markdown,
    format_debate_transcript_markdown,
    format_role_assignments_markdown,
    proposed_metrics_section,
)
from council.models import CouncilRunResult


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

    format_debate_transcript_markdown(lines, result)
    format_role_assignments_markdown(lines, result)

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

    bullet_section(lines, "Evidence Gaps", dossier.evidence_gaps)
    proposed_metrics_section(lines, "Proposed Metrics", dossier.proposed_metrics)
    bullet_section(lines, "Unsupported Assumptions", dossier.unsupported_assumptions)

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

    bullet_section(lines, "Assumptions", dossier.assumptions)
    bullet_section(lines, "Arguments For", dossier.arguments_for)
    bullet_section(lines, "Arguments Against", dossier.arguments_against)
    bullet_section(lines, "Risks", dossier.risks)

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

    bullet_section(lines, "Kill Criteria", dossier.kill_criteria)
    bullet_section(lines, "Next Actions", dossier.next_actions)
    bullet_section(lines, "Open Questions", dossier.open_questions)

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

    return "\n".join(lines).rstrip() + "\n"


def _is_council_run(result: CouncilRunResult) -> bool:
    return result.council_mode == "multi" or bool(result.role_assignments)


def save_run(
    result: CouncilRunResult,
    settings: Settings | None = None,
    *,
    implementation_pack_paths: list[Path] | None = None,
) -> tuple[Path, Path]:
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
    if _is_council_run(result):
        md_text = format_council_run_markdown(
            result,
            implementation_pack_paths=implementation_pack_paths,
        )
    else:
        md_text = _format_markdown(result)
    md_path.write_text(md_text, encoding="utf-8")

    return json_path, md_path
