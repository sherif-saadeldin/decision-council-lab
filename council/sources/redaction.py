from __future__ import annotations

import re

REDACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bearer token",
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}"),
    ),
    (
        "api key assignment",
        re.compile(
            r"(?i)\b(?:api[_\- ]?key|token|secret)\b\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?"
        ),
    ),
    (
        "password assignment",
        re.compile(r"(?i)\bpassword\b\s*[:=]\s*['\"]?[^\s'\"]+['\"]?"),
    ),
    (
        "env assignment",
        re.compile(r"(?m)^\s*[A-Z][A-Z0-9_]{2,}\s*=\s*[^\s#]+"),
    ),
)


def redact_line(line: str) -> tuple[str, bool]:
    redacted = line
    changed = False
    for _label, pattern in REDACTION_PATTERNS:
        next_text, count = pattern.subn("[REDACTED]", redacted)
        if count > 0:
            changed = True
            redacted = next_text
    return redacted, changed

