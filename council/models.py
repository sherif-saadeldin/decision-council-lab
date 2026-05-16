from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from council.providers.models import ProviderMetadata, ProviderResponse


class AgentRole(str, Enum):
    CONTEXT = "context"
    RESEARCH = "research"
    SKEPTIC = "skeptic"
    RISK = "risk"
    OPERATOR = "operator"
    CHAIR = "chair"


class AgentBrief(BaseModel):
    role: AgentRole
    headline: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    source_refs: list[str] = Field(default_factory=list)


class DecisionDossier(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision_question: str
    assumptions: list[str] = Field(default_factory=list)
    arguments_for: list[str] = Field(default_factory=list)
    arguments_against: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendation: str = ""
    confidence_score: float = Field(ge=0.0, le=1.0, default=0.0)
    kill_criteria: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


RUN_SCHEMA_VERSION = "1.1"


class CouncilRunResult(BaseModel):
    schema_version: str = RUN_SCHEMA_VERSION
    dossier: DecisionDossier
    agent_briefs: list[AgentBrief] = Field(default_factory=list)
    provider_metadata: ProviderMetadata
    provider_responses: list[ProviderResponse] = Field(default_factory=list)

    @property
    def provider_name(self) -> str:
        return self.provider_metadata.provider_name

    @property
    def model_name(self) -> str:
        return self.provider_metadata.model_name


def _rebuild_models() -> None:
    from council.providers.models import ProviderMetadata, ProviderResponse

    CouncilRunResult.model_rebuild(
        _types_namespace={
            "ProviderMetadata": ProviderMetadata,
            "ProviderResponse": ProviderResponse,
        }
    )


_rebuild_models()
