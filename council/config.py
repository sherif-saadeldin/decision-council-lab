from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    llm_mode: str
    runs_dir: Path
    mock_model: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_mode=os.getenv("LLM_MODE", "mock").lower(),
            runs_dir=Path(os.getenv("RUNS_DIR", "./runs")),
            mock_model=os.getenv("DEFAULT_MODEL_MOCK", "mock-council-v1"),
        )
