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
from council.prompt_debug import PromptDebugCollector
from council.providers.base import LLMProvider
from council.providers.factory import create_provider
from council.providers.models import ProviderRequest, ProviderResponse

AGENT_PIPELINE: tuple[AgentRole, ...] = (
    AgentRole.CONTEXT,
    AgentRole.RESEARCH,
    AgentRole.SKEPTIC,
    AgentRole.RISK,
    AgentRole.OPERATOR,
)


class CouncilState(TypedDict):
    question: str
    run_id: str
    briefs: list[AgentBrief]
    provider_responses: list[ProviderResponse]
    debug_collector: PromptDebugCollector | None


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or Settings.from_env()
    return create_provider(settings)


def _make_agent_node(role: AgentRole, provider: LLMProvider):
    def node(state: CouncilState) -> dict[str, list[AgentBrief] | list[ProviderResponse]]:
        response = provider.complete(
            ProviderRequest(
                role=role,
                question=state["question"],
                prior_briefs=state["briefs"],
                run_id=state["run_id"],
                debug_collector=state["debug_collector"],
            )
        )
        return {
            "briefs": [*state["briefs"], response.brief],
            "provider_responses": [*state["provider_responses"], response],
        }

    return node


def build_agent_graph(provider: LLMProvider) -> StateGraph:
    graph = StateGraph(CouncilState)
    previous = START

    for role in AGENT_PIPELINE:
        node_name = role.value
        graph.add_node(node_name, _make_agent_node(role, provider))
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
) -> tuple[CouncilRunResult, PromptDebugCollector | None]:
    settings = settings or Settings.from_env()
    provider = get_provider(settings)
    run_id = str(uuid4())
    debug_collector = PromptDebugCollector() if save_prompt_debug else None

    graph = build_agent_graph(provider)
    app = graph.compile()

    final_state = app.invoke(
        {
            "question": question.strip(),
            "run_id": run_id,
            "briefs": [],
            "provider_responses": [],
            "debug_collector": debug_collector,
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
        )

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
    )

    result = CouncilRunResult(
        dossier=dossier,
        agent_briefs=briefs,
        debate_transcript=debate_transcript,
        provider_metadata=provider.metadata,
        provider_responses=final_state["provider_responses"],
    )
    return result, debug_collector
