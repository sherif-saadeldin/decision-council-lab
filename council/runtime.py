from __future__ import annotations

from dataclasses import dataclass

from council.providers.api_mode import DEFAULT_API_MODE, ApiModePreference

FAST_DEBATE_ROUNDS = 0
DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class RuntimeOptions:
    timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = 0
    fast_mode: bool = False
    show_progress: bool = True
    repair_json: bool = False
    api_mode: ApiModePreference = DEFAULT_API_MODE
