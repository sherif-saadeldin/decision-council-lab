from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

from council.review_model import LifecycleState


_LIFECYCLE_STYLES: dict[str, str] = {
    LifecycleState.DRAFT.value: "dim",
    LifecycleState.UNDER_REVIEW.value: "yellow",
    LifecycleState.APPROVED.value: "green",
    LifecycleState.REJECTED.value: "red",
    LifecycleState.SUPERSEDED.value: "magenta",
    LifecycleState.ARCHIVED.value: "dim",
}


def lifecycle_style(status: str) -> str:
    return _LIFECYCLE_STYLES.get(status, "white")


def render_review(console: Console, run_id: str, result) -> None:
    review = result.review
    thread = getattr(result, "decision_thread", None)
    status_style = lifecycle_style(review.status.value)
    lines = [
        f"Run ID    : [cyan]{run_id}[/cyan]",
        f"Status    : [{status_style}]{review.status.value}[/{status_style}]",
    ]
    if review.approved_by:
        lines.append(f"Approved  : {review.approved_by}")
    if review.rejected_by:
        lines.append(f"Rejected  : {review.rejected_by}")
    if review.review_reason:
        lines.append(f"Note      : {review.review_reason}")
    if review.reviewed_at:
        lines.append(
            f"Reviewed  : {review.reviewed_at.strftime('%Y-%m-%d %H:%M:%SZ')}"
        )
    if review.is_revision_of:
        lines.append(f"Revision of : [cyan]{review.is_revision_of}[/cyan]")
    if review.superseded_by_run_id:
        lines.append(f"Superseded by: [cyan]{review.superseded_by_run_id}[/cyan]")
    if thread is not None:
        lines.append(f"Thread    : [cyan]{thread.thread_id}[/cyan]")
        lines.append(f"Parent    : [cyan]{thread.parent_run_id}[/cyan]")
    if review.history:
        lines.append("")
        lines.append("History:")
        for event in review.history:
            ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%SZ")
            note = f" — {event.note}" if event.note else ""
            lines.append(f"  {ts}  {event.action.value} by {event.actor}{note}")
    console.print(Panel("\n".join(lines), title="Decision Review", border_style="blue"))

