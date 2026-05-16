from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


@dataclass(frozen=True)
class Settings:
    llm_mode: str
    runs_dir: Path
    mock_model: str
    openai_api_key: str | None = None
    openai_model: str = DEFAULT_OPENAI_MODEL

    @classmethod
    def from_env(cls) -> Settings:
        openai_key = os.getenv("OPENAI_API_KEY", "").strip() or None
        openai_model = os.getenv("DEFAULT_MODEL_OPENAI", "").strip() or DEFAULT_OPENAI_MODEL
        return cls(
            llm_mode=os.getenv("LLM_MODE", "mock").lower(),
            runs_dir=Path(os.getenv("RUNS_DIR", "./runs")),
            mock_model=os.getenv("DEFAULT_MODEL_MOCK", "mock-council-v1"),
            openai_api_key=openai_key,
            openai_model=openai_model,
        )
