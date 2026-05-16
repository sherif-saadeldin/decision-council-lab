from __future__ import annotations

from pathlib import Path

from council.models import CouncilRunResult

PROJECT_NAME = "decision-council-lab"

IMPLEMENTATION_PACK_FILENAMES: tuple[str, ...] = (
    "mvp_scope.md",
    "implementation_plan.md",
    "task_breakdown.md",
    "cursor_build_prompt.md",
    "risk_register.md",
    "approval_checklist.md",
)

# Legacy filename from Slice 5.2 — removed on write.
_LEGACY_PACK_FILES: tuple[str, ...] = ("cursor_prompt.md",)


def _bullet_lines(items: list[str], *, prefix: str = "- ") -> list[str]:
    if items:
        return [f"{prefix}{item}" for item in items]
    return [f"{prefix}(none recorded)"]


def _checkbox_lines(items: list[str]) -> list[str]:
    if items:
        return [f"- [ ] {item}" for item in items]
    return ["- [ ] (none recorded)"]


def _pack_header_lines(result: CouncilRunResult) -> list[str]:
    dossier = result.dossier
    return [
        f"**Project:** {PROJECT_NAME}",
        f"**Decision question:** {dossier.decision_question}",
        f"**Source run ID:** `{dossier.run_id}`",
        f"**Verdict:** {dossier.decision_type.value.replace('_', ' ')}",
        f"**Confidence:** {dossier.confidence_score:.0%} ({dossier.confidence_score:.2f})",
        "",
    ]


def _scope_boundary_lines(dossier) -> list[str]:
    return [
        "## Scope boundaries",
        "",
        "**In scope**",
        "",
        *_bullet_lines(dossier.next_actions[:5] if dossier.next_actions else ["Council-approved next actions only"]),
        "",
        "**Out of scope (unless re-approved)**",
        "",
        "- Features not listed in this pack or council dossier",
        "- Provider additions, auth, frontend, or infrastructure not requested in the verdict",
        "",
    ]


def _assumptions_lines(dossier) -> list[str]:
    combined = list(dossier.assumptions) + list(dossier.unsupported_assumptions)
    return ["## Assumptions", "", *_bullet_lines(combined), ""]


def _approval_gate_lines(*gates: str) -> list[str]:
    return [
        "## Approval gates",
        "",
        "Do not proceed past a gate without explicit human approval.",
        "",
        *[f"- [ ] {gate}" for gate in gates],
        "",
    ]


def _model_assignment_lines(result: CouncilRunResult) -> list[str]:
    if not result.role_assignments:
        meta = result.provider_metadata
        return [
            "## Models",
            "",
            f"- Chair: {meta.provider_name} / `{meta.model_name}`",
            "",
        ]
    lines = ["## Models per role", ""]
    for item in result.role_assignments:
        lines.append(
            f"- **{item.slot}:** {item.preset} → {item.provider_name} / `{item.model_name}`"
        )
    lines.append("")
    return lines


def write_implementation_pack(run_dir: Path, result: CouncilRunResult) -> list[Path]:
    dossier = result.dossier
    header = _pack_header_lines(result)
    scope = _scope_boundary_lines(dossier)
    assumptions = _assumptions_lines(dossier)
    models = _model_assignment_lines(result)

    gates_plan = _approval_gate_lines(
        "Scope and MVP boundaries reviewed",
        "Implementation plan accepted",
        "Kill criteria acknowledged before build",
    )
    gates_mvp = _approval_gate_lines(
        "MVP scope matches council verdict",
        "Out-of-scope items explicitly deferred",
    )
    gates_tasks = _approval_gate_lines(
        "Task breakdown prioritized and owned",
        "Evidence gaps assigned before coding",
    )
    gates_cursor = _approval_gate_lines(
        "Build prompt reviewed against scope",
        "No secrets or credentials in prompt artifacts",
    )
    gates_risk = _approval_gate_lines(
        "Risk register reviewed",
        "Kill criteria wired to checkpoints",
    )

    for legacy in _LEGACY_PACK_FILES:
        legacy_path = run_dir / legacy
        if legacy_path.is_file():
            legacy_path.unlink()

    mvp_path = run_dir / "mvp_scope.md"
    mvp_path.write_text(
        "\n".join(
            [
                "# MVP Scope",
                "",
                *header,
                *gates_mvp,
                *scope,
                *assumptions,
                "## Acceptance criteria",
                "",
                *_checkbox_lines(
                    [
                        "Delivers council next actions with tests for touched behavior",
                        "Respects kill criteria and evidence-gap closure plan",
                        "No scope beyond this pack without a new council run",
                    ]
                ),
                "",
                "## Council deciding factor",
                "",
                dossier.deciding_factor,
                "",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    plan_path = run_dir / "implementation_plan.md"
    plan_path.write_text(
        "\n".join(
            [
                "# Implementation Plan",
                "",
                *header,
                *gates_plan,
                *models,
                "## Phases",
                "",
                "1. Close evidence gaps and validate assumptions.",
                "2. Execute prioritized tasks from `task_breakdown.md`.",
                "3. Checkpoint against kill criteria; re-run council if gates fail.",
                "",
                "## Deciding factor",
                "",
                dossier.deciding_factor,
                "",
                "## Kill criteria",
                "",
                *_bullet_lines(dossier.kill_criteria),
                "",
                *assumptions,
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
                *header,
                *gates_tasks,
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
                "",
                "## Acceptance criteria",
                "",
                *_checkbox_lines(
                    [
                        "Each completed task maps to a council next action or evidence gap",
                        "Tests cover changed behavior; no live keys in repo",
                    ]
                ),
                "",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    cursor_path = run_dir / "cursor_build_prompt.md"
    cursor_path.write_text(
        "\n".join(
            [
                "# Cursor Build Prompt",
                "",
                *header,
                *gates_cursor,
                *scope,
                "Implement the council verdict in this repository.",
                "",
                f"**Recommendation:** {dossier.recommendation}",
                "",
                f"**Deciding factor:** {dossier.deciding_factor}",
                "",
                "## Constraints",
                "",
                "- Match existing CLI patterns; use `uv` only.",
                "- Minimal, reviewable diffs; no new providers unless council approves.",
                "- Do not expose secrets in code, logs, or markdown.",
                "- Honor kill criteria before expanding scope.",
                "",
                "## Deliverables",
                "",
                "- Code aligned with `task_breakdown.md`",
                "- Tests for touched behavior (no live network in tests)",
                "- Update docs only when behavior changes",
                "",
                "## Reference artifacts",
                "",
                "- `implementation_plan.md`, `mvp_scope.md`, `risk_register.md`",
                "- Council run: `run.md` in this folder",
                "",
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
                *header,
                *gates_risk,
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
                "",
                "## Mitigation checkpoints",
                "",
                *_checkbox_lines(
                    [
                        "Re-run council if kill criteria triggered",
                        "Document residual risk before shipping",
                    ]
                ),
                "",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    checklist_path = run_dir / "approval_checklist.md"
    checklist_path.write_text(
        "\n".join(
            [
                "# Approval Checklist",
                "",
                *header,
                "Complete all gates before treating implementation as approved.",
                "",
                "## Pack review",
                "",
                *_checkbox_lines(
                    [
                        "mvp_scope.md — scope and acceptance criteria",
                        "implementation_plan.md — phases and kill criteria",
                        "task_breakdown.md — owners and priorities",
                        "cursor_build_prompt.md — build constraints",
                        "risk_register.md — risks and mitigations",
                    ]
                ),
                "",
                "## Final approval gates",
                "",
                *_checkbox_lines(
                    [
                        "Human approver confirms verdict still valid",
                        "Budget/time box agreed for MVP",
                        "Kill criteria monitoring plan in place",
                        "Ready to start implementation",
                    ]
                ),
                "",
            ]
        ).rstrip()
        + "\n",
        encoding="utf-8",
    )

    return [
        mvp_path,
        plan_path,
        tasks_path,
        cursor_path,
        risk_path,
        checklist_path,
    ]
