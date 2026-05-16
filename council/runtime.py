from __future__ import annotations

from dataclasses import dataclass

from council.providers.api_mode import DEFAULT_API_MODE, ApiModePreference

FAST_DEBATE_ROUNDS = 0
DEFAULT_TIMEOUT_SECONDS = 120.0
LOCAL_PROVIDER_MAX_RETRIES = 1
DEFAULT_SMOKE_MAX_RUN_SECONDS = 900.0


class RunBudgetExceededError(RuntimeError):
    def __init__(self, limit_seconds: float) -> None:
        self.limit_seconds = limit_seconds
        super().__init__(f"Council run exceeded maximum wall time of {limit_seconds:g}s.")


@dataclass(frozen=True)
class RuntimeOptions:
    timeout_seconds: float | None = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = 0
    fast_mode: bool = False
    show_progress: bool = True
    repair_json: bool = False
    api_mode: ApiModePreference = DEFAULT_API_MODE
    max_run_seconds: float | None = None
    system_profile: str = "default"


def cap_retries_for_provider(max_retries: int, provider_name: str) -> int:
    capped = max(0, max_retries)
    if provider_name == "ollama":
        return min(capped, LOCAL_PROVIDER_MAX_RETRIES)
    return capped
