from __future__ import annotations

from typing import Literal

from openai import APIConnectionError, APIStatusError, AuthenticationError, RateLimitError

ApiModePreference = Literal["responses", "chat", "auto"]
ApiModeUsed = Literal["responses", "chat"]

SUPPORTED_API_MODE_PREFERENCES: frozenset[str] = frozenset({"responses", "chat", "auto"})
DEFAULT_API_MODE: ApiModePreference = "auto"


class InvalidApiModeError(ValueError):
    def __init__(self, value: str) -> None:
        supported = ", ".join(sorted(SUPPORTED_API_MODE_PREFERENCES))
        super().__init__(f"Invalid api mode {value!r}. Supported: {supported}.")


def normalize_api_mode(value: str | None) -> ApiModePreference:
    if value is None or not str(value).strip():
        return DEFAULT_API_MODE
    normalized = str(value).strip().lower()
    if normalized not in SUPPORTED_API_MODE_PREFERENCES:
        raise InvalidApiModeError(normalized)
    return normalized  # type: ignore[return-value]


def should_fallback_to_chat(
    exc: BaseException,
    *,
    provider_name: str,
) -> bool:
    """Whether auto mode should retry via chat completions after a responses API failure."""
    if isinstance(exc, (AuthenticationError, RateLimitError)):
        return False
    if isinstance(exc, APIStatusError):
        if exc.status_code in {401, 403, 429}:
            return False
        if exc.status_code in {404, 405, 501, 500, 502, 503}:
            return True
    if isinstance(exc, AttributeError):
        return True
    if provider_name == "ollama":
        return True
    message = str(exc).lower()
    if "responses" in message and any(
        token in message for token in ("not found", "unsupported", "unknown", "invalid")
    ):
        return True
    if isinstance(exc, APIConnectionError):
        return provider_name == "ollama"
    return provider_name == "ollama"
