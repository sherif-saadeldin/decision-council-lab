from __future__ import annotations

from council.config import Settings
from council.providers.base import LLMProvider
from council.providers.errors import MissingProviderCredentialError, UnsupportedProviderModeError
from council.providers.mock import MockProvider
from council.providers.openai_provider import OpenAIProvider

SUPPORTED_LLM_MODES: tuple[str, ...] = ("mock", "openai")


def create_provider(settings: Settings) -> LLMProvider:
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
        )
    raise UnsupportedProviderModeError(mode, SUPPORTED_LLM_MODES)
