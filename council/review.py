"""Decision review storage helpers — Slice 5.9.

Loads and mutates the persisted lifecycle state on `runs/<id>/run.json`.
Writes are atomic (tmp + replace) and always regenerate the markdown so
the on-disk artifacts stay in sync.

Pure I/O over the existing `runs/<id>/run.json` files — no network, no
secrets, no auth.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from council.council_markdown import format_council_run_markdown
from council.markdown_format import (
    bullet_section,
    format_agent_brief_markdown,
    format_debate_transcript_markdown,
    format_role_assignments_markdown,
    proposed_metrics_section,
)
from council.models import CouncilRunResult
from council.review_model import (
    DecisionReview,
    LifecycleState,
    ReviewAction,
    ReviewEvent,
    default_review,
    resolve_actor,
)
from council.run_catalog import RunNotFoundError
from council.verdict_quality import decision_label, format_verdict_sections_markdown


class ReviewTransitionError(ValueError):
    """Raised when an `apply_review_action` call is rejected (already in
    that state, or attempting to act on an archived run)."""


def run_json_path(runs_dir: Path, run_id: str) -> Path:
    return runs_dir / run_id / "run.json"


def run_md_path(runs_dir: Path, run_id: str) -> Path:
    return runs_dir / run_id / "run.md"


def load_run_result(runs_dir: Path, run_id: str) -> CouncilRunResult:
    json_path = run_json_path(runs_dir, run_id)
    if not json_path.is_file():
        raise RunNotFoundError(run_id, runs_dir)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return CouncilRunResult.model_validate(payload)


def load_review(runs_dir: Path, run_id: str) -> DecisionReview:
    return load_run_result(runs_dir, run_id).review or default_review()


def approve_run(
    runs_dir: Path,
    run_id: str,
    *,
    actor: str | None = None,
    note: str = "",
) -> CouncilRunResult:
    """Mark a run as approved. If the run is a revision of an approved
    parent, the parent transitions to `superseded` and links forward via
    `superseded_by_run_id`."""
    return _apply_status_transition(
        runs_dir,
        run_id,
        new_status=LifecycleState.APPROVED,
        action=ReviewAction.APPROVED,
        actor=resolve_actor(actor),
        note=note,
    )


def reject_run(
    runs_dir: Path,
    run_id: str,
    *,
    actor: str | None = None,
    note: str,
) -> CouncilRunResult:
    """Mark a run as rejected. A reason note is required."""
    if not note.strip():
        msg = "A reason note is required when rejecting a decision."
        raise ReviewTransitionError(msg)
    return _apply_status_transition(
        runs_dir,
        run_id,
        new_status=LifecycleState.REJECTED,
        action=ReviewAction.REJECTED,
        actor=resolve_actor(actor),
        note=note,
    )


def archive_run(
    runs_dir: Path,
    run_id: str,
    *,
    actor: str | None = None,
    note: str = "",
) -> CouncilRunResult:
    """Archive a run. Idempotent."""
    return _apply_status_transition(
        runs_dir,
        run_id,
        new_status=LifecycleState.ARCHIVED,
        action=ReviewAction.ARCHIVED,
        actor=resolve_actor(actor),
        note=note,
    )


def mark_revision_of(
    runs_dir: Path,
    run_id: str,
    parent_run_id: str,
    *,
    actor: str | None = None,
    note: str = "",
) -> CouncilRunResult:
    """Record that `run_id` is a revision of `parent_run_id`. Called by
    `/revise` after the new contextual council run completes; the actual
    supersession happens when the revision is approved."""
    return _mutate(
        runs_dir,
        run_id,
        mutate=lambda review: _set_revision_of(review, parent_run_id, resolve_actor(actor), note),
    )


def _set_revision_of(
    review: DecisionReview,
    parent_run_id: str,
    actor: str,
    note: str,
) -> None:
    review.is_revision_of = parent_run_id
    review.history.append(
        ReviewEvent(
            action=ReviewAction.REVISED,
            actor=actor,
            note=note or f"Recorded as revision of {parent_run_id}.",
        )
    )


def _apply_status_transition(
    runs_dir: Path,
    run_id: str,
    *,
    new_status: LifecycleState,
    action: ReviewAction,
    actor: str,
    note: str,
) -> CouncilRunResult:
    result = _mutate(
        runs_dir,
        run_id,
        mutate=lambda review: _set_status(review, new_status, action, actor, note),
    )
    # If approving a revision, supersede the parent.
    if new_status == LifecycleState.APPROVED:
        parent_id = result.review.is_revision_of
        if parent_id:
            _supersede_parent(runs_dir, parent_id, result.dossier.run_id, actor=actor)
    return result


def _set_status(
    review: DecisionReview,
    status: LifecycleState,
    action: ReviewAction,
    actor: str,
    note: str,
) -> None:
    review.status = status
    review.reviewed_at = datetime.now(timezone.utc)
    if status == LifecycleState.APPROVED:
        review.approved_by = actor
        review.review_reason = note or review.review_reason
    elif status == LifecycleState.REJECTED:
        review.rejected_by = actor
        review.review_reason = note
    elif status == LifecycleState.ARCHIVED:
        review.review_reason = note or review.review_reason
    review.history.append(
        ReviewEvent(action=action, actor=actor, note=note)
    )


def _supersede_parent(
    runs_dir: Path,
    parent_id: str,
    revision_id: str,
    *,
    actor: str,
) -> None:
    """Best-effort: mark the parent as superseded and link forward.

    Missing parents are tolerated — the chain may have been deleted or the
    revision was recorded against a run from a different runs_dir.
    """
    try:
        load_run_result(runs_dir, parent_id)
    except RunNotFoundError:
        return

    def mutate(review: DecisionReview) -> None:
        review.status = LifecycleState.SUPERSEDED
        review.superseded_by_run_id = revision_id
        review.reviewed_at = datetime.now(timezone.utc)
        review.history.append(
            ReviewEvent(
                action=ReviewAction.SUPERSEDED,
                actor=actor,
                note=f"Superseded by revision {revision_id}.",
            )
        )

    _mutate(runs_dir, parent_id, mutate=mutate)


def _mutate(
    runs_dir: Path,
    run_id: str,
    *,
    mutate,
) -> CouncilRunResult:
    result = load_run_result(runs_dir, run_id)
    review = result.review or default_review()
    if review.status == LifecycleState.ARCHIVED:
        msg = f"Run {run_id} is archived; unarchive manually before further review actions."
        raise ReviewTransitionError(msg)
    mutate(review)
    # Re-assign to ensure default_factory-created reviews are kept on the model.
    result.review = review
    _save_run_result_in_place(runs_dir, run_id, result)
    return result


def _save_run_result_in_place(
    runs_dir: Path,
    run_id: str,
    result: CouncilRunResult,
) -> None:
    """Atomically rewrite run.json and re-render run.md from `result`."""
    json_path = run_json_path(runs_dir, run_id)
    md_path = run_md_path(runs_dir, run_id)
    payload = result.model_dump(mode="json")
    _atomic_write_text(
        json_path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )
    if _is_council_run(result):
        md_text = format_council_run_markdown(result)
    else:
        md_text = _format_standard_markdown(result)
    _atomic_write_text(md_path, md_text)


def _is_council_run(result: CouncilRunResult) -> bool:
    return result.council_mode == "multi" or bool(result.role_assignments)


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


# --- minimal mirror of storage._format_markdown for standalone runs ---------
# We can't import council.storage from here without a cycle, and the standard
# (non-council) format is small enough to mirror locally. Council runs use the
# canonical council markdown writer.


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
