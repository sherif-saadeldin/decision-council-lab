from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from council.sources.models import SourceFileSummary, SourceSnippet
from council.sources.redaction import redact_line

_WORD_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_]{2,}\b")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")
_PY_SYMBOL_RE = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_TS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
_STOP_WORDS = {
    "the",
    "and",
    "for",
    "that",
    "with",
    "this",
    "from",
    "into",
    "your",
    "will",
    "have",
    "about",
    "should",
    "would",
    "there",
    "their",
    "else",
    "when",
    "then",
    "true",
    "false",
    "none",
    "null",
}


def summarize_text_file(
    *,
    file_path: Path,
    rel_path: str,
    extension: str,
    content: str,
    size_bytes: int,
) -> SourceFileSummary:
    lines = content.splitlines()
    snippets: list[SourceSnippet] = []
    headings: list[str] = []
    symbols: list[str] = []
    warnings: list[str] = []
    words: Counter[str] = Counter()

    useful_added = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        redacted_line, had_secret = redact_line(line)
        if had_secret:
            warnings.append("secret-like material redacted")
        heading = _heading_from_line(line, extension)
        if heading:
            headings.append(heading)
        symbol = _symbol_from_line(line, extension)
        if symbol:
            symbols.append(symbol)
        if useful_added < 3:
            snippets.append(
                SourceSnippet(
                    path=rel_path,
                    label="useful_line",
                    text=redacted_line[:220],
                )
            )
            useful_added += 1
        for word in _WORD_RE.findall(line.lower()):
            if word not in _STOP_WORDS:
                words[word] += 1

    return SourceFileSummary(
        path=rel_path,
        extension=extension,
        size_bytes=size_bytes,
        headings=_dedupe(headings)[:8],
        symbols=_dedupe(symbols)[:12],
        keywords=[item for item, _count in words.most_common(8)],
        snippets=snippets[:4],
        warnings=_dedupe(warnings),
    )


def _heading_from_line(line: str, extension: str) -> str | None:
    if extension != ".md":
        return None
    match = _HEADING_RE.match(line)
    if not match:
        return None
    return match.group(1).strip()


def _symbol_from_line(line: str, extension: str) -> str | None:
    if extension == ".py":
        match = _PY_SYMBOL_RE.match(line)
        return match.group(1) if match else None
    if extension in {".js", ".ts", ".tsx"}:
        match = _TS_SYMBOL_RE.match(line)
        return match.group(1) if match else None
    return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output

