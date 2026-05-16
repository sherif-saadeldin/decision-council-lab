from __future__ import annotations

from typing import Literal

from council.model_presets import MODEL_PRESETS, list_preset_names
from council.preset_economics import get_preset_economics, tier_rank

RoutingMode = Literal["economy", "balanced", "premium", "manual"]

ROUTING_MODES: tuple[str, ...] = ("economy", "balanced", "premium", "manual")
DEFAULT_ROUTING_MODE: RoutingMode = "economy"

# Fixed maps for deterministic routing (cheapest viable presets).
ECONOMY_SLOT_PRESETS: dict[str, str] = {
    "researcher": "mock",
    "advocate": "mock",
    "skeptic": "mock",
    "risk": "mock",
    "operator": "mock",
    "chair": "openrouter-free-qwen",
}

BALANCED_SLOT_PRESETS: dict[str, str] = {
    "researcher": "nvidia-nemotron",
    "advocate": "groq-llama",
    "skeptic": "cerebras-qwen",
    "risk": "nvidia-qwen",
    "operator": "nvidia-nemotron",
    "chair": "openrouter-sonnet",
}

PREMIUM_SLOT_PRESETS: dict[str, str] = {
    "researcher": "openrouter-sonnet",
    "advocate": "openrouter-gemini",
    "skeptic": "groq-llama",
    "risk": "cerebras-qwen",
    "operator": "openai-mini",
    "chair": "openrouter-sonnet",
}

MODE_DEBATE_DEFAULTS: dict[str, int] = {
    "economy": 0,
    "balanced": 1,
    "premium": 2,
    "manual": 1,
}


def normalize_routing_mode(value: str | None) -> RoutingMode:
    if not value:
        return DEFAULT_ROUTING_MODE
    key = value.strip().lower()
    if key not in ROUTING_MODES:
        msg = f"Invalid routing mode {value!r}. Choose: {', '.join(ROUTING_MODES)}."
        raise ValueError(msg)
    return key  # type: ignore[return-value]


def has_explicit_slot_presets(
    *,
    council_presets: list[str] | None,
    researcher_preset: str | None = None,
    advocate_preset: str | None = None,
    skeptic_preset: str | None = None,
    risk_preset: str | None = None,
    operator_preset: str | None = None,
    chair_preset: str | None = None,
) -> bool:
    if council_presets:
        return True
    return any(
        (
            researcher_preset,
            advocate_preset,
            skeptic_preset,
            risk_preset,
            operator_preset,
            chair_preset,
        )
    )


def uses_manual_slot_selection(routing_mode: str, *, explicit_presets: bool) -> bool:
    if routing_mode == "manual":
        return True
    return explicit_presets


def default_debate_rounds_for_mode(routing_mode: str) -> int:
    return MODE_DEBATE_DEFAULTS.get(routing_mode, MODE_DEBATE_DEFAULTS["manual"])


def slot_presets_for_mode(routing_mode: str) -> dict[str, str]:
    if routing_mode == "economy":
        return dict(ECONOMY_SLOT_PRESETS)
    if routing_mode == "balanced":
        return dict(BALANCED_SLOT_PRESETS)
    if routing_mode == "premium":
        return dict(PREMIUM_SLOT_PRESETS)
    msg = f"slot_presets_for_mode does not apply to mode {routing_mode!r}."
    raise ValueError(msg)


def _candidates_for_slot(slot: str, *, max_tier: str) -> list[str]:
    max_rank = tier_rank(max_tier)
    names: list[str] = []
    for name in list_preset_names():
        if name not in MODEL_PRESETS:
            continue
        econ = get_preset_economics(name)
        if tier_rank(econ.cost_tier) > max_rank:
            continue
        if slot in econ.recommended_role_fit or not econ.recommended_role_fit:
            names.append(name)
    if not names:
        for name in list_preset_names():
            econ = get_preset_economics(name)
            if tier_rank(econ.cost_tier) <= max_rank:
                names.append(name)
    names.sort(
        key=lambda n: (
            tier_rank(get_preset_economics(n).cost_tier),
            get_preset_economics(n).estimated_cost_per_call_usd,
            n,
        )
    )
    return names


def cheapest_preset_for_slot(slot: str) -> str:
    candidates = _candidates_for_slot(slot, max_tier="free")
    if candidates:
        return candidates[0]
    cheap = _candidates_for_slot(slot, max_tier="cheap")
    if cheap:
        return cheap[0]
    return "mock"


def strongest_preset_for_slot(slot: str, *, max_tier: str = "premium") -> str:
    candidates = _candidates_for_slot(slot, max_tier=max_tier)
    if not candidates:
        return "openrouter-sonnet"
    return candidates[-1]


def resolve_debate_rounds(
    routing_mode: str,
    *,
    cli_debate_rounds: int | None,
    max_debate_rounds: int | None,
) -> int:
    if cli_debate_rounds is not None:
        rounds = max(0, int(cli_debate_rounds))
    else:
        rounds = default_debate_rounds_for_mode(routing_mode)
    if max_debate_rounds is not None:
        rounds = min(rounds, max(0, int(max_debate_rounds)))
    return rounds
