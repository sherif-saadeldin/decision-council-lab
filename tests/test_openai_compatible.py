from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openai import AuthenticationError

from council.config import Settings
from council.providers.errors import MissingProviderConfigError, MissingProviderCredentialError
from council.providers.factory import SUPPORTED_LLM_MODES, create_provider
from council.providers.openai_compatible import OpenAICompatibleProvider
from council.providers.openai_errors import auth_failed_message
from council.providers.openai_provider import OpenAIProvider
from council.providers.models import ProviderRequest
from council.models import AgentRole
from tests.test_openai_provider import VALID_BRIEF_PAYLOAD

PLACEHOLDER_KEY = "your-key-here"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def test_supported_modes_includes_openai_compatible() -> None:
    assert "openai_compatible" in SUPPORTED_LLM_MODES


def test_factory_creates_openai_provider() -> None:
    settings = Settings(
        llm_mode="openai",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        openai_api_key="test-key",
        openai_model="gpt-4.1-mini",
    )
    provider = create_provider(settings)
    assert isinstance(provider, OpenAIProvider)


def test_factory_creates_openai_compatible_provider() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="openrouter",
        llm_base_url=OPENROUTER_BASE,
        llm_api_key="test-key",
        llm_model="anthropic/claude-sonnet-4.5",
    )
    provider = create_provider(settings)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.metadata.provider_name == "openrouter"
    assert provider.metadata.mode == "openai_compatible"
    assert provider.metadata.model_name == "anthropic/claude-sonnet-4.5"


def test_missing_llm_api_key_raises_credential_error() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="openrouter",
        llm_base_url=OPENROUTER_BASE,
        llm_api_key=None,
        llm_model="anthropic/claude-sonnet-4.5",
    )
    with pytest.raises(MissingProviderCredentialError) as exc_info:
        create_provider(settings)
    assert exc_info.value.env_var == "LLM_API_KEY"
    assert exc_info.value.provider_name == "openrouter"
    assert PLACEHOLDER_KEY not in str(exc_info.value)


def test_missing_llm_base_url_raises_config_error() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="groq",
        llm_base_url=None,
        llm_api_key="test-key",
        llm_model="llama-3.3-70b-versatile",
    )
    with pytest.raises(MissingProviderConfigError) as exc_info:
        create_provider(settings)
    assert exc_info.value.setting_name == "LLM_BASE_URL"
    assert exc_info.value.provider_name == "groq"


def test_missing_llm_model_raises_config_error() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="together",
        llm_base_url="https://api.together.xyz/v1",
        llm_api_key="test-key",
        llm_model="",
    )
    with pytest.raises(MissingProviderConfigError) as exc_info:
        create_provider(settings)
    assert exc_info.value.setting_name == "LLM_MODEL"


def test_compatible_auth_error_mentions_provider_name_not_key() -> None:
    response = MagicMock()
    response.status_code = 401
    exc = AuthenticationError(
        f"Invalid key: {PLACEHOLDER_KEY}",
        response=response,
        body=None,
    )
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = exc

    provider = OpenAICompatibleProvider(
        provider_name="openrouter",
        api_key=PLACEHOLDER_KEY,
        model_name="anthropic/claude-sonnet-4.5",
        base_url=OPENROUTER_BASE,
        client=mock_client,
    )

    from council.providers.errors import ProviderResponseError

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.complete(
            ProviderRequest(role=AgentRole.CONTEXT, question="Test?", prior_briefs=[])
        )

    message = str(exc_info.value)
    expected = auth_failed_message("openrouter", "LLM_API_KEY")
    assert expected in message
    assert PLACEHOLDER_KEY not in message
    assert "your-key" not in message.lower()
    assert OPENROUTER_BASE not in message


def test_compatible_client_uses_base_url() -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = json.dumps(VALID_BRIEF_PAYLOAD)
    mock_client.responses.create.return_value = mock_response

    provider = OpenAICompatibleProvider(
        provider_name="openrouter",
        api_key="test-key",
        model_name="anthropic/claude-sonnet-4.5",
        base_url=OPENROUTER_BASE,
        client=mock_client,
    )
    provider.complete(ProviderRequest(role=AgentRole.RESEARCH, question="Q?", prior_briefs=[]))
    mock_client.responses.create.assert_called_once()
