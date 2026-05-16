from __future__ import annotations

from dataclasses import dataclass

from council.config import Settings
from council.model_presets import apply_preset, preset_role_metadata
from council.providers.factory import create_provider
from council.providers.models import ProviderMetadata
from council.routing_modes import (
    has_explicit_slot_presets,
    slot_presets_for_mode,
    uses_manual_slot_selection,
)
from council.runtime import RuntimeOptions

ROLE_PLAY_WARNING = (
    "This is role-play debate, not multi-model debate. "
    "All roles share one preset/model — assign different --*-preset flags or "
    "use --council-presets with multiple distinct presets."
)

COUNCIL_SLOTS: tuple[str, ...] = (
    "researcher",
    "advocate",
    "skeptic",
    "risk",
    "operator",
    "chair",
)

AGENT_SLOTS: tuple[str, ...] = ("researcher", "skeptic", "risk", "operator")
DEBATE_SLOTS: tuple[str, ...] = ("advocate", "skeptic", "risk")


@dataclass(frozen=True)
class RoleAssignment:
    slot: str
    preset: str
    provider_name: str
    model_name: str
    mode: str


@dataclass(frozen=True)
class CouncilRouting:
    assignments: dict[str, RoleAssignment]
    unique_preset_count: int
    role_play_only: bool
    role_play_warning: str | None
    routing_mode: str = "economy"
    auto_routed: bool = False

    def preset_for(self, slot: str) -> str:
        return self.assignments[slot].preset

    def metadata_for(self, slot: str) -> ProviderMetadata:
        assignment = self.assignments[slot]
        return ProviderMetadata(
            provider_name=assignment.provider_name,
            model_name=assignment.model_name,
            mode=assignment.mode,
        )


def parse_council_preset_list(value: str | None) -> list[str]:
    if not value or not str(value).strip():
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def spread_presets_to_slots(presets: list[str]) -> dict[str, str]:
    if not presets:
        msg = "At least one preset is required for council mode."
        raise ValueError(msg)
    mapping: dict[str, str] = {}
    last = presets[-1]
    for index, slot in enumerate(COUNCIL_SLOTS):
        mapping[slot] = presets[index] if index < len(presets) else last
    return mapping


def build_council_routing(
    *,
    routing_mode: str = "economy",
    council_presets: list[str] | None = None,
    researcher_preset: str | None = None,
    advocate_preset: str | None = None,
    skeptic_preset: str | None = None,
    risk_preset: str | None = None,
    operator_preset: str | None = None,
    chair_preset: str | None = None,
    base_settings: Settings | None = None,
    runtime: RuntimeOptions | None = None,
) -> CouncilRouting:
    _ = base_settings, runtime  # used by provider_for_slot at call time

    explicit: dict[str, str | None] = {
        "researcher": researcher_preset,
        "advocate": advocate_preset,
        "skeptic": skeptic_preset,
        "risk": risk_preset,
        "operator": operator_preset,
        "chair": chair_preset,
    }
    explicit_count = sum(1 for value in explicit.values() if value)
    if council_presets and explicit_count:
        msg = "Use either --council-presets or individual --*-preset flags, not both."
        raise ValueError(msg)

    explicit_presets = has_explicit_slot_presets(
        council_presets=council_presets,
        researcher_preset=researcher_preset,
        advocate_preset=advocate_preset,
        skeptic_preset=skeptic_preset,
        risk_preset=risk_preset,
        operator_preset=operator_preset,
        chair_preset=chair_preset,
    )
    manual = uses_manual_slot_selection(routing_mode, explicit_presets=explicit_presets)
    auto_routed = False

    if manual:
        if council_presets:
            slot_presets = spread_presets_to_slots(council_presets)
        elif explicit_count:
            default = next(value for value in explicit.values() if value)
            slot_presets = {slot: (explicit[slot] or default) for slot in COUNCIL_SLOTS}
        else:
            msg = (
                "Council mode requires --council-presets, role presets, "
                "or an automatic routing mode (economy, balanced, premium)."
            )
            raise ValueError(msg)
    else:
        slot_presets = slot_presets_for_mode(routing_mode)
        auto_routed = True

    assignments: dict[str, RoleAssignment] = {}
    for slot, preset_name in slot_presets.items():
        provider_name, model_name, mode = preset_role_metadata(preset_name)
        assignments[slot] = RoleAssignment(
            slot=slot,
            preset=preset_name,
            provider_name=provider_name,
            model_name=model_name,
            mode=mode,
        )

    unique_presets = {assignment.preset for assignment in assignments.values()}
    role_play_only = len(unique_presets) <= 1
    warning = ROLE_PLAY_WARNING if role_play_only else None
    return CouncilRouting(
        assignments=assignments,
        unique_preset_count=len(unique_presets),
        role_play_only=role_play_only,
        role_play_warning=warning,
        routing_mode=routing_mode,
        auto_routed=auto_routed,
    )


def provider_for_slot(
    routing: CouncilRouting,
    slot: str,
    *,
    base_settings: Settings | None = None,
    runtime: RuntimeOptions | None = None,
):
    from council.providers.base import LLMProvider

    preset_name = routing.preset_for(slot)
    base = base_settings or Settings.from_env()
    settings = apply_preset(base, preset_name)
    provider: LLMProvider = create_provider(settings, runtime=runtime or RuntimeOptions())
    return provider
