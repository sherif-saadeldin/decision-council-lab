from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from council.implementation_pack import write_implementation_pack
from council.models import CouncilRunResult
from council.review_model import PACK_GATE_BLOCKED_REASON, is_pack_allowed
from council.storage.run_store import RunStore
from council.verdict_quality import ensure_verdict_quality_for_pack


class PackGenerationBlockedError(ValueError):
    """Raised when lifecycle state blocks implementation pack generation."""


@dataclass(frozen=True)
class PackRequest:
    run_id: str
    allow_unapproved: bool = False
    resave_run_with_pack_paths: bool = False


@dataclass(frozen=True)
class PackResult:
    result: CouncilRunResult
    paths: list[Path]


class PackService:
    def __init__(self, store: RunStore) -> None:
        self._store = store

    def generate(self, request: PackRequest) -> PackResult:
        result = self._store.load_run(request.run_id)
        return self.generate_for_result(
            result,
            allow_unapproved=request.allow_unapproved,
            resave_run_with_pack_paths=request.resave_run_with_pack_paths,
        )

    def generate_for_result(
        self,
        result: CouncilRunResult,
        *,
        allow_unapproved: bool = False,
        resave_run_with_pack_paths: bool = False,
    ) -> PackResult:
        if not is_pack_allowed(result.review, override=allow_unapproved):
            raise PackGenerationBlockedError(PACK_GATE_BLOCKED_REASON)
        ensure_verdict_quality_for_pack(result.dossier)
        run_dir = self._store.runs_dir / result.dossier.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        paths = write_implementation_pack(run_dir, result)
        if resave_run_with_pack_paths:
            self._store.update_run(
                result.dossier.run_id,
                result,
                implementation_pack_paths=paths or None,
            )
        return PackResult(result=result, paths=paths)

