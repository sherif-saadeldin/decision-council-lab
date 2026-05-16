from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from council.config import Settings
from council.models import AgentBrief, AgentRole, CouncilRunResult, DecisionDossier
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
    dossier: DecisionDossier | None


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
            )
        )
        return {
            "briefs": [*state["briefs"], response.brief],
            "provider_responses": [*state["provider_responses"], response],
        }

    return node


def _make_chair_node(provider: LLMProvider):
    synthesize = getattr(provider, "synthesize_dossier", None)
    if not callable(synthesize):
        msg = "Provider must implement synthesize_dossier for chair synthesis."
        raise TypeError(msg)

    def node(state: CouncilState) -> dict[str, DecisionDossier]:
        dossier = synthesize(
            question=state["question"],
            briefs=state["briefs"],
            run_id=state["run_id"],
        )
        return {"dossier": dossier}

    return node


def build_council_graph(provider: LLMProvider) -> StateGraph:
    if not callable(getattr(provider, "synthesize_dossier", None)):
        msg = "Provider must implement synthesize_dossier for chair synthesis."
        raise TypeError(msg)

    graph = StateGraph(CouncilState)
    previous = START

    for role in AGENT_PIPELINE:
        node_name = role.value
        graph.add_node(node_name, _make_agent_node(role, provider))
        graph.add_edge(previous, node_name)
        previous = node_name

    graph.add_node(AgentRole.CHAIR.value, _make_chair_node(provider))
    graph.add_edge(previous, AgentRole.CHAIR.value)
    graph.add_edge(AgentRole.CHAIR.value, END)
    return graph


def run_council(question: str, settings: Settings | None = None) -> CouncilRunResult:
    settings = settings or Settings.from_env()
    provider = get_provider(settings)
    run_id = str(uuid4())

    graph = build_council_graph(provider)
    app = graph.compile()

    final_state = app.invoke(
        {
            "question": question.strip(),
            "run_id": run_id,
            "briefs": [],
            "provider_responses": [],
            "dossier": None,
        }
    )

    dossier = final_state["dossier"]
    if dossier is None:
        msg = "Council run completed without a decision dossier."
        raise RuntimeError(msg)

    return CouncilRunResult(
        dossier=dossier,
        agent_briefs=final_state["briefs"],
        provider_metadata=provider.metadata,
        provider_responses=final_state["provider_responses"],
    )
