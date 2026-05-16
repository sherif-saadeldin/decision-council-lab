from __future__ import annotations

from council.config import Settings
from council.runtime import RuntimeOptions
from council.providers.base import LLMProvider
from council.providers.errors import (
    MissingProviderConfigError,
    MissingProviderCredentialError,
    UnsupportedProviderModeError,
)
from council.providers.mock import MockProvider
from council.providers.openai_compatible import OpenAICompatibleProvider
from council.providers.openai_provider import OpenAIProvider

SUPPORTED_LLM_MODES: tuple[str, ...] = ("mock", "openai", "openai_compatible")


def create_provider(
    settings: Settings,
    runtime: RuntimeOptions | None = None,
) -> LLMProvider:
    runtime = runtime or RuntimeOptions()
    mode = settings.llm_mode.lower()
    if mode not in SUPPORTED_LLM_MODES:
        raise UnsupportedProviderModeError(mode, SUPPORTED_LLM_MODES)
    if mode == "mock":
        return MockProvider(model_name=settings.mock_model, mode=mode)
    if mode == "openai":
        if not settings.openai_api_key:
            raise MissingProviderCredentialError("openai", "OPENAI_API_KEY")
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model_name=settings.openai_model,
            mode=mode,
            timeout_seconds=runtime.timeout_seconds,
            max_retries=runtime.max_retries,
            runs_dir=settings.runs_dir,
            repair_json=runtime.repair_json,
        )
    if mode == "openai_compatible":
        provider_name = settings.llm_provider_name
        if not settings.llm_api_key:
            raise MissingProviderCredentialError(provider_name, "LLM_API_KEY")
        if not settings.llm_base_url:
            raise MissingProviderConfigError(provider_name, "LLM_BASE_URL")
        if not settings.llm_model:
            raise MissingProviderConfigError(provider_name, "LLM_MODEL")
        return OpenAICompatibleProvider(
            provider_name=provider_name,
            api_key=settings.llm_api_key,
            model_name=settings.llm_model,
            mode=mode,
            base_url=settings.llm_base_url,
            credential_env="LLM_API_KEY",
            timeout_seconds=runtime.timeout_seconds,
            max_retries=runtime.max_retries,
            runs_dir=settings.runs_dir,
            repair_json=runtime.repair_json,
        )
    raise UnsupportedProviderModeError(mode, SUPPORTED_LLM_MODES)
