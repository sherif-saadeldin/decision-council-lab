from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from council.config import Settings
from council.costing import CouncilCostEstimate, estimate_council_cost
from council.decision_thread import (
    DecisionContext,
    DecisionThreadMeta,
    compose_question_with_context,
)
from council.provider_availability import (
    build_preset_availability_for_routing,
    validate_hosted_presets_live,
)
from council.models import (
    AgentBrief,
    AgentRole,
    CouncilCostEstimateRecord,
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
from council.prompt_loader import system_profile_context
from council.prompt_run import attach_prompt_metadata
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
    routing_mode: str = "economy"
    require_live_providers: bool = False
    council_presets: list[str] | None = None
    researcher_preset: str | None = None
    advocate_preset: str | None = None
    skeptic_preset: str | None = None
    risk_preset: str | None = None
    operator_preset: str | None = None
    chair_preset: str | None = None
    debate_rounds: int = DEFAULT_COUNCIL_DEBATE_ROUNDS
    max_cost_usd: float | None = None
    max_llm_calls: int | None = None
    max_debate_rounds: int | None = None
    dry_run_cost: bool = False
    allow_over_budget: bool = False
    create_pack: bool = False
    prompt_create_pack: bool = True
    runtime: RuntimeOptions | None = None
    base_settings: Settings | None = None
    # Decision-thread linkage (Slice 5.8). When set, the structured context
    # is prepended to the question and persisted as `decision_thread` on
    # the resulting CouncilRunResult.
    parent_context: DecisionContext | None = None
    parent_run_id: str | None = None
    thread_id: str | None = None


@dataclass(frozen=True)
class CouncilSessionPlan:
    routing: CouncilRouting
    cost_estimate: CouncilCostEstimate
    debate_rounds: int
    preset_availability: tuple = ()


@dataclass(frozen=True)
class CouncilSessionResult:
    result: CouncilRunResult
    routing: CouncilRouting
    role_play_warning: str | None
    cost_estimate: CouncilCostEstimate | None = None
    implementation_pack_paths: list = None  # type: ignore[assignment]
    debug_collector: PromptDebugCollector | None = None


def plan_council_session(request: CouncilSessionRequest) -> CouncilSessionPlan:
    base = request.base_settings or Settings.from_env()
    runtime = request.runtime or RuntimeOptions()
    routing = build_council_routing(
        routing_mode=request.routing_mode,
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
    estimate = estimate_council_cost(
        routing,
        routing_mode=request.routing_mode,
        debate_rounds=request.debate_rounds,
    )
    availability = build_preset_availability_for_routing(routing, base)
    if request.require_live_providers:
        unique_presets = sorted({assignment.preset for assignment in routing.assignments.values()})
        validate_hosted_presets_live(
            unique_presets,
            base,
            runtime,
        )
    return CouncilSessionPlan(
        routing=routing,
        cost_estimate=estimate,
        debate_rounds=request.debate_rounds,
        preset_availability=availability,
    )


def _cost_record(estimate: CouncilCostEstimate) -> CouncilCostEstimateRecord:
    data = estimate.to_record()
    return CouncilCostEstimateRecord(**data)


def run_council_session(
    request: CouncilSessionRequest,
    *,
    progress: ProgressReporter | None = None,
    create_pack_fn=None,
    plan: CouncilSessionPlan | None = None,
) -> CouncilSessionResult:
    runtime = request.runtime or RuntimeOptions()
    session_plan = plan or plan_council_session(request)
    routing = session_plan.routing
    cost_estimate = session_plan.cost_estimate

    run_id = str(uuid4())
    run_started = time.perf_counter()
    debug_collector = PromptDebugCollector()
    raw_question = request.question.strip()
    if request.parent_context is not None:
        question = compose_question_with_context(raw_question, request.parent_context)
    else:
        question = raw_question

    with system_profile_context(runtime.system_profile):
        return _run_council_session_inner(
            request,
            runtime=runtime,
            session_plan=session_plan,
            routing=routing,
            cost_estimate=cost_estimate,
            run_id=run_id,
            run_started=run_started,
            debug_collector=debug_collector,
            question=question,
            progress=progress,
        )


def _run_council_session_inner(
    request: CouncilSessionRequest,
    *,
    runtime: RuntimeOptions,
    session_plan: CouncilSessionPlan,
    routing: CouncilRouting,
    cost_estimate: CouncilCostEstimate,
    run_id: str,
    run_started: float,
    debug_collector: PromptDebugCollector,
    question: str,
    progress: ProgressReporter | None,
) -> CouncilSessionResult:
    base = request.base_settings or Settings.from_env()
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
    debate_rounds = session_plan.debate_rounds
    if debate_rounds > 0:
        debate_transcript = run_multi_model_debate(
            routing,
            question=question,
            briefs=briefs,
            rounds=debate_rounds,
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

    decision_thread = _build_decision_thread_meta(request)

    result = attach_prompt_metadata(
        CouncilRunResult(
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
            routing_mode=request.routing_mode,
            cost_estimate=_cost_record(cost_estimate),
            decision_thread=decision_thread,
        ),
        system_profile=runtime.system_profile,
    )

    return CouncilSessionResult(
        result=result,
        routing=routing,
        role_play_warning=routing.role_play_warning,
        cost_estimate=cost_estimate,
        implementation_pack_paths=[],
        debug_collector=debug_collector,
    )


def _build_decision_thread_meta(
    request: CouncilSessionRequest,
) -> DecisionThreadMeta | None:
    """Resolve parent_run_id / thread_id linkage from the request, if any."""
    context = request.parent_context
    if context is None:
        return None
    parent_run_id = request.parent_run_id or context.run_id
    thread_id = request.thread_id or parent_run_id
    return DecisionThreadMeta(
        parent_run_id=parent_run_id,
        thread_id=thread_id,
        context_summary=context,
    )


def _check_budget(runtime: RuntimeOptions, run_started: float) -> None:
    if runtime.max_run_seconds is None:
        return
    elapsed = time.perf_counter() - run_started
    if elapsed > runtime.max_run_seconds:
        raise RunBudgetExceededError(runtime.max_run_seconds)
