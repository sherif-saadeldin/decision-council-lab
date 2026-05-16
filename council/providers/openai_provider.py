from __future__ import annotations

from openai import OpenAI

from council.providers.openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    """OpenAI direct API (backward-compatible wrapper)."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        mode: str = "openai",
        client: OpenAI | None = None,
    ) -> None:
        super().__init__(
            provider_name="openai",
            api_key=api_key,
            model_name=model_name,
            mode=mode,
            base_url=None,
            credential_env="OPENAI_API_KEY",
            client=client,
        )
