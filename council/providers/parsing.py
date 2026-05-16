from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from council.models import AgentBrief, AgentRole, DecisionDossier, DecisionType
from council.providers.errors import ProviderResponseError


class _AgentBriefPayload(BaseModel):
    headline: str
    role_specific_finding: str
    evidence_basis: str
    uncertainty: str
    decision_implication: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)


class _DossierPayload(BaseModel):
    decision_type: DecisionType
    disagreement_resolution: str
    strongest_argument_for: str
    strongest_argument_against: str
    deciding_factor: str
    confidence_rationale: str
    assumptions: list[str]
    arguments_for: list[str]
    arguments_against: list[str]
    risks: list[str]
    recommendation: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    kill_criteria: list[str]
    next_actions: list[str]
    open_questions: list[str]


def parse_agent_brief_payload(
    raw_text: str,
    role: AgentRole,
    *,
    provider_name: str,
) -> AgentBrief:
    payload = _load_json_object(raw_text, provider_name)
    try:
        parsed = _AgentBriefPayload.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(provider_name, f"agent brief validation failed: {exc}") from exc

    return AgentBrief(
        role=role,
        headline=parsed.headline.strip(),
        role_specific_finding=parsed.role_specific_finding.strip(),
        evidence_basis=parsed.evidence_basis.strip(),
        uncertainty=parsed.uncertainty.strip(),
        decision_implication=parsed.decision_implication.strip(),
        reasoning=parsed.reasoning.strip(),
        confidence=parsed.confidence,
        source_refs=parsed.source_refs,
    )


def parse_dossier_payload(
    raw_text: str,
    *,
    question: str,
    run_id: str,
    provider_name: str,
) -> DecisionDossier:
    payload = _load_json_object(raw_text, provider_name)
    try:
        parsed = _DossierPayload.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(provider_name, f"dossier validation failed: {exc}") from exc

    return DecisionDossier(
        run_id=run_id,
        decision_question=question,
        decision_type=parsed.decision_type,
        disagreement_resolution=parsed.disagreement_resolution.strip(),
        strongest_argument_for=parsed.strongest_argument_for.strip(),
        strongest_argument_against=parsed.strongest_argument_against.strip(),
        deciding_factor=parsed.deciding_factor.strip(),
        confidence_rationale=parsed.confidence_rationale.strip(),
        assumptions=parsed.assumptions,
        arguments_for=parsed.arguments_for,
        arguments_against=parsed.arguments_against,
        risks=parsed.risks,
        recommendation=parsed.recommendation.strip(),
        confidence_score=parsed.confidence_score,
        kill_criteria=parsed.kill_criteria,
        next_actions=parsed.next_actions,
        open_questions=parsed.open_questions,
    )


def _load_json_object(raw_text: str, provider_name: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError(provider_name, "response was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ProviderResponseError(provider_name, "response JSON must be an object.")
    return data
