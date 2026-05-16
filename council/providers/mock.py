from __future__ import annotations

import json
import time

from council.models import AgentBrief, AgentRole, DecisionDossier
from council.providers.base import LLMProvider
from council.providers.models import ProviderMetadata, ProviderRequest, ProviderResponse


class MockProvider(LLMProvider):
    def __init__(self, model_name: str = "mock-council-v1", mode: str = "mock") -> None:
        self._metadata = ProviderMetadata(
            provider_name="mock",
            model_name=model_name,
            mode=mode,
            supports_structured_output=True,
            supports_streaming=False,
        )

    @property
    def metadata(self) -> ProviderMetadata:
        return self._metadata

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        started = time.perf_counter()
        brief = self._build_brief(request.role, request.question, request.prior_briefs)
        latency_ms = (time.perf_counter() - started) * 1000
        return ProviderResponse(
            brief=brief,
            token_usage=None,
            latency_ms=round(latency_ms, 3),
            raw_response=json.dumps(brief.model_dump(mode="json")),
        )

    def synthesize_dossier(
        self,
        question: str,
        briefs: list[AgentBrief],
        run_id: str,
    ) -> DecisionDossier:
        internal_tool = self._mentions_internal_tool(question)
        recommendation = (
            "Build the decision council engine as an internal tool first."
            if internal_tool
            else "Validate the idea with a small internal prototype before wider rollout."
        )
        confidence = 0.78 if internal_tool else 0.72

        return DecisionDossier(
            run_id=run_id,
            decision_question=question,
            assumptions=[
                "The team can iterate quickly without external customer commitments.",
                "Mock-mode council output is sufficient to shape early product direction.",
                "Provider integration can be swapped in without rewriting agent roles.",
            ],
            arguments_for=[
                "Internal tooling de-risks architecture before UI or SaaS packaging.",
                "Structured dossiers create an audit trail for important decisions.",
                "Agent roles map cleanly to executive decision workflows.",
            ],
            arguments_against=[
                "Without real LLM calls, conclusions may feel generic until providers land.",
                "Manual CLI usage limits adoption until later slice enhancements.",
                "Parallel specialist debate is simplified in the first mock slice.",
            ],
            risks=[
                "Scope creep into SaaS, auth, or trading features before core engine is stable.",
                "Overfitting mock heuristics to sample questions instead of real reasoning.",
                "Underestimating effort to add provider parity across vendors.",
            ],
            recommendation=recommendation,
            confidence_score=confidence,
            kill_criteria=[
                "Two consecutive runs fail to produce a complete dossier on representative questions.",
                "Structured output validation errors exceed an agreed error budget.",
                "Internal users stop referencing saved run artifacts within one sprint.",
            ],
            next_actions=[
                "Run the council on three real product decisions and review dossier quality.",
                "Wire OpenAI provider in Slice 2 behind the existing provider interface.",
                "Add tests that lock dossier schema and provider contracts.",
            ],
            open_questions=[
                "Which decisions require multi-round debate versus a single council pass?",
                "What retention policy should apply to runs stored on disk?",
                "When should confidence scores block automated downstream actions?",
            ],
        )

    def _build_brief(
        self,
        role: AgentRole,
        question: str,
        prior_briefs: list[AgentBrief],
    ) -> AgentBrief:
        builders = {
            AgentRole.CONTEXT: self._context_brief,
            AgentRole.RESEARCH: self._research_brief,
            AgentRole.SKEPTIC: self._skeptic_brief,
            AgentRole.RISK: self._risk_brief,
            AgentRole.OPERATOR: self._operator_brief,
        }
        builder = builders.get(role)
        if builder is None:
            msg = f"Mock provider does not generate role: {role}"
            raise ValueError(msg)
        return builder(question, prior_briefs)

    def _mentions_internal_tool(self, question: str) -> bool:
        lowered = question.lower()
        return "internal" in lowered or "tool first" in lowered

    def _context_brief(self, question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return AgentBrief(
            role=AgentRole.CONTEXT,
            headline="Frame the decision as an engineering leverage bet.",
            reasoning=(
                f"Decision under review: {question}. "
                "Stakeholders likely include product, engineering, and future operators. "
                "Success means faster, higher-quality decisions with traceable rationale."
            ),
            confidence=0.74,
            source_refs=[],
        )

    def _research_brief(self, question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return AgentBrief(
            role=AgentRole.RESEARCH,
            headline="Favor a narrow internal engine before external packaging.",
            reasoning=(
                "Comparable systems start as CLI or notebook workflows, then add UI. "
                "Structured JSON + Markdown artifacts support async review. "
                f"Question emphasis: {question[:120]}"
            ),
            confidence=0.76,
            source_refs=[],
        )

    def _skeptic_brief(self, _question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return AgentBrief(
            role=AgentRole.SKEPTIC,
            headline="Challenge premature platform expansion.",
            reasoning=(
                "A mock council can look complete while hiding weak reasoning quality. "
                "Adding providers late may require revisiting prompt contracts. "
                "Internal-only scope should stay enforced until dossier quality is proven."
            ),
            confidence=0.71,
            source_refs=[],
        )

    def _risk_brief(self, _question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return AgentBrief(
            role=AgentRole.RISK,
            headline="Primary risks are scope drift and false confidence.",
            reasoning=(
                "Shipping UI or auth early splits focus from the council core. "
                "Low-cost mock runs may encourage decisions without human review. "
                "Missing kill criteria can let weak recommendations persist too long."
            ),
            confidence=0.73,
            source_refs=[],
        )

    def _operator_brief(self, _question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return AgentBrief(
            role=AgentRole.OPERATOR,
            headline="Operational path: CLI now, providers next, UI last.",
            reasoning=(
                "Persist every run under a unique run_id for reproducibility. "
                "Keep agent roles explicit in code and saved artifacts. "
                "Use uv-managed workflows for local execution and CI parity."
            ),
            confidence=0.77,
            source_refs=[],
        )
