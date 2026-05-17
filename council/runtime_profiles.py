from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from council.config import Settings
from council.model_presets import apply_preset
from council.secrets import credential_source

OperationalProfile = Literal["offline", "cheap", "balanced", "hosted"]


@dataclass(frozen=True)
class OperationalProfilePlan:
    requested: OperationalProfile
    effective: OperationalProfile
    run_preset: str
    routing_mode: str
    council_presets: list[str] | None
    startup_summary: str
    fallback_summary: str | None = None


def resolve_operational_profile(
    *,
    requested: str | None,
    settings: Settings,
) -> OperationalProfilePlan:
    profile = _normalize_profile(requested)
    llm_source = credential_source("LLM_API_KEY")
    openai_source = credential_source("OPENAI_API_KEY")
    has_hosted = llm_source != "missing" or openai_source != "missing"
    has_compatible_hosted = llm_source != "missing"

    if profile == "offline":
        return OperationalProfilePlan(
            requested=profile,
            effective="offline",
            run_preset="mock",
            routing_mode="manual",
            council_presets=["mock"],
            startup_summary="Running in offline mode using local reasoning.",
        )

    if profile == "cheap":
        if has_compatible_hosted:
            return OperationalProfilePlan(
                requested=profile,
                effective="cheap",
                run_preset="openrouter-free-qwen",
                routing_mode="economy",
                council_presets=None,
                startup_summary="Running in cheap mode with free-tier reasoning where available.",
            )
        return _downgraded_to_offline(profile)

    if profile == "balanced":
        if has_hosted:
            run_preset = "openrouter-sonnet" if has_compatible_hosted else "openai-mini"
            return OperationalProfilePlan(
                requested=profile,
                effective="balanced",
                run_preset=run_preset,
                routing_mode="balanced",
                council_presets=None,
                startup_summary="Running in balanced mode with mixed local and hosted reasoning.",
            )
        return _downgraded_to_offline(profile)

    # hosted
    if has_hosted:
        run_preset = "openrouter-sonnet" if has_compatible_hosted else "openai-mini"
        return OperationalProfilePlan(
            requested=profile,
            effective="hosted",
            run_preset=run_preset,
            routing_mode="premium",
            council_presets=None,
            startup_summary="Running in hosted mode with strongest available hosted reasoning.",
        )
    return _downgraded_to_offline(profile)


def apply_profile_to_settings(
    settings: Settings,
    *,
    plan: OperationalProfilePlan,
) -> Settings:
    return apply_preset(settings, plan.run_preset)


def _normalize_profile(value: str | None) -> OperationalProfile:
    if not value:
        return "offline"
    key = value.strip().lower()
    if key not in {"offline", "cheap", "balanced", "hosted"}:
        return "offline"
    return key  # type: ignore[return-value]


def _downgraded_to_offline(requested: OperationalProfile) -> OperationalProfilePlan:
    return OperationalProfilePlan(
        requested=requested,
        effective="offline",
        run_preset="mock",
        routing_mode="manual",
        council_presets=["mock"],
        startup_summary="Running in offline mode using local reasoning.",
        fallback_summary="Hosted models were unavailable, so I continued using local reasoning.",
    )

