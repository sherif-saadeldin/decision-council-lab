from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CostTier = Literal["free", "cheap", "medium", "premium", "unknown"]

TIER_ORDER: dict[str, int] = {
    "free": 0,
    "cheap": 1,
    "medium": 2,
    "premium": 3,
    "unknown": 4,
}


@dataclass(frozen=True)
class PresetEconomics:
    cost_tier: CostTier
    estimated_cost_per_call_usd: float
    recommended_role_fit: tuple[str, ...]
    is_local: bool = False
    is_hosted_free: bool = False
    notes: str = ""


# Conservative USD estimates per LLM call (not live pricing). Marked estimate in output.
PRESET_ECONOMICS: dict[str, PresetEconomics] = {
    "mock": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("researcher", "operator", "risk", "advocate", "skeptic", "chair"),
        notes="Local mock; no API spend.",
    ),
    "openai-mini": PresetEconomics(
        cost_tier="medium",
        estimated_cost_per_call_usd=0.003,
        recommended_role_fit=("operator", "researcher", "chair"),
        notes="Paid OpenAI; estimate only.",
    ),
    "openrouter-sonnet": PresetEconomics(
        cost_tier="premium",
        estimated_cost_per_call_usd=0.012,
        recommended_role_fit=("chair", "advocate"),
        is_hosted_free=False,
        notes="Hosted premium via OpenRouter.",
    ),
    "openrouter-gemini": PresetEconomics(
        cost_tier="premium",
        estimated_cost_per_call_usd=0.010,
        recommended_role_fit=("advocate", "chair"),
        notes="Hosted premium via OpenRouter.",
    ),
    "openrouter-deepseek": PresetEconomics(
        cost_tier="medium",
        estimated_cost_per_call_usd=0.004,
        recommended_role_fit=("advocate", "skeptic", "researcher"),
        notes="Hosted mid-tier via OpenRouter.",
    ),
    "openrouter-qwen": PresetEconomics(
        cost_tier="medium",
        estimated_cost_per_call_usd=0.004,
        recommended_role_fit=("advocate", "skeptic", "researcher"),
        notes="Hosted mid-tier via OpenRouter.",
    ),
    "nvidia-nemotron": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("researcher", "risk", "operator", "skeptic"),
        is_hosted_free=True,
        notes="Free-tier NVIDIA NIM; verify quota.",
    ),
    "nvidia-deepseek": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("skeptic", "risk", "researcher"),
        is_hosted_free=True,
        notes="Free-tier NVIDIA NIM.",
    ),
    "nvidia-qwen": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("researcher", "operator", "risk"),
        is_hosted_free=True,
        notes="Free-tier NVIDIA NIM.",
    ),
    "groq-llama": PresetEconomics(
        cost_tier="cheap",
        estimated_cost_per_call_usd=0.001,
        recommended_role_fit=("advocate", "skeptic", "chair"),
        is_hosted_free=False,
        notes="Low-cost Groq hosted.",
    ),
    "groq-mixtral": PresetEconomics(
        cost_tier="cheap",
        estimated_cost_per_call_usd=0.001,
        recommended_role_fit=("advocate", "skeptic"),
        is_hosted_free=False,
        notes="Low-cost Groq hosted.",
    ),
    "cerebras-qwen": PresetEconomics(
        cost_tier="cheap",
        estimated_cost_per_call_usd=0.001,
        recommended_role_fit=("advocate", "skeptic", "risk"),
        is_hosted_free=False,
        notes="Low-cost Cerebras hosted.",
    ),
    "openrouter-free-qwen": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("advocate", "skeptic", "chair", "researcher"),
        is_hosted_free=True,
        notes="OpenRouter free model slot.",
    ),
    "openrouter-free-deepseek": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("skeptic", "advocate", "researcher"),
        is_hosted_free=True,
        notes="OpenRouter free model slot.",
    ),
    "ollama-qwen": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("researcher", "operator", "risk"),
        is_local=True,
        notes="Local Ollama; electricity only.",
    ),
    "ollama-qwen35": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("researcher", "operator"),
        is_local=True,
        notes="Local Ollama.",
    ),
    "ollama-qwen3": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("researcher", "operator"),
        is_local=True,
        notes="Local Ollama.",
    ),
    "ollama-qwen25": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("researcher", "operator", "risk"),
        is_local=True,
        notes="Local Ollama.",
    ),
    "ollama-mistral": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("skeptic", "advocate"),
        is_local=True,
        notes="Local Ollama.",
    ),
    "ollama-llama3": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("advocate", "skeptic"),
        is_local=True,
        notes="Local Ollama.",
    ),
    "ollama-deepseek-coder": PresetEconomics(
        cost_tier="free",
        estimated_cost_per_call_usd=0.0,
        recommended_role_fit=("operator", "researcher"),
        is_local=True,
        notes="Local Ollama.",
    ),
}

_UNKNOWN = PresetEconomics(
    cost_tier="unknown",
    estimated_cost_per_call_usd=0.005,
    recommended_role_fit=(),
    notes="Unknown preset; conservative estimate.",
)


def get_preset_economics(preset_name: str) -> PresetEconomics:
    return PRESET_ECONOMICS.get(preset_name.strip().lower(), _UNKNOWN)


def tier_rank(tier: str) -> int:
    return TIER_ORDER.get(tier, TIER_ORDER["unknown"])
