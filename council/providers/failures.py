from __future__ import annotations

from council.providers.errors import (
    FailureKind,
    MissingProviderCredentialError,
    ProviderResponseError,
)


def classify_provider_failure(exc: Exception) -> FailureKind:
    """Map exceptions to a concise smoke-safe failure category."""
    if isinstance(exc, ProviderResponseError):
        if exc.failure_kind:
            return exc.failure_kind
        if exc.source == "response":
            return "parse_failure"
        detail = exc.detail.lower()
        if "authentication" in detail or "api key" in detail:
            return "auth_failure"
        if "timed out" in detail or "timeout" in detail:
            return "timeout"
        if "network" in detail or "connection" in detail:
            return "network_failure"
        return "api_failure"

    if isinstance(exc, MissingProviderCredentialError):
        return "auth_failure"

    message = str(exc).lower()
    if "timed out" in message or "timeout" in message:
        return "timeout"
    if "network" in message or "connection" in message:
        return "network_failure"
    if "authentication" in message or "api key" in message:
        return "auth_failure"
    if "json" in message or "validation failed" in message or "malformed" in message:
        return "parse_failure"
    return "unknown"
