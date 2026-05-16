from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from council.config import Settings
from council.credentials import is_ollama_openai_compatible, resolve_llm_api_key
from council.live_completion import run_live_completion_check
from council.model_presets import apply_preset, get_preset
from council.routing_modes import HOSTED_CHAIR_FALLBACK_WARNING
from council.runtime import RuntimeOptions
from council.secrets import credential_source

AvailabilityLabel = Literal["available", "missing_key", "live_unchecked", "live_failed"]

MOCK_CHAIR_FALLBACK_PRESET = "mock"


@dataclass(frozen=True)
class PresetAvailability:
    preset: str
    provider_name: str
    credential_source: str
    availability: AvailabilityLabel
    is_hosted: bool
    is_local: bool

    def display_availability(self) -> str:
        if self.availability == "available":
            return "available"
        if self.availability == "missing_key":
            return "missing key"
        if self.availability == "live_failed":
            return "live failed"
        return "live unchecked"


class HostedProviderUnavailableError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def preset_is_mock(preset_name: str) -> bool:
    return get_preset(preset_name).llm_mode == "mock"


def preset_is_local(preset_name: str) -> bool:
    preset = get_preset(preset_name)
    return preset.llm_mode == "openai_compatible" and preset.provider_name == "ollama"


def preset_is_hosted(preset_name: str) -> bool:
    preset = get_preset(preset_name)
    if preset.llm_mode == "mock":
        return False
    if preset_is_local(preset_name):
        return False
    return preset.llm_mode in {"openai", "openai_compatible"}


def preset_requires_llm_api_key(preset_name: str, base_settings: Settings | None = None) -> bool:
    settings = apply_preset(base_settings or Settings.from_env(), preset_name)
    if settings.llm_mode == "mock":
        return False
    if settings.llm_mode == "openai":
        return True
    if is_ollama_openai_compatible(settings):
        return False
    return settings.llm_mode == "openai_compatible"


def credential_source_for_preset(
    preset_name: str,
    base_settings: Settings | None = None,
) -> str:
    settings = apply_preset(base_settings or Settings.from_env(), preset_name)
    if settings.llm_mode == "mock":
        return "not_required"
    if settings.llm_mode == "openai":
        return credential_source("OPENAI_API_KEY")
    if is_ollama_openai_compatible(settings):
        return "not_required"
    if resolve_llm_api_key(settings):
        return credential_source("LLM_API_KEY")
    return "missing"


def estimate_preset_availability(
    preset_name: str,
    base_settings: Settings | None = None,
) -> PresetAvailability:
    preset = get_preset(preset_name)
    hosted = preset_is_hosted(preset_name)
    local = preset_is_local(preset_name)
    source = credential_source_for_preset(preset_name, base_settings)
    if hosted and source == "missing":
        label: AvailabilityLabel = "missing_key"
    elif preset.llm_mode == "mock" or local:
        label = "available"
    elif source != "missing":
        label = "live_unchecked"
    else:
        label = "missing_key"
    return PresetAvailability(
        preset=preset_name,
        provider_name=preset.provider_name,
        credential_source=source,
        availability=label,
        is_hosted=hosted,
        is_local=local,
    )


def hosted_chair_needs_fallback(
    chair_preset: str,
    base_settings: Settings | None = None,
) -> bool:
    if not preset_is_hosted(chair_preset):
        return False
    availability = estimate_preset_availability(chair_preset, base_settings)
    return availability.availability == "missing_key"


def apply_auto_routing_guards(
    slot_presets: dict[str, str],
    base_settings: Settings | None,
) -> tuple[dict[str, str], list[str]]:
    """Avoid hosted chair presets when credentials are missing (no live network)."""
    warnings: list[str] = []
    result = dict(slot_presets)
    chair = result.get("chair")
    if chair and hosted_chair_needs_fallback(chair, base_settings):
        result["chair"] = MOCK_CHAIR_FALLBACK_PRESET
        warnings.append(HOSTED_CHAIR_FALLBACK_WARNING)
    return result, warnings


def build_preset_availability_for_routing(
    routing,
    base_settings: Settings | None,
) -> tuple[PresetAvailability, ...]:
    base = base_settings or Settings.from_env()
    lines: list[PresetAvailability] = []
    for slot in ("researcher", "advocate", "skeptic", "risk", "operator", "chair"):
        preset = routing.preset_for(slot)
        lines.append(estimate_preset_availability(preset, base))
    return tuple(lines)


def validate_hosted_presets_live(
    preset_names: list[str],
    base_settings: Settings,
    runtime: RuntimeOptions | None,
    *,
    provider_factory=None,
    live_ping_fn=None,
) -> None:
    """Require live completion for each hosted preset; raise if any fail."""
    failures: list[str] = []
    seen: set[str] = set()
    for preset_name in preset_names:
        if preset_name in seen or not preset_is_hosted(preset_name):
            continue
        seen.add(preset_name)
        settings = apply_preset(base_settings, preset_name)
        ok, reason = run_live_completion_check(
            settings,
            runtime,
            provider_factory=provider_factory,
            live_ping_fn=live_ping_fn,
        )
        if not ok:
            failures.append(f"{preset_name}: {reason or 'live completion failed'}")
    if failures:
        msg = "Hosted provider live validation failed:\n" + "\n".join(f"  - {line}" for line in failures)
        raise HostedProviderUnavailableError(msg)
