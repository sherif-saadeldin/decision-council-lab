from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from council.doctor import CheckStatus, DoctorCheck, run_doctor
from council.engine import effective_debate_rounds
from council.models import AgentRole
from council.ollama_probe import (
    format_missing_model_message,
    model_is_installed,
    ollama_tags_url,
    parse_ollama_tag_names,
)
from council.providers.api_mode import resolve_effective_api_mode
from council.providers.factory import create_provider
from council.providers.models import ProviderRequest
from council.providers.openai_compatible import OpenAICompatibleProvider
from council.runtime import DEFAULT_SMOKE_MAX_RUN_SECONDS, RuntimeOptions
from council.smoke import SmokeRequest, run_smoke, run_smoke_preflight
from tests.test_openai_provider import VALID_BRIEF_PAYLOAD


def test_resolve_effective_api_mode_ollama_auto_uses_chat() -> None:
    assert resolve_effective_api_mode("auto", provider_name="ollama") == "chat"
    assert resolve_effective_api_mode("auto", provider_name="nvidia") == "chat"
    assert resolve_effective_api_mode("auto", provider_name="groq") == "chat"
    assert resolve_effective_api_mode("auto", provider_name="openrouter") == "auto"


def test_ollama_auto_skips_responses_api() -> None:
    client = MagicMock()
    message = MagicMock(content=json.dumps(VALID_BRIEF_PAYLOAD))
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=message)]
    )
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key="ollama",
        model_name="qwen3.5:9b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        api_mode="auto",
    )
    provider.complete(
        ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="r1")
    )
    client.responses.create.assert_not_called()
    client.chat.completions.create.assert_called_once()
    assert provider.metadata.api_mode_used == "chat"


def test_chat_completion_receives_timeout_kwarg() -> None:
    client = MagicMock()
    message = MagicMock(content=json.dumps(VALID_BRIEF_PAYLOAD))
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=message)]
    )
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key="ollama",
        model_name="qwen3.5:9b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        timeout_seconds=42.0,
        api_mode="chat",
    )
    provider.complete(
        ProviderRequest(role=AgentRole.CONTEXT, question="Q?", prior_briefs=[], run_id="r2")
    )
    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["timeout"] == 42.0
    assert kwargs["stream"] is False


def test_openai_client_disables_sdk_retries() -> None:
    with patch("council.providers.openai_compatible.OpenAI") as openai_cls:
        OpenAICompatibleProvider(
            provider_name="ollama",
            api_key="ollama",
            model_name="qwen3.5:9b",
            mode="openai_compatible",
            base_url="http://localhost:11434/v1",
            timeout_seconds=30.0,
            api_mode="chat",
        )
    openai_cls.assert_called_once()
    assert openai_cls.call_args.kwargs["max_retries"] == 0
    assert openai_cls.call_args.kwargs["timeout"] == 30.0


def test_smoke_runtime_defaults_debate_zero_and_fast() -> None:
    request = SmokeRequest(preset="mock", debate_rounds=0)
    assert request.debate_rounds == 0


def test_fast_mode_forces_zero_debate_rounds() -> None:
    runtime = RuntimeOptions(fast_mode=True)
    assert effective_debate_rounds(3, runtime) == 0


def test_smoke_preflight_fails_on_doctor_failure() -> None:
    from council.config import Settings

    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="ollama",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="ollama",
        llm_model="missing-model:9b",
    )
    runtime = RuntimeOptions(timeout_seconds=30.0)

    def fake_doctor(*_a: object, **_k: object) -> list[DoctorCheck]:
        return [
            DoctorCheck(
                name="ollama_model",
                status=CheckStatus.FAIL,
                message="Model 'missing-model:9b' is not installed.",
            )
        ]

    ok, stage, message = run_smoke_preflight(settings, runtime, doctor_fn=fake_doctor)
    assert ok is False
    assert stage == "ollama_model"
    assert "not installed" in (message or "")


def test_run_smoke_fails_fast_on_preflight_without_council(tmp_path: Path) -> None:
    called = {"council": False}

    def fake_run_council(question: str, **kwargs: object) -> tuple[object, None]:
        called["council"] = True
        raise AssertionError("should not run council on failed preflight")

    def fake_doctor(*_a: object, **_k: object) -> list[DoctorCheck]:
        return [
            DoctorCheck(
                name="ollama",
                status=CheckStatus.FAIL,
                message="Could not reach Ollama",
            )
        ]

    report = run_smoke(
        SmokeRequest(preset="ollama-qwen", runs_dir=tmp_path),
        run_council_fn=fake_run_council,
        doctor_fn=fake_doctor,
    )
    assert report.success is False
    assert report.failed_stage == "ollama"
    assert report.failure_reason == "preflight_failure"
    assert called["council"] is False


def test_doctor_detects_missing_ollama_model_from_tags() -> None:
    from council.config import Settings

    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="ollama",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="ollama",
        llm_model="qwen3.5:9b",
    )

    def fake_tags(url: str, timeout: float) -> tuple[bool, list[str] | None, str]:
        assert url == ollama_tags_url("http://localhost:11434/v1")
        assert timeout == 5.0
        return True, ["mistral:7b"], "Found 1 installed model(s)"

    checks = run_doctor(settings, tags_fetcher=fake_tags)
    model_check = next(c for c in checks if c.name == "ollama_model")
    assert model_check.status == CheckStatus.FAIL
    assert "qwen3.5:9b" in model_check.message
    assert "mistral:7b" in model_check.message


def test_format_missing_model_message_lists_available() -> None:
    message = format_missing_model_message("qwen3.5:9b", ["mistral:7b", "llama3:8b"])
    assert "qwen3.5:9b" in message
    assert "mistral:7b" in message
    assert "llama3:8b" in message
    assert "ollama pull" in message


def test_parse_ollama_tag_names() -> None:
    payload = {"models": [{"name": "qwen3.5:9b"}, {"name": "mistral:7b"}]}
    assert parse_ollama_tag_names(payload) == ["qwen3.5:9b", "mistral:7b"]
    assert model_is_installed("qwen3.5:9b", parse_ollama_tag_names(payload))


def test_smoke_uses_max_run_budget(tmp_path: Path) -> None:
    from council.models import CouncilRunResult, DecisionDossier, DecisionType
    from council.providers.models import ProviderMetadata

    dossier = DecisionDossier(
        decision_question="Smoke?",
        decision_type=DecisionType.PROCEED,
        confidence_score=0.5,
        deciding_factor="ok",
        recommendation="ok",
    )
    result = CouncilRunResult(
        dossier=dossier,
        provider_metadata=ProviderMetadata(
            mode="mock",
            provider_name="mock",
            model_name="mock-council-v1",
        ),
        agent_briefs=[],
    )

    captured: dict[str, object] = {}

    def fake_run_council(question: str, **kwargs: object) -> tuple[CouncilRunResult, None]:
        captured["max_run_seconds"] = getattr(kwargs.get("runtime"), "max_run_seconds", None)
        return result, None

    def fake_save(
        saved: CouncilRunResult,
        settings: object | None = None,
    ) -> tuple[Path, Path]:
        run_dir = tmp_path / saved.dossier.run_id
        run_dir.mkdir(parents=True)
        json_path = run_dir / "run.json"
        md_path = run_dir / "run.md"
        json_path.write_text("{}", encoding="utf-8")
        md_path.write_text("# run", encoding="utf-8")
        return json_path, md_path

    run_smoke(
        SmokeRequest(preset="mock", runs_dir=tmp_path, skip_preflight=True),
        run_council_fn=fake_run_council,
        save_run_fn=fake_save,
    )
    assert captured["max_run_seconds"] == DEFAULT_SMOKE_MAX_RUN_SECONDS


def test_factory_caps_ollama_retries() -> None:
    from council.config import Settings

    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="ollama",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="ollama",
        llm_model="qwen3.5:9b",
    )
    provider = create_provider(settings, runtime=RuntimeOptions(max_retries=5))
    assert provider._max_retries == 1  # type: ignore[attr-defined]
