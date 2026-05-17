from __future__ import annotations

from dataclasses import dataclass

from council.models import CouncilRunResult
from council.run_catalog import RunSummary
from council.storage.run_store import RunStore


@dataclass(frozen=True)
class RunQuery:
    run_id: str


class RunService:
    def __init__(self, store: RunStore) -> None:
        self._store = store

    def load(self, query: RunQuery) -> CouncilRunResult:
        return self._store.load_run(query.run_id)

    def summary(self, query: RunQuery) -> RunSummary:
        return self._store.get_run_summary(query.run_id)

    def list_recent(self, *, limit: int = 10) -> list[RunSummary]:
        return self._store.list_runs(limit=limit)

    def thread_runs(self, thread_id: str) -> list[RunSummary]:
        return self._store.list_thread_runs(thread_id)

