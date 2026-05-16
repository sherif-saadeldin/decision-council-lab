from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from council.config import Settings
from council.engine import get_provider, run_council
from council.models import AgentRole, RUN_SCHEMA_VERSION
from council.providers.errors import MissingProviderCredentialError, ProviderResponseError
from council.providers.factory import create_provider
from council.providers.openai_provider import OpenAIProvider
from council.providers.parsing import parse_agent_brief_payload, parse_dossier_payload
from council.providers.models import ProviderRequest

VALID_BRIEF_PAYLOAD = {
    "headline": "Test headline",
    "role_specific_finding": "Specific finding",
    "evidence_basis": "Evidence text",
    "uncertainty": "Main uncertainty",
    "decision_implication": "Implication text",
    "reasoning": "Test reasoning",
    "confidence": 0.82,
    "source_refs": ["note-a"],
}

VALID_DOSSIER_PAYLOAD = {
    "decision_type": "proceed_with_constraints",
    "disagreement_resolution": "Resolved tension by weighting execution learning.",
    "strongest_argument_for": "For argument",
    "strongest_argument_against": "Against argument",
    "deciding_factor": "Deciding factor",
    "confidence_rationale": "Because evidence is mixed but directionally clear.",
    "assumptions": ["Assumption"],
    "arguments_for": ["For"],
    "arguments_against": ["Against"],
    "risks": ["Risk"],
    "recommendation": "Proceed with constraints",
    "confidence_score": 0.7,
    "kill_criteria": ["Kill"],
    "next_actions": ["Next"],
    "open_questions": ["Open"],
}


def test_openai_provider_metadata_without_api_call() -> None:
    provider = OpenAIProvider(api_key="test-key", model_name="gpt-4.1-mini")
    meta = provider.metadata

    assert meta.provider_name == "openai"
    assert meta.mode == "openai"
    assert meta.model_name == "gpt-4.1-mini"
    assert meta.supports_structured_output is True
    assert meta.supports_streaming is False


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


def test_missing_openai_api_key_raises_clear_error() -> None:
    settings = Settings(
        llm_mode="openai",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        openai_api_key=None,
        openai_model="gpt-4.1-mini",
    )

    with pytest.raises(MissingProviderCredentialError) as exc_info:
        create_provider(settings)

    err = exc_info.value
    assert err.env_var == "OPENAI_API_KEY"
    assert err.provider_name == "openai"
    assert "OPENAI_API_KEY" in str(err)
    assert "sk-" not in str(err)


def test_sk_prefix_key_not_in_generic_api_error() -> None:
    secret = "sk-super-secret-key-do-not-log"
    mock_client = MagicMock()
    mock_client.responses.create.side_effect = RuntimeError(f"unexpected failure: {secret}")

    provider = OpenAIProvider(
        api_key=secret,
        model_name="gpt-4.1-mini",
        client=mock_client,
    )

    with pytest.raises(ProviderResponseError) as exc_info:
        provider.complete(
            ProviderRequest(
                role=AgentRole.RISK,
                question="Should we ship?",
                prior_briefs=[],
            )
        )

    message = str(exc_info.value)
    assert secret not in message
    assert "sk-" not in message
    assert "unexpected failure" not in message


def test_parse_agent_brief_rejects_malformed_output() -> None:
    with pytest.raises(ProviderResponseError, match="valid JSON"):
        parse_agent_brief_payload("not-json", AgentRole.CONTEXT, provider_name="openai")

    malformed = json.dumps({"headline": "Only headline"})
    with pytest.raises(ProviderResponseError, match="validation failed"):
        parse_agent_brief_payload(malformed, AgentRole.RESEARCH, provider_name="openai")


def test_parse_agent_brief_accepts_valid_payload() -> None:
    brief = parse_agent_brief_payload(
        json.dumps(VALID_BRIEF_PAYLOAD),
        AgentRole.SKEPTIC,
        provider_name="openai",
    )
    assert brief.role == AgentRole.SKEPTIC
    assert brief.headline == "Test headline"
    assert brief.role_specific_finding == "Specific finding"
    assert brief.confidence == 0.82
    assert brief.source_refs == ["note-a"]


def test_parse_dossier_payload_requires_decision_type() -> None:
    dossier = parse_dossier_payload(
        json.dumps(VALID_DOSSIER_PAYLOAD),
        question="Ship?",
        run_id="run-1",
        provider_name="openai",
    )
    assert dossier.decision_type.value == "proceed_with_constraints"
    assert dossier.strongest_argument_for == "For argument"


def test_openai_complete_uses_mocked_client() -> None:
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.output_text = json.dumps(VALID_BRIEF_PAYLOAD)
    mock_client.responses.create.return_value = mock_response

    provider = OpenAIProvider(
        api_key="test-key",
        model_name="gpt-4.1-mini",
        client=mock_client,
    )
    response = provider.complete(
        ProviderRequest(
            role=AgentRole.CONTEXT,
            question="Should we ship?",
            prior_briefs=[],
        )
    )

    assert response.brief.headline == "Test headline"
    assert response.latency_ms is not None
    mock_client.responses.create.assert_called_once()
    call_kwargs = mock_client.responses.create.call_args.kwargs
    assert call_kwargs["stream"] is False
    assert "test-key" not in str(call_kwargs)


def test_mock_mode_still_works_after_openai_registration() -> None:
    settings = Settings(
        llm_mode="mock",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
    )
    provider = get_provider(settings)
    result, _ = run_council("Internal tool first?", settings=settings)

    assert provider.metadata.provider_name == "mock"
    assert result.provider_metadata.provider_name == "mock"
    assert result.schema_version == RUN_SCHEMA_VERSION
    assert result.dossier.decision_type
