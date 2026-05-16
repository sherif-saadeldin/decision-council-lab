from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from council.config import Settings
from council.models import (
    AgentBrief,
    AgentRole,
    CouncilRunResult,
    DebateTranscript,
    RoleAssignmentRecord,
)
from council.multi_debate import run_multi_model_debate
from council.progress import ProgressReporter
from council.prompt_debug import PromptDebugCollector
from council.providers.models import ProviderMetadata, ProviderRequest, ProviderResponse
from council.role_routing import (
    AGENT_SLOTS,
    CouncilRouting,
    build_council_routing,
    provider_for_slot,
)
from council.runtime import RunBudgetExceededError, RuntimeOptions

AGENT_ROLE_BY_SLOT: dict[str, AgentRole] = {
    "researcher": AgentRole.RESEARCH,
    "skeptic": AgentRole.SKEPTIC,
    "risk": AgentRole.RISK,
    "operator": AgentRole.OPERATOR,
}

DEFAULT_COUNCIL_DEBATE_ROUNDS = 1


@dataclass(frozen=True)
class CouncilSessionRequest:
    question: str
    council_presets: list[str] | None = None
    researcher_preset: str | None = None
    advocate_preset: str | None = None
    skeptic_preset: str | None = None
    risk_preset: str | None = None
    operator_preset: str | None = None
    chair_preset: str | None = None
    debate_rounds: int = DEFAULT_COUNCIL_DEBATE_ROUNDS
    create_pack: bool = False
    prompt_create_pack: bool = True
    runtime: RuntimeOptions | None = None
    base_settings: Settings | None = None


@dataclass(frozen=True)
class CouncilSessionResult:
    result: CouncilRunResult
    routing: CouncilRouting
    role_play_warning: str | None
    implementation_pack_paths: list = None  # type: ignore[assignment]
    debug_collector: PromptDebugCollector | None = None


def run_council_session(
    request: CouncilSessionRequest,
    *,
    progress: ProgressReporter | None = None,
    create_pack_fn=None,
) -> CouncilSessionResult:
    base = request.base_settings or Settings.from_env()
    runtime = request.runtime or RuntimeOptions()
    routing = build_council_routing(
        council_presets=request.council_presets,
        researcher_preset=request.researcher_preset,
        advocate_preset=request.advocate_preset,
        skeptic_preset=request.skeptic_preset,
        risk_preset=request.risk_preset,
        operator_preset=request.operator_preset,
        chair_preset=request.chair_preset,
        base_settings=base,
        runtime=runtime,
    )

    run_id = str(uuid4())
    run_started = time.perf_counter()
    debug_collector = PromptDebugCollector()
    question = request.question.strip()

    briefs: list[AgentBrief] = []
    responses: list[ProviderResponse] = []

    for slot in AGENT_SLOTS:
        _check_budget(runtime, run_started)
        role = AGENT_ROLE_BY_SLOT[slot]
        if progress is not None:
            progress.on_stage(f"{slot} ({routing.preset_for(slot)})")
        provider = provider_for_slot(routing, slot, base_settings=base, runtime=runtime)
        response = provider.complete(
            ProviderRequest(
                role=role,
                question=question,
                prior_briefs=briefs,
                run_id=run_id,
                debug_collector=debug_collector,
                fast_mode=runtime.fast_mode,
            )
        )
        briefs.append(response.brief)
        responses.append(response)

    _check_budget(runtime, run_started)
    debate_transcript: DebateTranscript | None = None
    if request.debate_rounds > 0:
        debate_transcript = run_multi_model_debate(
            routing,
            question=question,
            briefs=briefs,
            rounds=request.debate_rounds,
            run_id=run_id,
            debug_collector=debug_collector,
            progress=progress,
            base_settings=base,
            runtime=runtime,
        )

    _check_budget(runtime, run_started)
    if progress is not None:
        progress.on_stage(f"chair ({routing.preset_for('chair')})")

    chair_provider = provider_for_slot(routing, "chair", base_settings=base, runtime=runtime)
    synthesize = getattr(chair_provider, "synthesize_dossier", None)
    if not callable(synthesize):
        msg = "Chair provider must implement synthesize_dossier."
        raise TypeError(msg)

    dossier = synthesize(
        question=question,
        briefs=briefs,
        run_id=run_id,
        debug_collector=debug_collector,
        debate_transcript=debate_transcript,
        fast_mode=runtime.fast_mode,
    )

    chair_meta = chair_provider.metadata
    role_records = [
        RoleAssignmentRecord(
            slot=assignment.slot,
            preset=assignment.preset,
            provider_name=assignment.provider_name,
            model_name=assignment.model_name,
            mode=assignment.mode,
        )
        for assignment in routing.assignments.values()
    ]

    result = CouncilRunResult(
        dossier=dossier,
        agent_briefs=briefs,
        debate_transcript=debate_transcript,
        provider_metadata=ProviderMetadata(
            provider_name=chair_meta.provider_name,
            model_name=chair_meta.model_name,
            mode=chair_meta.mode,
            supports_structured_output=chair_meta.supports_structured_output,
            supports_streaming=chair_meta.supports_streaming,
            api_mode_preference=chair_meta.api_mode_preference,
            api_mode_used=chair_meta.api_mode_used,
        ),
        provider_responses=responses,
        council_mode="multi",
        multi_model=not routing.role_play_only,
        role_play_warning=routing.role_play_warning,
        role_assignments=role_records,
    )

    return CouncilSessionResult(
        result=result,
        routing=routing,
        role_play_warning=routing.role_play_warning,
        implementation_pack_paths=[],
        debug_collector=debug_collector,
    )


def _check_budget(runtime: RuntimeOptions, run_started: float) -> None:
    if runtime.max_run_seconds is None:
        return
    elapsed = time.perf_counter() - run_started
    if elapsed > runtime.max_run_seconds:
        raise RunBudgetExceededError(runtime.max_run_seconds)
