from __future__ import annotations

from typing import Literal

from council.models import AgentBrief, DebatePosition, DebateRound
from council.prompt_debug import PromptDebugCollector
from council.providers.base import LLMProvider

PositionKind = Literal["advocate", "skeptic", "risk_officer"]


def run_debate_position(
    provider: LLMProvider,
    *,
    kind: PositionKind,
    question: str,
    briefs: list[AgentBrief],
    run_id: str,
    round_number: int,
    total_rounds: int,
    prior_rounds: list[DebateRound],
    advocate_argument: str = "",
    skeptic_argument: str = "",
    debug_collector: PromptDebugCollector | None = None,
) -> DebatePosition:
    generate = getattr(provider, "generate_debate_position", None)
    if callable(generate):
        return generate(
            kind=kind,
            question=question,
            briefs=briefs,
            run_id=run_id,
            round_number=round_number,
            total_rounds=total_rounds,
            prior_rounds=prior_rounds,
            advocate_argument=advocate_argument,
            skeptic_argument=skeptic_argument,
            debug_collector=debug_collector,
        )

    run_round = getattr(provider, "run_debate_round", None)
    if callable(run_round) and kind in {"advocate", "skeptic"}:
        debate_round = run_round(
            question=question,
            briefs=briefs,
            prior_rounds=prior_rounds,
            round_number=round_number,
            total_rounds=total_rounds,
            run_id=run_id,
            debug_collector=debug_collector,
        )
        if kind == "advocate":
            return debate_round.advocate
        return debate_round.skeptic

    msg = f"Provider {provider.metadata.provider_name!r} cannot run debate position {kind!r}."
    raise TypeError(msg)
