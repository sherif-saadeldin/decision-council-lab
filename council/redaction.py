from __future__ import annotations

import re
from collections.abc import Iterable

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]+"),
    re.compile(r"OPENAI_API_KEY\s*=\s*\S+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"(?i)api[_\s-]?key\s*[:=]\s*['\"]?.+?['\"]?(?:\s|$)"),
    re.compile(r"(?i)incorrect api key provided:\s*\S+"),
    re.compile(r"(?i)invalid api key[:\s]+\S+"),
)

MIN_FRAGMENT_LENGTH = 6


def credential_fragments(secret: str, *, min_length: int = MIN_FRAGMENT_LENGTH) -> list[str]:
    """Return secret and word-like fragments for defensive redaction in debug output."""
    fragments: list[str] = []
    if not secret:
        return fragments
    if len(secret) >= min_length:
        fragments.append(secret)
    for part in re.split(r"[^\w]+", secret):
        if len(part) >= min_length and part not in fragments:
            fragments.append(part)
    hyphenated = secret.replace("_", "-")
    if "-" in hyphenated and len(hyphenated) >= min_length and hyphenated not in fragments:
        fragments.append(hyphenated)
    return fragments


def redact_secrets(text: str, secrets: Iterable[str] | None = None) -> str:
    redacted = text
    for secret in secrets or []:
        for fragment in credential_fragments(secret):
            redacted = redacted.replace(fragment, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def assert_no_credential_leaks(text: str, secrets: Iterable[str]) -> None:
    """Test helper: raise AssertionError if known secrets or fragments appear."""
    lowered = text.lower()
    for secret in secrets:
        if not secret:
            continue
        if secret.lower() in lowered:
            msg = "Credential material leaked into user-facing text."
            raise AssertionError(msg)
        for fragment in credential_fragments(secret):
            if fragment.lower() in lowered:
                msg = "Credential fragment leaked into user-facing text."
                raise AssertionError(msg)
