from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

from council.review_model import DecisionReview, default_review

if TYPE_CHECKING:
    from council.decision_thread import DecisionThreadMeta
    from council.providers.models import ProviderMetadata, ProviderResponse


class AgentRole(str, Enum):
    CONTEXT = "context"
    RESEARCH = "research"
    SKEPTIC = "skeptic"
    RISK = "risk"
    OPERATOR = "operator"
    CHAIR = "chair"


class DebateRole(str, Enum):
    ADVOCATE = "advocate"
    SKEPTIC = "skeptic"
    MODERATOR = "moderator"


class DecisionType(str, Enum):
    PROCEED = "proceed"
    PROCEED_WITH_CONSTRAINTS = "proceed_with_constraints"
    PAUSE = "pause"
    REJECT = "reject"


class AgentBrief(BaseModel):
    role: AgentRole
    headline: str
    role_specific_finding: str
    evidence_basis: str
    uncertainty: str
    decision_implication: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    source_refs: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    proposed_metrics: list[str] = Field(default_factory=list)
    unsupported_assumptions: list[str] = Field(default_factory=list)


class DebatePosition(BaseModel):
    role: DebateRole
    argument: str
    cited_roles: list[str] = Field(default_factory=list)
    responds_to_prior: str = ""
    uncertainty: str = ""


class ModeratorSummary(BaseModel):
    resolved_points: list[str] = Field(default_factory=list)
    unresolved_points: list[str] = Field(default_factory=list)
    deciding_tensions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)


class DebateRound(BaseModel):
    round_number: int = Field(ge=1)
    advocate: DebatePosition
    skeptic: DebatePosition
    risk_officer: DebatePosition | None = None
    moderator: ModeratorSummary


class DebateTranscript(BaseModel):
    rounds: list[DebateRound] = Field(default_factory=list)
    rounds_completed: int = Field(ge=0, default=0)
    final_unresolved_disagreements: list[str] = Field(default_factory=list)


class DecisionDossier(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision_question: str
    decision_type: DecisionType = DecisionType.PAUSE
    disagreement_resolution: str = ""
    strongest_argument_for: str = ""
    strongest_argument_against: str = ""
    deciding_factor: str = ""
    confidence_rationale: str = ""
    assumptions: list[str] = Field(default_factory=list)
    arguments_for: list[str] = Field(default_factory=list)
    arguments_against: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendation: str = ""
    direct_answer: str = ""
    why_this_decision: list[str] = Field(default_factory=list)
    what_would_change_mind: list[str] = Field(default_factory=list)
    do_not_do: list[str] = Field(default_factory=list)
    approval_gate: str = ""
    confidence_score: float = Field(ge=0.0, le=1.0, default=0.0)
    kill_criteria: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    proposed_metrics: list[str] = Field(default_factory=list)
    unsupported_assumptions: list[str] = Field(default_factory=list)


RUN_SCHEMA_VERSION = "1.9"


class RoleAssignmentRecord(BaseModel):
    slot: str
    preset: str
    provider_name: str
    model_name: str
    mode: str


class PromptRunMetadata(BaseModel):
    system_profile: str = "default"
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    prompt_files: list[str] = Field(default_factory=list)
    prompt_hash: str = ""


class CouncilCostEstimateRecord(BaseModel):
    routing_mode: str
    debate_rounds: int
    llm_call_count: int
    estimated_cost_usd: float
    estimated_cost_usd_low: float
    estimated_cost_usd_high: float
    is_estimate: bool = True
    slot_lines: list[dict] = Field(default_factory=list)


DEFAULT_DEBATE_ROUNDS = 2


class CouncilRunResult(BaseModel):
    schema_version: str = RUN_SCHEMA_VERSION
    dossier: DecisionDossier
    agent_briefs: list[AgentBrief] = Field(default_factory=list)
    debate_transcript: DebateTranscript | None = None
    provider_metadata: ProviderMetadata
    provider_responses: list[ProviderResponse] = Field(default_factory=list)
    council_mode: str | None = None
    multi_model: bool = False
    role_play_warning: str | None = None
    role_assignments: list[RoleAssignmentRecord] = Field(default_factory=list)
    routing_mode: str | None = None
    cost_estimate: CouncilCostEstimateRecord | None = None
    prompt_metadata: PromptRunMetadata | None = None
    # Decision-thread linkage (Slice 5.8). Only present when this run was
    # produced with an explicit prior-decision context attached.
    decision_thread: "DecisionThreadMeta | None" = None
    # Decision lifecycle + review metadata (Slice 5.9). New runs default to
    # the `draft` state with an empty history; review CLI/chat commands and
    # `council/review.py` mutate this in place.
    review: DecisionReview = Field(default_factory=default_review)

    @property
    def provider_name(self) -> str:
        return self.provider_metadata.provider_name

    @property
    def model_name(self) -> str:
        return self.provider_metadata.model_name


def _rebuild_models() -> None:
    from council.decision_thread import DecisionThreadMeta
    from council.providers.models import ProviderMetadata, ProviderResponse

    CouncilRunResult.model_rebuild(
        _types_namespace={
            "DecisionThreadMeta": DecisionThreadMeta,
            "ProviderMetadata": ProviderMetadata,
            "ProviderResponse": ProviderResponse,
        }
    )


_rebuild_models()
