from __future__ import annotations

import json
from pathlib import Path

import pytest

from council.config import Settings
from council.credentials import OLLAMA_DUMMY_API_KEY, resolve_llm_api_key
from council.doctor import CheckStatus, run_doctor
from council.model_presets import (
    CEREBRAS_BASE_URL,
    FREE_HOSTED_PRESET_NAMES,
    GROQ_BASE_URL,
    MODEL_PRESETS,
    NVIDIA_BASE_URL,
    OPENROUTER_BASE_URL,
    apply_preset,
    list_preset_names,
)
from council.providers.api_mode import resolve_effective_api_mode
from council.providers.errors import MissingProviderCredentialError
from council.providers.factory import create_provider

FREE_HOSTED_EXPECTATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("nvidia-nemotron", "nvidia", NVIDIA_BASE_URL, "nvidia/nvidia-nemotron-nano-9b-v2"),
    ("nvidia-deepseek", "nvidia", NVIDIA_BASE_URL, "deepseek-ai/deepseek-r1-distill-qwen-7b"),
    ("nvidia-qwen", "nvidia", NVIDIA_BASE_URL, "qwen/qwen-2.5-7b-instruct"),
    ("groq-llama", "groq", GROQ_BASE_URL, "llama-3.3-70b-versatile"),
    ("groq-mixtral", "groq", GROQ_BASE_URL, "mixtral-8x7b-32768"),
    ("cerebras-qwen", "cerebras", CEREBRAS_BASE_URL, "qwen-3-235b-a22b-instruct-2507"),
    ("openrouter-free-qwen", "openrouter", OPENROUTER_BASE_URL, "qwen/qwen3-235b-a22b:free"),
    (
        "openrouter-free-deepseek",
        "openrouter",
        OPENROUTER_BASE_URL,
        "deepseek/deepseek-r1-distill-qwen-14b:free",
    ),
)

CHAT_PREFERRED_HOSTED = ("nvidia", "groq", "cerebras")


@pytest.mark.parametrize("preset_name", FREE_HOSTED_PRESET_NAMES)
def test_free_hosted_presets_exist(preset_name: str) -> None:
    assert preset_name in MODEL_PRESETS
    assert preset_name in list_preset_names()


@pytest.mark.parametrize(
    ("preset_name", "provider_name", "base_url", "model_id"),
    FREE_HOSTED_EXPECTATIONS,
)
def test_free_hosted_preset_fields(
    preset_name: str,
    provider_name: str,
    base_url: str,
    model_id: str,
) -> None:
    preset = MODEL_PRESETS[preset_name]
    assert preset.llm_mode == "openai_compatible"
    assert preset.provider_name == provider_name
    assert preset.base_url == base_url
    assert preset.model == model_id
    settings = apply_preset(
        Settings(
            llm_mode="mock",
            runs_dir=Path("./runs"),
            mock_model="mock-council-v1",
            llm_api_key=None,
        ),
        preset_name,
    )
    assert settings.llm_mode == "openai_compatible"
    assert settings.llm_provider_name == provider_name
    assert settings.llm_base_url == base_url
    assert settings.llm_model == model_id


@pytest.mark.parametrize("provider_name", CHAT_PREFERRED_HOSTED)
def test_chat_preferred_providers_resolve_auto_to_chat(provider_name: str) -> None:
    assert resolve_effective_api_mode("auto", provider_name=provider_name) == "chat"


def test_hosted_presets_require_llm_api_key() -> None:
    for preset_name in FREE_HOSTED_PRESET_NAMES:
        settings = apply_preset(
            Settings(
                llm_mode="mock",
                runs_dir=Path("./runs"),
                mock_model="mock-council-v1",
                llm_api_key=None,
            ),
            preset_name,
        )
        assert resolve_llm_api_key(settings) is None
        with pytest.raises(MissingProviderCredentialError):
            create_provider(settings)


def test_ollama_still_works_without_llm_api_key() -> None:
    settings = apply_preset(
        Settings(
            llm_mode="mock",
            runs_dir=Path("./runs"),
            mock_model="mock-council-v1",
            llm_api_key=None,
        ),
        "ollama-qwen",
    )
    assert settings.llm_api_key == OLLAMA_DUMMY_API_KEY
    provider = create_provider(settings)
    assert provider.metadata.provider_name == "ollama"


def test_free_hosted_registry_contains_no_secrets() -> None:
    blob = json.dumps(
        {name: preset.__dict__ for name, preset in MODEL_PRESETS.items() if name in FREE_HOSTED_PRESET_NAMES}
    )
    assert "sk-" not in blob
    assert "OPENAI_API_KEY" not in blob
    assert "LLM_API_KEY" not in blob
    assert "nvapi-" not in blob


@pytest.mark.parametrize(
    ("preset_name", "provider_name"),
    [(name, provider) for name, provider, _, _ in FREE_HOSTED_EXPECTATIONS],
)
def test_doctor_hosted_validates_key_source_only(preset_name: str, provider_name: str) -> None:
    settings = apply_preset(
        Settings(
            llm_mode="mock",
            runs_dir=Path("./runs"),
            mock_model="mock-council-v1",
            llm_api_key=None,
        ),
        preset_name,
    )
    checks = run_doctor(settings)
    key_check = next(c for c in checks if c.name == "LLM_API_KEY")
    assert key_check.status == CheckStatus.FAIL
    assert "source: missing" in key_check.message
    assert provider_name in key_check.message or "openrouter" in key_check.message
    live_check = next(c for c in checks if c.name == "live")
    assert live_check.status == CheckStatus.SKIP


def test_doctor_hosted_ok_when_key_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "gsk-test-key-not-real")
    settings = apply_preset(Settings.from_env(), "groq-llama")
    checks = run_doctor(settings)
    key_check = next(c for c in checks if c.name == "LLM_API_KEY")
    assert key_check.status == CheckStatus.OK
    assert "gsk-test" not in key_check.message
    assert "source:" in key_check.message
