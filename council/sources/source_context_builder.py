from __future__ import annotations

from dataclasses import dataclass

from council.sources.models import SourcePack, SourceQueryContext, SourceRelevanceRecord
from council.sources.relevance import RankedSourceFile, rank_source_pack

DEFAULT_MAX_SNIPPETS = 8
DEFAULT_MAX_FILES = 5
DEFAULT_MAX_CHARS = 3_000


@dataclass(frozen=True)
class SourceContextCaps:
    max_snippets: int = DEFAULT_MAX_SNIPPETS
    max_files: int = DEFAULT_MAX_FILES
    max_chars: int = DEFAULT_MAX_CHARS


@dataclass(frozen=True)
class SourceContextBuildResult:
    summary: str
    relevance: list[SourceRelevanceRecord]
    excluded_files: list[str]
    warnings: list[str]
    matched_keywords: list[str]


def build_source_context(
    *,
    packs: list[SourcePack],
    query: SourceQueryContext,
    caps: SourceContextCaps | None = None,
) -> SourceContextBuildResult:
    caps = caps or SourceContextCaps()
    ranked: list[RankedSourceFile] = []
    warnings: list[str] = []
    ignored: list[str] = []
    for pack in packs:
        ranked.extend(rank_source_pack(pack, query))
        warnings.extend(pack.warnings)
        ignored.extend(pack.ignored_files)
    ranked.sort(key=lambda item: (-item.score, item.summary.path))

    selected: list[SourceRelevanceRecord] = []
    excluded: list[str] = []
    lines: list[str] = ["I reviewed the following materials before answering:"]
    seen_snippets: set[str] = set()
    seen_files: set[str] = set()
    snippet_count = 0
    char_count = len("\n".join(lines))
    matched: set[str] = set()

    for entry in ranked:
        file_key = f"{entry.source_pack_id}:{entry.summary.path}"
        if file_key in seen_files:
            continue
        if len(selected) >= caps.max_files:
            excluded.append(f"{entry.summary.path} (max files cap)")
            continue

        snippet_lines: list[str] = []
        for snippet in entry.summary.snippets:
            normalized = " ".join(snippet.text.strip().lower().split())
            if not normalized or normalized in seen_snippets:
                continue
            if snippet_count >= caps.max_snippets:
                break
            snippet_lines.append(snippet.text)
            seen_snippets.add(normalized)
            snippet_count += 1

        if not snippet_lines:
            excluded.append(f"{entry.summary.path} (duplicate or empty snippets)")
            continue

        preview = _render_preview(entry, snippet_lines)
        next_char_count = char_count + len(preview) + 1
        if next_char_count > caps.max_chars:
            excluded.append(f"{entry.summary.path} (max chars cap)")
            continue

        char_count = next_char_count
        lines.extend(preview)
        seen_files.add(file_key)
        matched.update(entry.matched_terms)
        selected.append(
            SourceRelevanceRecord(
                source_pack_id=entry.source_pack_id,
                path=entry.summary.path,
                extension=entry.summary.extension,
                score=entry.score,
                matched_terms=entry.matched_terms,
                why_selected=entry.reasons,
                snippets=snippet_lines,
            )
        )

    if excluded:
        lines.append("- Skipped to keep this concise:")
        lines.extend(f"  - {item}" for item in excluded[:10])
    if ignored:
        lines.append("- Ignored while scanning:")
        lines.extend(f"  - {item}" for item in ignored[:8])
    if warnings:
        lines.append("- Safety notes:")
        lines.extend(f"  - {item}" for item in _dedupe(warnings)[:5])
    summary = "\n".join(lines)
    if len(summary) > caps.max_chars:
        summary = summary[: max(0, caps.max_chars - 22)].rstrip() + "\n[context truncated]"
    return SourceContextBuildResult(
        summary=summary,
        relevance=selected,
        excluded_files=excluded + [f"{item} (ignored)" for item in ignored],
        warnings=_dedupe(warnings),
        matched_keywords=sorted(matched),
    )


def _render_preview(entry: RankedSourceFile, snippets: list[str]) -> list[str]:
    lines = [f"- {entry.summary.path} (relevance {entry.score:.2f})"]
    if entry.matched_terms:
        lines.append(f"  - matched themes: {', '.join(entry.matched_terms[:8])}")
    if entry.reasons:
        lines.append(f"  - selected because: {', '.join(entry.reasons[:5])}")
    for snippet in snippets[:2]:
        lines.append(f"  - snippet: {snippet[:180]}")
    return lines


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output

