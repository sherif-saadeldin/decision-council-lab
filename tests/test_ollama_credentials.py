from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from council.config import Settings
from council.credentials import OLLAMA_DUMMY_API_KEY, resolve_llm_api_key
from council.doctor import CheckStatus, run_doctor
from council.model_presets import apply_preset
from council.models import AgentRole
from council.providers.errors import MissingProviderCredentialError
from council.providers.factory import create_provider
from council.providers.models import ProviderRequest
from council.providers.openai_compatible import OpenAICompatibleProvider
from council.raw_response_debug import save_raw_response
from council.redaction import assert_no_credential_leaks
from council.smoke import SmokeRequest, run_smoke, run_smoke_preflight
REAL_LLM_KEY = "sk-test-openrouter-key-abc123xyz"


def _ollama_settings_without_key() -> Settings:
    return Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="ollama",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key=None,
        llm_model="qwen3.5:9b",
    )


def test_resolve_llm_api_key_uses_dummy_for_ollama() -> None:
    settings = _ollama_settings_without_key()
    assert resolve_llm_api_key(settings) == OLLAMA_DUMMY_API_KEY


def test_resolve_llm_api_key_openrouter_requires_real_key() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="openrouter",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_api_key=None,
        llm_model="anthropic/claude-sonnet-4.5",
    )
    assert resolve_llm_api_key(settings) is None


def test_ollama_preset_works_without_llm_api_key() -> None:
    base = Settings(
        llm_mode="mock",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_api_key=None,
    )
    settings = apply_preset(base, "ollama-qwen")
    assert settings.llm_api_key == OLLAMA_DUMMY_API_KEY
    provider = create_provider(settings)
    assert provider.metadata.provider_name == "ollama"


def test_factory_ollama_without_key_uses_dummy() -> None:
    provider = create_provider(_ollama_settings_without_key())
    assert provider._api_key == OLLAMA_DUMMY_API_KEY  # type: ignore[attr-defined]


def test_factory_openrouter_without_key_raises() -> None:
    settings = Settings(
        llm_mode="openai_compatible",
        runs_dir=Path("./runs"),
        mock_model="mock-council-v1",
        llm_provider_name="openrouter",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_api_key=None,
        llm_model="anthropic/claude-sonnet-4.5",
    )
    with pytest.raises(MissingProviderCredentialError) as exc_info:
        create_provider(settings)
    assert exc_info.value.env_var == "LLM_API_KEY"


def test_doctor_ollama_reports_key_not_required() -> None:
    settings = apply_preset(
        Settings(
            llm_mode="mock",
            runs_dir=Path("./runs"),
            mock_model="mock-council-v1",
            llm_api_key=None,
        ),
        "ollama-qwen",
    )

    def fake_tags(url: str, timeout: float) -> tuple[bool, list[str] | None, str]:
        return True, ["qwen3.5:9b"], "Found 1 installed model(s)"

    checks = run_doctor(settings, tags_fetcher=fake_tags)
    key_check = next(c for c in checks if c.name == "LLM_API_KEY")
    assert key_check.status == CheckStatus.OK
    assert "not required for Ollama" in key_check.message


def test_smoke_preflight_passes_without_llm_api_key_for_ollama() -> None:
    settings = apply_preset(
        Settings(
            llm_mode="mock",
            runs_dir=Path("./runs"),
            mock_model="mock-council-v1",
            llm_api_key=None,
        ),
        "ollama-qwen",
    )

    def fake_tags(url: str, timeout: float) -> tuple[bool, list[str] | None, str]:
        return True, ["qwen3.5:9b"], "Found 1 installed model(s)"

    from council.runtime import RuntimeOptions

    ok, stage, message = run_smoke_preflight(
        settings,
        RuntimeOptions(),
        doctor_fn=lambda s, **kw: run_doctor(s, tags_fetcher=fake_tags, **kw),
    )
    assert ok is True
    assert stage is None
    assert message is None


def test_dummy_key_not_written_to_raw_response_artifact(tmp_path: Path) -> None:
    raw = f"Bearer {OLLAMA_DUMMY_API_KEY} echoed in provider output"
    path = save_raw_response(tmp_path, "run-1", raw, None)
    text = path.read_text(encoding="utf-8")
    assert OLLAMA_DUMMY_API_KEY not in text
    assert "[REDACTED]" in text


def test_dummy_key_not_leaked_in_provider_error_redaction(tmp_path: Path) -> None:
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError(
        f"auth failed with key {OLLAMA_DUMMY_API_KEY}"
    )
    provider = OpenAICompatibleProvider(
        provider_name="ollama",
        api_key=OLLAMA_DUMMY_API_KEY,
        model_name="qwen3.5:9b",
        mode="openai_compatible",
        base_url="http://localhost:11434/v1",
        client=client,
        api_mode="chat",
    )
    with pytest.raises(Exception) as exc_info:
        provider.complete(
            ProviderRequest(
                role=AgentRole.CONTEXT,
                question="Q?",
                prior_briefs=[],
                run_id="no-leak",
            )
        )
    message = str(exc_info.value)
    assert_no_credential_leaks(message, [REAL_LLM_KEY])


def test_smoke_error_output_excludes_dummy_key(tmp_path: Path) -> None:
    from council.models import CouncilRunResult
    from council.providers.errors import ProviderResponseError

    def failing_run(question: str, **kwargs: object) -> tuple[CouncilRunResult, None]:
        raise ProviderResponseError(
            "ollama",
            f"failed with token {OLLAMA_DUMMY_API_KEY}",
            source="api",
        )

    report = run_smoke(
        SmokeRequest(preset="ollama-qwen", runs_dir=tmp_path, skip_preflight=True),
        run_council_fn=failing_run,
        save_run_fn=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("save")),
    )
    assert report.error
    assert OLLAMA_DUMMY_API_KEY not in report.error
    assert_no_credential_leaks(report.error, [REAL_LLM_KEY])
