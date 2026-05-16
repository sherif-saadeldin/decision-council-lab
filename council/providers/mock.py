from __future__ import annotations

import json
import time

from council.debate_prompts import (
    ADVOCATE_INSTRUCTIONS,
    SKEPTIC_INSTRUCTIONS,
    debate_round_instructions,
    format_debate_round_user_prompt,
)
from council.debate_runner import PositionKind
from council.models import (
    AgentBrief,
    AgentRole,
    DebatePosition,
    DebateRole,
    DebateRound,
    DebateTranscript,
    DecisionDossier,
    DecisionType,
    ModeratorSummary,
)
from council.prompt_debug import PromptDebugCollector
from council.prompts import (
    agent_instructions,
    chair_instructions,
    format_agent_user_prompt,
    format_dossier_user_prompt,
)
from council.providers.base import LLMProvider
from council.providers.models import ProviderMetadata, ProviderRequest, ProviderResponse
from council.providers.parsing import PROPOSED_METRIC_PREFIX


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

    def live_ping(self) -> tuple[bool, str | None]:
        from council.live_completion import validate_ping_json

        return validate_ping_json('{"ok": true}')

    def complete(self, request: ProviderRequest) -> ProviderResponse:
        started = time.perf_counter()
        instructions = agent_instructions(request.role)
        user_content = format_agent_user_prompt(request)
        if request.debug_collector is not None:
            request.debug_collector.record(
                step=f"{request.role.value}_agent",
                role=request.role.value,
                instructions=instructions,
                user_content=user_content,
            )
        brief = self._build_brief(request.role, request.question, request.prior_briefs)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        return ProviderResponse(
            brief=brief,
            token_usage=None,
            latency_ms=latency_ms,
            raw_response=json.dumps(brief.model_dump(mode="json")),
        )

    def run_debate_round(
        self,
        *,
        question: str,
        briefs: list[AgentBrief],
        prior_rounds: list[DebateRound],
        round_number: int,
        total_rounds: int,
        run_id: str,
        debug_collector: PromptDebugCollector | None = None,
    ) -> DebateRound:
        instructions = debate_round_instructions()
        user_content = format_debate_round_user_prompt(
            question=question,
            briefs=briefs,
            prior_rounds=prior_rounds,
            round_number=round_number,
            total_rounds=total_rounds,
        )
        if debug_collector is not None:
            debug_collector.record(
                step=f"debate_round_{round_number}",
                role="debate",
                instructions=instructions,
                user_content=user_content,
            )
        return self._build_debate_round(
            question=question,
            briefs=briefs,
            prior_rounds=prior_rounds,
            round_number=round_number,
        )

    def generate_debate_position(
        self,
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
        if kind == "risk_officer":
            position = DebatePosition(
                role=DebateRole.SKEPTIC,
                argument=(
                    f"[{self.metadata.model_name}] Risk challenges advocate and skeptic: "
                    "second-order execution drag and false confidence from polished dossiers "
                    "must be weighed before proceeding."
                ),
                cited_roles=["risk", "skeptic"],
                responds_to_prior=(
                    f"Advocate: {advocate_argument[:200]} | Skeptic: {skeptic_argument[:200]}"
                ),
                uncertainty="Whether mitigations are feasible within the stated constraints.",
            )
        else:
            full_round = self._build_debate_round(
                question=question,
                briefs=briefs,
                prior_rounds=prior_rounds,
                round_number=round_number,
            )
            position = full_round.advocate if kind == "advocate" else full_round.skeptic
            position = position.model_copy(
                update={"argument": f"[{self.metadata.model_name}] {position.argument}"}
            )
        if debug_collector is not None:
            debug_collector.record(
                step=f"debate_{kind}_round_{round_number}",
                role=kind,
                instructions=ADVOCATE_INSTRUCTIONS if kind == "advocate" else SKEPTIC_INSTRUCTIONS,
                user_content=question,
            )
        return position

    def synthesize_dossier(
        self,
        question: str,
        briefs: list[AgentBrief],
        run_id: str,
        *,
        debug_collector: PromptDebugCollector | None = None,
        debate_transcript: DebateTranscript | None = None,
        fast_mode: bool = False,
    ) -> DecisionDossier:
        instructions = chair_instructions()
        user_content = format_dossier_user_prompt(question, briefs, debate_transcript)
        if fast_mode:
            user_content = f"{user_content}\n\nFast mode: chair synthesis should be concise."
        if debug_collector is not None:
            debug_collector.record(
                step="chair_synthesis",
                role="chair",
                instructions=instructions,
                user_content=user_content,
            )

        internal_tool = self._mentions_internal_tool(question)
        if internal_tool:
            decision_type = DecisionType.PROCEED_WITH_CONSTRAINTS
            recommendation = (
                "Proceed with building the decision council as an internal tool first, "
                "with scope constrained to CLI + structured artifacts until dossier quality is proven."
            )
            confidence = 0.78
        else:
            decision_type = DecisionType.PROCEED
            recommendation = "Proceed with a time-boxed internal prototype before external packaging."
            confidence = 0.72

        strongest_for = (
            "Internal tooling de-risks architecture and produces reusable decision artifacts."
        )
        strongest_against = (
            "Mock-only reasoning can look rigorous while remaining generic on novel questions."
        )
        deciding_factor = (
            "The question prioritizes learning velocity and traceability over immediate distribution."
            if internal_tool
            else "The cost of a small prototype is lower than committing to a full platform prematurely."
        )

        disagreement_resolution = (
            "Research and Operator favor shipping an internal engine now; Skeptic and Risk warn "
            "against overconfidence in mock output. Chair weights execution learning over polish, "
            "but requires measurable kill criteria before expanding scope."
        )
        if debate_transcript and debate_transcript.rounds:
            last = debate_transcript.rounds[-1]
            tensions = ", ".join(last.moderator.deciding_tensions) or "execution vs quality"
            disagreement_resolution = (
                f"After {debate_transcript.rounds_completed} debate rounds, the chair weighs council "
                f"briefs and debate exchanges. Moderator deciding tensions: {tensions}. "
                f"Unresolved: {', '.join(debate_transcript.final_unresolved_disagreements) or '(none)'}."
            )
            if last.moderator.deciding_tensions:
                deciding_factor = (
                    f"Debate moderator identified deciding tension: {last.moderator.deciding_tensions[0]}"
                )

        return DecisionDossier(
            run_id=run_id,
            decision_question=question,
            decision_type=decision_type,
            disagreement_resolution=disagreement_resolution,
            strongest_argument_for=strongest_for,
            strongest_argument_against=strongest_against,
            deciding_factor=deciding_factor,
            confidence_rationale=(
                f"Confidence is {confidence:.2f} because specialist briefs align on sequencing "
                "(internal first) but disagree on output quality until real providers are wired."
            ),
            assumptions=[
                "The team can iterate quickly without external customer commitments.",
                "Structured dossiers will be reviewed by humans before driving major commitments.",
                "Provider contracts remain stable as OpenAI and later vendors are added.",
            ],
            arguments_for=[
                strongest_for,
                "Agent roles map cleanly to executive decision workflows.",
                "Saved JSON/Markdown runs create an audit trail for revisits.",
            ],
            arguments_against=[
                strongest_against,
                "Manual CLI usage limits adoption until later slice enhancements.",
                "Single-pass council may miss issues that debate rounds would surface.",
            ],
            risks=[
                "Scope creep into SaaS, auth, or trading features before core engine is stable.",
                "Overfitting prompts to sample questions instead of general decision quality.",
                "False confidence if chair synthesis reads more decisive than evidence supports.",
            ],
            recommendation=recommendation,
            confidence_score=confidence,
            kill_criteria=[
                "proposed: two consecutive schema-valid dossier runs on representative questions — stop if not met",
                "proposed: internal stakeholders reference saved runs within one review cycle — stop if not met",
            ],
            next_actions=[
                "Run three real product decisions through mock and OpenAI modes and compare dossiers.",
                "Add debate rounds only after baseline dossier quality is stable.",
                "Track disagreement_resolution usefulness with a short reviewer checklist.",
            ],
            open_questions=[
                "Which decisions require multi-round debate versus a single council pass?",
                "What retention policy should apply to runs stored on disk?",
                "When should confidence scores block automated downstream actions?",
            ],
            evidence_gaps=[
                "No baseline quality rubric scores comparing mock vs live providers.",
                "Stakeholder adoption metrics were not provided in the question.",
            ],
            proposed_metrics=[
                "proposed: two consecutive schema-valid dossier runs on representative questions",
                "proposed: internal stakeholders reference saved runs within one review cycle",
            ],
            unsupported_assumptions=[
                "Assumes the team can iterate without external customer commitments.",
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

    def _build_debate_round(
        self,
        *,
        question: str,
        briefs: list[AgentBrief],
        prior_rounds: list[DebateRound],
        round_number: int,
    ) -> DebateRound:
        if round_number == 1:
            advocate = DebatePosition(
                role=DebateRole.ADVOCATE,
                argument=(
                    "Proceeding is justified because research and context briefs support an internal "
                    "CLI-first engine with saved artifacts before broader packaging."
                ),
                cited_roles=["research", "context"],
                responds_to_prior="Opening round — no prior debate positions.",
                uncertainty="Whether mock output quality will transfer when live models are wired.",
            )
            skeptic = DebatePosition(
                role=DebateRole.SKEPTIC,
                argument=(
                    "Pausing full commitment is justified because the skeptic and risk briefs warn that "
                    "polished dossiers can mask weak reasoning without adversarial review."
                ),
                cited_roles=["skeptic", "risk"],
                responds_to_prior=advocate.argument,
                uncertainty="Whether proposed kill criteria will be enforced by human reviewers.",
            )
            moderator = ModeratorSummary(
                resolved_points=[
                    "Both sides agree the decision concerns internal tooling sequencing, not GTM launch."
                ],
                unresolved_points=[
                    "Whether to proceed now or wait for live-provider quality proof.",
                    "How to validate dossier usefulness without invented metrics.",
                ],
                deciding_tensions=[
                    "Execution learning speed vs evidence quality before scaling scope.",
                ],
                evidence_gaps=[
                    "No user-provided success metrics or comparison between mock and live outputs.",
                ],
            )
        else:
            prior = prior_rounds[-1]
            advocate = DebatePosition(
                role=DebateRole.ADVOCATE,
                argument=(
                    "The skeptic's quality concern does not block a constrained internal pilot because "
                    "operator and research briefs emphasize CLI artifacts and iterative prompt tuning."
                ),
                cited_roles=["operator", "research"],
                responds_to_prior=prior.skeptic.argument,
                uncertainty="Engineering capacity to run representative decisions through live providers.",
            )
            skeptic = DebatePosition(
                role=DebateRole.SKEPTIC,
                argument=(
                    "The advocate's pilot path still risks false confidence unless the skeptic brief's "
                    "proposed schema-valid runs and human review gates are treated as hard constraints."
                ),
                cited_roles=["skeptic", "risk"],
                responds_to_prior=advocate.argument,
                uncertainty="Whether one review cycle is enough to detect systematic reasoning gaps.",
            )
            moderator = ModeratorSummary(
                resolved_points=[
                    "Both sides accept a constrained internal scope with human review of artifacts."
                ],
                unresolved_points=[
                    "Timing of expanding beyond mock mode to live provider comparisons.",
                ],
                deciding_tensions=[
                    "Pilot now with constraints vs delay until live-provider rubric exists.",
                ],
                evidence_gaps=[
                    "No baseline rubric scores comparing mock vs live dossiers on this question.",
                ],
            )

        return DebateRound(
            round_number=round_number,
            advocate=advocate,
            skeptic=skeptic,
            moderator=moderator,
        )

    def _context_brief(self, question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return _make_brief(
            role=AgentRole.CONTEXT,
            headline="Decision is an internal capability bet, not a go-to-market launch.",
            role_specific_finding=(
                f"The question '{question[:80]}' is primarily about build-vs-buy sequencing "
                "for an internal decision system."
            ),
            evidence_basis=(
                "Wording emphasizes internal tool-first delivery and engineering leverage."
            ),
            uncertainty="Exact stakeholder map and success metrics are not fully specified.",
            decision_implication="Chair should optimize for learning speed and artifact quality, not distribution.",
            reasoning=(
                "Stakeholders are likely product and engineering leaders evaluating whether to invest "
                "in an internal council workflow before external packaging."
            ),
            confidence=0.74,
            evidence_gaps=["No explicit success metrics or stakeholder list in the question."],
            proposed_metrics=[
                "proposed: reviewer signs off dossier usefulness on a simple qualitative rubric",
            ],
        )

    def _research_brief(self, question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return _make_brief(
            role=AgentRole.RESEARCH,
            headline="Precedent supports CLI/internal tooling before UI and SaaS packaging.",
            role_specific_finding="Comparable decision systems begin as internal workflows with saved artifacts.",
            evidence_basis=(
                "Pattern from internal AI tooling: notebook/CLI prototypes precede productized surfaces."
            ),
            uncertainty="Limited evidence on how quickly mock output transfers to real LLM quality.",
            decision_implication="Favor an internal engine milestone with explicit quality gates.",
            reasoning=(
                f"Research angle on '{question[:80]}' suggests reducing scope to structured runs "
                "that teams can review asynchronously."
            ),
            confidence=0.76,
            evidence_gaps=["No user-provided comparison data between mock and live provider output."],
        )

    def _skeptic_brief(self, question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return _make_brief(
            role=AgentRole.SKEPTIC,
            headline="A polished dossier can mask weak underlying reasoning in mock mode.",
            role_specific_finding="The council may appear decisive without adversarial depth.",
            evidence_basis="Mock agents use templated phrasing that may not stress-test the specific question.",
            uncertainty="How much skepticism is enough before delaying a useful internal prototype.",
            decision_implication="Chair must include kill criteria and explicit uncertainty in the final call.",
            reasoning=(
                f"For '{question[:80]}', the risk is approving internal tooling because the format "
                "looks executive-ready, not because reasoning was truly tested."
            ),
            confidence=0.71,
            evidence_gaps=["No adversarial review process or external benchmark for dossier quality."],
            proposed_metrics=[
                "proposed: two consecutive schema-valid dossier runs on representative questions",
            ],
            unsupported_assumptions=[
                "Assumes templated mock phrasing generalizes to novel decisions.",
            ],
        )

    def _risk_brief(self, _question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return _make_brief(
            role=AgentRole.RISK,
            headline="Scope drift and false confidence are the dominant failure modes.",
            role_specific_finding="Adding platform features early could collapse focus on dossier quality.",
            evidence_basis="Roadmap slices explicitly defer UI, auth, and multi-provider complexity.",
            uncertainty="Whether kill criteria will be enforced in practice by reviewers.",
            decision_implication="Proceed only with constraints that cap scope and require human review.",
            reasoning=(
                "Operational risk is not building the wrong tool—it is building too much too soon "
                "while trusting low-cost mock runs as if they were audited analysis."
            ),
            confidence=0.73,
            unsupported_assumptions=["Assumes kill criteria will be enforced by reviewers."],
        )

    def _operator_brief(self, _question: str, _prior: list[AgentBrief]) -> AgentBrief:
        return _make_brief(
            role=AgentRole.OPERATOR,
            headline="Next execution focus: harden prompts, run real decisions, compare mock vs OpenAI.",
            role_specific_finding="Execution path is CLI-first with saved runs under versioned schema.",
            evidence_basis="Current repo supports uv workflows, structured JSON, and provider switching via env.",
            uncertainty="Engineering time to tune OpenAI prompts to beat mock usefulness.",
            decision_implication="Approve internal rollout to a small pilot group with weekly dossier review.",
            reasoning=(
                "Operators can ship value by running the council on live decisions and iterating prompts "
                "before any UI or external user onboarding."
            ),
            confidence=0.77,
        )


def _make_brief(
    *,
    role: AgentRole,
    headline: str,
    role_specific_finding: str,
    evidence_basis: str,
    uncertainty: str,
    decision_implication: str,
    reasoning: str,
    confidence: float,
    source_refs: list[str] | None = None,
    evidence_gaps: list[str] | None = None,
    proposed_metrics: list[str] | None = None,
    unsupported_assumptions: list[str] | None = None,
) -> AgentBrief:
    for metric in proposed_metrics or []:
        text = metric.strip()
        if text and not text.lower().startswith(PROPOSED_METRIC_PREFIX):
            msg = f"mock proposed_metrics must start with '{PROPOSED_METRIC_PREFIX}': {metric!r}"
            raise ValueError(msg)
    return AgentBrief(
        role=role,
        headline=headline,
        role_specific_finding=role_specific_finding,
        evidence_basis=evidence_basis,
        uncertainty=uncertainty,
        decision_implication=decision_implication,
        reasoning=reasoning,
        confidence=confidence,
        source_refs=source_refs or [],
        evidence_gaps=evidence_gaps or [],
        proposed_metrics=proposed_metrics or [],
        unsupported_assumptions=unsupported_assumptions or [],
    )
