from __future__ import annotations

import json
import os

import pytest

from council.config import Settings
from council.engine import run_council
from council.models import AgentRole, DecisionType
from council.providers.errors import ProviderResponseError
from council.providers.parsing import parse_agent_brief_payload
from tests.conftest import assert_mock_run_schema


def test_settings_from_env_returns_mock_under_harness() -> None:
    assert Settings.from_env().llm_mode == "mock"
    assert Settings.from_env().mock_model == "mock-council-v1"


def test_env_leakage_does_not_change_patched_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5:7b")
    assert Settings.from_env().llm_mode == "mock"


def test_run_council_without_settings_uses_mock(mock_settings: Settings) -> None:
    result, _ = run_council("Isolation check?", settings=mock_settings)
    assert result.provider_metadata.provider_name == "mock"
    assert result.provider_metadata.mode == "mock"


def test_run_council_from_env_defaults_to_mock() -> None:
    result, _ = run_council("Default harness path?")
    assert result.provider_metadata.provider_name == "mock"


def test_mock_outputs_satisfy_guardrail_schema(run_mock_council) -> None:
    result = run_mock_council("Should we build an internal tool first?")
    assert_mock_run_schema(result)
    assert result.dossier.decision_type in DecisionType


def test_malformed_proposed_metrics_fail_validation() -> None:
    payload = {
        "headline": "H",
        "role_specific_finding": "F",
        "evidence_basis": "E",
        "uncertainty": "U",
        "decision_implication": "I",
        "reasoning": "R",
        "confidence": 0.5,
        "source_refs": [],
        "evidence_gaps": [],
        "proposed_metrics": ["error rate below 5%"],
        "unsupported_assumptions": [],
    }
    with pytest.raises(ProviderResponseError, match="validation failed"):
        parse_agent_brief_payload(json.dumps(payload), AgentRole.RISK, provider_name="mock")


def test_no_live_llm_calls_during_mock_run(
    mock_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from council.providers import openai_compatible

    calls = 0
    original = openai_compatible.OpenAI

    def counting_openai(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(openai_compatible, "OpenAI", counting_openai)
    result, _ = run_council("No network?", settings=mock_settings)
    assert result.provider_metadata.provider_name == "mock"
    assert calls == 0


def test_real_from_env_reads_env_when_opted_in(
    real_settings_from_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODE", "openai_compatible")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    assert Settings.from_env().llm_mode == "openai_compatible"
    assert Settings.from_env().llm_base_url == "http://localhost:11434/v1"


def test_provider_env_keys_cleared_from_os_environ() -> None:
    for key in ("OPENAI_API_KEY", "LLM_API_KEY", "LLM_BASE_URL"):
        assert os.getenv(key) in (None, "")
