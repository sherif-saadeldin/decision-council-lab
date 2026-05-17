from __future__ import annotations

from dataclasses import dataclass

from council.models import CouncilRunResult
from council.review import (
    approve_run,
    archive_run,
    load_run_result,
    mark_revision_of,
    reject_run,
)
from council.storage.run_store import RunStore


@dataclass(frozen=True)
class ReviewRequest:
    run_id: str
    actor: str | None = None
    note: str = ""


@dataclass(frozen=True)
class RejectRequest:
    run_id: str
    reason: str
    actor: str | None = None


@dataclass(frozen=True)
class RevisionRequest:
    run_id: str
    parent_run_id: str
    actor: str | None = None
    note: str = ""


class ReviewService:
    def __init__(self, store: RunStore) -> None:
        self._store = store

    def load(self, run_id: str) -> CouncilRunResult:
        return load_run_result(self._store.runs_dir, run_id)

    def approve(self, request: ReviewRequest) -> CouncilRunResult:
        return approve_run(
            self._store.runs_dir,
            request.run_id,
            actor=request.actor,
            note=request.note,
        )

    def reject(self, request: RejectRequest) -> CouncilRunResult:
        return reject_run(
            self._store.runs_dir,
            request.run_id,
            actor=request.actor,
            note=request.reason,
        )

    def archive(self, request: ReviewRequest) -> CouncilRunResult:
        return archive_run(
            self._store.runs_dir,
            request.run_id,
            actor=request.actor,
            note=request.note,
        )

    def mark_revision(self, request: RevisionRequest) -> CouncilRunResult:
        return mark_revision_of(
            self._store.runs_dir,
            request.run_id,
            request.parent_run_id,
            actor=request.actor,
            note=request.note,
        )

