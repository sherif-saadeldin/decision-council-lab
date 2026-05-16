from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from council.config import Settings

from council.credentials import OLLAMA_DUMMY_API_KEY, resolve_llm_api_key  # noqa: F401

__all__ = (
    "OLLAMA_DUMMY_API_KEY",
    "OLLAMA_BASE_URL",
    "MODEL_PRESETS",
    "OLLAMA_PRESET_NAMES",
    "FREE_HOSTED_PRESET_NAMES",
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OLLAMA_BASE_URL = "http://localhost:11434/v1"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"

# Model IDs are centralized here for easy edits. Verify against each provider's model
# catalog/console — hosted providers change model availability without notice.
NVIDIA_MODEL_NEMOTRON = "nvidia/nvidia-nemotron-nano-9b-v2"
NVIDIA_MODEL_DEEPSEEK = "deepseek-ai/deepseek-r1-distill-qwen-7b"
NVIDIA_MODEL_QWEN = "qwen/qwen-2.5-7b-instruct"
GROQ_MODEL_LLAMA = "llama-3.3-70b-versatile"
GROQ_MODEL_MIXTRAL = "mixtral-8x7b-32768"
CEREBRAS_MODEL_QWEN = "qwen-3-235b-a22b-instruct-2507"
OPENROUTER_FREE_MODEL_QWEN = "qwen/qwen3-235b-a22b:free"
OPENROUTER_FREE_MODEL_DEEPSEEK = "deepseek/deepseek-r1-distill-qwen-14b:free"


@dataclass(frozen=True)
class ModelPreset:
    """Named routing preset. API keys are never stored here — only from env."""

    name: str
    llm_mode: str
    provider_name: str
    model: str
    base_url: str | None = None
    mock_model: str | None = None


MODEL_PRESETS: dict[str, ModelPreset] = {
    "mock": ModelPreset(
        name="mock",
        llm_mode="mock",
        provider_name="mock",
        model="mock-council-v1",
        mock_model="mock-council-v1",
    ),
    "openai-mini": ModelPreset(
        name="openai-mini",
        llm_mode="openai",
        provider_name="openai",
        model="gpt-4.1-mini",
    ),
    "openrouter-sonnet": ModelPreset(
        name="openrouter-sonnet",
        llm_mode="openai_compatible",
        provider_name="openrouter",
        base_url=OPENROUTER_BASE_URL,
        model="anthropic/claude-sonnet-4.5",
    ),
    "openrouter-gemini": ModelPreset(
        name="openrouter-gemini",
        llm_mode="openai_compatible",
        provider_name="openrouter",
        base_url=OPENROUTER_BASE_URL,
        model="google/gemini-2.5-pro-preview",
    ),
    "openrouter-deepseek": ModelPreset(
        name="openrouter-deepseek",
        llm_mode="openai_compatible",
        provider_name="openrouter",
        base_url=OPENROUTER_BASE_URL,
        model="deepseek/deepseek-chat-v3-0324",
    ),
    "openrouter-qwen": ModelPreset(
        name="openrouter-qwen",
        llm_mode="openai_compatible",
        provider_name="openrouter",
        base_url=OPENROUTER_BASE_URL,
        model="qwen/qwen-2.5-72b-instruct",
    ),
    # Free/cheap hosted presets (Slice 5) — verify model IDs in provider consoles.
    "nvidia-nemotron": ModelPreset(
        name="nvidia-nemotron",
        llm_mode="openai_compatible",
        provider_name="nvidia",
        base_url=NVIDIA_BASE_URL,
        model=NVIDIA_MODEL_NEMOTRON,
    ),
    "nvidia-deepseek": ModelPreset(
        name="nvidia-deepseek",
        llm_mode="openai_compatible",
        provider_name="nvidia",
        base_url=NVIDIA_BASE_URL,
        model=NVIDIA_MODEL_DEEPSEEK,
    ),
    "nvidia-qwen": ModelPreset(
        name="nvidia-qwen",
        llm_mode="openai_compatible",
        provider_name="nvidia",
        base_url=NVIDIA_BASE_URL,
        model=NVIDIA_MODEL_QWEN,
    ),
    "groq-llama": ModelPreset(
        name="groq-llama",
        llm_mode="openai_compatible",
        provider_name="groq",
        base_url=GROQ_BASE_URL,
        model=GROQ_MODEL_LLAMA,
    ),
    "groq-mixtral": ModelPreset(
        name="groq-mixtral",
        llm_mode="openai_compatible",
        provider_name="groq",
        base_url=GROQ_BASE_URL,
        model=GROQ_MODEL_MIXTRAL,
    ),
    "cerebras-qwen": ModelPreset(
        name="cerebras-qwen",
        llm_mode="openai_compatible",
        provider_name="cerebras",
        base_url=CEREBRAS_BASE_URL,
        model=CEREBRAS_MODEL_QWEN,
    ),
    "openrouter-free-qwen": ModelPreset(
        name="openrouter-free-qwen",
        llm_mode="openai_compatible",
        provider_name="openrouter",
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_FREE_MODEL_QWEN,
    ),
    "openrouter-free-deepseek": ModelPreset(
        name="openrouter-free-deepseek",
        llm_mode="openai_compatible",
        provider_name="openrouter",
        base_url=OPENROUTER_BASE_URL,
        model=OPENROUTER_FREE_MODEL_DEEPSEEK,
    ),
    # Ollama: model tags must exactly match `ollama list` — edit presets if your tags differ.
    "ollama-qwen": ModelPreset(
        name="ollama-qwen",
        llm_mode="openai_compatible",
        provider_name="ollama",
        base_url=OLLAMA_BASE_URL,
        model="qwen3.5:9b",
    ),
    "ollama-qwen35": ModelPreset(
        name="ollama-qwen35",
        llm_mode="openai_compatible",
        provider_name="ollama",
        base_url=OLLAMA_BASE_URL,
        model="qwen3.5:9b",
    ),
    "ollama-qwen3": ModelPreset(
        name="ollama-qwen3",
        llm_mode="openai_compatible",
        provider_name="ollama",
        base_url=OLLAMA_BASE_URL,
        model="qwen3:8b",
    ),
    "ollama-qwen25": ModelPreset(
        name="ollama-qwen25",
        llm_mode="openai_compatible",
        provider_name="ollama",
        base_url=OLLAMA_BASE_URL,
        model="qwen2.5:7b-instruct",
    ),
    "ollama-mistral": ModelPreset(
        name="ollama-mistral",
        llm_mode="openai_compatible",
        provider_name="ollama",
        base_url=OLLAMA_BASE_URL,
        model="mistral:7b",
    ),
    "ollama-llama3": ModelPreset(
        name="ollama-llama3",
        llm_mode="openai_compatible",
        provider_name="ollama",
        base_url=OLLAMA_BASE_URL,
        model="llama3:8b",
    ),
    "ollama-deepseek-coder": ModelPreset(
        name="ollama-deepseek-coder",
        llm_mode="openai_compatible",
        provider_name="ollama",
        base_url=OLLAMA_BASE_URL,
        model="deepseek-coder:6.7b-instruct",
    ),
}

FREE_HOSTED_PRESET_NAMES: tuple[str, ...] = (
    "nvidia-nemotron",
    "nvidia-deepseek",
    "nvidia-qwen",
    "groq-llama",
    "groq-mixtral",
    "cerebras-qwen",
    "openrouter-free-qwen",
    "openrouter-free-deepseek",
)

OLLAMA_PRESET_NAMES: tuple[str, ...] = (
    "ollama-qwen",
    "ollama-qwen35",
    "ollama-qwen3",
    "ollama-qwen25",
    "ollama-mistral",
    "ollama-llama3",
    "ollama-deepseek-coder",
)


def list_preset_names() -> tuple[str, ...]:
    return tuple(MODEL_PRESETS.keys())


def get_preset(name: str) -> ModelPreset:
    key = name.strip().lower()
    preset = MODEL_PRESETS.get(key)
    if preset is None:
        from council.providers.errors import UnknownModelPresetError

        raise UnknownModelPresetError(key, list_preset_names())
    return preset


def apply_preset(settings: "Settings", preset_name: str) -> "Settings":
    from council.config import Settings

    preset = get_preset(preset_name)
    if preset.llm_mode == "mock":
        return Settings(
            llm_mode="mock",
            mock_model=preset.mock_model or preset.model,
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            llm_provider_name=settings.llm_provider_name,
            llm_base_url=settings.llm_base_url,
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
            runs_dir=settings.runs_dir,
        )
    if preset.llm_mode == "openai":
        return Settings(
            llm_mode="openai",
            runs_dir=settings.runs_dir,
            mock_model=settings.mock_model,
            openai_api_key=settings.openai_api_key,
            openai_model=preset.model,
            llm_provider_name=settings.llm_provider_name,
            llm_base_url=settings.llm_base_url,
            llm_api_key=settings.llm_api_key,
            llm_model=settings.llm_model,
        )
    if preset.llm_mode == "openai_compatible":
        return Settings(
            llm_mode="openai_compatible",
            runs_dir=settings.runs_dir,
            mock_model=settings.mock_model,
            openai_api_key=settings.openai_api_key,
            openai_model=settings.openai_model,
            llm_provider_name=preset.provider_name,
            llm_base_url=preset.base_url,
            llm_api_key=resolve_llm_api_key(
                Settings(
                    llm_mode="openai_compatible",
                    runs_dir=settings.runs_dir,
                    mock_model=settings.mock_model,
                    llm_provider_name=preset.provider_name,
                    llm_base_url=preset.base_url,
                    llm_api_key=settings.llm_api_key,
                    llm_model=preset.model,
                )
            ),
            llm_model=preset.model,
        )
    msg = f"Unsupported preset mode: {preset.llm_mode}"
    raise ValueError(msg)
