from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from council.secrets import resolve_secret_value

load_dotenv()

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_COMPATIBLE_PROVIDER_NAME = "openrouter"


@dataclass(frozen=True)
class Settings:
    llm_mode: str
    runs_dir: Path
    mock_model: str
    openai_api_key: str | None = None
    openai_model: str = DEFAULT_OPENAI_MODEL
    llm_provider_name: str = DEFAULT_COMPATIBLE_PROVIDER_NAME
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        openai_key = resolve_secret_value("OPENAI_API_KEY")
        openai_model = os.getenv("DEFAULT_MODEL_OPENAI", "").strip() or DEFAULT_OPENAI_MODEL
        provider_name = os.getenv("LLM_PROVIDER_NAME", "").strip() or DEFAULT_COMPATIBLE_PROVIDER_NAME
        base_url = os.getenv("LLM_BASE_URL", "").strip() or None
        llm_key = resolve_secret_value("LLM_API_KEY")
        llm_model = os.getenv("LLM_MODEL", "").strip()
        return cls(
            llm_mode=os.getenv("LLM_MODE", "mock").lower(),
            runs_dir=Path(os.getenv("RUNS_DIR", "./runs")),
            mock_model=os.getenv("DEFAULT_MODEL_MOCK", "mock-council-v1"),
            openai_api_key=openai_key,
            openai_model=openai_model,
            llm_provider_name=provider_name,
            llm_base_url=base_url,
            llm_api_key=llm_key,
            llm_model=llm_model,
        )
