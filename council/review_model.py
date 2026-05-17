"""Decision lifecycle and review metadata models — Slice 5.9.

Lifecycle states are tracked on `CouncilRunResult.review`. Helpers here are
pure Pydantic / Enum types so they can be imported by `council/models.py`
without dragging in storage I/O.

Persistence (`apply_review_action`, `load_review`) lives in
`council/review.py` to keep this module dependency-free.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class LifecycleState(str, Enum):
    """Decision lifecycle states.

    `draft` is the default for any new council run. The state transitions:

        draft -> under_review -> approved | rejected
        approved -> superseded (when a revision is approved)
        any -> archived (terminal, manual)

    Backwards transitions are allowed (e.g. revoking an approval by
    re-running /reject); the full history lives in `DecisionReview.history`.
    """

    DRAFT = "draft"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


# Stable phrase used by chat UX to refuse pack generation on un-approved runs.
PACK_GATE_BLOCKED_REASON = "Decision is not approved yet."


class ReviewAction(str, Enum):
    """Audit-trail action labels for `ReviewEvent`."""

    CREATED = "created"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVISED = "revised"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    NOTED = "noted"


class ReviewEvent(BaseModel):
    """A single audit-trail entry on a `DecisionReview`."""

    action: ReviewAction
    actor: str
    note: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DecisionReview(BaseModel):
    """Lifecycle + review metadata for a council run.

    None of the actor fields are tied to authentication today — they are
    free-text labels recorded for traceability. Resolve the current actor
    via `resolve_actor()` so callers don't have to.
    """

    status: LifecycleState = LifecycleState.DRAFT
    approved_by: str | None = None
    rejected_by: str | None = None
    review_reason: str | None = None
    reviewed_at: datetime | None = None
    superseded_by_run_id: str | None = None
    is_revision_of: str | None = None
    history: list[ReviewEvent] = Field(default_factory=list)


def default_review() -> DecisionReview:
    """Initial review block: draft state, no history events yet."""
    return DecisionReview(status=LifecycleState.DRAFT)


def resolve_actor(explicit: str | None = None) -> str:
    """Pick an actor label for a review event.

    Order of preference: explicit argument → DCOUNCIL_REVIEW_ACTOR env var →
    USER / USERNAME → 'local'. Authentication is intentionally out of scope
    for this slice.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env_actor = (
        os.environ.get("DCOUNCIL_REVIEW_ACTOR")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or ""
    ).strip()
    return env_actor or "local"


def is_pack_allowed(review: DecisionReview | None, *, override: bool = False) -> bool:
    """Return True when implementation pack generation is permitted.

    Packs require `approved` status unless the caller passes the explicit
    override flag (CLI: `--allow-unapproved-pack`, chat: `/pack last
    --allow-unapproved`).
    """
    if override:
        return True
    if review is None:
        return False
    return review.status == LifecycleState.APPROVED
