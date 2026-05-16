from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


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
    findings: list[str] = Field(default_factory=list)


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
    provider_name: str = "mock"
    model_name: str = "mock-council-v1"
