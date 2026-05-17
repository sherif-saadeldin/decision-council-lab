from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class SourceSnippet(BaseModel):
    path: str
    label: str
    text: str


class SourceFileSummary(BaseModel):
    path: str
    extension: str
    size_bytes: int = Field(ge=0)
    headings: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    snippets: list[SourceSnippet] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SourceQueryContext(BaseModel):
    question: str
    intake_summary: str = ""
    decision_mode: str = ""
    source_pack_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class SourceRelevanceRecord(BaseModel):
    source_pack_id: str
    path: str
    extension: str
    score: float = Field(ge=0.0, le=1.0)
    matched_terms: list[str] = Field(default_factory=list)
    why_selected: list[str] = Field(default_factory=list)
    snippets: list[str] = Field(default_factory=list)


class SourcePack(BaseModel):
    source_pack_id: str
    name: str
    root_path: str | None = None
    file_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    file_count: int = Field(ge=0, default=0)
    total_bytes: int = Field(ge=0, default=0)
    included_extensions: list[str] = Field(default_factory=list)
    ignored_files: list[str] = Field(default_factory=list)
    summaries: list[SourceFileSummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def display_root(self) -> str:
        if self.root_path:
            return self.root_path
        if self.file_paths:
            return ", ".join(self.file_paths[:3])
        return "(empty)"

    @classmethod
    def from_paths(
        cls,
        *,
        source_pack_id: str,
        name: str,
        root_path: Path | None,
        file_paths: list[Path],
        summaries: list[SourceFileSummary],
        ignored_files: list[str],
        warnings: list[str],
    ) -> "SourcePack":
        total_bytes = sum(item.size_bytes for item in summaries)
        extensions = sorted({item.extension for item in summaries if item.extension})
        return cls(
            source_pack_id=source_pack_id,
            name=name,
            root_path=str(root_path) if root_path is not None else None,
            file_paths=[str(path) for path in file_paths],
            file_count=len(summaries),
            total_bytes=total_bytes,
            included_extensions=extensions,
            ignored_files=ignored_files,
            summaries=summaries,
            warnings=warnings,
        )
