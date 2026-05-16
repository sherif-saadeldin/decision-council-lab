from __future__ import annotations

from dataclasses import dataclass

from council.preset_economics import get_preset_economics
from council.role_routing import AGENT_SLOTS, CouncilRouting

AGENT_CALL_SLOTS: tuple[str, ...] = AGENT_SLOTS
DEBATE_CALLS_PER_ROUND = 3  # advocate, skeptic, risk


class CouncilBudgetExceededError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class SlotCostLine:
    slot: str
    preset: str
    cost_tier: str
    llm_calls: int
    estimated_usd: float


@dataclass(frozen=True)
class CouncilCostEstimate:
    routing_mode: str
    debate_rounds: int
    llm_call_count: int
    estimated_cost_usd: float
    estimated_cost_usd_low: float
    estimated_cost_usd_high: float
    is_estimate: bool = True
    slot_lines: tuple[SlotCostLine, ...] = ()

    def to_record(self) -> dict:
        return {
            "routing_mode": self.routing_mode,
            "debate_rounds": self.debate_rounds,
            "llm_call_count": self.llm_call_count,
            "estimated_cost_usd": self.estimated_cost_usd,
            "estimated_cost_usd_low": self.estimated_cost_usd_low,
            "estimated_cost_usd_high": self.estimated_cost_usd_high,
            "is_estimate": self.is_estimate,
            "slot_lines": [
                {
                    "slot": line.slot,
                    "preset": line.preset,
                    "cost_tier": line.cost_tier,
                    "llm_calls": line.llm_calls,
                    "estimated_usd": line.estimated_usd,
                }
                for line in self.slot_lines
            ],
        }


def count_council_llm_calls(debate_rounds: int) -> int:
    agent_calls = len(AGENT_CALL_SLOTS)
    chair_calls = 1
    debate_calls = max(0, debate_rounds) * DEBATE_CALLS_PER_ROUND
    return agent_calls + chair_calls + debate_calls


def _calls_per_slot(debate_rounds: int) -> dict[str, int]:
    counts = {slot: 1 for slot in AGENT_CALL_SLOTS}
    counts["chair"] = 1
    if debate_rounds > 0:
        counts["advocate"] = counts.get("advocate", 0) + debate_rounds
        counts["skeptic"] = counts.get("skeptic", 0) + debate_rounds
        counts["risk"] = counts.get("risk", 0) + debate_rounds
    return counts


def estimate_council_cost(
    routing: CouncilRouting,
    *,
    routing_mode: str,
    debate_rounds: int,
) -> CouncilCostEstimate:
    calls_by_slot = _calls_per_slot(debate_rounds)
    slot_lines: list[SlotCostLine] = []
    total = 0.0
    for slot, assignment in routing.assignments.items():
        calls = calls_by_slot.get(slot, 0)
        if calls <= 0:
            continue
        econ = get_preset_economics(assignment.preset)
        slot_cost = calls * econ.estimated_cost_per_call_usd
        total += slot_cost
        slot_lines.append(
            SlotCostLine(
                slot=slot,
                preset=assignment.preset,
                cost_tier=econ.cost_tier,
                llm_calls=calls,
                estimated_usd=slot_cost,
            )
        )
    llm_calls = count_council_llm_calls(debate_rounds)
    low = total * 0.85
    high = total * 1.25 + (0.002 * llm_calls)  # conservative buffer
    return CouncilCostEstimate(
        routing_mode=routing_mode,
        debate_rounds=debate_rounds,
        llm_call_count=llm_calls,
        estimated_cost_usd=total,
        estimated_cost_usd_low=low,
        estimated_cost_usd_high=high,
        is_estimate=True,
        slot_lines=tuple(slot_lines),
    )


def enforce_cost_budget(
    estimate: CouncilCostEstimate,
    *,
    max_cost_usd: float | None,
    max_llm_calls: int | None,
    allow_over_budget: bool,
) -> None:
    if allow_over_budget:
        return
    if max_cost_usd is not None and estimate.estimated_cost_usd_high > max_cost_usd:
        msg = (
            f"Estimated cost high bound ${estimate.estimated_cost_usd_high:.4f} exceeds "
            f"--max-cost-usd {max_cost_usd:.4f} (estimate only). "
            "Pass --allow-over-budget to run anyway."
        )
        raise CouncilBudgetExceededError(msg)
    if max_llm_calls is not None and estimate.llm_call_count > max_llm_calls:
        msg = (
            f"Planned {estimate.llm_call_count} LLM calls exceeds --max-llm-calls {max_llm_calls}. "
            "Reduce debate rounds or pass --allow-over-budget."
        )
        raise CouncilBudgetExceededError(msg)
