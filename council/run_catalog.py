from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class RunNotFoundError(Exception):
    def __init__(self, run_id: str, runs_dir: Path) -> None:
        self.run_id = run_id
        self.runs_dir = runs_dir
        super().__init__(f"Run not found: {run_id} (searched in {runs_dir})")


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    timestamp: datetime
    question: str
    run_kind: str
    chair_provider: str
    chair_model: str
    json_path: Path
    md_path: Path
    decision_preview: str
    confidence_score: float | None
    parent_run_id: str | None = None
    thread_id: str | None = None
    # Lifecycle metadata (Slice 5.9). Defaults to "draft" for runs saved
    # without an explicit review block; legacy runs (pre-1.9) still
    # surface as draft.
    lifecycle_status: str = "draft"
    is_revision_of: str | None = None
    superseded_by_run_id: str | None = None


def _parse_run_payload(payload: dict, run_dir: Path) -> RunSummary | None:
    dossier = payload.get("dossier")
    if not isinstance(dossier, dict):
        return None
    run_id = str(dossier.get("run_id") or run_dir.name)
    question = str(dossier.get("decision_question") or "").strip()
    ts_raw = dossier.get("timestamp")
    try:
        timestamp = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        timestamp = datetime.fromtimestamp(run_dir.stat().st_mtime)

    council_mode = payload.get("council_mode")
    role_assignments = payload.get("role_assignments") or []
    if council_mode == "multi" or role_assignments:
        run_kind = "council"
    else:
        run_kind = "standard"

    meta = payload.get("provider_metadata") or {}
    chair_provider = str(meta.get("provider_name") or "—")
    chair_model = str(meta.get("model_name") or "—")
    if run_kind == "council" and role_assignments:
        for item in role_assignments:
            if isinstance(item, dict) and item.get("slot") == "chair":
                chair_provider = str(item.get("provider_name") or chair_provider)
                chair_model = str(item.get("model_name") or chair_model)
                break

    direct_answer = str(dossier.get("direct_answer") or "").strip()
    recommendation = str(dossier.get("recommendation") or "").strip()
    if direct_answer:
        preview = direct_answer[:120]
    elif recommendation:
        preview = recommendation.split("\n", 1)[0][:120]
    else:
        preview = question[:120]
    confidence = dossier.get("confidence_score")
    confidence_score = float(confidence) if confidence is not None else None

    thread_block = payload.get("decision_thread") or {}
    parent_run_id = (
        str(thread_block.get("parent_run_id"))
        if isinstance(thread_block, dict) and thread_block.get("parent_run_id")
        else None
    )
    thread_id = (
        str(thread_block.get("thread_id"))
        if isinstance(thread_block, dict) and thread_block.get("thread_id")
        else None
    )

    review_block = payload.get("review") or {}
    if isinstance(review_block, dict):
        lifecycle_status = str(review_block.get("status") or "draft")
        is_revision_of = (
            str(review_block.get("is_revision_of"))
            if review_block.get("is_revision_of")
            else None
        )
        superseded_by = (
            str(review_block.get("superseded_by_run_id"))
            if review_block.get("superseded_by_run_id")
            else None
        )
    else:
        lifecycle_status = "draft"
        is_revision_of = None
        superseded_by = None

    return RunSummary(
        run_id=run_id,
        timestamp=timestamp,
        question=question,
        run_kind=run_kind,
        chair_provider=chair_provider,
        chair_model=chair_model,
        json_path=run_dir / "run.json",
        md_path=run_dir / "run.md",
        decision_preview=preview,
        confidence_score=confidence_score,
        parent_run_id=parent_run_id,
        thread_id=thread_id,
        lifecycle_status=lifecycle_status,
        is_revision_of=is_revision_of,
        superseded_by_run_id=superseded_by,
    )


def _load_run_json(run_dir: Path) -> dict | None:
    json_path = run_dir / "run.json"
    if not json_path.is_file():
        return None
    try:
        return json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_recent_runs(runs_dir: Path, *, limit: int = 10) -> list[RunSummary]:
    if not runs_dir.is_dir():
        return []

    summaries: list[RunSummary] = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        payload = _load_run_json(child)
        if payload is None:
            continue
        summary = _parse_run_payload(payload, child)
        if summary is not None:
            summaries.append(summary)

    summaries.sort(key=lambda item: item.timestamp, reverse=True)
    return summaries[:limit]


def get_run_summary(runs_dir: Path, run_id: str) -> RunSummary:
    run_dir = runs_dir / run_id
    if not run_dir.is_dir():
        raise RunNotFoundError(run_id, runs_dir)
    payload = _load_run_json(run_dir)
    if payload is None:
        raise RunNotFoundError(run_id, runs_dir)
    summary = _parse_run_payload(payload, run_dir)
    if summary is None:
        raise RunNotFoundError(run_id, runs_dir)
    return summary


def list_thread_runs(runs_dir: Path, thread_id: str) -> list[RunSummary]:
    """Return every run on the given thread, sorted oldest first.

    The thread anchor (the parent_run_id of the first contextual child) is
    included if a run with that id exists, even if its own decision_thread
    is empty — it's the root the chain hangs off of.
    """
    if not runs_dir.is_dir():
        return []
    results: list[RunSummary] = []
    for child in runs_dir.iterdir():
        if not child.is_dir():
            continue
        payload = _load_run_json(child)
        if payload is None:
            continue
        summary = _parse_run_payload(payload, child)
        if summary is None:
            continue
        if summary.thread_id == thread_id:
            results.append(summary)
            continue
        if summary.run_id == thread_id:
            # Root run of the thread — may have no decision_thread block
            # itself but every child still points at this id.
            results.append(summary)
    results.sort(key=lambda item: item.timestamp)
    return results
