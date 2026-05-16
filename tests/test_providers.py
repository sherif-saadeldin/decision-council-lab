from __future__ import annotations

from pathlib import Path

import pytest

from council.config import Settings
from council.engine import get_provider, run_council
from council.models import RUN_SCHEMA_VERSION, AgentRole
from council.providers.errors import UnsupportedProviderModeError
from council.providers.factory import SUPPORTED_LLM_MODES, create_provider
from council.providers.mock import MockProvider
from council.providers.models import ProviderRequest


def test_mock_provider_metadata() -> None:
    provider = MockProvider()
    meta = provider.metadata

    assert meta.provider_name == "mock"
    assert meta.model_name == "mock-council-v1"
    assert meta.mode == "mock"
    assert meta.supports_structured_output is True
    assert meta.supports_streaming is False


def test_provider_response_shape() -> None:
    provider = MockProvider()
    response = provider.complete(
        ProviderRequest(
            role=AgentRole.CONTEXT,
            question="Should we prototype internally?",
            prior_briefs=[],
            run_id="test-run",
        )
    )

    brief = response.brief
    assert brief.role == AgentRole.CONTEXT
    assert brief.headline
    assert brief.reasoning
    assert 0.0 <= brief.confidence <= 1.0
    assert brief.source_refs == []
    assert response.raw_response is not None
    assert response.latency_ms is not None
    assert response.token_usage is None


def test_unsupported_provider_mode_error() -> None:
    settings = Settings(
        llm_mode="anthropic",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
    )

    with pytest.raises(UnsupportedProviderModeError) as exc_info:
        create_provider(settings)

    err = exc_info.value
    assert err.mode == "anthropic"
    assert err.supported_modes == SUPPORTED_LLM_MODES
    assert "anthropic" in str(err).lower()

    with pytest.raises(UnsupportedProviderModeError):
        get_provider(settings)


def test_run_council_keeps_schema_version_and_metadata() -> None:
    result = run_council("Internal tool first?")

    assert result.schema_version == RUN_SCHEMA_VERSION
    assert result.provider_metadata.provider_name == "mock"
    assert len(result.provider_responses) == len(AgentRole) - 1
    assert all(item.raw_response for item in result.provider_responses)
