from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from council.redaction import redact_secrets

RAW_RESPONSE_FILENAME = "raw_response.txt"


def save_raw_response(
    runs_dir: Path,
    run_id: str,
    raw_text: str,
    secrets: Iterable[str] | None = None,
) -> Path:
    """Write sanitized provider output for parse-failure debugging."""
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / RAW_RESPONSE_FILENAME
    sanitized = redact_secrets(raw_text, secrets)
    path.write_text(sanitized, encoding="utf-8")
    return path
