from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from openai import APIStatusError

from council.config import Settings
from council.models import AgentRole
from council.providers.api_mode import normalize_api_mode, should_fallback_to_chat
from council.providers.factory import create_provider
from council.providers.models import ProviderRequest
from council.providers.openai_compatible import OpenAICompatibleProvider
from council.runtime import RuntimeOptions
from tests.test_openai_provider import VALID_BRIEF_PAYLOAD


def _api_status_error(status: int, message: str = "error") -> APIStatusError:
    response = MagicMock()
    response.status_code = status
    return APIStatusError(message, response=response, body=None)


def test_normalize_api_mode_defaults_to_auto() -> None:
    assert normalize_api_mode(None) == "auto"
    assert normalize_api_mode("auto") == "auto"


def test_normalize_api_mode_invalid_raises() -> None:
    with pytest.raises(ValueError, match="Invalid api mode"):
        normalize_api_mode("websocket")


def test_should_fallback_for_ollama_and_404() -> None:
    assert should_fallback_to_chat(_api_status_error(404), provider_name="openrouter")
    assert should_fallback_to_chat(_api_status_error(404), provider_name="ollama")
    assert not should_fallback_to_chat(_api_status_error(401), provider_name="ollama")


def test_responses_mode_uses_responses_api_only() -> None:
    client = MagicMock()
    client.responses.create.return_value = MagicMock(
        output_text=json.dumps(VALID_BRIEF_PAYLOAD)
    )
    provider = OpenAICompatibleProvider(
        provider_name="openrouter",
        api_key="test-key",
        model_name="test-model",
        mode="openai_compatible",
        base_url="https://example.com/v1",
        client=client,
        api_mode="responses",
    )
    provider.complete(
        ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="r1")
    )
    client.responses.create.assert_called_once()
    client.chat.completions.create.assert_not_called()
    assert provider.metadata.api_mode_used == "responses"


def test_chat_mode_uses_chat_completions_only() -> None:
    client = MagicMock()
    message = MagicMock(content=json.dumps(VALID_BRIEF_PAYLOAD))
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=message)]
    )
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key="ollama",
        model_name="qwen2.5:7b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        api_mode="chat",
    )
    provider.complete(
        ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="r2")
    )
    client.chat.completions.create.assert_called_once()
    client.responses.create.assert_not_called()
    assert provider.metadata.api_mode_used == "chat"


def test_auto_mode_falls_back_to_chat_on_responses_failure() -> None:
    client = MagicMock()
    client.responses.create.side_effect = _api_status_error(404, "responses not found")
    message = MagicMock(content=json.dumps(VALID_BRIEF_PAYLOAD))
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=message)]
    )
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key="ollama",
        model_name="qwen2.5:7b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        api_mode="auto",
    )
    provider.complete(
        ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="r3")
    )
    client.responses.create.assert_called_once()
    client.chat.completions.create.assert_called_once()
    assert provider.metadata.api_mode_used == "chat"


def test_auto_locks_chat_for_subsequent_calls() -> None:
    client = MagicMock()
    client.responses.create.side_effect = _api_status_error(404)
    message = MagicMock(content=json.dumps(VALID_BRIEF_PAYLOAD))
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=message)]
    )
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key="ollama",
        model_name="qwen2.5:7b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        api_mode="auto",
    )
    request = ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="r4")
    provider.complete(request)
    provider.complete(request)
    assert client.responses.create.call_count == 1
    assert client.chat.completions.create.call_count == 2


def test_repair_json_works_in_chat_mode(tmp_path: Path) -> None:
    client = MagicMock()
    invalid = json.dumps({**VALID_BRIEF_PAYLOAD, "confidence": "high"})
    valid = json.dumps(VALID_BRIEF_PAYLOAD)
    message_invalid = MagicMock(content=invalid)
    message_valid = MagicMock(content=valid)
    client.chat.completions.create.side_effect = [
        MagicMock(choices=[MagicMock(message=message_invalid)]),
        MagicMock(choices=[MagicMock(message=message_valid)]),
    ]
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key="ollama",
        model_name="qwen2.5:7b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        runs_dir=tmp_path,
        repair_json=True,
        api_mode="chat",
    )
    brief = provider.complete(
        ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="repair-chat")
    ).brief
    assert brief.headline == VALID_BRIEF_PAYLOAD["headline"]
    assert client.chat.completions.create.call_count == 2


def test_factory_defaults_api_mode_auto() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="ollama",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="ollama",
        llm_model="qwen2.5:7b",
    )
    provider = create_provider(settings, runtime=RuntimeOptions())
    assert provider.metadata.api_mode_preference == "auto"


def test_runtime_options_passes_chat_mode() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="ollama",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="ollama",
        llm_model="qwen2.5:7b",
    )
    provider = create_provider(settings, runtime=RuntimeOptions(api_mode="chat"))
    assert provider.metadata.api_mode_preference == "chat"
