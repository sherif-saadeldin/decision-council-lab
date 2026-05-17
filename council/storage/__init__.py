from __future__ import annotations

import json
import os
import tempfile
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
from council.verdict_quality import decision_label, format_verdict_sections_markdown


def _format_standard_markdown(result: CouncilRunResult) -> str:
    dossier = result.dossier
    meta = result.provider_metadata
    confidence_pct = f"{dossier.confidence_score:.0%}"

    direct = dossier.direct_answer.strip() or dossier.recommendation.split("\n", 1)[0]
    lines = [
        "# Decision Council Dossier",
        "",
        "## Direct Answer",
        "",
        direct,
        "",
        "## Executive Summary",
        "",
        dossier.recommendation,
        "",
        f"**Decision:** {decision_label(dossier.decision_type)}",
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
    format_verdict_sections_markdown(lines, dossier)

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
    if result.source_pack_ids or result.source_context_summary.strip():
        lines.extend(["", "## Sources Used", ""])
        for source_pack_id in result.source_pack_ids:
            lines.append(f"- Source pack: `{source_pack_id}`")
        if result.source_context_summary.strip():
            lines.append("- What I reviewed before deciding:")
            for raw_line in result.source_context_summary.splitlines():
                if raw_line.strip():
                    lines.append(f"  - {raw_line.strip()}")
    if result.source_relevance:
        lines.extend(["", "## Why These Sources Were Prioritized", ""])
        for item in result.source_relevance:
            lines.append(f"- `{item.path}`")
            lines.append(f"  - relevance: {item.score:.2f}")
            if item.matched_terms:
                lines.append(f"  - matched themes: {', '.join(item.matched_terms[:8])}")
            if item.why_selected:
                lines.append(f"  - selected because: {', '.join(item.why_selected[:6])}")
        if result.source_excluded_files:
            lines.append("- Skipped to keep the context concise:")
            lines.extend(f"  - {entry}" for entry in result.source_excluded_files[:10])
        if result.source_context_warnings:
            lines.append("- Safety notes:")
            lines.extend(f"  - {entry}" for entry in result.source_context_warnings[:5])

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
    assignment_by_slot = {assignment.slot: assignment for assignment in result.role_assignments}
    for brief in result.agent_briefs:
        slot = slot_by_role.get(brief.role.value)
        model_label = None
        if slot and slot in assignment_by_slot:
            assignment = assignment_by_slot[slot]
            model_label = f"{assignment.provider_name}/{assignment.model_name}"
        format_agent_brief_markdown(lines, brief, model_label=model_label)

    return "\n".join(lines).rstrip() + "\n"


def _is_council_run(result: CouncilRunResult) -> bool:
    return result.council_mode == "multi" or bool(result.role_assignments)


def format_run_markdown(
    result: CouncilRunResult,
    *,
    implementation_pack_paths: list[Path] | None = None,
) -> str:
    if _is_council_run(result):
        return format_council_run_markdown(
            result,
            implementation_pack_paths=implementation_pack_paths,
        )
    return _format_standard_markdown(result)


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def save_run_to_dir(
    result: CouncilRunResult,
    runs_dir: Path,
    *,
    implementation_pack_paths: list[Path] | None = None,
) -> tuple[Path, Path]:
    run_dir = runs_dir / result.dossier.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / "run.json"
    md_path = run_dir / "run.md"

    payload = result.model_dump(mode="json")
    _atomic_write_text(
        json_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    _atomic_write_text(
        md_path,
        format_run_markdown(
            result,
            implementation_pack_paths=implementation_pack_paths,
        ),
    )

    return json_path, md_path


def save_run(
    result: CouncilRunResult,
    settings: Settings | None = None,
    *,
    implementation_pack_paths: list[Path] | None = None,
) -> tuple[Path, Path]:
    settings = settings or Settings.from_env()
    return save_run_to_dir(
        result,
        settings.runs_dir,
        implementation_pack_paths=implementation_pack_paths,
    )
