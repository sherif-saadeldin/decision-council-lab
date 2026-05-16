from __future__ import annotations

from pathlib import Path

from council.models import CouncilRunResult


def _bullet_lines(items: list[str], *, prefix: str = "- ") -> list[str]:
    if items:
        return [f"{prefix}{item}" for item in items]
    return [f"{prefix}(none recorded)"]


def _checkbox_lines(items: list[str]) -> list[str]:
    if items:
        return [f"- [ ] {item}" for item in items]
    return ["- [ ] (none recorded)"]


def write_implementation_pack(run_dir: Path, result: CouncilRunResult) -> list[Path]:
    dossier = result.dossier
    assignments = result.role_assignments
    model_lines = "\n".join(
        f"- **{item.slot}:** {item.preset} → {item.provider_name} / `{item.model_name}`"
        for item in assignments
    )
    if not model_lines:
        model_lines = (
            f"- Chair model: {result.provider_metadata.provider_name} / "
            f"`{result.provider_metadata.model_name}`"
        )

    plan_path = run_dir / "implementation_plan.md"
    plan_path.write_text(
        "\n".join(
            [
                "# Implementation Plan",
                "",
                f"**Decision question:** {dossier.decision_question}",
                "",
                f"**Verdict:** {dossier.decision_type.value.replace('_', ' ')} — {dossier.recommendation}",
                "",
                f"**Deciding factor:** {dossier.deciding_factor}",
                "",
                "## Models per role",
                "",
                model_lines,
                "",
                "## Phases",
                "",
                "1. Validate assumptions and close evidence gaps listed in the dossier.",
                "2. Execute next actions with owner assignment and time boxes.",
                "3. Re-run council after kill-criteria checkpoints.",
                "",
                "## Kill criteria",
                "",
                *_bullet_lines(dossier.kill_criteria),
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    tasks_path = run_dir / "task_breakdown.md"
    tasks_path.write_text(
        "\n".join(
            [
                "# Task Breakdown",
                "",
                "## Immediate",
                "",
                *_checkbox_lines(dossier.next_actions),
                "",
                "## Follow-up",
                "",
                *_checkbox_lines(dossier.open_questions),
                "",
                "## Evidence gaps to close",
                "",
                *_checkbox_lines(dossier.evidence_gaps),
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    cursor_path = run_dir / "cursor_prompt.md"
    cursor_path.write_text(
        "\n".join(
            [
                "# Cursor Implementation Prompt",
                "",
                "Implement the council verdict below in this repository. Do not expose secrets.",
                "",
                f"Question: {dossier.decision_question}",
                "",
                f"Decision: {dossier.decision_type.value}",
                "",
                f"Recommendation: {dossier.recommendation}",
                "",
                "Constraints:",
                "- Match existing CLI patterns and tests.",
                "- Keep changes minimal and reviewable.",
                "- Honor kill criteria before expanding scope.",
                "",
                "Deliverables:",
                "- Code changes aligned with next_actions in task_breakdown.md",
                "- Tests for touched behavior",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    risk_path = run_dir / "risk_register.md"
    risk_path.write_text(
        "\n".join(
            [
                "# Risk Register",
                "",
                "## Identified risks",
                "",
                *_bullet_lines(dossier.risks),
                "",
                "## Kill criteria",
                "",
                *_bullet_lines(dossier.kill_criteria),
                "",
                "## Unsupported assumptions",
                "",
                *_bullet_lines(dossier.unsupported_assumptions),
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    return [plan_path, tasks_path, cursor_path, risk_path]
