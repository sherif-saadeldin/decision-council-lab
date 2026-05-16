from __future__ import annotations

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

from council.providers.errors import ProviderResponseError

AUTH_FAILED_MESSAGE = "OpenAI authentication failed. Check OPENAI_API_KEY."
RATE_LIMIT_MESSAGE = "OpenAI rate limit exceeded. Retry later or use a lower-cost model."
NETWORK_MESSAGE = "OpenAI request failed due to a network or connection issue."
GENERIC_API_MESSAGE = "OpenAI API call failed. See debug logs if enabled."


def safe_openai_error_message(exc: BaseException) -> str:
    """Map OpenAI SDK exceptions to safe, user-facing messages without raw payloads."""
    if isinstance(exc, AuthenticationError):
        return AUTH_FAILED_MESSAGE
    if isinstance(exc, APIStatusError) and exc.status_code == 401:
        return AUTH_FAILED_MESSAGE
    if isinstance(exc, RateLimitError):
        return RATE_LIMIT_MESSAGE
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return NETWORK_MESSAGE
    return GENERIC_API_MESSAGE


def raise_openai_provider_error(exc: BaseException) -> None:
    """Raise ProviderResponseError with a sanitized message and no exception chain."""
    detail = safe_openai_error_message(exc)
    raise ProviderResponseError("openai", detail, source="api") from None
