from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from council.models import CouncilRunResult
from council.run_catalog import RunSummary, get_run_summary, list_recent_runs, list_thread_runs
from council.storage import save_run_to_dir


@dataclass(frozen=True)
class RunArtifacts:
    json_path: Path
    md_path: Path


class RunStore(Protocol):
    """Storage boundary for run artifacts.

    Callers above this layer should not assume JSON files, directory names, or
    filesystem layout details. The current implementation remains filesystem
    JSON/Markdown, but the app boundary now has a seam for a later UI/service.
    """

    @property
    def runs_dir(self) -> Path: ...

    def save_run(
        self,
        result: CouncilRunResult,
        *,
        implementation_pack_paths: list[Path] | None = None,
    ) -> RunArtifacts: ...

    def load_run(self, run_id: str) -> CouncilRunResult: ...

    def list_runs(self, *, limit: int = 10) -> list[RunSummary]: ...

    def get_run_summary(self, run_id: str) -> RunSummary: ...

    def update_run(
        self,
        run_id: str,
        result: CouncilRunResult,
        *,
        implementation_pack_paths: list[Path] | None = None,
    ) -> RunArtifacts: ...

    def archive_run(
        self,
        run_id: str,
        *,
        actor: str | None = None,
        note: str = "",
    ) -> CouncilRunResult: ...

    def list_thread_runs(self, thread_id: str) -> list[RunSummary]: ...

    def thread_queries(self, thread_id: str) -> list[RunSummary]: ...


@dataclass(frozen=True)
class FileRunStore:
    """Filesystem-backed implementation of the RunStore boundary."""

    runs_dir: Path

    def save_run(
        self,
        result: CouncilRunResult,
        *,
        implementation_pack_paths: list[Path] | None = None,
    ) -> RunArtifacts:
        json_path, md_path = save_run_to_dir(
            result,
            self.runs_dir,
            implementation_pack_paths=implementation_pack_paths,
        )
        return RunArtifacts(json_path=json_path, md_path=md_path)

    def load_run(self, run_id: str) -> CouncilRunResult:
        from council.run_catalog import RunNotFoundError

        json_path = self.runs_dir / run_id / "run.json"
        if not json_path.is_file():
            raise RunNotFoundError(run_id, self.runs_dir)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        return CouncilRunResult.model_validate(payload)

    def list_runs(self, *, limit: int = 10) -> list[RunSummary]:
        return list_recent_runs(self.runs_dir, limit=limit)

    def get_run_summary(self, run_id: str) -> RunSummary:
        return get_run_summary(self.runs_dir, run_id)

    def update_run(
        self,
        run_id: str,
        result: CouncilRunResult,
        *,
        implementation_pack_paths: list[Path] | None = None,
    ) -> RunArtifacts:
        if result.dossier.run_id != run_id:
            msg = (
                f"Run ID mismatch: update target {run_id!r} does not match "
                f"result {result.dossier.run_id!r}."
            )
            raise ValueError(msg)
        return self.save_run(
            result,
            implementation_pack_paths=implementation_pack_paths,
        )

    def archive_run(
        self,
        run_id: str,
        *,
        actor: str | None = None,
        note: str = "",
    ) -> CouncilRunResult:
        from council.review import archive_run

        return archive_run(self.runs_dir, run_id, actor=actor, note=note)

    def list_thread_runs(self, thread_id: str) -> list[RunSummary]:
        return list_thread_runs(self.runs_dir, thread_id)

    def thread_queries(self, thread_id: str) -> list[RunSummary]:
        return self.list_thread_runs(thread_id)
