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

    recommendation = str(dossier.get("recommendation") or "").strip()
    preview = recommendation.split("\n", 1)[0][:120] if recommendation else question[:120]
    confidence = dossier.get("confidence_score")
    confidence_score = float(confidence) if confidence is not None else None

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
