from __future__ import annotations

from openai import APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError

from council.providers.errors import ProviderResponseError


def provider_label(provider_name: str) -> str:
    return "OpenAI" if provider_name == "openai" else provider_name


def auth_failed_message(provider_name: str, credential_env: str) -> str:
    return f"{provider_label(provider_name)} authentication failed. Check {credential_env}."


def rate_limit_message(provider_name: str) -> str:
    return (
        f"{provider_label(provider_name)} rate limit exceeded. "
        "Retry later or use a lower-cost model."
    )


def network_message(provider_name: str) -> str:
    return f"{provider_label(provider_name)} request failed due to a network or connection issue."


def timeout_message(seconds: float) -> str:
    return f"Provider request timed out after {seconds:g} seconds."


def generic_api_message(provider_name: str) -> str:
    return f"{provider_label(provider_name)} API call failed. See debug logs if enabled."


# Backward-compatible OpenAI direct constants
AUTH_FAILED_MESSAGE = auth_failed_message("openai", "OPENAI_API_KEY")
RATE_LIMIT_MESSAGE = rate_limit_message("openai")
NETWORK_MESSAGE = network_message("openai")
GENERIC_API_MESSAGE = generic_api_message("openai")


def safe_compatible_error_message(
    exc: BaseException,
    *,
    provider_name: str,
    credential_env: str,
    timeout_seconds: float | None = None,
) -> str:
    """Map OpenAI SDK exceptions to safe, user-facing messages without raw payloads."""
    if isinstance(exc, AuthenticationError):
        return auth_failed_message(provider_name, credential_env)
    if isinstance(exc, APIStatusError) and exc.status_code == 401:
        return auth_failed_message(provider_name, credential_env)
    if isinstance(exc, RateLimitError):
        return rate_limit_message(provider_name)
    if isinstance(exc, APITimeoutError):
        if timeout_seconds is not None:
            return timeout_message(timeout_seconds)
        return network_message(provider_name)
    if isinstance(exc, APIConnectionError):
        return network_message(provider_name)
    return generic_api_message(provider_name)


def safe_openai_error_message(exc: BaseException) -> str:
    return safe_compatible_error_message(
        exc,
        provider_name="openai",
        credential_env="OPENAI_API_KEY",
    )


def raise_compatible_provider_error(
    exc: BaseException,
    *,
    provider_name: str,
    credential_env: str,
    timeout_seconds: float | None = None,
) -> None:
    """Raise ProviderResponseError with a sanitized message and no exception chain."""
    detail = safe_compatible_error_message(
        exc,
        provider_name=provider_name,
        credential_env=credential_env,
        timeout_seconds=timeout_seconds,
    )
    raise ProviderResponseError(provider_name, detail, source="api") from None


def raise_openai_provider_error(exc: BaseException) -> None:
    raise_compatible_provider_error(
        exc,
        provider_name="openai",
        credential_env="OPENAI_API_KEY",
    )
