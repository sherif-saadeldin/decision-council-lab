from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from council.models import (
    AgentBrief,
    AgentRole,
    DebatePosition,
    DebateRole,
    DebateRound,
    DecisionDossier,
    DecisionType,
    ModeratorSummary,
)
from council.json_extract import extract_json_text
from council.providers.errors import ProviderResponseError

PROPOSED_METRIC_PREFIX = "proposed:"


class _AgentBriefPayload(BaseModel):
    headline: str
    role_specific_finding: str
    evidence_basis: str
    uncertainty: str
    decision_implication: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    proposed_metrics: list[str] = Field(default_factory=list)
    unsupported_assumptions: list[str] = Field(default_factory=list)

    @field_validator("proposed_metrics")
    @classmethod
    def validate_proposed_metrics(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in values:
            text = item.strip()
            if not text:
                continue
            if not text.lower().startswith(PROPOSED_METRIC_PREFIX):
                msg = f"proposed metric must start with '{PROPOSED_METRIC_PREFIX}': {item!r}"
                raise ValueError(msg)
            normalized.append(text)
        return normalized


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
    evidence_gaps: list[str] = Field(default_factory=list)
    proposed_metrics: list[str] = Field(default_factory=list)
    unsupported_assumptions: list[str] = Field(default_factory=list)

    @field_validator("proposed_metrics")
    @classmethod
    def validate_proposed_metrics(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in values:
            text = item.strip()
            if not text:
                continue
            if not text.lower().startswith(PROPOSED_METRIC_PREFIX):
                msg = f"proposed metric must start with '{PROPOSED_METRIC_PREFIX}': {item!r}"
                raise ValueError(msg)
            normalized.append(text)
        return normalized


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
        raise ProviderResponseError(
            provider_name,
            f"agent brief validation failed: {exc}",
            failure_kind="parse_failure",
        ) from exc

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
        evidence_gaps=[item.strip() for item in parsed.evidence_gaps if item.strip()],
        proposed_metrics=parsed.proposed_metrics,
        unsupported_assumptions=[
            item.strip() for item in parsed.unsupported_assumptions if item.strip()
        ],
    )


class _DebatePositionPayload(BaseModel):
    argument: str
    cited_roles: list[str] = Field(default_factory=list)
    responds_to_prior: str
    uncertainty: str


class _ModeratorPayload(BaseModel):
    resolved_points: list[str] = Field(default_factory=list)
    unresolved_points: list[str] = Field(default_factory=list)
    deciding_tensions: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)


class _DebateRoundPayload(BaseModel):
    advocate: _DebatePositionPayload
    skeptic: _DebatePositionPayload
    moderator: _ModeratorPayload


def _position_from_payload(role: DebateRole, payload: _DebatePositionPayload) -> DebatePosition:
    return DebatePosition(
        role=role,
        argument=payload.argument.strip(),
        cited_roles=[item.strip() for item in payload.cited_roles if item.strip()],
        responds_to_prior=payload.responds_to_prior.strip(),
        uncertainty=payload.uncertainty.strip(),
    )


def parse_debate_position_payload(
    raw_text: str,
    *,
    role: DebateRole,
    provider_name: str,
) -> DebatePosition:
    payload = _load_json_object(raw_text, provider_name)
    try:
        parsed = _DebatePositionPayload.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(
            provider_name,
            f"debate position validation failed: {exc}",
            failure_kind="parse_failure",
        ) from exc
    return _position_from_payload(role, parsed)


def parse_debate_round_payload(
    raw_text: str,
    *,
    round_number: int,
    provider_name: str,
) -> DebateRound:
    payload = _load_json_object(raw_text, provider_name)
    try:
        parsed = _DebateRoundPayload.model_validate(payload)
    except ValidationError as exc:
        raise ProviderResponseError(
            provider_name,
            f"debate round validation failed: {exc}",
            failure_kind="parse_failure",
        ) from exc

    return DebateRound(
        round_number=round_number,
        advocate=_position_from_payload(DebateRole.ADVOCATE, parsed.advocate),
        skeptic=_position_from_payload(DebateRole.SKEPTIC, parsed.skeptic),
        moderator=ModeratorSummary(
            resolved_points=[item.strip() for item in parsed.moderator.resolved_points if item.strip()],
            unresolved_points=[
                item.strip() for item in parsed.moderator.unresolved_points if item.strip()
            ],
            deciding_tensions=[
                item.strip() for item in parsed.moderator.deciding_tensions if item.strip()
            ],
            evidence_gaps=[item.strip() for item in parsed.moderator.evidence_gaps if item.strip()],
        ),
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
        raise ProviderResponseError(
            provider_name,
            f"dossier validation failed: {exc}",
            failure_kind="parse_failure",
        ) from exc

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
        evidence_gaps=[item.strip() for item in parsed.evidence_gaps if item.strip()],
        proposed_metrics=parsed.proposed_metrics,
        unsupported_assumptions=[
            item.strip() for item in parsed.unsupported_assumptions if item.strip()
        ],
    )


def _load_json_object(raw_text: str, provider_name: str) -> dict[str, Any]:
    candidates: list[str] = []
    stripped = raw_text.strip()
    if stripped:
        candidates.append(stripped)
    extracted = extract_json_text(raw_text)
    if extracted and extracted not in candidates:
        candidates.append(extracted)

    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(data, dict):
            raise ProviderResponseError(
                provider_name,
                "response JSON must be an object.",
                failure_kind="parse_failure",
            )
        return data

    raise ProviderResponseError(
        provider_name,
        "response was not valid JSON.",
        failure_kind="parse_failure",
    ) from last_error
