from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from council.config import Settings
from council.debate import run_debate
from council.models import (
    DEFAULT_DEBATE_ROUNDS,
    AgentBrief,
    AgentRole,
    CouncilRunResult,
    DebateTranscript,
    DecisionDossier,
)
from council.progress import ProgressReporter
from council.prompt_debug import PromptDebugCollector
from council.providers.base import LLMProvider
from council.providers.factory import create_provider
from council.providers.models import ProviderRequest, ProviderResponse
from council.runtime import FAST_DEBATE_ROUNDS, RuntimeOptions

AGENT_PIPELINE: tuple[AgentRole, ...] = (
    AgentRole.CONTEXT,
    AgentRole.RESEARCH,
    AgentRole.SKEPTIC,
    AgentRole.RISK,
    AgentRole.OPERATOR,
)


class CouncilState(TypedDict, total=False):
    question: str
    run_id: str
    briefs: list[AgentBrief]
    provider_responses: list[ProviderResponse]
    debug_collector: PromptDebugCollector | None
    fast_mode: bool


def get_provider(
    settings: Settings | None = None,
    runtime: RuntimeOptions | None = None,
) -> LLMProvider:
    settings = settings or Settings.from_env()
    return create_provider(settings, runtime=runtime)


def _make_agent_node(role: AgentRole, provider: LLMProvider, progress: ProgressReporter | None):
    def node(state: CouncilState) -> dict[str, list[AgentBrief] | list[ProviderResponse]]:
        if progress is not None:
            progress.on_stage(role.value)
        response = provider.complete(
            ProviderRequest(
                role=role,
                question=state["question"],
                prior_briefs=state["briefs"],
                run_id=state["run_id"],
                debug_collector=state.get("debug_collector"),
                fast_mode=bool(state.get("fast_mode")),
            )
        )
        return {
            "briefs": [*state["briefs"], response.brief],
            "provider_responses": [*state["provider_responses"], response],
        }

    return node


def build_agent_graph(
    provider: LLMProvider,
    progress: ProgressReporter | None = None,
) -> StateGraph:
    graph = StateGraph(CouncilState)
    previous = START

    for role in AGENT_PIPELINE:
        node_name = role.value
        graph.add_node(node_name, _make_agent_node(role, provider, progress))
        graph.add_edge(previous, node_name)
        previous = node_name

    graph.add_edge(previous, END)
    return graph


def run_council(
    question: str,
    settings: Settings | None = None,
    *,
    debate_rounds: int = DEFAULT_DEBATE_ROUNDS,
    save_prompt_debug: bool = False,
    runtime: RuntimeOptions | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[CouncilRunResult, PromptDebugCollector | None]:
    settings = settings or Settings.from_env()
    runtime = runtime or RuntimeOptions()
    debate_rounds = effective_debate_rounds(debate_rounds, runtime)
    provider = get_provider(settings, runtime=runtime)
    run_id = str(uuid4())
    debug_collector = PromptDebugCollector() if save_prompt_debug else None
    fast_mode = runtime.fast_mode

    graph = build_agent_graph(provider, progress=progress)
    app = graph.compile()

    final_state = app.invoke(
        {
            "question": question.strip(),
            "run_id": run_id,
            "briefs": [],
            "provider_responses": [],
            "debug_collector": debug_collector,
            "fast_mode": fast_mode,
        }
    )

    briefs = final_state["briefs"]
    debate_transcript: DebateTranscript | None = None
    if debate_rounds > 0:
        debate_transcript = run_debate(
            provider,
            question=question.strip(),
            briefs=briefs,
            rounds=debate_rounds,
            run_id=run_id,
            debug_collector=debug_collector,
            progress=progress,
        )

    if progress is not None:
        progress.on_stage("chair")

    synthesize = getattr(provider, "synthesize_dossier", None)
    if not callable(synthesize):
        msg = "Provider must implement synthesize_dossier for chair synthesis."
        raise TypeError(msg)

    dossier: DecisionDossier = synthesize(
        question=question.strip(),
        briefs=briefs,
        run_id=run_id,
        debug_collector=debug_collector,
        debate_transcript=debate_transcript,
        fast_mode=fast_mode,
    )

    result = CouncilRunResult(
        dossier=dossier,
        agent_briefs=briefs,
        debate_transcript=debate_transcript,
        provider_metadata=provider.metadata,
        provider_responses=final_state["provider_responses"],
    )
    return result, debug_collector


def effective_debate_rounds(requested: int, runtime: RuntimeOptions) -> int:
    if runtime.fast_mode:
        return FAST_DEBATE_ROUNDS
    return max(0, requested)
