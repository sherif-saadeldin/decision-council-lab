from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from council.json_extract import extract_json_text
from council.models import AgentRole
from council.providers.errors import ProviderResponseError
from council.providers.failures import classify_provider_failure
from council.providers.models import ProviderRequest
from council.providers.openai_compatible import OpenAICompatibleProvider
from council.providers.parsing import parse_agent_brief_payload
from council.raw_response_debug import RAW_RESPONSE_FILENAME, save_raw_response
from council.redaction import assert_no_credential_leaks
SECRET = "sk-9f3a2b1c8e7d6a5b4c3d2e1f0a9b8c7d6"

VALID_BRIEF = {
    "headline": "Smoke headline",
    "role_specific_finding": "Finding",
    "evidence_basis": "Basis",
    "uncertainty": "Low",
    "decision_implication": "Proceed",
    "reasoning": "Reasoning",
    "confidence": 0.7,
    "source_refs": [],
    "evidence_gaps": [],
    "proposed_metrics": ["proposed: latency p95"],
    "unsupported_assumptions": [],
}


def test_extract_fenced_json_block() -> None:
    raw = 'Here is output:\n```json\n{"headline": "Hi"}\n```\nThanks'
    extracted = extract_json_text(raw)
    assert json.loads(extracted)["headline"] == "Hi"


def test_extract_json_after_prose() -> None:
    raw = f"Analysis first.\n{json.dumps(VALID_BRIEF)}"
    payload = json.loads(extract_json_text(raw))
    assert payload["headline"] == "Smoke headline"


def test_malformed_json_still_fails_safely() -> None:
    with pytest.raises(ProviderResponseError) as exc_info:
        parse_agent_brief_payload(
            "not json at all { broken",
            AgentRole.CONTEXT,
            provider_name="ollama",
        )
    assert exc_info.value.failure_kind == "parse_failure"


def test_parse_agent_brief_with_fenced_json() -> None:
    raw = f"```json\n{json.dumps(VALID_BRIEF)}\n```"
    brief = parse_agent_brief_payload(raw, AgentRole.CONTEXT, provider_name="ollama")
    assert brief.headline == "Smoke headline"


def test_raw_response_saved_on_parse_failure(tmp_path: Path) -> None:
    run_id = "run-parse-fail"
    client = MagicMock()
    broken = json.dumps({**VALID_BRIEF, "confidence": "not-a-number"})
    client.responses.create.return_value = MagicMock(output_text=broken)
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key=SECRET,
        model_name="qwen2.5:7b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        runs_dir=tmp_path,
        repair_json=False,
    )

    with pytest.raises(ProviderResponseError):
        provider.complete(
            ProviderRequest(
                role=AgentRole.CONTEXT,
                question="Smoke?",
                prior_briefs=[],
                run_id=run_id,
            )
        )

    raw_path = tmp_path / run_id / RAW_RESPONSE_FILENAME
    assert raw_path.exists()
    assert_no_credential_leaks(raw_path.read_text(encoding="utf-8"), [SECRET])


def test_raw_response_redacts_secrets(tmp_path: Path) -> None:
    path = save_raw_response(tmp_path, "run-1", f"Bearer {SECRET}", [SECRET])
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "[REDACTED]" in text


def test_repair_json_retries_once_on_parse_failure(tmp_path: Path) -> None:
    client = MagicMock()
    invalid = "Sure! " + json.dumps({**VALID_BRIEF, "confidence": "high"})
    valid = json.dumps(VALID_BRIEF)
    client.responses.create.side_effect = [
        MagicMock(output_text=invalid),
        MagicMock(output_text=valid),
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
    )
    response = provider.complete(
        ProviderRequest(
            role=AgentRole.CONTEXT,
            question="Smoke?",
            prior_briefs=[],
            run_id="repair-run",
        )
    )
    assert response.brief.headline == "Smoke headline"
    assert client.responses.create.call_count == 2
    repair_instructions = client.responses.create.call_args_list[1].kwargs["instructions"]
    assert "ONLY valid JSON" in repair_instructions


def test_repair_json_not_used_for_openai_mode(tmp_path: Path) -> None:
    client = MagicMock()
    invalid = json.dumps({**VALID_BRIEF, "confidence": "bad"})
    client.responses.create.return_value = MagicMock(output_text=invalid)
    provider = OpenAICompatibleProvider(
        provider_name="openai",
        api_key="test-key",
        model_name="gpt-4.1-mini",
        mode="openai",
        client=client,
        runs_dir=tmp_path,
        repair_json=True,
    )
    with pytest.raises(ProviderResponseError):
        provider.complete(
            ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="x")
        )
    assert client.responses.create.call_count == 1


def test_classify_parse_failure() -> None:
    exc = ProviderResponseError("ollama", "response was not valid JSON.", failure_kind="parse_failure")
    assert classify_provider_failure(exc) == "parse_failure"


def test_classify_auth_failure_from_api_error() -> None:
    exc = ProviderResponseError(
        "openrouter",
        "openrouter authentication failed. Check LLM_API_KEY.",
        source="api",
        failure_kind="auth_failure",
    )
    assert classify_provider_failure(exc) == "auth_failure"
