from __future__ import annotations

from council.config import Settings
from council.providers.base import LLMProvider
from council.providers.errors import UnsupportedProviderModeError
from council.providers.mock import MockProvider

SUPPORTED_LLM_MODES: tuple[str, ...] = ("mock",)


def create_provider(settings: Settings) -> LLMProvider:
    mode = settings.llm_mode.lower()
    if mode not in SUPPORTED_LLM_MODES:
        raise UnsupportedProviderModeError(mode, SUPPORTED_LLM_MODES)
    if mode == "mock":
        return MockProvider(model_name=settings.mock_model, mode=mode)
    raise UnsupportedProviderModeError(mode, SUPPORTED_LLM_MODES)
