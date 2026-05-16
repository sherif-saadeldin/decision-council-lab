from __future__ import annotations

from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from council.config import Settings
from council.models import AgentBrief, AgentRole, CouncilRunResult, DecisionDossier
from council.providers.base import LLMProvider
from council.providers.mock import MockProvider

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
    dossier: DecisionDossier | None


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or Settings.from_env()
    if settings.llm_mode != "mock":
        msg = (
            f"Unsupported LLM_MODE={settings.llm_mode!r}. "
            "Mock mode only until provider slices land (OpenAI in Slice 2)."
        )
        raise ValueError(msg)
    return MockProvider(model_name=settings.mock_model)


def _make_agent_node(role: AgentRole, provider: LLMProvider):
    def node(state: CouncilState) -> dict[str, list[AgentBrief]]:
        brief = provider.generate_brief(
            role=role,
            question=state["question"],
            prior_briefs=state["briefs"],
        )
        return {"briefs": [*state["briefs"], brief]}

    return node


def _make_chair_node(provider: MockProvider):
    def node(state: CouncilState) -> dict[str, DecisionDossier]:
        dossier = provider.synthesize_dossier(
            question=state["question"],
            briefs=state["briefs"],
            run_id=state["run_id"],
        )
        return {"dossier": dossier}

    return node


def build_council_graph(provider: LLMProvider) -> StateGraph:
    if not isinstance(provider, MockProvider):
        msg = "Slice 1 council graph requires MockProvider for chair synthesis."
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
        provider_name=provider.name,
        model_name=provider.model_name,
    )
