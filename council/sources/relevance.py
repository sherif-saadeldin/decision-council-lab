from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from council.sources.models import SourceFileSummary, SourcePack, SourceQueryContext

_TOKEN_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9_\-]{1,}\b")
_PHRASE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9 _/\-]{3,}")
_TECH_HINTS = {
    "python",
    "typescript",
    "javascript",
    "react",
    "pydantic",
    "langgraph",
    "mypy",
    "ruff",
    "cli",
    "chat",
    "architecture",
    "service",
    "services",
    "roadmap",
    "build_order",
    "spec",
}
_EXTENSION_WEIGHTS = {
    ".md": 0.12,
    ".txt": 0.06,
    ".toml": 0.08,
    ".yaml": 0.08,
    ".yml": 0.08,
    ".json": 0.04,
    ".py": 0.10,
    ".ts": 0.08,
    ".tsx": 0.06,
    ".js": 0.05,
    ".csv": 0.02,
}
_DOC_PATH_HINTS = ("architecture", "build_order", "roadmap", "spec", "docs")


@dataclass(frozen=True)
class RankedSourceFile:
    source_pack_id: str
    summary: SourceFileSummary
    score: float
    matched_terms: list[str]
    reasons: list[str]


def rank_source_pack(pack: SourcePack, query: SourceQueryContext) -> list[RankedSourceFile]:
    terms = _query_terms(query)
    phrases = _query_phrases(query.question)
    ranked: list[RankedSourceFile] = []
    for summary in pack.summaries:
        ranked.append(_score_file(pack.source_pack_id, summary, terms, phrases))
    ranked.sort(key=lambda item: (-item.score, item.summary.path))
    return ranked


def _score_file(
    source_pack_id: str,
    summary: SourceFileSummary,
    terms: list[str],
    phrases: list[str],
) -> RankedSourceFile:
    path_text = summary.path.lower()
    file_name = PurePosixPath(summary.path).name.lower()
    heading_text = " ".join(summary.headings).lower()
    keyword_text = " ".join(summary.keywords).lower()
    snippet_text = " ".join(snippet.text for snippet in summary.snippets).lower()
    symbol_text = " ".join(summary.symbols).lower()
    combined = " ".join([path_text, heading_text, keyword_text, snippet_text, symbol_text])

    matched_terms = [term for term in terms if term in combined]
    overlap_score = min(len(matched_terms), 8) / max(1, min(len(terms), 8))
    reasons: list[str] = []
    score = overlap_score * 0.40
    if matched_terms:
        reasons.append("keyword overlap")

    filename_hits = [term for term in terms if term in file_name]
    if filename_hits:
        score += 0.18
        reasons.append("filename match")

    heading_hits = [term for term in terms if term in heading_text]
    if heading_hits:
        score += 0.14
        reasons.append("heading/title match")

    freq = _frequency_score(terms, combined)
    if freq > 0:
        score += 0.10 * min(freq, 1.0)
        reasons.append("term frequency")

    path_hits = [term for term in terms if term in path_text]
    if path_hits:
        score += 0.08
        reasons.append("path weighting")
    if any(hint in path_text for hint in _DOC_PATH_HINTS):
        score += 0.07
        reasons.append("architecture/spec path bias")

    ext_weight = _EXTENSION_WEIGHTS.get(summary.extension, 0.0)
    if ext_weight > 0:
        score += ext_weight
        reasons.append("extension weighting")

    phrase_hits = [phrase for phrase in phrases if phrase in combined]
    if phrase_hits:
        score += 0.16
        reasons.append("exact phrase boost")

    tech_hits = [term for term in terms if term in _TECH_HINTS and term in combined]
    if tech_hits:
        score += 0.07
        reasons.append("explicit technology match")

    score += _recency_boost_placeholder(summary.path)
    if score > 1.0:
        score = 1.0
    if score < 0.0:
        score = 0.0
    return RankedSourceFile(
        source_pack_id=source_pack_id,
        summary=summary,
        score=round(score, 4),
        matched_terms=sorted(set(matched_terms + filename_hits + heading_hits + phrase_hits + tech_hits)),
        reasons=_dedupe(reasons),
    )


def _frequency_score(terms: list[str], combined: str) -> float:
    if not terms or not combined:
        return 0.0
    hits = sum(combined.count(term) for term in terms)
    return hits / (len(terms) * 2)


def _query_terms(query: SourceQueryContext) -> list[str]:
    parts = [
        query.question,
        query.intake_summary,
        query.decision_mode,
        " ".join(query.keywords),
        " ".join(query.constraints),
    ]
    terms = [token.lower() for token in _TOKEN_RE.findall(" ".join(parts))]
    filtered = [term for term in terms if len(term) > 2]
    return _dedupe(filtered)


def _query_phrases(question: str) -> list[str]:
    phrases = []
    for fragment in _PHRASE_RE.findall(question.lower()):
        cleaned = " ".join(fragment.split())
        if len(cleaned.split()) >= 2:
            phrases.append(cleaned)
    return _dedupe(phrases)[:6]


def _recency_boost_placeholder(_path: str) -> float:
    # Hook retained for future source metadata timestamps. Deterministic for now.
    return 0.0


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output

