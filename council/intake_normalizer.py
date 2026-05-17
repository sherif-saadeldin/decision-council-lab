from __future__ import annotations

import re

_SPLIT_RE = re.compile(r"[,;\n]+|\s{2,}")

_CONSTRAINT_LABELS: tuple[tuple[str, str], ...] = (
    ("time", "Limited time"),
    ("money", "Financial pressure"),
    ("budget", "Financial pressure"),
    ("cash", "Financial pressure"),
    ("legal", "Legal constraints"),
    ("compliance", "Legal constraints"),
    ("team", "Small team capacity"),
    ("solo", "Single-operator constraints"),
    ("skill", "Capability constraints"),
    ("network", "Limited network access"),
)

_RISK_LABELS: tuple[tuple[str, str], ...] = (
    ("burnout", "Burnout risk"),
    ("cash", "Runway risk"),
    ("legal", "Legal exposure"),
    ("adoption", "Adoption risk"),
    ("competition", "Competitive pressure"),
    ("execution", "Execution risk"),
)
_LABEL_KEYWORDS = {keyword for keyword, _ in (*_CONSTRAINT_LABELS, *_RISK_LABELS)}


def normalize_constraint_fragments(answer: str) -> list[str]:
    return _normalize_fragments(answer, _CONSTRAINT_LABELS)


def normalize_risk_fragments(answer: str) -> list[str]:
    return _normalize_fragments(answer, _RISK_LABELS)


def normalize_context_text(answer: str) -> str:
    text = " ".join(answer.strip().split())
    if not text:
        return ""
    if len(text) < 4:
        return ""
    return text


def _normalize_fragments(answer: str, labels: tuple[tuple[str, str], ...]) -> list[str]:
    raw = answer.strip()
    if not raw:
        return []
    chunks = [part.strip() for part in _SPLIT_RE.split(raw) if part.strip()]
    if not chunks:
        chunks = [raw]
    output: list[str] = []
    seen: set[str] = set()
    lowered_whole = raw.lower()
    for keyword, normalized in labels:
        if keyword in lowered_whole and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    # retain meaningful custom fragments that did not map
    for chunk in chunks:
        if len(output) >= 2 and len(chunk.split()) >= 3:
            # Treat dense shorthand chunks as already explained by mapped labels.
            continue
        if output and chunk.lower() in _LABEL_KEYWORDS:
            continue
        normalized_chunk = _clean_phrase(chunk)
        if len(normalized_chunk) < 4:
            continue
        if normalized_chunk.lower() in {"none", "na", "n/a", "idk"}:
            continue
        if normalized_chunk not in seen:
            seen.add(normalized_chunk)
            output.append(normalized_chunk)
    return output


def _clean_phrase(text: str) -> str:
    cleaned = " ".join(text.strip().split())
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""

