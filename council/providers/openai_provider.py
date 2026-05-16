from __future__ import annotations

from pathlib import Path

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
        timeout_seconds: float | None = None,
        max_retries: int = 0,
        runs_dir: Path | None = None,
        repair_json: bool = False,
    ) -> None:
        super().__init__(
            provider_name="openai",
            api_key=api_key,
            model_name=model_name,
            mode=mode,
            base_url=None,
            credential_env="OPENAI_API_KEY",
            client=client,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            runs_dir=runs_dir,
            repair_json=repair_json,
        )
