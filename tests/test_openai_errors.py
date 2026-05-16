from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from openai import APIConnectionError, AuthenticationError, RateLimitError

from council.models import AgentRole
from council.providers.errors import ProviderResponseError
from council.providers.models import ProviderRequest
from council.providers.openai_errors import (
    AUTH_FAILED_MESSAGE,
    GENERIC_API_MESSAGE,
    NETWORK_MESSAGE,
    RATE_LIMIT_MESSAGE,
    safe_openai_error_message,
)
from council.providers.openai_provider import OpenAIProvider
from council.redaction import assert_no_credential_leaks, redact_secrets

PLACEHOLDER_KEY = "your-key-here"


def _auth_error(message: str) -> AuthenticationError:
    response = MagicMock()
    response.status_code = 401
    return AuthenticationError(message, response=response, body=None)


def test_safe_message_for_authentication_error() -> None:
    exc = _auth_error(f"Incorrect API key provided: {PLACEHOLDER_KEY}")
    assert safe_openai_error_message(exc) == AUTH_FAILED_MESSAGE


def test_authentication_error_never_leaks_placeholder_key() -> None:
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = _auth_error(
        f"Error code: 401 - Incorrect API key provided: {PLACEHOLDER_KEY}. "
        f"You can find your API key at https://platform.openai.com/account/api-keys."
    )

    provider = OpenAIProvider(
        api_key=PLACEHOLDER_KEY,
        model_name="gpt-4.1-mini",
        client=mock_client,
    )

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.complete(
            ProviderRequest(
                role=AgentRole.CONTEXT,
                question="Should we ship?",
                prior_briefs=[],
            )
        )

    message = str(exc_info.value)
    assert exc_info.value.source == "api"
    assert AUTH_FAILED_MESSAGE in message
    assert "your-key" not in message.lower()
    assert PLACEHOLDER_KEY not in message
    assert "401" not in message
    assert "Incorrect API key" not in message
    assert exc_info.value.__cause__ is None
    assert_no_credential_leaks(message, [PLACEHOLDER_KEY])


def test_rate_limit_error_message() -> None:
    response = MagicMock()
    response.status_code = 429
    exc = RateLimitError("Rate limit exceeded", response=response, body=None)
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = exc

    provider = OpenAIProvider(api_key="test-key", model_name="gpt-4.1-mini", client=mock_client)

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.complete(
            ProviderRequest(role=AgentRole.RISK, question="Q?", prior_briefs=[])
        )

    assert RATE_LIMIT_MESSAGE in str(exc_info.value)
    assert "Rate limit exceeded" not in str(exc_info.value)


def test_connection_error_message() -> None:
    request = MagicMock()
    exc = APIConnectionError(request=request)
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = exc

    provider = OpenAIProvider(api_key="test-key", model_name="gpt-4.1-mini", client=mock_client)

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.complete(
            ProviderRequest(role=AgentRole.OPERATOR, question="Q?", prior_briefs=[])
        )

    assert NETWORK_MESSAGE in str(exc_info.value)


def test_generic_api_error_message() -> None:
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = RuntimeError(
        f"server exploded while using key {PLACEHOLDER_KEY}"
    )

    provider = OpenAIProvider(api_key=PLACEHOLDER_KEY, model_name="gpt-4.1-mini", client=mock_client)

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.complete(
            ProviderRequest(role=AgentRole.RESEARCH, question="Q?", prior_briefs=[])
        )

    message = str(exc_info.value)
    assert GENERIC_API_MESSAGE in message
    assert PLACEHOLDER_KEY not in message
    assert "server exploded" not in message


def test_redaction_removes_placeholder_fragments_in_debug_text() -> None:
    raw = f"Incorrect API key provided: {PLACEHOLDER_KEY}"
    redacted = redact_secrets(raw, [PLACEHOLDER_KEY])
    assert PLACEHOLDER_KEY not in redacted
    assert "your-key" not in redacted.lower()
