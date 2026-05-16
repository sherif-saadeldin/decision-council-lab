from __future__ import annotations

from council.models import AgentBrief, DebateRound, DebateTranscript
from council.progress import ProgressReporter
from council.prompt_debug import PromptDebugCollector
from council.providers.base import LLMProvider


def run_debate(
    provider: LLMProvider,
    *,
    question: str,
    briefs: list[AgentBrief],
    rounds: int,
    run_id: str,
    debug_collector: PromptDebugCollector | None = None,
    progress: ProgressReporter | None = None,
) -> DebateTranscript:
    if rounds <= 0:
        return DebateTranscript(rounds=[], rounds_completed=0, final_unresolved_disagreements=[])

    run_round = getattr(provider, "run_debate_round", None)
    if not callable(run_round):
        msg = "Provider must implement run_debate_round for debate mode."
        raise TypeError(msg)

    completed: list[DebateRound] = []
    for round_number in range(1, rounds + 1):
        if progress is not None:
            progress.on_stage(f"debate round {round_number}")
        debate_round = run_round(
            question=question,
            briefs=briefs,
            prior_rounds=completed,
            round_number=round_number,
            total_rounds=rounds,
            run_id=run_id,
            debug_collector=debug_collector,
        )
        completed.append(debate_round)

    final_unresolved = completed[-1].moderator.unresolved_points if completed else []
    return DebateTranscript(
        rounds=completed,
        rounds_completed=len(completed),
        final_unresolved_disagreements=final_unresolved,
    )
