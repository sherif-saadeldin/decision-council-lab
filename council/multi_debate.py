from __future__ import annotations

from council.debate_runner import run_debate_position
from council.models import AgentBrief, DebatePosition, DebateRound, DebateTranscript, ModeratorSummary
from council.progress import ProgressReporter
from council.prompt_debug import PromptDebugCollector
from council.role_routing import CouncilRouting, provider_for_slot


def build_round_moderator_summary(
    advocate: DebatePosition,
    skeptic: DebatePosition,
    risk_officer: DebatePosition | None,
) -> ModeratorSummary:
    unresolved = [
        "Advocate and skeptic still disagree on timing and evidence quality.",
    ]
    tensions = ["Execution learning speed vs proof of dossier quality under real providers."]
    gaps: list[str] = []
    if risk_officer is not None:
        tensions.append(risk_officer.argument[:240])
        unresolved.append("Risk officer challenges both advocate and skeptic premises.")
    return ModeratorSummary(
        resolved_points=[
            "Both sides agree the decision concerns sequencing and internal capability, not GTM launch."
        ],
        unresolved_points=unresolved,
        deciding_tensions=tensions,
        evidence_gaps=gaps,
    )


def run_multi_model_debate(
    routing: CouncilRouting,
    *,
    question: str,
    briefs: list[AgentBrief],
    rounds: int,
    run_id: str,
    debug_collector: PromptDebugCollector | None = None,
    progress: ProgressReporter | None = None,
    base_settings=None,
    runtime=None,
) -> DebateTranscript:
    if rounds <= 0:
        return DebateTranscript(rounds=[], rounds_completed=0, final_unresolved_disagreements=[])

    advocate_provider = provider_for_slot(
        routing, "advocate", base_settings=base_settings, runtime=runtime
    )
    skeptic_provider = provider_for_slot(
        routing, "skeptic", base_settings=base_settings, runtime=runtime
    )
    risk_provider = provider_for_slot(routing, "risk", base_settings=base_settings, runtime=runtime)

    completed: list[DebateRound] = []
    for round_number in range(1, rounds + 1):
        if progress is not None:
            progress.on_stage(f"debate advocate round {round_number}")
        advocate = run_debate_position(
            advocate_provider,
            kind="advocate",
            question=question,
            briefs=briefs,
            run_id=run_id,
            round_number=round_number,
            total_rounds=rounds,
            prior_rounds=completed,
            debug_collector=debug_collector,
        )
        if progress is not None:
            progress.on_stage(f"debate skeptic round {round_number}")
        skeptic = run_debate_position(
            skeptic_provider,
            kind="skeptic",
            question=question,
            briefs=briefs,
            run_id=run_id,
            round_number=round_number,
            total_rounds=rounds,
            prior_rounds=completed,
            advocate_argument=advocate.argument,
            debug_collector=debug_collector,
        )
        if progress is not None:
            progress.on_stage(f"debate risk round {round_number}")
        risk_officer = run_debate_position(
            risk_provider,
            kind="risk_officer",
            question=question,
            briefs=briefs,
            run_id=run_id,
            round_number=round_number,
            total_rounds=rounds,
            prior_rounds=completed,
            advocate_argument=advocate.argument,
            skeptic_argument=skeptic.argument,
            debug_collector=debug_collector,
        )
        moderator = build_round_moderator_summary(advocate, skeptic, risk_officer)
        completed.append(
            DebateRound(
                round_number=round_number,
                advocate=advocate,
                skeptic=skeptic,
                risk_officer=risk_officer,
                moderator=moderator,
            )
        )

    final_unresolved = completed[-1].moderator.unresolved_points if completed else []
    return DebateTranscript(
        rounds=completed,
        rounds_completed=len(completed),
        final_unresolved_disagreements=final_unresolved,
    )
