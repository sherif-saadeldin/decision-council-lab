from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from council.sources.models import SourcePack, SourceQueryContext, SourceRelevanceRecord
from council.sources.scanner import ScanLimits, scan_source_input
from council.sources.source_context_builder import (
    SourceContextCaps,
    build_source_context,
)

DEFAULT_CONTEXT_CHAR_LIMIT = 3_000


@dataclass(frozen=True)
class SourceContextPayload:
    source_pack_ids: list[str]
    summary: str
    relevance: list[SourceRelevanceRecord]
    excluded_files: list[str]
    warnings: list[str]
    matched_keywords: list[str]


class SourceService:
    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = (base_dir or Path(".dcouncil/sources")).resolve()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def scan_and_save(
        self,
        source_path: Path,
        *,
        name: str | None = None,
        limits: ScanLimits | None = None,
    ) -> SourcePack:
        pack_id = str(uuid4())
        resolved = source_path.expanduser().resolve()
        pack_name = name or resolved.name or "source-pack"
        pack = scan_source_input(
            source_pack_id=pack_id,
            name=pack_name,
            source_path=resolved,
            limits=limits,
        )
        self.save(pack)
        return pack

    def scan_temporary(
        self,
        source_path: Path,
        *,
        name: str | None = None,
        limits: ScanLimits | None = None,
    ) -> SourcePack:
        pack_id = f"temp-{uuid4()}"
        resolved = source_path.expanduser().resolve()
        pack_name = name or f"temp:{resolved.name}"
        return scan_source_input(
            source_pack_id=pack_id,
            name=pack_name,
            source_path=resolved,
            limits=limits,
        )

    def list_packs(self) -> list[SourcePack]:
        if not self._base_dir.is_dir():
            return []
        packs: list[SourcePack] = []
        for file_path in sorted(self._base_dir.glob("*.json")):
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            packs.append(SourcePack.model_validate(payload))
        packs.sort(key=lambda item: item.created_at, reverse=True)
        return packs

    def load(self, source_pack_id: str) -> SourcePack:
        path = self._pack_path(source_pack_id)
        if not path.is_file():
            raise ValueError(f"Source pack not found: {source_pack_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SourcePack.model_validate(payload)

    def remove(self, source_pack_id: str) -> bool:
        path = self._pack_path(source_pack_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def save(self, pack: SourcePack) -> Path:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._pack_path(pack.source_pack_id)
        data = pack.model_dump(mode="json")
        _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        return path

    def build_context(
        self,
        *,
        source_pack_ids: list[str] | None = None,
        source_paths: list[Path] | None = None,
        question: str = "",
        intake_summary: str = "",
        decision_mode: str = "",
        keywords: list[str] | None = None,
        constraints: list[str] | None = None,
        context_char_limit: int = DEFAULT_CONTEXT_CHAR_LIMIT,
        max_snippets: int = 8,
        max_files: int = 5,
    ) -> SourceContextPayload:
        packs: list[SourcePack] = []
        ids: list[str] = []
        for source_pack_id in source_pack_ids or []:
            pack = self.load(source_pack_id)
            packs.append(pack)
            ids.append(pack.source_pack_id)
        for source_path in source_paths or []:
            pack = self.scan_temporary(source_path)
            packs.append(pack)
            ids.append(pack.source_pack_id)
        if not packs:
            return SourceContextPayload(
                source_pack_ids=[],
                summary="",
                relevance=[],
                excluded_files=[],
                warnings=[],
                matched_keywords=[],
            )
        query = SourceQueryContext(
            question=question,
            intake_summary=intake_summary,
            decision_mode=decision_mode,
            source_pack_ids=ids,
            keywords=list(keywords or []),
            constraints=list(constraints or []),
        )
        built = build_source_context(
            packs=packs,
            query=query,
            caps=SourceContextCaps(
                max_snippets=max_snippets,
                max_files=max_files,
                max_chars=context_char_limit,
            ),
        )
        return SourceContextPayload(
            source_pack_ids=ids,
            summary=built.summary,
            relevance=built.relevance,
            excluded_files=built.excluded_files,
            warnings=built.warnings,
            matched_keywords=built.matched_keywords,
        )

    def query(self, source_pack_id: str, question: str) -> SourceContextPayload:
        pack = self.load(source_pack_id)
        return self.build_context(
            source_pack_ids=[pack.source_pack_id],
            question=question,
        )

    def _pack_path(self, source_pack_id: str) -> Path:
        return self._base_dir / f"{source_pack_id}.json"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
    ) as handle:
        handle.write(content)
        temp_name = handle.name
    Path(temp_name).replace(path)

